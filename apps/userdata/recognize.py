"""
Распознавание таблицы: столбец территорий, шапка, форма, роли столбцов, периоды, разрезы
и вопросы к человеку.

Формы: длинная (регион, период, показатель, разрезы, значение — в своих столбцах), годы
или периоды в столбцах, показатели в столбцах (год — вопросом), перевёрнутая (регионы
в шапке, годы в строках). Решения человека приходят из рецепта и сильнее догадок.
"""

from __future__ import annotations

import itertools
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import PurePosixPath
from typing import Any

from apps.sources import periods
from apps.sources.periods import Period, Stamp

from . import cells, matching
from .ingest import TableInfo, cell_text
from .tables import Loaded

# Формы таблиц.
LONG = "long"
WIDE = "wide"
INDICATORS = "indicators"
TRANSPOSED = "transposed"
UNKNOWN = "unknown"

# Роли столбцов.
TERRITORY = "territory"
TERRITORY_CODE = "territory_code"
LEVEL = "level"
PERIOD = "period"
INDICATOR = "indicator"
INDICATOR_CODE = "indicator_code"
UNIT = "unit"
SLICE = "slice"
VALUE = "value"
MISSING_REASON = "missing_reason"
NOTE = "note"
SKIP = "skip"
ROLES = (
    TERRITORY,
    TERRITORY_CODE,
    LEVEL,
    PERIOD,
    INDICATOR,
    INDICATOR_CODE,
    UNIT,
    SLICE,
    VALUE,
    MISSING_REASON,
    NOTE,
    SKIP,
)

# Ответ человека «это не территория»: строка в набор не попадает.
NOT_TERRITORY = "none"
# Столбец территорий — тот, где узнаются хотя бы три субъекта.
MIN_REGIONS = 3
# Разрез — столбец с не слишком большим числом разных значений.
SLICE_DISTINCT = 300
# Имя столбца длиннее этого — название показателя, а не служебное имя.
HINT_LENGTH = 40
# Длинный текст в ячейках — примечание, а не разрез.
NOTE_LENGTH = 80
# Доля чисел, с которой столбец — значения.
NUMERIC_SHARE = 0.8
# Доля годов и периодов, с которой столбец — период.
PERIOD_SHARE = 0.9
# Рядов в наборе не больше этого; больше — отбор значений разрезов.
MAX_SERIES = 500
# Сколько самых частых значений столбца хватает для его профиля.
PROFILE_VALUES = 1500
# Строк начала таблицы достаточно, чтобы найти шапку и роли.
HEAD_ROWS = 400

# Подсказки по названиям столбцов на двух языках; сравниваются целые слова и части имён.
_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    # Группы показателей ЕБТ («indicator_section», «topic») — пояснение, а не показатель.
    (NOTE, ("indicator_section", "section", "раздел", "topic", "problem", "тема")),
    (MISSING_REASON, ("reason_na", "причина пропуска", "причина отсутствия")),
    (TERRITORY_CODE, ("oktmo", "okato", "октмо", "окато", "iso", "код региона", "region_code")),
    (LEVEL, ("object_level", "уровень", "level", "тип территории")),
    (INDICATOR_CODE, ("indicator_code", "код показателя", "measure_code")),
    (UNIT, ("indicator_unit", "единица", "ед. изм", "ед.изм", "unit")),
    (VALUE, ("indicator_value", "значение", "value", "величина")),
    (INDICATOR, ("indicator_name", "indicator", "показатель", "наименование показателя")),
    (PERIOD, ("year", "год", "период", "period", "дата", "date", "месяц", "month")),
    (
        TERRITORY,
        ("object_name", "регион", "субъект", "территори", "region", "subject", "territory"),
    ),
    (NOTE, ("comment", "примечание", "source", "источник", "описание", "note")),
)
_CODE_SUFFIX = re.compile(r"(_code|_id|code|код)$")
_TOTAL_VALUES = frozenset(
    {"всего", "итого", "оба пола", "все возрасты", "все", "total", "all", "both sexes", "both"}
)
_LEAD_IN = re.compile(r"^(в том числе|в т\.\s*ч\.|из них|включая|including|of which)\W*$", re.I)
_TITLE_SKIP = re.compile(r"^(к содержанию|продолжение|окончание|содержание)\W*$", re.I)
_UNIT_WORDS = re.compile(
    r"^(рубл|руб\.|тыс\.|млн|млрд|процент|в процентах|%|человек|чел\.|единиц|штук|тонн|"
    r"кв\. ?м|га\b|лет\b|на \d|в % к|в %|на 1 ?000|на 100 ?000|рублей)",
    re.I,
)
_YEAR_IN_TEXT = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
_NAMED_POINT = re.compile(r"на\s+1\s+([а-я]+)\s+((?:19|20)\d{2})")
_NAMED_YEAR = re.compile(r"(?:в|за)\s+((?:19|20)\d{2})\s*г")
_CODE_VALUE = re.compile(r"^\d{8}(\d{3})?(\.0)?$")
_FOOTNOTE_ROW = re.compile(r"^\s*[\W_]*(\d{1,2})(?:[)\.]\s*|\s+|(?=[^\d\s]))(\S.*)$", re.DOTALL)


