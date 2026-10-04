"""
Ряды внешних источников и помесячный слой склада.

Из разобранных выпусков Банка России, ФНС и бюллетеня Росстата строятся значения
по месяцам (``fact_month``) и годовые значения рядов проекта (версии в ``fact_vintage``).
Каждый выпуск даёт свою версию: годовые значения пересматриваются так же, как значения
набора; в помесячном слое остаётся значение самого позднего выпуска.
"""

from __future__ import annotations

import logging

import duckdb
import numpy as np
import pandas as pd

from apps.catalog.constants import ValueFlag, ValueQuality
from apps.sources import parsed
from apps.sources.registry import POPULATION, DatasetMonthly, Registry, SourceSeries, registry
from apps.sources.territories import region_codes

from ..population import population_frame
from ..queries.common import COUNTRY_CODE, featured_set

logger = logging.getLogger(__name__)

# Численность России в пределах этих границ; иначе у рядов знаменателя сменилась единица.
POPULATION_RANGE = (100e6, 200e6)

MONTH_COLUMNS = (
    "series_key",
    "territory_code",
    "year",
    "month",
    "kind",
    "value",
    "quality",
    "flags",
    "edition_code",
)
VINTAGE_COLUMNS = (
    "series_key",
    "territory_code",
    "year",
    "edition_code",
    "edition_year",
    "value",
    "quality",
    "flags",
)


class SourceSeriesError(RuntimeError):
    """Ряды внешних источников не строятся: сменился вид данных."""


