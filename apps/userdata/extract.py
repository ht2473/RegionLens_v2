"""
Длинная таблица из рецепта: ряд (показатель, разрезы, период года), территория, год, значение.

Строки читаются по форме и ролям распознавания с решениями человека; подписи территорий —
по сопоставлению столбца с ответами. Большая таблица сначала сужается в DuckDB (нужные
столбцы и отобранные значения разрезов), остальное — построчно. Коды-маски вместо чисел
(9999, 8888…) становятся пропусками (``masks``). Итог — файл Parquet в каталоге версии
и перечень рядов в отчёте: по перечню строится шаг «Показатели», из файла — сборка набора.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from collections import Counter, defaultdict
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from django.conf import settings
from django.utils.translation import gettext as _

from apps.catalog.constants import ValueQuality
from apps.sources import periods

from . import cells, jobs, masks, matching, naming, recognize, tables
from .ingest import IngestError, cell_text, iter_rows
from .models import DatasetVersion

logger = logging.getLogger(__name__)

EXTRACT_FILE = "extract.parquet"
# Редакция извлечения: меняется вместе с устройством файла и отчёта (2 — коды-маски).
REVISION = 2
# Причина пропуска со словами о тайне — значение скрыто, а не отсутствует.
_HIDDEN_REASONS = ("конфиденц", "скрыт", "confidential", "suppress", "hidden")
# Строк большой таблицы за одно обращение к DuckDB.
BATCH_ROWS = 50_000


class ExtractError(ValueError):
    """Таблицу по рецепту не разобрать: нет года, слишком много рядов или значений."""


@dataclass(slots=True)
class SeriesInfo:
    """Ряд таблицы: показатель, значения разрезов, период года и что о нём известно."""

    indicator: str
    slices: tuple[tuple[str, str], ...]
    period: str
    units: Counter[str] = field(default_factory=Counter)
    values: int = 0
    regions: set[str] = field(default_factory=set)
    years: set[int] = field(default_factory=set)

    @property
    def code(self) -> str:
        return naming.series_code(self.indicator, self.slices, self.period)


@dataclass(slots=True)
class Collector:
    """Наблюдения по рядам: повторы сливаются, расхождения считаются."""

    limit: int
    series: dict[tuple[str, tuple[tuple[str, str], ...], str], int] = field(default_factory=dict)
    infos: list[SeriesInfo] = field(default_factory=list)
    # (ряд, территория или подпись вне справочника, год) → номер наблюдения.
    seen: dict[tuple[int, str, int], int] = field(default_factory=dict)
    series_index: list[int] = field(default_factory=list)
    codes: list[str | None] = field(default_factory=list)
    labels: list[str | None] = field(default_factory=list)
    years: list[int] = field(default_factory=list)
    values: list[float | None] = field(default_factory=list)
    qualities: list[int] = field(default_factory=list)
    conflicts: int = 0
    text_cells: int = 0
    skipped: Counter[str] = field(default_factory=Counter)
    notes: dict[str, str] = field(default_factory=dict)

    def add(
        self,
        *,
        indicator: str,
        slices: tuple[tuple[str, str], ...],
        period: str,
        unit: str,
        place: tuple[str | None, str | None],
        year: int,
        cell: cells.Cell,
        hidden: bool = False,
    ) -> None:
        """Добавить наблюдение; пустая ячейка не наблюдение."""
        if cell.status == cells.EMPTY:
            return
        key = (indicator, slices, period)
        index = self.series.get(key)
        if index is None:
            index = len(self.infos)
            self.series[key] = index
            self.infos.append(SeriesInfo(indicator, slices, period))
        info = self.infos[index]
        if unit:
            info.units[unit] += 1
        value = cell.value if cell.status == cells.VALUE else None
        if cell.status == cells.TEXT:
            self.text_cells += 1
        quality = ValueQuality.OBSERVED
        if value is None:
            hidden = hidden or cell.status == cells.HIDDEN
            quality = ValueQuality.HIDDEN if hidden else ValueQuality.NO_DATA
        code, label = place
        where = (index, code or f"\0{label}", year)
        existing = self.seen.get(where)
        if existing is not None:
            before = self.values[existing]
            if before is None and value is not None:
                # Строки дополняют друг друга: прочерк в одной, число в другой.
                self.values[existing] = value
                self.qualities[existing] = int(ValueQuality.OBSERVED)
            elif before is not None and value is not None and before != value:
                self.conflicts += 1
            return
        if len(self.values) >= self.limit:
            raise ExtractError(
                _(
                    "Получается больше %(limit)s значений. Выберите меньше значений разрезов "
                    "или загрузите часть таблицы."
                )
                % {"limit": f"{self.limit:,}".replace(",", " ")}
            )
        self.seen[where] = len(self.values)
        self.series_index.append(index)
        self.codes.append(code)
        self.labels.append(label if code is None else None)
        self.years.append(year)
        self.values.append(value)
        self.qualities.append(int(quality))
        if value is not None:
            info.values += 1
            info.years.add(year)
            if code is not None:
                info.regions.add(code)


# --- Вход -----------------------------------------------------------------------------------------


def recipe_digest(recipe: Mapping[str, Any]) -> str:
    """Отпечаток рецепта: извлечение устаревает, когда меняется описание таблицы."""
    relevant = {key: value for key, value in recipe.items() if key not in {"indicators"}}
    text = json.dumps(relevant, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(f"{REVISION}|{text}".encode()).hexdigest()[:16]


def is_current(version: DatasetVersion) -> bool:
    """Извлечение версии соответствует её рецепту и лежит на диске."""
    report = version.report.get("extract") or {}
    return (
        report.get("recipe") == recipe_digest(version.recipe)
        and (version.directory / EXTRACT_FILE).exists()
    )


def value_limit(version: DatasetVersion) -> int:
    """Предел значений набора: у гостя меньше."""
    if version.dataset.owner_id is None:
        return int(settings.USERDATA_GUEST_MAX_VALUES)
    return int(settings.USERDATA_MAX_VALUES)


def extract(version: DatasetVersion) -> dict[str, Any]:
    """Извлечь длинную таблицу версии в Parquet; вернуть отчёт извлечения (он же в версии)."""
    from . import describe

    started = time.perf_counter()
    loaded, result = describe.load_and_recognize(version, version.dataset.owner)
    if result.form == recognize.UNKNOWN or result.territories is None:
        raise ExtractError(
            _("Столбец с регионами не найден. Отметьте его на шаге «Что в таблице».")
        )
    if result.series > recognize.MAX_SERIES:
        raise ExtractError(
            _("Получается %(count)s рядов — больше %(limit)s. Выберите значения разрезов.")
            % {"count": result.series, "limit": recognize.MAX_SERIES}
        )
    if result.form == recognize.INDICATORS and result.needs_year:
        raise ExtractError(_("Годы в таблице не найдены. Укажите год на шаге «Что в таблице»."))
    places = _places(result)
    collector = Collector(limit=value_limit(version))
    reader = _Reader(version, result, places, collector, loaded)
    reader.run()
    _name_by_codes(collector)
    masked = _apply_masks(collector, version.recipe.get("masks"))
    if not collector.infos:
        raise ExtractError(_("В таблице не нашлось ни одного значения по территориям."))
    if len(collector.infos) > recognize.MAX_SERIES:
        raise ExtractError(
            _("Получается %(count)s рядов — больше %(limit)s. Выберите значения разрезов.")
            % {"count": len(collector.infos), "limit": recognize.MAX_SERIES}
        )
    _write(collector, version.directory / EXTRACT_FILE)
    report = _report(version, result, collector, places)
    report["masks"] = masked
    report["seconds"] = round(time.perf_counter() - started, 2)
    logger.info(
        "userdata: извлечена версия %s: рядов %s, значений %s, %s с",
        version.pk,
        len(collector.infos),
        len(collector.values),
        report["seconds"],
    )
    return report


# Разделитель названия и кода показателя в ключе ряда на время чтения строк.
CODE_MARK = chr(0x1F)


def _name_by_codes(collector: Collector) -> None:
    """
    Названия показателей после чтения: у названия с одним кодом — название, у названия
    с несколькими кодами — «название (код)»: это разные показатели, а не повторы.
    """
    codes: dict[str, set[str]] = {}
    for info in collector.infos:
        name, _mark, code = info.indicator.partition(CODE_MARK)
        codes.setdefault(name, set()).add(code)
    renamed: dict[str, str] = {}
    for info in collector.infos:
        name, mark, code = info.indicator.partition(CODE_MARK)
        final = f"{name} ({code})" if mark and code and len(codes[name]) > 1 else name
        renamed[info.indicator] = final
        info.indicator = final
    collector.series = {
        (renamed.get(indicator, indicator), slices, period): index
        for (indicator, slices, period), index in collector.series.items()
    }
    collector.notes = {
        renamed.get(indicator, indicator): note for indicator, note in collector.notes.items()
    }


def _apply_masks(collector: Collector, answer: list[str] | None) -> list[dict[str, Any]]:
    """
    Коды-маски вместо чисел — в пропуски: найденные по значениям показателей или выбранные
    человеком (``answer`` — перечень кодов; пустой — ни одного). Вернуть перечень для отчёта.
    """
    groups: defaultdict[str, list[float]] = defaultdict(list)
    for series, value in zip(collector.series_index, collector.values, strict=True):
        if value is not None:
            groups[collector.infos[series].indicator].append(value)
    found = masks.detect(groups)
    if not found:
        return []
    codes = masks.chosen(found, answer)
    if codes:
        for index, value in enumerate(collector.values):
            if value in codes:
                collector.values[index] = None
                collector.qualities[index] = int(ValueQuality.NO_DATA)
        _recount(collector)
    return [
        {
            "value": mask.text,
            "count": mask.count,
            "detected": mask.detected,
            "masked": value in codes,
        }
        for value, mask in sorted(found.items())
        if mask.detected or value in codes
    ]


def _recount(collector: Collector) -> None:
    """Значения, субъекты и годы рядов заново — после замены кодов пропусками."""
    for info in collector.infos:
        info.values = 0
        info.years = set()
        info.regions = set()
    for series, code, year, value in zip(
        collector.series_index, collector.codes, collector.years, collector.values, strict=True
    ):
        if value is None:
            continue
        info = collector.infos[series]
        info.values += 1
        info.years.add(year)
        if code is not None:
            info.regions.add(code)


def read(version: DatasetVersion) -> pa.Table:
    """Извлечённые наблюдения версии."""
    return pq.read_table(version.directory / EXTRACT_FILE)


# --- Территории ----------------------------------------------------------------------------------


@dataclass(slots=True)
class Places:
    """Подпись → код справочника или причина «вне справочника»; прочие подписи пропускаются."""

    codes: dict[str, str]
    outside: dict[str, str]

    def of(self, label: str) -> tuple[str | None, str | None] | None:
        code = self.codes.get(label)
        if code is not None:
            return code, None
        if label in self.outside:
            return None, label
        return None


def _places(result: recognize.Recognition) -> Places:
    assert result.territories is not None
    codes: dict[str, str] = {}
    outside: dict[str, str] = {}
    for label, match in result.territories.matches.items():
        if match.code and (match.is_resolved or match.kind == matching.NESTED):
            # Вложенная без ответа — итог с округами: так считает Росстат.
            codes[label] = match.code
        elif match.kind == matching.OUTSIDE:
            outside[label] = match.reason or "outside"
    return Places(codes=codes, outside=outside)


# --- Чтение строк по форме -----------------------------------------------------------------------


class _Reader:
    """Обход строк выбранной таблицы по форме распознавания."""

    def __init__(
        self,
        version: DatasetVersion,
        result: recognize.Recognition,
        places: Places,
        collector: Collector,
        loaded: tables.Loaded,
    ) -> None:
        self.version = version
        self.result = result
        self.places = places
        self.collector = collector
        self.loaded = loaded
        self.table = jobs.table_of(version)
        self.path = version.directory / version.recipe["file"]
        self.large = tables.is_large(self.path, self.table)
        self.title = result.title or version.dataset.title
        role = result.role_of
        self.territory = result.territory_columns[0] if result.territory_columns else 0
        self.period_column = role(recognize.PERIOD)[0].index if role(recognize.PERIOD) else None
        names = role(recognize.INDICATOR) or role(recognize.INDICATOR_CODE)
        self.indicator_column = names[0].index if names else None
        # Код показателя рядом с названием: одно название у нескольких кодов — разные показатели.
        codes = role(recognize.INDICATOR_CODE) if role(recognize.INDICATOR) else []
        self.code_column = codes[0].index if codes else None
        self.unit_column = role(recognize.UNIT)[0].index if role(recognize.UNIT) else None
        reasons = role(recognize.MISSING_REASON)
        self.reason_column = reasons[0].index if reasons else None
        # Примечание показателя — из самого длинного столбца примечаний (не раздел и не тема).
        notes = sorted(role(recognize.NOTE), key=lambda column: -column.length)
        self.note_column = notes[0].index if notes else None
        self.slices = [
            (column.index, column.header, set(result.slices[column.index]["selected"]))
            for column in role(recognize.SLICE)
            if column.index in result.slices
        ]
        self.styles: dict[int, cells.NumberStyle] = {}
        self.stamps: dict[str, periods.Stamp | None] = {}

    # --- Общее ----------------------------------------------------------------------------

    def run(self) -> None:
        form = self.result.form
        if form == recognize.LONG:
            self._long()
        elif form == recognize.TRANSPOSED:
            self._transposed()
        else:
            self._sheet()

    def style(self, column: int, values: Mapping[str, int] | None = None) -> cells.NumberStyle:
        if column not in self.styles:
            known = values if values is not None else self._column_values(column)
            self.styles[column] = cells.guess_style(list(known)[:2000])
        return self.styles[column]

    def _column_values(self, column: int) -> dict[str, int]:
        return self.loaded.column_values(column, self.result.data_start)

    def stamp(self, raw: Any) -> periods.Stamp | None:
        if not isinstance(raw, str):
            return periods.stamp(raw)
        text = raw.strip()
        if text not in self.stamps:
            self.stamps[text] = periods.stamp(text)
        return self.stamps[text]

    def text(self, row: list[Any] | tuple[Any, ...], column: int | None) -> str:
        if column is None or column >= len(row):
            return ""
        return " ".join(cell_text(row[column]).split())

    def row_slices(self, row: list[Any] | tuple[Any, ...]) -> tuple[tuple[str, str], ...] | None:
        """Значения разрезов строки; ``None`` — значение не отобрано."""
        chosen = []
        for index, header, selected in self.slices:
            value = cell_text(row[index]).strip() if index < len(row) else ""
            if selected and value not in selected:
                return None
            chosen.append((header, value))
        return tuple(chosen)

    def hidden(self, row: list[Any] | tuple[Any, ...]) -> bool:
        reason = self.text(row, self.reason_column).lower()
        return any(word in reason for word in _HIDDEN_REASONS)

    def place(self, label: str) -> tuple[str | None, str | None] | None:
        found = self.places.of(label)
        if found is None:
            self.collector.skipped["territory"] += 1
        return found

    def remember_note(self, indicator: str, row: list[Any] | tuple[Any, ...]) -> None:
        if self.note_column is not None and indicator not in self.collector.notes:
            note = self.text(row, self.note_column)
            if note:
                self.collector.notes[indicator] = note[:2000]

    # --- Длинная таблица --------------------------------------------------------------------

    def _long(self) -> None:
        values = self.result.value_columns
        if not values:
            raise ExtractError(
                _("Столбец значений не найден. Отметьте его на шаге «Что в таблице».")
            )
        if self.period_column is None:
            raise ExtractError(
                _("Столбец с годами не найден. Отметьте его на шаге «Что в таблице».")
            )
        named = len(values) > 1
        for row in self._long_rows():
            label = (
                " ".join(cell_text(row[self.territory]).split())
                if self.territory < len(row)
                else ""
            )
            if not label:
                continue
            found = self.stamp(row[self.period_column] if self.period_column < len(row) else "")
            if found is None or found.year is None:
                self.collector.skipped["period"] += 1
                continue
            sliced = self.row_slices(row)
            if sliced is None:
                continue
            place = self.place(label)
            if place is None:
                continue
            base = self.text(row, self.indicator_column) or self.title
            # Код показателя — в ключ ряда; названия расставит _name_by_codes после чтения.
            code = (
                f"{CODE_MARK}{self.text(row, self.code_column)}"
                if self.code_column is not None
                else ""
            )
            unit = self.text(row, self.unit_column)
            hidden = self.hidden(row)
            for column in values:
                indicator = f"{base} — {column.header}" if named and column.header else base
                indicator += code
                raw = row[column.index] if column.index < len(row) else None
                self.collector.add(
                    indicator=indicator,
                    slices=sliced,
                    period=found.period.key,
                    unit=unit,
                    place=place,
                    year=found.year,
                    cell=cells.parse(raw, self.style(column.index)),
                    hidden=hidden,
                )
                self.remember_note(indicator, row)

    def _long_rows(self) -> Iterator[list[Any] | tuple[Any, ...]]:
        """Строки длинной таблицы: в памяти или, у большой, отобранные в DuckDB."""
        if not self.large:
            yield from self.loaded.rows[self.result.data_start :]
            return
        yield from self._duckdb_rows()

    def _duckdb_rows(self) -> Iterator[tuple[Any, ...]]:
        """Большая таблица: в DuckDB остаются строки с отобранными значениями разрезов."""
        connection = tables.parse_connection(self.path)
        try:
            names = tables.read_into(connection, self.path, self.table)
            conditions = []
            arguments: list[Any] = []
            for index, _header, selected in self.slices:
                if selected and index < len(names):
                    marks = ", ".join("?" * len(selected))
                    conditions.append(f'trim(CAST("{names[index]}" AS VARCHAR)) IN ({marks})')
                    arguments.extend(sorted(selected))
            where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
            quoted = ", ".join(f'"{name}"' for name in names)
            cursor = connection.execute(f"SELECT {quoted} FROM t {where}", arguments)  # noqa: S608 — имена столбцов из DESCRIBE
            while batch := cursor.fetchmany(BATCH_ROWS):
                yield from batch
        finally:
            connection.close()

    # --- Листы: годы, периоды или показатели в столбцах ----------------------------------------

    def _sheet(self) -> None:
        result = self.result
        values = result.value_columns
        if not values:
            raise ExtractError(
                _("Столбцы значений не найдены. Отметьте их на шаге «Что в таблице».")
            )
        blocks = recognize._blocks(result)
        by_block = {block: set(indexes) for block, (_territory, indexes) in enumerate(blocks)}
        for block, label, row in self._sheet_rows(blocks):
            place = self.place(label)
            if place is None:
                continue
            sliced = self.row_slices(row)
            if sliced is None:
                continue
            row_indicator = self.text(row, self.indicator_column)
            unit = self.text(row, self.unit_column)
            hidden = self.hidden(row)
            for column in values:
                if column.index not in by_block.get(block, set()) and len(blocks) > 1:
                    continue
                found = self._column_period(column)
                if found is None:
                    continue
                year, period, group = found
                if result.form == recognize.INDICATORS:
                    indicator = _without_year(column.header) or self.title
                    extra: tuple[tuple[str, str], ...] = ()
                else:
                    indicator = row_indicator or self.title
                    extra = (("", group),) if group else ()
                raw = row[column.index] if column.index < len(row) else None
                self.collector.add(
                    indicator=indicator,
                    slices=sliced + extra,
                    period=period,
                    unit=unit,
                    place=place,
                    year=year,
                    cell=cells.parse(raw, self.style(column.index)),
                    hidden=hidden,
                )
                self.remember_note(indicator, row)

    def _column_period(self, column: recognize.ColumnInfo) -> tuple[int, str, str] | None:
        """Год, период и группа шапки столбца значений."""
        stamp = column.stamp
        if stamp:
            return int(stamp["year"]), str(stamp["period"]), str(stamp.get("group") or "")
        if self.result.form == recognize.INDICATORS and self.result.year:
            return int(self.result.year), periods.ANNUAL.key, ""
        return None

    def _sheet_rows(
        self, blocks: list[tuple[int, list[int]]]
    ) -> Iterator[tuple[int, str, list[Any] | tuple[Any, ...]]]:
        """Строки территорий листа: найденные распознаванием или, у большой таблицы, все."""
        if self.result.rows:
            rows = self.loaded.rows
            for item in self.result.rows:
                yield item.block, item.label, rows[item.index]
            return
        territories = [territory for territory, _values in blocks] or [self.territory]
        source: Iterator[Any] = (
            iter(self.loaded.rows) if not self.large else iter_rows(self.path, self.table)
        )
        for number, row in enumerate(source):
            if number < self.result.data_start:
                continue
            for block, territory in enumerate(territories):
                label = " ".join(cell_text(row[territory]).split()) if territory < len(row) else ""
                if label:
                    yield block, label, row

    # --- Перевёрнутая таблица --------------------------------------------------------------------

    def _transposed(self) -> None:
        result = self.result
        rows = self.loaded.rows
        header = rows[result.header_rows[0]] if result.header_rows else []
        labels = {
            column.index: " ".join(cell_text(header[column.index]).split())
            for column in result.value_columns
            if column.index < len(header)
        }
        for row in rows[result.data_start :]:
            if not row:
                continue
            found = self.stamp(row[0])
            if found is None or found.year is None:
                continue
            for index, label in labels.items():
                if not label:
                    continue
                place = self.place(label)
                if place is None:
                    continue
                raw = row[index] if index < len(row) else None
                self.collector.add(
                    indicator=self.title,
                    slices=(),
                    period=found.period.key,
                    unit="",
                    place=place,
                    year=found.year,
                    cell=cells.parse(raw, self.style(index)),
                )


def _without_year(header: str) -> str:
    """Название показателя из шапки столбца без года: «… на 1 января 2024 г.» → «…»."""
    text = header
    for pattern in (recognize._NAMED_POINT, recognize._NAMED_YEAR):
        found = pattern.search(text.lower())
        if found is not None:
            text = text[: found.start()] + text[found.end() :]
    return " ".join(text.replace(" г.", " ").split()).strip(" ,.:;–-")


# --- Запись и отчёт ------------------------------------------------------------------------------


def _write(collector: Collector, path: Any) -> None:
    """Наблюдения в Parquet в постоянном порядке: ряд, территория, год."""
    table = pa.table(
        {
            "series": pa.array(collector.series_index, pa.int32()),
            "territory_code": pa.array(collector.codes, pa.string()),
            "label": pa.array(collector.labels, pa.string()),
            "year": pa.array(collector.years, pa.int16()),
            "value": pa.array(collector.values, pa.float64()),
            "quality": pa.array(collector.qualities, pa.int8()),
        }
    )
    order = [
        ("series", "ascending"),
        ("territory_code", "ascending"),
        ("label", "ascending"),
        ("year", "ascending"),
    ]
    table = table.sort_by(order)
    # Своё имя на процесс: два разбора одной версии не пишут в один файл.
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    pq.write_table(table, temporary)
    temporary.replace(path)


def _report(
    version: DatasetVersion,
    result: recognize.Recognition,
    collector: Collector,
    places: Places,
) -> dict[str, Any]:
    """Перечень рядов и итоги извлечения для шага «Показатели» и страницы набора."""
    outside_labels = sorted({label for label in collector.labels if label})
    nested_present = sorted(
        {
            code
            for info in collector.infos
            for code in info.regions
            if code in matching.NESTED_PARENTS
        }
    )
    series = [
        {
            "code": info.code,
            "indicator": info.indicator,
            "slices": [list(pair) for pair in info.slices],
            "period": info.period,
            "unit": info.units.most_common(1)[0][0] if info.units else "",
            "values": info.values,
            "regions": len(_as_regions(info.regions)),
            "first_year": min(info.years) if info.years else None,
            "last_year": max(info.years) if info.years else None,
        }
        for info in collector.infos
    ]
    return {
        "recipe": recipe_digest(version.recipe),
        "form": result.form,
        "series": series,
        "notes": collector.notes,
        "values": sum(1 for value in collector.values if value is not None),
        "observations": len(collector.values),
        "conflicts": collector.conflicts,
        "text_cells": collector.text_cells,
        "skipped": dict(collector.skipped),
        "outside": [[label, places.outside.get(label, "")] for label in outside_labels],
        "nested": nested_present,
        "title": result.title,
    }


def _as_regions(codes: set[str]) -> set[str]:
    """Субъекты ряда; итог с автономными округами считается своей областью."""
    return {matching.NESTED_PARENTS.get(code, code) for code in codes} & _region_codes()


def _region_codes() -> set[str]:
    from apps.sources.territories import region_codes

    return set(region_codes())


def failure_text(error: Exception) -> str:
    """Текст отказа для человека: свой — как есть, прочие — общий, без подробностей файла."""
    if isinstance(error, ExtractError | IngestError):
        return str(error)
    return _(
        "Разобрать таблицу не удалось. Мы получили сообщение об ошибке; сам файл не пересылался."
    )