@dataclass(slots=True)
class ColumnInfo:
    """Столбец таблицы: текст шапки, профиль ячеек и роль."""

    index: int
    header: str
    role: str = SKIP
    guessed: str = SKIP
    numeric: float = 0.0
    periods: float = 0.0
    regions: int = 0
    distinct: int = 0
    length: float = 0.0
    codes: float = 0.0
    # У столбца значений в широкой форме: год, период и группа шапки над ним.
    stamp: dict[str, Any] | None = None
    values: list[tuple[str, int]] = field(default_factory=list)


@dataclass(slots=True)
class TerritoryRow:
    """Строка таблицы с подписью территории (у листов Росстата и вставок)."""

    index: int
    label: str
    block: int = 0
    merged_from: tuple[int, ...] = ()


@dataclass(slots=True)
class Recognition:
    """Что понято в таблице и о чём нужно спросить."""

    form: str
    columns: list[ColumnInfo]
    header_rows: list[int]
    data_start: int
    title: str = ""
    territory_columns: list[int] = field(default_factory=list)
    rows: list[TerritoryRow] = field(default_factory=list)
    labels: dict[str, int] = field(default_factory=dict)
    territories: matching.ColumnMatch | None = None
    footnotes: dict[int, str] = field(default_factory=dict)
    year: int | None = None
    year_hint: int | None = None
    periods: dict[str, Any] = field(default_factory=dict)
    slices: dict[int, dict[str, Any]] = field(default_factory=dict)
    series: int = 0
    indicators: list[str] = field(default_factory=list)
    text_cells: list[tuple[str, int]] = field(default_factory=list)
    text_cell_count: int = 0
    duplicates: list[dict[str, Any]] = field(default_factory=list)
    same_headers: list[dict[str, Any]] = field(default_factory=list)
    municipal: bool = False

    def role_of(self, role: str) -> list[ColumnInfo]:
        return [column for column in self.columns if column.role == role]

    @property
    def value_columns(self) -> list[ColumnInfo]:
        return self.role_of(VALUE)

    @property
    def needs_year(self) -> bool:
        """Года нет ни в столбце, ни в шапке: его спрашивают."""
        return (
            self.form == INDICATORS
            and self.year is None
            and any(not column.stamp for column in self.value_columns)
        )


# --- Вход ----------------------------------------------------------------------------------------


def recognize(
    loaded: Loaded,
    table: TableInfo,
    recipe: Mapping[str, Any] | None = None,
    *,
    file_name: str = "",
    remembered: Mapping[str, str] | None = None,
) -> Recognition:
    """Распознать таблицу; ``recipe`` — решения человека поверх догадок."""
    recipe = recipe or {}
    rows = loaded.rows
    territory_columns = _territory_columns(loaded, recipe)
    if not territory_columns:
        transposed = _transposed(loaded)
        if transposed is not None:
            return transposed
        return Recognition(form=UNKNOWN, columns=_bare_columns(rows), header_rows=[], data_start=0)
    main = territory_columns[0]
    data_start = _data_start(rows, main)
    header_rows, title_lines = _header(rows[:data_start], main)
    columns = _profile(loaded, data_start, header_rows, main)
    result = Recognition(
        form=UNKNOWN,
        columns=columns,
        header_rows=header_rows,
        data_start=data_start,
        title=_title(title_lines),
        territory_columns=territory_columns,
    )
    for index in territory_columns:
        columns[index].role = columns[index].guessed = TERRITORY
    _guess_form_and_roles(result)
    _apply_overrides(result, recipe)
    if result.form == INDICATORS:
        result.year = recipe.get("year") or None
        result.year_hint = _year_hint(result, table, file_name)
    if result.form in {WIDE, INDICATORS} and loaded.complete:
        _sheet_rows(result, rows)
    else:
        result.labels = loaded.column_values(main, data_start)
    codes = _territory_codes(result, loaded)
    result.territories = matching.match_column(result.labels, codes=codes, remembered=remembered)
    _apply_territory_choices(result, recipe)
    result.municipal = _looks_municipal(result)
    _summarize_periods(result, loaded)
    _summarize_slices(result, loaded, recipe)
    _check_values(result, loaded)
    _find_duplicates(result, rows)
    _find_same_headers(result)
    return result


# --- Территории и шапка --------------------------------------------------------------------------