def load_source_series(connection: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """Измерения рядов проекта, годовые версии их значений и помесячный слой."""
    book = registry()
    sources = {item.source for item in book.series} | {item.source for item in book.dataset_monthly}
    releases = {source: parsed.releases(source) for source in sorted(sources)}
    known = _territories(connection)
    active = [item for item in book.series if releases.get(item.source)]
    if active:
        register_dimensions(connection, book, active)

    population = _population(connection, book) if active else pd.DataFrame()
    months: list[pd.DataFrame] = []
    vintages: list[pd.DataFrame] = []
    for source, items in releases.items():
        own = [item for item in active if item.source == source]
        layers = [entry for entry in book.dataset_monthly if entry.source == source]
        for info, path in items:
            frame = parsed.read(path)
            if own:
                monthly = build_months(frame, own, population)
                months.append(_month_rows(monthly, info.edition_code))
                annual = build_annual(monthly, own, population)
                vintages.append(_vintage_rows(annual, info))
            for entry in layers:
                months.append(_dataset_layer(frame, entry, info.edition_code))

    vintage_frame = _keep_known(pd.concat(vintages, ignore_index=True), known) if vintages else None
    if vintage_frame is not None and not vintage_frame.empty:
        _insert(connection, "fact_vintage", vintage_frame, VINTAGE_COLUMNS)
    month_count = 0
    if months:
        month_frame = _keep_known(pd.concat(months, ignore_index=True), known)
        month_count = _insert_latest_months(connection, month_frame)

    statistics = {
        "series": len(active),
        "vintages": 0 if vintage_frame is None else len(vintage_frame),
        "months": month_count,
    }
    logger.info(
        "Рядов источников: %(series)d, версий: %(vintages)d, месяцев: %(months)d", statistics
    )
    return statistics


# ---------------------------------------------------------------------------------------
# Измерения
# ---------------------------------------------------------------------------------------


def section_code(code: str) -> str:
    """Код раздела рядов проекта в складе."""
    return f"sec_rl_{code}"


def unit_code(code: str) -> str:
    """Код единицы рядов проекта в складе."""
    return f"unit_rl_{code}"


def register_dimensions(
    connection: duckdb.DuckDBPyConnection, book: Registry, series: list[SourceSeries]
) -> None:
    """Разделы, единицы, показатели и ряды проекта — рядом с измерениями набора."""
    used_sections = {item.section for item in series}
    sections = pd.DataFrame(
        [
            {
                "section_code": section_code(section.code),
                "source_name": section.name_ru,
                "name_ru": section.name_ru,
                "name_en": section.name_en,
                "indicator_count": sum(1 for item in series if item.section == section.code),
                "series_count": 0,
            }
            for section in book.sections
            if section.code in used_sections
        ]
    )
    _insert(connection, "dim_section", sections, tuple(sections.columns))

    units = pd.DataFrame(
        [
            {
                "unit_code": unit_code(unit.code),
                "source_name": unit.name_ru,
                "name_ru": unit.name_ru,
                "name_en": unit.name_en,
                "short_name_ru": unit.short_ru,
                "short_name_en": unit.short_en,
                "kind": unit.kind,
                "multiplier": 1.0,
                "derived_from_name": False,
                "country_scale": 1.0,
                "country_scale_unknown": False,
                "observation_count": 0,
            }
            for unit in book.units.values()
            if any(item.unit.code == unit.code for item in series)
        ]
    )
    _insert(connection, "dim_unit", units, tuple(units.columns))

    indicators = pd.DataFrame(
        [
            {
                "indicator_code": item.indicator,
                "name_ru": item.name_ru,
                "name_en": item.name_en,
                "section_code": section_code(item.section),
                "series_count": 1,
                "observation_count": 0,
            }
            for item in series
        ]
    )
    _insert(connection, "dim_indicator", indicators, tuple(indicators.columns))

    featured = featured_set().by_key()
    rows = pd.DataFrame(
        [
            {
                "series_key": item.key,
                "indicator_code": item.indicator,
                "section_code": section_code(item.section),
                "unit_code": unit_code(item.unit.code),
                "indicator_name_ru": item.name_ru,
                "indicator_name_en": item.name_en,
                "has_subsection": False,
                "full_title_ru": item.name_ru,
                "full_title_en": item.name_en,
                "polarity": featured[item.key].polarity if item.key in featured else "unknown",
                "source_code": item.source,
            }
            for item in series
        ]
    )
    _insert(connection, "dim_series", rows, tuple(rows.columns))


# ---------------------------------------------------------------------------------------
# Месяцы и годы
# ---------------------------------------------------------------------------------------


def build_months(
    frame: pd.DataFrame, series: list[SourceSeries], population: pd.DataFrame
) -> dict[str, pd.DataFrame]:
    """
    Значения по месяцам рядов проекта из одного выпуска.

    Столбцы: ``territory_code``, ``year``, ``month``, ``level``, ``ytd`` (у потоков),
    ``hidden``, ``computed``, ``frozen``, ``preliminary``.
    """
    result: dict[str, pd.DataFrame] = {}
    pending = list(series)
    while pending:
        progressed = False
        for item in list(pending):
            ratio = item.ratio
            needs = [] if ratio is None else [ratio.numerator, ratio.denominator]
            if any(key != POPULATION and key not in result for key in needs):
                continue
            months = (
                _measure_months(frame, item)
                if ratio is None
                else _ratio_months(item, result, population)
            )
            if item.flow and "ytd" not in months:
                months["ytd"] = _year_to_date(months)
            result[item.key] = months
            pending.remove(item)
            progressed = True
        if not progressed:
            listed = ", ".join(item.key for item in pending)
            raise SourceSeriesError(f"отношения рядов ссылаются на неизвестные ряды: {listed}")
    return result


def build_annual(
    months: dict[str, pd.DataFrame], series: list[SourceSeries], population: pd.DataFrame
) -> dict[str, pd.DataFrame]:
    """Годовые значения рядов проекта из месяцев одного выпуска."""
    result: dict[str, pd.DataFrame] = {}
    for item in series:
        if item.annual != "ratio":
            result[item.key] = _annual_direct(item, months)
    for item in series:
        if item.annual == "ratio":
            result[item.key] = _annual_ratio(item, result, population)
    return result


def _measure_months(frame: pd.DataFrame, item: SourceSeries) -> pd.DataFrame:
    rows = frame[(frame["measure"] == item.measure) & (frame["period_kind"] == "month")]
    months = pd.DataFrame(
        {
            "territory_code": rows["territory_code"].astype(str),
            "year": rows["year"].astype(int),
            "month": rows["period"].astype(int),
            "level": pd.to_numeric(rows["value"], errors="coerce"),
            "hidden": rows["hidden"].astype(bool),
            "computed": False,
            "frozen": False,
            "preliminary": rows["preliminary"].astype(bool),
        }
    ).reset_index(drop=True)
    months.loc[months["hidden"], "level"] = np.nan
    if item.country_sum:
        months = _country_as_sum(months)
    return months


def _country_as_sum(months: pd.DataFrame) -> pd.DataFrame:
    """
    Значение по России — сумма субъектов справочника.

    У ФНС Россия включает субъекты вне справочника; без замены значение страны
    не сходилось бы с суммой субъектов и с численностью, на которую оно делится.
    """
    regions = months[months["territory_code"].isin(region_codes())]
    complete = regions.groupby(["year", "month"]).agg(
        level=("level", lambda values: values.sum(min_count=len(region_codes()))),
        hidden=("hidden", "any"),
        preliminary=("preliminary", "any"),
    )
    country = complete.reset_index()
    country["territory_code"] = COUNTRY_CODE
    country["computed"] = True
    country["frozen"] = False
    others = months[months["territory_code"] != COUNTRY_CODE]
    return pd.concat([others, country[others.columns]], ignore_index=True)


def _ratio_months(
    item: SourceSeries, done: dict[str, pd.DataFrame], population: pd.DataFrame
) -> pd.DataFrame:
    assert item.ratio is not None
    ratio = item.ratio
    numerator = done[ratio.numerator]
    keys = ["territory_code", "year", "month"]
    if ratio.denominator == POPULATION:
        merged = _with_population(numerator, population)
        denominator_level = merged["population"]
        denominator_ytd = merged["population"]
        hidden = merged["hidden"]
        frozen = merged["frozen"] | merged["population_frozen"]
    else:
        other = done[ratio.denominator]
        merged = numerator.merge(other, on=keys, suffixes=("", "_den"))
        denominator_level = merged["level_den"]
        denominator_ytd = merged.get("ytd_den")
        hidden = merged["hidden"] | merged["hidden_den"]
        frozen = merged["frozen"] | merged["frozen_den"]
    months = merged[keys].copy()
    months["level"] = _divide(merged["level"], denominator_level) * ratio.factor
    if item.flow and "ytd" in merged and denominator_ytd is not None:
        months["ytd"] = _divide(merged["ytd"], denominator_ytd) * ratio.factor
    months["hidden"] = hidden.to_numpy()
    months["computed"] = True
    months["frozen"] = frozen.to_numpy()
    months["preliminary"] = merged["preliminary"].to_numpy()
    return months.reset_index(drop=True)


def _with_population(frame: pd.DataFrame, population: pd.DataFrame) -> pd.DataFrame:
    """
    Подставить численность года значения; после последнего известного года — последнюю.

    ``population_frozen`` — знаменатель взят за более ранний год.
    """
    if population.empty:
        merged = frame.copy()
        merged["population"] = np.nan
        merged["population_frozen"] = False
        return merged
    last_year = population.groupby("territory_code")["year"].max().rename("last_year")
    merged = frame.merge(last_year, on="territory_code", how="left")
    merged["base_year"] = np.minimum(
        merged["year"], merged["last_year"].fillna(merged["year"])
    ).astype(int)
    merged = merged.merge(
        population.rename(columns={"year": "base_year"}),
        on=["territory_code", "base_year"],
        how="left",
    )
    merged["population_frozen"] = merged["base_year"] < merged["year"]
    return merged.drop(columns=["last_year"])


def _year_to_date(months: pd.DataFrame) -> pd.Series:
    """Нарастающий итог с января; пропущенный месяц обрывает итог до конца года."""
    ordered = months.sort_values(["territory_code", "year", "month"])
    values = []
    for (_territory, _year), rows in ordered.groupby(["territory_code", "year"], sort=False):
        total = 0.0
        broken = False
        expected = 1
        for month, level, hidden in zip(rows["month"], rows["level"], rows["hidden"], strict=True):
            if month != expected or hidden or pd.isna(level):
                broken = True
            expected = month + 1
            total = total if broken else total + float(level)
            values.append(np.nan if broken else total)
    return pd.Series(values, index=ordered.index).reindex(months.index)


def _annual_direct(item: SourceSeries, months: dict[str, pd.DataFrame]) -> pd.DataFrame:
    frame = months[item.key]
    if item.annual == "sum":
        rows = frame[frame["month"] == 12].assign(value=lambda data: data["ytd"])  # noqa: PLR2004
        rows = rows.assign(computed=True)
    elif item.annual == "december":
        rows = frame[frame["month"] == 12].assign(value=lambda data: data["level"])  # noqa: PLR2004
    elif item.annual == "january_next":
        rows = frame[frame["month"] == 1].assign(
            value=lambda data: data["level"], year=lambda data: data["year"] - 1
        )
    else:
        rows = _weighted_mean(frame, months[item.weights])
    return rows[["territory_code", "year", "value", "hidden", "computed", "frozen", "preliminary"]]


def _weighted_mean(frame: pd.DataFrame, weights: pd.DataFrame) -> pd.DataFrame:
    """Среднее двенадцати месяцев с весами; год без полного набора месяцев не считается."""
    merged = frame.merge(
        weights[["territory_code", "year", "month", "level"]].rename(columns={"level": "weight"}),
        on=["territory_code", "year", "month"],
    )
    merged = merged.dropna(subset=["level", "weight"])
    merged = merged[merged["weight"] > 0]
    merged["product"] = merged["level"] * merged["weight"]
    grouped = merged.groupby(["territory_code", "year"]).agg(
        months=("month", "nunique"),
        product=("product", "sum"),
        weight=("weight", "sum"),
        hidden=("hidden", "any"),
        preliminary=("preliminary", "any"),
    )
    grouped = grouped[grouped["months"] == 12].reset_index()  # noqa: PLR2004
    grouped["value"] = grouped["product"] / grouped["weight"]
    grouped["computed"] = True
    grouped["frozen"] = False
    return grouped


def _annual_ratio(
    item: SourceSeries, done: dict[str, pd.DataFrame], population: pd.DataFrame
) -> pd.DataFrame:
    assert item.ratio is not None
    ratio = item.ratio
    numerator = done[ratio.numerator]
    keys = ["territory_code", "year"]
    if ratio.denominator == POPULATION:
        merged = _with_population(numerator, population)
        denominator = merged["population"]
        frozen = merged["frozen"] | merged["population_frozen"]
        hidden = merged["hidden"]
    else:
        merged = numerator.merge(done[ratio.denominator], on=keys, suffixes=("", "_den"))
        denominator = merged["value_den"]
        frozen = merged["frozen"] | merged["frozen_den"]
        hidden = merged["hidden"] | merged["hidden_den"]
    result = merged[keys].copy()
    result["value"] = _divide(merged["value"], denominator) * ratio.factor
    result["hidden"] = hidden.to_numpy()
    result["computed"] = True
    result["frozen"] = frozen.to_numpy()
    result["preliminary"] = merged["preliminary"].to_numpy()
    return result


def _divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    top = pd.to_numeric(numerator, errors="coerce").to_numpy(dtype=float)
    bottom = pd.to_numeric(denominator, errors="coerce").to_numpy(dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        values = np.where((bottom == 0) | np.isnan(bottom), np.nan, top / bottom)
    return pd.Series(values, index=numerator.index)


# ---------------------------------------------------------------------------------------
# Помесячный слой рядов набора
# ---------------------------------------------------------------------------------------


def _dataset_layer(frame: pd.DataFrame, entry: DatasetMonthly, edition_code: str) -> pd.DataFrame:
    """Месячные значения ряда набора из показателя бюллетеня как есть."""
    rows = frame[frame["measure"] == entry.measure]
    if entry.period_kind == "ytd":
        # Январь публикуется одним месяцем: он же период «с начала года».
        rows = rows[
            (rows["period_kind"] == "ytd")
            | ((rows["period_kind"] == "month") & (rows["period"] == 1))
        ]
    else:
        rows = rows[rows["period_kind"] == entry.period_kind]
    hidden = rows["hidden"].astype(bool)
    flags = np.where(rows["preliminary"].astype(bool), int(ValueFlag.PRELIMINARY), 0)
    return pd.DataFrame(
        {
            "series_key": entry.series,
            "territory_code": rows["territory_code"].astype(str),
            "year": rows["year"].astype(int),
            "month": rows["period"].astype(int),
            "kind": entry.kind,
            "value": pd.to_numeric(rows["value"], errors="coerce").where(~hidden),
            "quality": np.where(hidden, int(ValueQuality.HIDDEN), int(ValueQuality.OBSERVED)),
            "flags": flags,
            "edition_code": edition_code,
        },
        columns=list(MONTH_COLUMNS),
    ).drop_duplicates(subset=["series_key", "territory_code", "year", "month", "kind"])


# ---------------------------------------------------------------------------------------
# Запись
# ---------------------------------------------------------------------------------------


def _flags(frame: pd.DataFrame) -> np.ndarray:
    flags = np.zeros(len(frame), dtype=int)
    flags |= np.where(frame["preliminary"].astype(bool), int(ValueFlag.PRELIMINARY), 0)
    flags |= np.where(frame["computed"].astype(bool), int(ValueFlag.COMPUTED), 0)
    flags |= np.where(frame["frozen"].astype(bool), int(ValueFlag.FROZEN_DENOMINATOR), 0)
    return flags


def _month_rows(months: dict[str, pd.DataFrame], edition_code: str) -> pd.DataFrame:
    parts = []
    for key, frame in months.items():
        for kind in ("level", "ytd"):
            if kind not in frame:
                continue
            values = pd.to_numeric(frame[kind], errors="coerce")
            hidden = frame["hidden"].astype(bool)
            keep = values.notna() | hidden
            chosen = frame[keep]
            parts.append(
                pd.DataFrame(
                    {
                        "series_key": key,
                        "territory_code": chosen["territory_code"],
                        "year": chosen["year"].astype(int),
                        "month": chosen["month"].astype(int),
                        "kind": kind,
                        "value": values[keep].where(~hidden[keep]),
                        "quality": np.where(
                            hidden[keep], int(ValueQuality.HIDDEN), int(ValueQuality.OBSERVED)
                        ),
                        "flags": _flags(chosen),
                        "edition_code": edition_code,
                    },
                    columns=list(MONTH_COLUMNS),
                )
            )
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=MONTH_COLUMNS)


def _vintage_rows(annual: dict[str, pd.DataFrame], info: parsed.ReleaseInfo) -> pd.DataFrame:
    parts = []
    for key, frame in annual.items():
        values = pd.to_numeric(frame["value"], errors="coerce")
        hidden = frame["hidden"].astype(bool)
        keep = values.notna() | hidden
        chosen = frame[keep]
        parts.append(
            pd.DataFrame(
                {
                    "series_key": key,
                    "territory_code": chosen["territory_code"],
                    "year": chosen["year"].astype(int),
                    "edition_code": info.edition_code,
                    "edition_year": info.reference_year,
                    "value": values[keep].where(~hidden[keep]),
                    "quality": np.where(
                        hidden[keep], int(ValueQuality.HIDDEN), int(ValueQuality.OBSERVED)
                    ),
                    "flags": _flags(chosen),
                },
                columns=list(VINTAGE_COLUMNS),
            )
        )
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=VINTAGE_COLUMNS)


