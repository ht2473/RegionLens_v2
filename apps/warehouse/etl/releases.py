"""
Выпуски внешних источников в складе: издания, версии значений, сноски и сшивка с набором.

Бюллетень продолжает ряд после последнего года набора (``continue``); таблица ВРП —
новая редакция того же ряда и заменяет значения набора (``supersede``). Сшивка сверяет
источник с набором по общим годам: расхождение сверх допуска делает связь условной,
и на стыке ставится разрыв (``catalog_sync.sync_source_breaks``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from apps.catalog.constants import ValueFlag, ValueQuality
from apps.sources import parsed
from apps.sources.annual import Link, derive, links
from apps.sources.collect import SOURCES
from apps.warehouse.duckdb_client import require_row

logger = logging.getLogger(__name__)

# Сверка идёт по трём общим годам перед последним годом набора: последний год набора
# у многих рядов предварительный, и его уточнение источником — не расхождение способов.
RECONCILE_YEARS = 3
# Связь полная, если в допуске не меньше этой доли пар «субъект × год».
FULL_LINK_SHARE = 0.9
# Ряды, которые выпуск источника заменяет целиком (та же статистика в новой редакции).
SUPERSEDING_SOURCES = frozenset({"rosstat_grp"})

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


@dataclass(slots=True)
class LinkOutcome:
    """Итог сшивки одного ряда: связь, стык, сверка и число загруженных версий."""

    link: Link
    mode: str
    kind: str = "full"
    dataset_last_year: int | None = None
    junction_year: int | None = None
    last_year: int | None = None
    release_code: str = ""
    compared: list[int] = field(default_factory=list)
    pairs: int = 0
    within_share: float | None = None
    median_deviation: float | None = None
    max_deviation: float | None = None
    worst_territory: str = ""
    worst_year: int | None = None
    revised_count: int = 0
    rows: list[dict[str, Any]] = field(default_factory=list)


def load_releases(connection: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """Добавить в склад издания и версии значений из разобранных выпусков источников."""
    releases = [item for item in parsed.releases() if item[0].source in SOURCES]
    if not releases:
        return {"releases": 0, "links": 0, "vintages": 0}

    _register_editions(connection, [info for info, _path in releases])
    frames = {info.edition_code: parsed.read(path) for info, path in releases}
    first_year = int(
        require_row(connection.execute("SELECT min(year) FROM fact_vintage").fetchone(), "min")[0]
    )

    outcomes: list[LinkOutcome] = []
    for link in links():
        own = [info for info, _path in releases if info.source == link.source]
        if not own:
            continue
        dataset = _dataset_values(connection, link.series)
        if dataset.empty:
            logger.warning("Ряда %s нет в наборе: связь с источником пропущена", link.series)
            continue
        denominators = _denominators(connection, link) if link.method == "per_capita" else None
        outcome = _stitch(
            link, own, frames, dataset, denominators=denominators, first_year=first_year
        )
        outcomes.append(outcome)

    rows = [row for outcome in outcomes for row in outcome.rows]
    if rows:
        frame = pd.DataFrame(rows, columns=list(VINTAGE_COLUMNS))
        connection.register("tmp_release_vintages", frame)
        connection.execute(
            f"INSERT INTO fact_vintage ({', '.join(VINTAGE_COLUMNS)}) "
            "SELECT * FROM tmp_release_vintages"
        )
        connection.unregister("tmp_release_vintages")
    _write_links(connection, outcomes)
    _write_notes(connection, outcomes, releases)
    refresh_edition_counters(connection)

    conditional = sum(1 for outcome in outcomes if outcome.kind == "conditional")
    logger.info(
        "Выпусков источников: %d, связей: %d (условных %d), версий значений: %d",
        len(releases),
        len(outcomes),
        conditional,
        len(rows),
    )
    return {
        "releases": len(releases),
        "links": len(outcomes),
        "conditional": conditional,
        "vintages": len(rows),
    }


# ---------------------------------------------------------------------------------------
# Сшивка одного ряда
# ---------------------------------------------------------------------------------------


def _stitch(
    link: Link,
    releases: list[parsed.ReleaseInfo],
    frames: dict[str, pd.DataFrame],
    dataset: pd.DataFrame,
    *,
    denominators: pd.DataFrame | None,
    first_year: int,
) -> LinkOutcome:
    mode = "supersede" if link.source in SUPERSEDING_SOURCES else "continue"
    outcome = LinkOutcome(link=link, mode=mode)
    last_year = int(dataset["year"].max())
    outcome.dataset_last_year = last_year
    known = dataset.set_index(["territory_code", "year"])["value"]

    derived = {
        info.edition_code: derive(frames[info.edition_code], link, denominators)
        for info in releases
    }
    latest = releases[-1]
    outcome.release_code = latest.code
    _reconcile(outcome, derived[latest.edition_code], dataset, last_year)
    if mode == "supersede":
        outcome.kind = "revision"
    elif outcome.within_share is None or outcome.within_share < FULL_LINK_SHARE:
        outcome.kind = "conditional"

    for info in releases:
        annual = derived[info.edition_code]
        if annual.empty:
            continue
        complete = annual.dropna(subset=["value"])
        newest = int(complete["year"].max()) if not complete.empty else None
        for item in annual.itertuples(index=False):
            year = int(item.year)
            if year < first_year:
                continue
            if mode == "continue" and year < last_year:
                continue
            if mode == "continue" and year == last_year:
                # Последний год набора источник уточняет при полной связи и той же величине:
                # среднее месяцев и расчёт по ценам отличаются от годового значения способом.
                if outcome.kind != "full" or not _same_statistic(link):
                    continue
                if not _revises(link, item, known):
                    continue
            elif mode == "supersede" and not _revises(link, item, known):
                continue
            if (item.territory_code, year) in known.index and year <= last_year:
                outcome.revised_count += info is latest
            outcome.rows.append(_vintage(link.series, info, item, preliminary=year == newest))
    years_after = [row["year"] for row in outcome.rows if row["year"] > last_year]
    outcome.junction_year = last_year + 1 if years_after else None
    outcome.last_year = max(years_after) if years_after else None
    return outcome


def _reconcile(
    outcome: LinkOutcome, annual: pd.DataFrame, dataset: pd.DataFrame, last_year: int
) -> None:
    """Сверить значения источника с набором по общим годам."""
    link = outcome.link
    merged = annual.dropna(subset=["value"]).merge(
        dataset, on=["territory_code", "year"], suffixes=("", "_dataset")
    )
    if outcome.mode == "continue":
        years = sorted(year for year in merged["year"].unique() if year < last_year)
        years = years[-RECONCILE_YEARS:]
    else:
        years = sorted(merged["year"].unique())
    merged = merged[merged["year"].isin(years)]
    if merged.empty:
        return
    deviations = [
        link.deviation(source, value)
        for source, value in zip(merged["value"], merged["value_dataset"], strict=True)
    ]
    within = [
        link.within(source, value)
        for source, value in zip(merged["value"], merged["value_dataset"], strict=True)
    ]
    merged = merged.assign(deviation=deviations)
    worst = merged.sort_values("deviation", ascending=False).iloc[0]
    outcome.compared = [int(year) for year in years]
    outcome.pairs = len(merged)
    outcome.within_share = sum(within) / len(within)
    outcome.median_deviation = float(pd.Series(deviations).median())
    outcome.max_deviation = float(max(deviations))
    outcome.worst_territory = str(worst["territory_code"])
    outcome.worst_year = int(worst["year"])


def _same_statistic(link: Link) -> bool:
    """Источник публикует ту же величину, что набор, или её итог на жителя."""
    return not link.is_computed or link.method == "per_capita"


def _revises(link: Link, item: Any, known: pd.Series) -> bool:
    """
    Значение источника уточняет значение набора: расходится с ним больше допуска связи.

    Меньшее расхождение — запись числа с другой точностью, а не новое значение.
    """
    key = (item.territory_code, int(item.year))
    if key not in known.index:
        return True
    if item.value is None or pd.isna(item.value):
        return False
    return not link.within(float(item.value), float(known.loc[key]))


def _vintage(
    series_key: str, info: parsed.ReleaseInfo, item: Any, *, preliminary: bool
) -> dict[str, Any]:
    flags = ValueFlag(0)
    if item.preliminary or preliminary:
        flags |= ValueFlag.PRELIMINARY
    if item.computed:
        flags |= ValueFlag.COMPUTED
    if item.frozen:
        flags |= ValueFlag.FROZEN_DENOMINATOR
    hidden = bool(item.hidden)
    value = None if hidden or item.value is None or pd.isna(item.value) else float(item.value)
    return {
        "series_key": series_key,
        "territory_code": item.territory_code,
        "year": int(item.year),
        "edition_code": info.edition_code,
        "edition_year": info.reference_year,
        "value": value,
        "quality": int(ValueQuality.HIDDEN if hidden else ValueQuality.OBSERVED),
        "flags": int(flags),
    }


# ---------------------------------------------------------------------------------------
# Данные набора
# ---------------------------------------------------------------------------------------


def _dataset_values(connection: duckdb.DuckDBPyConnection, series_key: str) -> pd.DataFrame:
    """Значения ряда в наборе: из версий побеждает более поздний сборник."""
    return connection.execute(
        """
        SELECT territory_code, year, value
        FROM (
            SELECT v.territory_code, v.year, v.value,
                   row_number() OVER (
                       PARTITION BY v.territory_code, v.year
                       ORDER BY e.edition_year DESC, e.edition_code ASC
                   ) AS priority
            FROM fact_vintage AS v
            JOIN dim_edition  AS e ON e.edition_code = v.edition_code
            WHERE v.series_key = ? AND v.value IS NOT NULL AND e.source_code IS NULL
        )
        WHERE priority = 1
        """,
        [series_key],
    ).df()


def _denominators(connection: duckdb.DuckDBPyConnection, link: Link) -> pd.DataFrame:
    """Численность, на которую набор делит итог: итог ряда ``total_series`` на душевое значение."""
    totals = _dataset_values(connection, link.total_series).rename(columns={"value": "total"})
    per_person = _dataset_values(connection, link.series).rename(columns={"value": "per_person"})
    merged = totals.merge(per_person, on=["territory_code", "year"])
    merged = merged[merged["per_person"] != 0]
    merged["population"] = merged["total"] / merged["per_person"]
    return merged[["territory_code", "year", "population"]]


# ---------------------------------------------------------------------------------------
# Издания и витрина связей
# ---------------------------------------------------------------------------------------


def _register_editions(
    connection: duckdb.DuckDBPyConnection, releases: list[parsed.ReleaseInfo]
) -> None:
    rows = []
    for info in releases:
        module = SOURCES[info.source]
        rows.append(
            {
                "edition_code": info.edition_code,
                "source_name": f"{module.title_ru}, {info.code}",
                "publication_ru": module.title_ru,
                "publication_en": module.title_en,
                "edition_year": info.reference_year,
                "observation_count": 0,
                "first_year": None,
                "last_year": None,
                "edition_label": info.code,
                "released_on": date.fromisoformat(info.published_on),
                "source_code": info.source,
            }
        )
    frame = pd.DataFrame(rows)
    connection.register("tmp_release_editions", frame)
    connection.execute(
        """
        INSERT INTO dim_edition (
            edition_code, source_name, publication_ru, publication_en, edition_year,
            observation_count, first_year, last_year, edition_label, released_on, source_code
        )
        SELECT * FROM tmp_release_editions
        """
    )
    connection.unregister("tmp_release_editions")


def refresh_edition_counters(connection: duckdb.DuckDBPyConnection) -> None:
    """Число версий значений и годы у выпусков источников."""
    connection.execute(
        """
        UPDATE dim_edition AS e
        SET observation_count = c.observation_count, first_year = c.first_year,
            last_year = c.last_year
        FROM (
            SELECT edition_code, count(*) AS observation_count,
                   min(year) AS first_year, max(year) AS last_year
            FROM fact_vintage
            GROUP BY 1
        ) AS c
        WHERE c.edition_code = e.edition_code AND e.source_code IS NOT NULL
        """
    )


def _write_links(connection: duckdb.DuckDBPyConnection, outcomes: list[LinkOutcome]) -> None:
    rows = [
        {
            "series_key": outcome.link.series,
            "source_code": outcome.link.source,
            "method": outcome.link.method,
            "mode": outcome.mode,
            "link": outcome.kind,
            "dataset_last_year": outcome.dataset_last_year,
            "junction_year": outcome.junction_year,
            "last_year": outcome.last_year,
            "release_code": outcome.release_code,
            "compared_from": min(outcome.compared) if outcome.compared else None,
            "compared_to": max(outcome.compared) if outcome.compared else None,
            "pairs": outcome.pairs,
            "within_share": outcome.within_share,
            "median_deviation": outcome.median_deviation,
            "max_deviation": outcome.max_deviation,
            "deviation_unit": "points" if outcome.link.tolerance_points is not None else "share",
            "tolerance": (
                outcome.link.tolerance_points
                if outcome.link.tolerance_points is not None
                else outcome.link.tolerance_relative
            ),
            "worst_territory": outcome.worst_territory or None,
            "worst_year": outcome.worst_year,
            "revised_count": outcome.revised_count,
            "loaded_count": len(outcome.rows),
        }
        for outcome in outcomes
    ]
    if not rows:
        return
    frame = pd.DataFrame(rows)
    connection.register("tmp_source_links", frame)
    columns = ", ".join(frame.columns)
    connection.execute(f"INSERT INTO mart_source_link ({columns}) SELECT * FROM tmp_source_links")
    connection.unregister("tmp_source_links")


def _write_notes(
    connection: duckdb.DuckDBPyConnection,
    outcomes: list[LinkOutcome],
    releases: list[tuple[parsed.ReleaseInfo, Path]],
) -> None:
    """Сноски последнего выпуска источника к показателю каждой связи."""
    latest = {info.source: (info, path) for info, path in releases}
    rows: list[dict[str, Any]] = []
    for outcome in outcomes:
        info, path = latest[outcome.link.source]
        rows.extend(
            {
                "series_key": outcome.link.series,
                "source_code": info.source,
                "edition_code": info.edition_code,
                "position": position,
                "note_text": text,
            }
            for position, (measure, text) in enumerate(parsed.read_notes(path))
            if measure == outcome.link.measure
        )
    if not rows:
        return
    frame = pd.DataFrame(rows)
    connection.register("tmp_source_notes", frame)
    columns = ", ".join(frame.columns)
    connection.execute(f"INSERT INTO mart_source_note ({columns}) SELECT * FROM tmp_source_notes")
    connection.unregister("tmp_source_notes")
