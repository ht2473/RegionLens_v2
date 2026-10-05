"""
Формулы таблицы: какие ряды им доступны, хранение в рецепте, проверка до сохранения,
зависимости между таблицами и значения рядов для расчёта.

Формула живёт в таблице, где её создали, и считается при её сборке. Ссылаться можно на ряды
этой таблицы (и на формулы выше по списку), на ряды других своих таблиц и на официальные
ряды сайта. Таблица, на ряды которой ссылаются формулы, после новой сборки или удаления
пересобирает зависящие от неё таблицы; круг зависимостей не допускается при сохранении.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.warehouse import routing
from apps.warehouse.duckdb_client import fetch_dicts, using_source

from . import formulas, indicators, naming
from .formulas import Candidate, Directory, FormulaError
from .models import Dataset, DatasetSeries, DatasetVersion

TITLE_LENGTH = 300
UNIT_LENGTH = 120
KINDS = indicators.KINDS
POLARITIES = indicators.POLARITIES
# Период ряда формулы: годовые значения.
PERIOD = indicators.YEAR_PERIOD


@dataclass(slots=True)
class Definition:
    """Показатель по формуле, как он хранится в рецепте."""

    code: str
    title: str
    expression: str
    unit: str = ""
    kind: str = indicators.RELATIVE
    polarity: str = DatasetSeries.Polarity.NEUTRAL.value
    labels: dict[str, str] = field(default_factory=dict)

    @property
    def keys(self) -> list[str]:
        return formulas.keys_of(self.expression)

    def as_recipe(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "title": self.title,
            "expression": self.expression,
            "unit": self.unit,
            "kind": self.kind,
            "polarity": self.polarity,
            "labels": self.labels,
        }

    @classmethod
    def from_recipe(cls, item: Mapping[str, Any]) -> Definition:
        return cls(
            code=str(item["code"]),
            title=str(item.get("title") or ""),
            expression=str(item.get("expression") or ""),
            unit=str(item.get("unit") or ""),
            kind=str(item.get("kind") or indicators.RELATIVE),
            polarity=str(item.get("polarity") or DatasetSeries.Polarity.NEUTRAL.value),
            labels=dict(item.get("labels") or {}),
        )


def definitions(version: DatasetVersion | None) -> list[Definition]:
    """Формулы версии в порядке расчёта."""
    if version is None:
        return []
    return [Definition.from_recipe(item) for item in version.recipe.get("formulas") or []]


def plan_items(version: DatasetVersion) -> list[dict[str, Any]]:
    """Ряды формул для плана сборки — после рядов таблицы, в порядке расчёта."""
    return [
        {
            "source": -1,
            "code": item.code,
            "indicator": f"formula:{item.code}",
            "title": item.title,
            "unit": item.unit,
            "kind": item.kind,
            "base_kind": item.kind,
            "polarity": item.polarity,
            "slices": [],
            "period": PERIOD,
            "derived": DatasetSeries.Derived.FORMULA.value,
            "base_code": "",
            "members": [],
            "method": "",
            "expression": item.expression,
            "labels": item.labels,
        }
        for item in definitions(version)
    ]


# --- Доступные ряды -------------------------------------------------------------------------


def siblings(dataset: Dataset) -> QuerySet[Dataset]:
    """Таблицы того же владельца (или того же гостя), кроме самой таблицы."""
    if dataset.owner_id is not None:
        found = Dataset.objects.filter(owner_id=dataset.owner_id)
    else:
        # Истёкшую таблицу гостя удалит очистка; до неё её уже нет, как в ``access.owned``.
        found = Dataset.objects.filter(
            owner__isnull=True, guest_key=dataset.guest_key, expires_at__gt=timezone.now()
        )
    return found.exclude(pk=dataset.pk)


def directory(dataset: Dataset, *, before: str = "") -> Directory:
    """
    Ряды, доступные формуле таблицы: свои (формулы — только выше ``before`` по списку),
    других своих таблиц и официальные.
    """
    found = Directory()
    version = dataset.current_version
    allowed = _formula_codes_before(version, before)
    for record in _built_series(version):
        if record.derived == DatasetSeries.Derived.FORMULA and record.code not in allowed:
            continue
        title = _series_title(record)
        found.add(
            Candidate(_key(dataset, record), title, "this", table=dataset.title),
            [title, record.title],
        )
    for other in siblings(dataset).select_related("current_version"):
        for record in _built_series(other.current_version):
            title = _series_title(record)
            found.add(
                Candidate(_key(other, record), title, "table", table=other.title),
                [title, record.title],
            )
    _official(found)
    return found


def _formula_codes_before(version: DatasetVersion | None, code: str) -> set[str]:
    codes: set[str] = set()
    for item in definitions(version):
        if item.code == code:
            break
        codes.add(item.code)
    return codes


def _built_series(version: DatasetVersion | None) -> list[DatasetSeries]:
    if version is None or version.state != DatasetVersion.State.BUILT:
        return []
    return list(version.series.order_by("order"))


def _series_title(record: DatasetSeries) -> str:
    return naming.full_title(record.title, record.slices, record.period)


def _key(dataset: Dataset, record: DatasetSeries) -> str:
    return f"{routing.USER_PREFIX}{dataset.code}:{record.code}"


def _official(found: Directory) -> None:
    """Официальные ряды: основной набор — короткими названиями, остальные — полными."""
    from apps.catalog.selectors import analysis_ready_series
    from apps.warehouse.queries import featured_series

    for item in featured_series():
        found.add(
            Candidate(item.key, item.short_title, "official"),
            [item.short_title_ru, item.short_title_en],
        )
    for series in analysis_ready_series():
        labels = []
        for indicator, name in (
            (series.indicator.name_ru, series.name_ru),
            (series.indicator.name_en, series.name_en),
        ):
            if not indicator:
                continue
            if series.has_subsection and name:
                labels.append(f"{indicator} — {name}")
            else:
                labels.append(indicator)
        title = series.indicator.name
        if series.has_subsection and series.name:
            title = f"{title} — {series.name}"
        found.add(Candidate(series.key, title, "official"), labels)


def titles(dataset: Dataset, keys: Iterable[str]) -> dict[str, str]:
    """Названия рядов для показа формулы: своей таблицы — как есть, других — с таблицей."""
    wanted = set(keys)
    result: dict[str, str] = {}
    codes = {routing.dataset_code(key) for key in wanted if routing.is_user_key(key)}
    tables = {dataset.code: dataset}
    tables.update({item.code: item for item in siblings(dataset).filter(code__in=codes)})
    for code, table in tables.items():
        for record in _built_series(table.current_version):
            key = _key(table, record)
            if key in wanted:
                title = _series_title(record)
                result[key] = title if code == dataset.code else f"{table.title}: {title}"
    official = [key for key in wanted if not routing.is_user_key(key)]
    if official:
        from apps.catalog.models import Series
        from apps.warehouse.queries import featured_set

        featured = featured_set().by_key()
        for series in Series.objects.filter(key__in=official).select_related("indicator"):
            item = featured.get(series.key)
            if item is not None:
                result[series.key] = item.short_title
            else:
                title = series.indicator.name
                result[series.key] = (
                    f"{title} — {series.name}" if series.has_subsection and series.name else title
                )
    return result


# --- Сохранение -----------------------------------------------------------------------------


@dataclass(slots=True)
class Check:
    """Проверка формулы до сохранения: ключи, итог расчёта по нынешним данным."""

    definition: Definition
    outcome: formulas.Outcome
    sources: list[tuple[str, str]]  # подпись и откуда ряд


def check(dataset: Dataset, data: Mapping[str, Any], *, code: str = "") -> Check:
    """Разобрать, сопоставить и посчитать формулу; ``FormulaError`` — с текстом для человека."""
    title = " ".join(str(data.get("title") or "").split())[:TITLE_LENGTH]
    if not title:
        raise FormulaError(_("Назовите показатель."))
    found = directory(dataset, before=code)
    expression, keys = formulas.canonical(str(data.get("expression") or ""), found)
    _check_cycle(dataset, keys)
    kind = str(data.get("kind") or "")
    polarity = str(data.get("polarity") or "")
    definition = Definition(
        code=code or formulas.new_code(_taken_codes(dataset)),
        title=title,
        expression=expression,
        unit=" ".join(str(data.get("unit") or "").split())[:UNIT_LENGTH],
        kind=kind if kind in KINDS else indicators.RELATIVE,
        polarity=polarity if polarity in POLARITIES else DatasetSeries.Polarity.NEUTRAL.value,
        labels={key: found.by_key[key].title for key in keys},
    )
    tree = formulas.parse(expression)
    outcome = formulas.evaluate(tree, file_inputs(dataset, keys), territory_flags())
    if not outcome.rows:
        raise FormulaError(
            _(
                "По этой формуле не получилось ни одного значения: у показателей нет общих "
                "лет и регионов."
            )
        )
    sources = [
        (
            found.by_key[key].title,
            {"this": _("эта таблица"), "official": _("официальный ряд")}.get(
                found.by_key[key].source, found.by_key[key].table
            ),
        )
        for key in keys
    ]
    return Check(definition=definition, outcome=outcome, sources=sources)


def save(dataset: Dataset, definition: Definition) -> None:
    """Добавить или заменить формулу в рецепте текущей версии."""
    version = dataset.current_version
    if version is None:
        raise FormulaError(_("Таблица ещё не собрана."))
    with transaction.atomic():
        version.refresh_from_db(fields=["recipe"])
        items = definitions(version)
        if not any(item.code == definition.code for item in items):
            if len(items) >= formulas.MAX_FORMULAS:
                raise FormulaError(
                    _("Формул уже %(count)s — это предел.") % {"count": formulas.MAX_FORMULAS}
                )
            items.append(definition)
        else:
            items = [definition if item.code == definition.code else item for item in items]
        version.recipe = {**version.recipe, "formulas": [item.as_recipe() for item in items]}
        version.save(update_fields=["recipe", "updated_at"])


def remove(dataset: Dataset, code: str) -> bool:
    """Убрать формулу; формулы ниже, ссылавшиеся на неё, останутся без значений."""
    version = dataset.current_version
    if version is None:
        return False
    with transaction.atomic():
        version.refresh_from_db(fields=["recipe"])
        items = definitions(version)
        kept = [item for item in items if item.code != code]
        if len(kept) == len(items):
            return False
        version.recipe = {**version.recipe, "formulas": [item.as_recipe() for item in kept]}
        version.save(update_fields=["recipe", "updated_at"])
    return True


def _taken_codes(dataset: Dataset) -> set[str]:
    version = dataset.current_version
    codes = {item.code for item in definitions(version)}
    if version is not None:
        codes |= set(version.series.values_list("code", flat=True))
    return codes


# --- Зависимости между таблицами ------------------------------------------------------------


def referenced_tables(version: DatasetVersion | None) -> set[str]:
    """Коды других таблиц, на ряды которых ссылаются формулы версии."""
    own = version.dataset.code if version is not None else ""
    return {
        routing.dataset_code(key)
        for item in definitions(version)
        for key in item.keys
        if routing.is_user_key(key) and routing.dataset_code(key) != own
    }


def dependents(dataset: Dataset) -> list[Dataset]:
    """Таблицы того же владельца, формулы которых ссылаются на ряды этой таблицы."""
    return [
        other
        for other in siblings(dataset).select_related("current_version")
        if dataset.code in referenced_tables(other.current_version)
    ]


def _check_cycle(dataset: Dataset, keys: Iterable[str]) -> None:
    """Таблица, на которую ссылается формула, не должна сама зависеть от этой таблицы."""
    tables = {item.code: item for item in siblings(dataset).select_related("current_version")}
    graph = {code: referenced_tables(item.current_version) for code, item in tables.items()}
    targets = {
        routing.dataset_code(key)
        for key in keys
        if routing.is_user_key(key) and routing.dataset_code(key) != dataset.code
    }
    seen: set[str] = set()
    stack = list(targets)
    while stack:
        code = stack.pop()
        if code == dataset.code:
            raise FormulaError(
                _(
                    "Таблица, на показатель которой ссылается формула, сама считается по этой "
                    "таблице. Такой круг не посчитать."
                )
            )
        if code in seen:
            continue
        seen.add(code)
        stack.extend(graph.get(code, ()))


def rebuild_dependents(dataset: Dataset) -> None:
    """Пересобрать таблицы, формулы которых ссылаются на эту (после сборки или удаления)."""
    from . import jobs

    for other in dependents(dataset):
        version = other.current_version
        if version is not None and version.state == DatasetVersion.State.BUILT:
            jobs.start(version, jobs.BUILD)


# --- Значения для расчёта -------------------------------------------------------------------

Rows = list[tuple[str, int, float | None]]


def territory_flags() -> dict[str, bool]:
    """Территории справочника: код → субъект ли (для RANK и счёта субъектов)."""
    from apps.catalog.models import Territory

    regions = set(Territory.objects.comparable().values_list("code", flat=True))
    return {code: code in regions for code in Territory.objects.values_list("code", flat=True)}


def file_inputs(dataset: Dataset, keys: Iterable[str]) -> dict[str, Rows]:
    """Значения рядов из собранных файлов таблиц и склада — для проверки формулы."""
    wanted = list(dict.fromkeys(keys))
    inputs: dict[str, Rows] = {}
    by_table: dict[str, list[str]] = {}
    for key in wanted:
        if routing.is_user_key(key):
            by_table.setdefault(routing.dataset_code(key), []).append(key)
    tables = {dataset.code: dataset}
    tables.update({item.code: item for item in siblings(dataset).filter(code__in=list(by_table))})
    for code, table_keys in by_table.items():
        table = tables.get(code)
        if table is not None:
            inputs.update(table_rows(table.current_version, table_keys))
    inputs.update(official_rows([key for key in wanted if not routing.is_user_key(key)]))
    return inputs


def table_rows(version: DatasetVersion | None, keys: list[str]) -> dict[str, Rows]:
    """
    Значения рядов из файла собранной версии таблицы — через соединения слоя рядов: файл,
    уже открытый процессом, второе соединение с другими настройками не открывает.
    """
    from .scope import source_of_version

    source = source_of_version(version)
    if source is None or not keys:
        return {}
    with using_source(source):
        rows = fetch_dicts(
            "SELECT series_key, territory_code, CAST(year AS INTEGER) AS year, value "
            "FROM fact_observation WHERE series_key IN (SELECT unnest(?)) "
            "ORDER BY 1, 2, 3",
            [keys],
        )
    result: dict[str, Rows] = {}
    for row in rows:
        result.setdefault(str(row["series_key"]), []).append(
            (str(row["territory_code"]), int(row["year"]), row["value"])
        )
    return result


def official_rows(keys: list[str]) -> dict[str, Rows]:
    """Значения официальных рядов по всем территориям справочника."""
    if not keys:
        return {}
    from apps.warehouse.queries import series_values

    values = series_values(keys, sorted(territory_flags()))
    return {
        key: [
            (code, int(year), value)
            for code, years in by_code.items()
            for year, value in years.items()
        ]
        for key, by_code in values.items()
    }