def _keep_known(frame: pd.DataFrame, known: set[str]) -> pd.DataFrame:
    """Только территории справочника: субъекты вне него остаются в разобранном выпуске."""
    return frame[frame["territory_code"].isin(known)].reset_index(drop=True)


def _insert_latest_months(connection: duckdb.DuckDBPyConnection, frame: pd.DataFrame) -> int:
    """Записать помесячный слой: из выпусков побеждает поздний."""
    connection.register("tmp_month_versions", frame.reindex(columns=list(MONTH_COLUMNS)))
    columns = ", ".join(MONTH_COLUMNS)
    connection.execute(
        f"""
        INSERT INTO fact_month ({columns})
        SELECT {", ".join(f"m.{column}" for column in MONTH_COLUMNS)}
        FROM (
            SELECT v.*,
                   row_number() OVER (
                       PARTITION BY v.series_key, v.territory_code, v.year, v.month, v.kind
                       ORDER BY (v.value IS NULL) ASC, e.edition_rank DESC
                   ) AS priority
            FROM tmp_month_versions AS v
            JOIN dim_edition AS e ON e.edition_code = v.edition_code
        ) AS m
        WHERE m.priority = 1
        ORDER BY m.series_key, m.territory_code, m.year, m.month, m.kind
        """
    )
    connection.unregister("tmp_month_versions")
    row = connection.execute("SELECT count(*) FROM fact_month").fetchone()
    return int(row[0]) if row else 0