def _territory_columns(loaded: Loaded, recipe: Mapping[str, Any]) -> list[int]:
    """Столбцы территорий: выбранные человеком или те, где узнаются субъекты."""
    roles = recipe.get("roles") or {}
    if roles:
        # Роли уже описаны человеком: столбец регионов, снятый им, не угадывается снова.
        return sorted(int(index) for index, role in roles.items() if role == TERRITORY)
    width = min(max((len(row) for row in loaded.rows), default=0), 200)
    scored = []
    for column in range(width):
        values = loaded.column_values(column)
        if not values or len(values) > SLICE_DISTINCT * 20:
            continue
        found = len(matching.subjects_in(values))
        codes = sum(1 for value in values if _CODE_VALUE.match(value)) / len(values)
        if found >= MIN_REGIONS and codes < PERIOD_SHARE:
            scored.append((found, column))
    if not scored:
        return []
    best = max(found for found, _column in scored)
    # Таблица «в две колонки»: в каждой половине — своя часть субъектов.
    return sorted(column for found, column in scored if found >= max(MIN_REGIONS, best // 4))


def _data_start(rows: list[list[Any]], column: int) -> int:
    """
    Первая строка данных: с узнанной территорией или с подписью и числами под шапкой
    (строка страны бывает названа показателем: «Валовой региональный продукт по субъектам…»).
    """
    first = next(
        (
            index
            for index, row in enumerate(rows[:HEAD_ROWS])
            if column < len(row) and _is_territory(cell_text(row[column]).strip())
        ),
        None,
    )
    if first is None:
        return 1 if rows else 0
    for index in range(first):
        row = rows[index]
        label = cell_text(row[column]).strip() if column < len(row) else ""
        if label and _is_data_row(row, column):
            return index
    return first


def _is_territory(label: str) -> bool:
    found = matching.quick_match(label)
    return found.is_resolved or found.kind == matching.NESTED


def _is_data_row(row: list[Any], territory: int) -> bool:
    """В строке больше чисел, чем подписей, и числа — не годы шапки."""
    filled = [
        cell for index, cell in enumerate(row) if index != territory and cell_text(cell).strip()
    ]
    if not filled:
        return False
    numbers = [cell for cell in filled if cells.parse(cell).status == cells.VALUE]
    years = [cell for cell in numbers if _is_period(cell_text(cell))]
    return len(numbers) - len(years) >= max(1, len(filled) // 2)


def _header(rows: list[list[Any]], territory: int) -> tuple[list[int], list[str]]:
    """Строки шапки (есть подписи над столбцами данных) и строки заголовка над ней."""
    header: list[int] = []
    titles: list[str] = []
    for index, row in enumerate(rows):
        texts = [(column, cell_text(cell).strip()) for column, cell in enumerate(row)]
        filled = [(column, text) for column, text in texts if text]
        if not filled:
            continue
        others = [text for column, text in filled if column != territory]
        if len(filled) == 1 and not header and (filled[0][0] <= territory or not others):
            titles.append(filled[0][1])
        elif others:
            header.append(index)
        else:
            titles.append(filled[0][1])
    return header, titles


def _title(lines: list[str]) -> str:
    useful = [" ".join(line.split()) for line in lines if not _TITLE_SKIP.match(line.strip())]
    return " ".join(useful)[:300]


def _header_texts(
    rows: list[list[Any]], header_rows: list[int], has_values: list[bool], territory: int
) -> list[list[str]]:
    """
    Тексты шапки по столбцам: объединённая клетка хранит текст в первой клетке, он
    продлевается вправо (правила — в ``_spans_to``).
    """
    width = len(has_values)
    raw = [
        [
            cell_text(rows[index][column]).strip() if column < len(rows[index]) else ""
            for column in range(width)
        ]
        for index in header_rows
    ]
    moved = _shifted_headers(raw, has_values, territory)
    filled: list[list[str]] = []
    for level, texts in enumerate(raw):
        present = [column for column, text in enumerate(texts) if text]
        line = [""] * width
        if len(present) == 1 and _is_dated(texts[present[0]]):
            # Одна клетка с периодом под всей шапкой: «май 2026 г. - июль 2026 г.».
            line[present[0] :] = [texts[present[0]]] * (width - present[0])
            filled.append(line)
            continue
        current, start = "", 0
        for column in range(width):
            if column in moved:
                continue
            if texts[column]:
                current, start = texts[column], column
            elif current and not _spans_to(
                raw, filled, level, start=start, column=column, text=current
            ):
                current = ""
            line[column] = current
        filled.append(line)
    return [[line[column] for line in filled if line[column]] for column in range(width)]


def _shifted_headers(raw: list[list[str]], has_values: list[bool], territory: int) -> set[int]:
    """
    Шапка, сдвинутая на столбец относительно чисел (таблицы из Word): подписи стоят над
    пустыми столбцами, числа — в соседних без подписи. Подписи переносятся к числам, если
    такие столбцы строго чередуются; служебные столбцы правее таблицы (подряд без подписи)
    не трогаются. Возвращает опустевшие столбцы.
    """
    kinds = []
    for column, filled in enumerate(has_values):
        titled = any(line[column] for line in raw)
        if column == territory or titled == filled:
            kinds.append("")
        else:
            kinds.append("values" if filled else "header")
    moved: set[int] = set()
    column = 0
    while column < len(kinds):
        end = column
        while end < len(kinds) and kinds[end]:
            end += 1
        run = list(range(column, end))
        alternating = all(kinds[a] != kinds[b] for a, b in itertools.pairwise(run))
        if run and len(run) % 2 == 0 and alternating:
            for first, second in zip(run[::2], run[1::2], strict=True):
                source, target = (first, second) if kinds[first] == "header" else (second, first)
                for line in raw:
                    line[target], line[source] = line[source], ""
                moved.add(source)
        column = end + 1
    return moved


def _spans_to(
    raw: list[list[str]], filled: list[list[str]], level: int, *, start: int, column: int, text: str
) -> bool:
    """
    Продлевается ли подпись строки шапки ``level`` из столбца ``start`` в ``column``.
    Не продлевается за границу группы строки выше и за последний столбец со своими подписями
    ниже (служебные столбцы правее таблицы шапки не получают). Подпись раздела под строкой
    лет («2020 2022 2020 2022», ниже «Всего» и «Земли») идёт до следующей подписи.
    """
    if level == len(raw) - 1 and periods.stamp(text) is not None:
        # Период нижней строки шапки — свой у каждого столбца.
        return False
    above = [line[column] for line in filled[:level]]
    if (
        any(above)
        and all(_is_dated(upper) for upper in above if upper)
        and all(_is_dated(line[start]) for line in filled[:level] if line[start])
        and periods.stamp(text) is None
    ):
        return True
    if any(line[column] for line in raw[:level]):
        return False
    if any(line[column] != line[start] for line in filled[:level]):
        return False
    below = raw[level + 1 :]
    return not any(line[start] for line in below) or any(line[column] for line in below)


def _is_dated(text: str) -> bool:
    """Подпись — период с годом: «2025 год», «май 2026 г. - июль 2026 г.»."""
    found = periods.stamp(text) or periods.stamp(_strip_note(text))
    return found is not None and found.year is not None


# --- Профиль столбцов -----------------------------------------------------------------------------


def _profile(
    loaded: Loaded, data_start: int, header_rows: list[int], territory: int
) -> list[ColumnInfo]:
    rows = loaded.rows
    width = max((len(row) for row in rows), default=0)
    all_values = [loaded.column_values(index, data_start) for index in range(width)]
    texts = (
        _header_texts(rows, header_rows, [bool(values) for values in all_values], territory)
        if header_rows
        else [[] for _ in range(width)]
    )
    columns = []
    for index, values in enumerate(all_values):
        header = " | ".join(" ".join(text.split()) for text in texts[index])
        column = ColumnInfo(index=index, header=header)
        if values:
            _shares(column, values)
        column.distinct = (
            loaded.distinct.get(index, len(values)) if not loaded.complete else len(values)
        )
        column.values = sorted(values.items(), key=lambda item: (-item[1], item[0]))[:60]
        if index != territory and values and len(values) <= SLICE_DISTINCT * 20:
            column.regions = len(matching.subjects_in(values))
        columns.append(column)
    return columns


def _shares(column: ColumnInfo, values: dict[str, int]) -> None:
    """Доли чисел, периодов и кодов по разным значениям столбца с учётом числа строк."""
    frequent = sorted(values.items(), key=lambda item: -item[1])[:PROFILE_VALUES]
    total = sum(count for _value, count in frequent)
    column.length = sum(len(value) * count for value, count in frequent) / total
    if column.length > NOTE_LENGTH:
        return
    style = cells.guess_style([value for value, _count in frequent])
    numeric = present = periods_found = codes = 0
    for value, count in frequent:
        status = cells.parse(value, style).status
        if status != cells.EMPTY:
            present += count
            numeric += count if status in {cells.VALUE, cells.MISSING, cells.HIDDEN} else 0
        periods_found += count if _is_period(value) else 0
        codes += count if _CODE_VALUE.match(value) else 0
    column.numeric = numeric / present if present else 0.0
    column.periods = periods_found / total
    column.codes = codes / total


@lru_cache(maxsize=65536)
def _is_period(value: str) -> bool:
    found = periods.stamp(value)
    return found is not None and found.year is not None


def _hint(header: str) -> str | None:
    """Роль по названию столбца."""
    name = header.lower().split(" | ")[-1].strip()
    # Подсказки — для коротких имён столбцов («year», «Регион», «Значение»); длинный заголовок
    # — название показателя, слова «в месяц» или «на конец года» в нём роли не задают.
    if not name or len(name) > HINT_LENGTH:
        return None
    for role, words in _HINTS:
        for word in words:
            # Английское имя столбца — целым словом, русское — с окончанием: «Регионы».
            tail = r"($|[\s_])" if word.isascii() else ""
            if name == word or re.search(rf"(^|[\s_]){re.escape(word)}{tail}", name):
                return role
    return None


# --- Форма и роли ---------------------------------------------------------------------------------


def _guess_form_and_roles(result: Recognition) -> None:
    columns = result.columns
    stamped = _header_periods(result)
    period_columns = [
        column
        for column in columns
        if column.role != TERRITORY
        and column.periods >= PERIOD_SHARE
        and (column.numeric < 1.0 or _hint(column.header) == PERIOD)
    ]
    if stamped:
        result.form = WIDE
        for column in stamped:
            column.role = column.guessed = VALUE
    elif period_columns:
        result.form = LONG
        best = max(
            period_columns, key=lambda column: (_hint(column.header) == PERIOD, column.periods)
        )
        best.role = best.guessed = PERIOD
    for column in columns:
        if column.role in {TERRITORY, VALUE, PERIOD}:
            continue
        column.role = column.guessed = _role(column, result.form)
    numeric = [column for column in columns if column.role == VALUE]
    if numeric and result.form == UNKNOWN:
        result.form = INDICATORS
    elif result.form == LONG and len(numeric) > 1:
        _one_value_column(numeric)
    _codes_beside_names(columns)
    if result.form == WIDE:
        result.indicators = _wide_indicators(result)
    elif result.form == INDICATORS:
        _years_in_names(result)


def _one_value_column(numeric: list[ColumnInfo]) -> None:
    """Несколько числовых столбцов при столбце периода: «значение» — по названию."""
    named = [column for column in numeric if _hint(column.header) == VALUE]
    if len(named) != 1:
        return
    for column in numeric:
        if column is not named[0]:
            column.role = column.guessed = SLICE if _small_integers(column) else SKIP


def _years_in_names(result: Recognition) -> None:
    """
    Год в названии столбца показателя: «Численность населения на 1 января 2024 г.»,
    «Валовой региональный продукт в 2023 г.»; у остальных столбцов год спрашивается.
    """
    for column in result.value_columns:
        found = _NAMED_POINT.search(column.header) or _NAMED_YEAR.search(column.header)
        if found is None:
            continue
        if found.re is _NAMED_POINT:
            stamp = periods.stamp(f"на 1 {found.group(1)} {found.group(2)}")
        else:
            stamp = periods.stamp(found.group(1))
        if stamp is not None and stamp.year is not None:
            column.stamp = {
                "year": stamp.year,
                "period": stamp.period.key,
                "group": column.header,
                "notes": [],
            }


def _header_periods(result: Recognition) -> list[ColumnInfo]:
    """Столбцы, в шапке которых год (с периодом или без): годы и периоды в столбцах."""
    found = []
    for column in result.columns:
        if column.role == TERRITORY or not column.header:
            continue
        stamp = _column_stamp(column.header)
        if stamp is not None and column.numeric >= 0.5:  # noqa: PLR2004
            column.stamp = stamp
            found.append(column)
    return found if len(found) >= 2 or (found and not _has_period_cells(result)) else []  # noqa: PLR2004


def _has_period_cells(result: Recognition) -> bool:
    return any(column.periods >= PERIOD_SHARE for column in result.columns)


def _column_stamp(header: str) -> dict[str, Any] | None:
    """Год, период и группа из текстов шапки столбца: «2016 год | январь», «year2011»."""
    year: int | None = None
    period: Period | None = None
    notes: list[int] = []
    group: list[str] = []
    for text in header.split(" | "):
        found = periods.stamp(text) or periods.stamp(_strip_note(text))
        if found is not None and (found.year is not None or period is None):
            if found.year is not None and year is None:
                year = found.year
                notes.extend(found.notes)
                if found.period != periods.ANNUAL:
                    period = found.period
            elif found.year is None and period is None:
                period = found.period
            else:
                group.append(text)
        else:
            group.append(text)
    if year is None:
        return None
    return {
        "year": year,
        "period": (period or periods.ANNUAL).key,
        "group": " | ".join(group),
        "notes": notes,
    }


def _strip_note(text: str) -> str:
    return re.sub(r"[\d¹²³⁴⁵⁶⁷⁸⁹*)\s]+$", "", text) if not text.strip().isdigit() else text


def _role(column: ColumnInfo, form: str) -> str:  # noqa: PLR0911 — таблица правил
    """Роль столбца по названию и профилю ячеек."""
    hint = _hint(column.header)
    if column.codes >= PERIOD_SHARE:
        return TERRITORY_CODE if hint in {TERRITORY_CODE, None} else SKIP
    if hint == TERRITORY_CODE:
        return TERRITORY_CODE
    if column.numeric >= NUMERIC_SHARE:
        # Числа — значения, кроме кодов и номеров: «Валовой региональный продукт» — значение,
        # хотя в названии есть «регион».
        if hint in {TERRITORY_CODE, INDICATOR_CODE, PERIOD, LEVEL} or _CODE_SUFFIX.search(
            column.header.lower()
        ):
            return SKIP
        return VALUE if form != WIDE else SKIP
    if hint in {MISSING_REASON, LEVEL, UNIT, INDICATOR, INDICATOR_CODE, NOTE}:
        return hint
    if column.length > NOTE_LENGTH:
        return NOTE
    if column.values and all(_UNIT_WORDS.match(value) for value, _count in column.values[:5]):
        return UNIT
    if 0 < column.distinct <= SLICE_DISTINCT:
        return SLICE if column.distinct > 1 else SKIP
    return SKIP


def _small_integers(column: ColumnInfo) -> bool:
    """Возраст «0, 15, 50, 65» — значения разреза, а не показателя."""
    if not 1 < column.distinct <= SLICE_DISTINCT // 10 or _CODE_SUFFIX.search(
        column.header.lower()
    ):
        return False
    return all(re.fullmatch(r"\d{1,3}", value) for value, _count in column.values)


def _codes_beside_names(columns: list[ColumnInfo]) -> None:
    """«reason_code» рядом с «reason_name» — код того же разреза, разрезом он не считается."""
    names = {column.header.lower() for column in columns}
    for column in columns:
        header = column.header.lower()
        match = _CODE_SUFFIX.search(header)
        if column.role == SLICE and match:
            stem = header[: match.start()].rstrip("_ ")
            if {f"{stem}_name", f"{stem} name", f"{stem}name", f"наименование {stem}"} & names:
                column.role = column.guessed = SKIP


def _wide_indicators(result: Recognition) -> list[str]:
    """Показатели широкой таблицы: группы шапки над периодами или заголовок."""
    groups = list(
        dict.fromkeys(column.stamp["group"] for column in result.value_columns if column.stamp)
    )
    return [group or result.title for group in groups]


def _apply_overrides(result: Recognition, recipe: Mapping[str, Any]) -> None:
    """Роли, выбранные человеком."""
    for raw_index, role in (recipe.get("roles") or {}).items():
        index = int(raw_index)
        if 0 <= index < len(result.columns) and role in ROLES:
            result.columns[index].role = role
    if recipe.get("form") in {LONG, WIDE, INDICATORS}:
        result.form = recipe["form"]


# --- Строки листов и вставок ----------------------------------------------------------------------


def _sheet_rows(result: Recognition, rows: list[list[Any]]) -> None:
    """
    Строки территорий листа: сноски под таблицей, вводные «в том числе», подписи,
    разорванные по строкам (у таблиц из Word значения бывают то на первой, то на второй).
    """
    value_indexes = [column.index for column in result.value_columns]
    blocks = _blocks(result)
    labels: Counter[str] = Counter()
    for block, (territory, values) in enumerate(blocks):
        pending: list[tuple[int, str]] = []
        in_notes = False
        for index in range(result.data_start, len(rows)):
            row = rows[index]
            label = " ".join(cell_text(row[territory]).split()) if territory < len(row) else ""
            has_values = any(
                column < len(row) and cell_text(row[column]).strip()
                for column in values or value_indexes
            )
            if not label:
                continue
            note = _FOOTNOTE_ROW.match(label)
            # Длинная сноска бывает с числами в строке: под таблицей стоят проверочные суммы.
            if in_notes or (
                note
                and (not has_values or len(label) >= NOTE_LENGTH)
                and not matching.quick_match(label).is_territory
            ):
                in_notes = True
                if note:
                    result.footnotes[int(note.group(1))] = " ".join(note.group(2).split())
                elif result.footnotes:
                    last = max(result.footnotes)
                    result.footnotes[last] += " " + label
                continue
            if _LEAD_IN.match(label):
                continue
            if not has_values and _continues_last(result, labels, block, index, label):
                continue
            pending.append((index, label))
            if not has_values:
                continue
            row_label, used = _join_pending(pending)
            for first_index, first_label in pending[: len(pending) - len(used)]:
                _add_row(result, labels, TerritoryRow(first_index, first_label, block))
            _add_row(
                result,
                labels,
                TerritoryRow(used[-1], row_label, block, tuple(used[:-1])),
                has_values=True,
            )
            pending = []
        for index, label in pending:
            _add_row(result, labels, TerritoryRow(index, label, block))
    result.labels = dict(labels)


def _continues_last(
    result: Recognition, labels: Counter[str], block: int, index: int, label: str
) -> bool:
    """
    Окончание подписи строками ниже: «Ямало-Ненецкий автономный» со значениями и «округ»
    без них, «Ханты-Мансийский» и «автономный округ –», «Югра». Строка присоединяется, если
    вместе подпись узнаётся, а сама строка — обрывок или та же территория.
    """
    if not result.rows:
        return False
    last = result.rows[-1]
    if last.block != block or index - max((last.index, *last.merged_from)) > 2:  # noqa: PLR2004
        return False
    joined = f"{last.label} {label}"
    whole = matching.match(joined)
    if not (whole.is_resolved or whole.kind == matching.NESTED):
        return False
    alone = matching.quick_match(label)
    if alone.is_territory and alone.code != whole.code:
        return False
    before = matching.quick_match(last.label)
    if before.is_resolved and before.code != whole.code and alone.is_territory:
        return False
    labels[last.label] -= 1
    if not labels[last.label]:
        del labels[last.label]
    result.rows[-1] = TerritoryRow(last.index, joined, block, (*last.merged_from, index))
    labels[joined] += 1
    return True


def _blocks(result: Recognition) -> list[tuple[int, list[int]]]:
    """Половины таблицы «в две колонки»: столбец территорий и столбцы значений правее него."""
    territories = result.territory_columns
    value_indexes = [column.index for column in result.value_columns]
    if len(territories) == 1:
        return [(territories[0], value_indexes)]
    bounds = [*territories, 10**6]
    return [
        (start, [index for index in value_indexes if start < index < bounds[number + 1]])
        for number, start in enumerate(territories)
    ]


def _join_pending(pending: list[tuple[int, str]]) -> tuple[str, list[int]]:
    """Подпись строки со значениями вместе с обрывками над ней, если так узнаётся территория."""
    for start in range(len(pending)):
        part = pending[start:]
        label = " ".join(text for _index, text in part)
        found = matching.quick_match(label)
        if len(part) == 1 or found.is_resolved or found.kind == matching.NESTED:
            return label, [index for index, _text in part]
    return pending[-1][1], [pending[-1][0]]


def _add_row(
    result: Recognition, labels: Counter[str], row: TerritoryRow, *, has_values: bool = False
) -> None:
    # Подпись без значений и без территории — обрывок шапки или пояснение; строка
    # со значениями остаётся и с неузнанной подписью: о ней спросят.
    if not has_values and not matching.quick_match(row.label).is_territory:
        found = matching.match(row.label)
        if found.kind == matching.NONE:
            return
    result.rows.append(row)
    labels[row.label] += 1


def _territory_codes(result: Recognition, loaded: Loaded) -> dict[str, str]:
    """Код ОКАТО или ОКТМО у подписи — по строкам в памяти, если столбец кода есть."""
    code_columns = result.role_of(TERRITORY_CODE)
    if not code_columns or not loaded.complete:
        return {}
    territory = result.territory_columns[0]
    found: dict[str, str] = {}
    for row in loaded.rows[result.data_start :]:
        if territory < len(row) and code_columns[0].index < len(row):
            label = cell_text(row[territory]).strip()
            if label and label not in found:
                found[label] = cell_text(row[code_columns[0].index]).strip()
    return found


def _apply_territory_choices(result: Recognition, recipe: Mapping[str, Any]) -> None:
    """Выбор человека для неузнанных подписей и вложенных областей."""
    if result.territories is None:
        return
    choices = {**(recipe.get("territories") or {}), **(recipe.get("nested") or {})}
    for label, code in choices.items():
        if label not in result.territories.matches:
            continue
        if code == matching.OUTSIDE:
            result.territories.matches[label] = matching.Match(
                matching.OUTSIDE, reason="chosen", rule="chosen"
            )
        elif code == NOT_TERRITORY:
            result.territories.matches[label] = matching.Match(matching.NONE, rule="chosen")
        else:
            result.territories.matches[label] = matching.Match(
                matching.EXACT, code=code, rule="chosen"
            )
    answered = set(recipe.get("nested") or {})
    result.territories.nested = [
        item for item in result.territories.nested if item.label not in answered
    ]


def _looks_municipal(result: Recognition) -> bool:
    """Больше половины подписей — районы или города: таблица не по субъектам."""
    if result.territories is None or not result.labels:
        return False
    matches = result.territories.matches
    municipal = sum(1 for label in result.labels if matches[label].kind == matching.MUNICIPAL)
    resolved = sum(1 for label in result.labels if matches[label].is_resolved)
    return municipal > max(resolved, len(result.labels) // 2)


# --- Периоды, разрезы, значения -------------------------------------------------------------------


def _summarize_periods(result: Recognition, loaded: Loaded) -> None:
    stamps: list[tuple[int, Period]] = []
    for column in result.value_columns:
        if column.stamp:
            stamps.append((column.stamp["year"], Period.from_key(column.stamp["period"])))
    if result.form == INDICATORS and result.year:
        stamps.append((result.year, periods.ANNUAL))
    for column in result.role_of(PERIOD):
        for value in loaded.column_values(column.index, result.data_start):
            found = periods.stamp(value)
            if found is not None and found.year is not None:
                stamps.append((found.year, found.period))
    if not stamps:
        result.periods = {}
        return
    kinds = Counter(period.key for _year, period in stamps)
    years = [year for year, _period in stamps]
    result.periods = {
        "first": min(years),
        "last": max(years),
        "kinds": dict(sorted(kinds.items())),
        "count": len(set(stamps)),
    }


def _summarize_slices(result: Recognition, loaded: Loaded, recipe: Mapping[str, Any]) -> None:
    """Значения разрезов с числом строк; отбор — из рецепта или по умолчанию."""
    chosen = recipe.get("slices") or {}
    indicator_count = _indicator_count(result, loaded)
    total = indicator_count * max(len(result.periods.get("kinds", {})), 1)
    defaults: dict[int, list[str]] = {}
    for column in result.role_of(SLICE):
        values = loaded.column_values(column.index, result.data_start)
        result.slices[column.index] = {"header": column.header, "values": values}
        defaults[column.index] = sorted(values)
    product = total
    for selected in defaults.values():
        product *= max(len(selected), 1)
    if product > MAX_SERIES:
        for index, selected in defaults.items():
            totals = [value for value in selected if is_total(value, selected)]
            if totals:
                defaults[index] = totals[:1]
    for index, info in result.slices.items():
        picked = chosen.get(str(index))
        info["default"] = defaults[index]
        info["selected"] = (
            [value for value in picked if value in info["values"]] if picked else defaults[index]
        )
    series = total
    for info in result.slices.values():
        series *= max(len(info["selected"]), 1)
    result.series = series


def _indicator_count(result: Recognition, loaded: Loaded) -> int:
    indicator = result.role_of(INDICATOR) or result.role_of(INDICATOR_CODE)
    named = len(loaded.column_values(indicator[0].index, result.data_start)) if indicator else 1
    if result.form == WIDE:
        return max(named * len(result.indicators), 1)
    return max(named if indicator else len(result.value_columns), 1)


def is_total(value: str, siblings: Iterable[str]) -> bool:
    """Значение разреза — итог по нему: «Всего», «Оба пола», «T» у типа поселения ЕБТ."""
    text = value.strip().lower()
    if text in _TOTAL_VALUES:
        return True
    # Тип поселения ЕБТ: U — города, R — село, T — всего.
    return text == "t" and {item.strip().lower() for item in siblings} == {"u", "r", "t"}


def _check_values(result: Recognition, loaded: Loaded) -> None:
    """Ячейки значений, в которых не число и не знак пропуска."""
    texts: Counter[str] = Counter()
    for column in result.value_columns:
        values = loaded.column_values(column.index, result.data_start)
        style = cells.guess_style(list(values)[:2000])
        for value, count in values.items():
            if cells.parse(value, style).status == cells.TEXT:
                texts[value] += count
    result.text_cell_count = sum(texts.values())
    result.text_cells = texts.most_common(10)


def _year_hint(result: Recognition, table: TableInfo, file_name: str) -> int | None:
    """Год для таблицы «показатели в столбцах»: из столбцов с годом, заголовка, листа, файла."""
    named = Counter(column.stamp["year"] for column in result.value_columns if column.stamp)
    if named:
        return named.most_common(1)[0][0]
    for text in (result.title, table.sheet, PurePosixPath(table.member or file_name).stem):
        years = _YEAR_IN_TEXT.findall(text or "")
        if years:
            return int(years[-1])
    return None


def _find_duplicates(result: Recognition, rows: list[list[Any]]) -> None:
    """Одна территория дважды в листе с разными числами в одной клетке."""
    if result.territories is None or result.form not in {WIDE, INDICATORS}:
        return
    codes = result.territories.codes()
    # Строки различают показатель, единица и разрезы, если они в своих столбцах.
    keys = [
        column.index
        for column in result.columns
        if column.role in {INDICATOR, INDICATOR_CODE, UNIT, SLICE}
    ]
    # У таблицы «в две колонки» строка читает только столбцы своей половины;
    # одинаковые столбцы половин сравниваются по шапке.
    headers = {column.index: column.header for column in result.value_columns}
    halves = [values for _territory, values in _blocks(result)]
    seen: dict[tuple[Any, ...], tuple[str, int]] = {}
    for row in result.rows:
        code = codes.get(row.label)
        if code is None:
            continue
        line = rows[row.index]
        distinct = tuple(cell_text(line[index]) if index < len(line) else "" for index in keys)
        for column in result.value_columns:
            if column.index >= len(line) or column.index not in halves[row.block]:
                continue
            raw = cell_text(line[column.index]).strip()
            # Прочерк в одной строке и число в другой — строки дополняют друг друга
            # (Бурятия до 2018 года в Сибирском округе, после — в Дальневосточном).
            parsed = cells.parse(raw)
            if parsed.status != cells.VALUE:
                continue
            key = (code, headers[column.index], *distinct)
            if (
                key in seen
                and seen[key][1] != row.index
                and cells.parse(seen[key][0]).value != parsed.value
            ):
                result.duplicates.append(
                    {
                        "code": code,
                        "column": column.header,
                        "rows": [seen[key][1] + 1, row.index + 1],
                        "values": [seen[key][0], raw],
                    }
                )
            seen.setdefault(key, (raw, row.index))


def _find_same_headers(result: Recognition) -> None:
    """Столбцы одной половины таблицы с одинаковой подписью: числа попадут в один период."""
    if result.form != WIDE:
        return
    for _territory, values in _blocks(result):
        groups: dict[str, list[int]] = defaultdict(list)
        for column in result.value_columns:
            if column.index in values and column.header:
                groups[column.header].append(column.index)
        result.same_headers.extend(
            {"header": header, "columns": [index + 1 for index in indexes]}
            for header, indexes in groups.items()
            if len(indexes) > 1
        )


def _transposed(loaded: Loaded) -> Recognition | None:
    """Регионы в строке шапки, годы — в первом столбце."""
    for index, row in enumerate(loaded.rows[:30]):
        labels = [cell_text(cell).strip() for cell in row]
        if len(matching.subjects_in(labels)) < MIN_REGIONS:
            continue
        below = [line[0] for line in loaded.rows[index + 1 :] if line]
        if below and sum(_is_period(value) for value in below) / len(below) >= PERIOD_SHARE:
            columns = _bare_columns(loaded.rows)
            columns[0].role = columns[0].guessed = PERIOD
            for column in columns[1:]:
                column.header = labels[column.index] if column.index < len(labels) else ""
                column.role = column.guessed = VALUE
            result = Recognition(
                form=TRANSPOSED, columns=columns, header_rows=[index], data_start=index + 1
            )
            result.labels = {label: 1 for label in labels[1:] if label}
            result.territories = matching.match_column(result.labels)
            return result
    return None


def _bare_columns(rows: list[list[Any]]) -> list[ColumnInfo]:
    width = max((len(row) for row in rows), default=0)
    return [ColumnInfo(index=index, header="") for index in range(width)]


def as_stamp(column: ColumnInfo) -> Stamp | None:
    """Период столбца значений широкой таблицы."""
    if not column.stamp:
        return None
    return Stamp(
        column.stamp["year"], Period.from_key(column.stamp["period"]), tuple(column.stamp["notes"])
    )