def _insert(
    connection: duckdb.DuckDBPyConnection,
    table: str,
    frame: pd.DataFrame,
    columns: tuple[str, ...],
) -> None:
    if frame.empty:
        return
    name = f"tmp_insert_{table}"
    connection.register(name, frame.reindex(columns=list(columns)))
    listed = ", ".join(columns)
    # Имена таблиц и столбцов — из констант модуля.
    connection.execute(f"INSERT INTO {table} ({listed}) SELECT {listed} FROM {name}")
    connection.unregister(name)


def _territories(connection: duckdb.DuckDBPyConnection) -> set[str]:
    return {
        row[0] for row in connection.execute("SELECT territory_code FROM dim_territory").fetchall()
    }


def _population(connection: duckdb.DuckDBPyConnection, book: Registry) -> pd.DataFrame:
    """Среднегодовая численность населения (:mod:`apps.warehouse.population`) с проверкой единиц."""
    frame = population_frame(connection, book)
    country = frame[frame["territory_code"] == COUNTRY_CODE].sort_values("year")
    if country.empty:
        raise SourceSeriesError("нет численности России для рядов на жителя")
    latest = float(country["population"].iloc[-1])
    low, high = POPULATION_RANGE
    if not low <= latest <= high:
        raise SourceSeriesError(
            f"численность России {latest:,.0f} вне пределов: сменилась единица рядов "
            f"{book.population_total} или {book.population_per_capita}"
        )
    return frame
