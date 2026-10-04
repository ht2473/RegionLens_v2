"""Выборки помесячного слоя: ход по месяцам, последний период по субъектам, «Что сейчас»."""

from __future__ import annotations

from typing import Any

from ..duckdb_client import fetch_dicts, placeholders
from ..routing import by_key, by_keys
from .common import LEVEL_REGION, MIN_YEAR_COVERAGE, by_generation

# Столбцы месячного значения вместе с выпуском, из которого оно взято.
_ROW = """
    f.series_key, f.territory_code, CAST(f.year AS INTEGER) AS year,
    CAST(f.month AS INTEGER) AS month, f.kind, f.value, f.quality, f.flags,
    e.source_code, e.edition_label, e.released_on
"""


@by_generation("month_series")
def month_series() -> dict[str, list[str]]:
    """Ряды с помесячным слоем и виды их значений."""
    rows = fetch_dicts(
        "SELECT series_key, list(DISTINCT kind ORDER BY kind) AS kinds "
        "FROM fact_month GROUP BY 1 ORDER BY 1"
    )
    return {row["series_key"]: list(row["kinds"]) for row in rows}


@by_key()
@by_generation("month_timeline")
def month_timeline(series_key: str, territory_code: str, kind: str) -> list[dict[str, Any]]:
    """Значения ряда по месяцам для территории, от ранних к поздним."""
    return fetch_dicts(
        f"""
        SELECT {_ROW}
        FROM fact_month AS f
        JOIN dim_edition AS e ON e.edition_code = f.edition_code
        WHERE f.series_key = ? AND f.territory_code = ? AND f.kind = ?
        ORDER BY f.year, f.month
        """,  # noqa: S608 - столбцы из константы модуля
        [series_key, territory_code, kind],
    )


@by_key()
@by_generation("month_latest")
def month_latest(series_key: str, kind: str) -> dict[str, Any] | None:
    """
    Последний месяц, за который есть значения не меньше чем у 80 % субъектов, и сами значения.

    Порог — от наибольшего числа субъектов ряда за месяц, как у года по умолчанию.
    """
    counts = fetch_dicts(
        """
        SELECT CAST(f.year AS INTEGER) AS year, CAST(f.month AS INTEGER) AS month,
               count(f.value) AS filled
        FROM fact_month AS f
        JOIN dim_territory AS t ON t.territory_code = f.territory_code
        WHERE f.series_key = ? AND f.kind = ? AND t.level = ? AND NOT t.is_aggregate
        GROUP BY 1, 2
        ORDER BY 1, 2
        """,
        [series_key, kind, LEVEL_REGION],
    )
    if not counts:
        return None
    fullest = max(row["filled"] for row in counts)
    full = [row for row in counts if row["filled"] and row["filled"] >= MIN_YEAR_COVERAGE * fullest]
    if not full:
        return None
    last = full[-1]
    rows = fetch_dicts(
        f"""
        SELECT {_ROW}, t.name_ru, t.name_en
        FROM fact_month AS f
        JOIN dim_edition AS e ON e.edition_code = f.edition_code
        JOIN dim_territory AS t ON t.territory_code = f.territory_code
        WHERE f.series_key = ? AND f.kind = ? AND f.year = ? AND f.month = ?
          AND t.level = ? AND NOT t.is_aggregate
        ORDER BY f.territory_code
        """,  # noqa: S608 - столбцы из константы модуля
        [series_key, kind, last["year"], last["month"], LEVEL_REGION],
    )
    return {"year": last["year"], "month": last["month"], "rows": rows}


@by_keys()
@by_generation("month_points")
def month_points(territory_code: str, series_keys: list[str]) -> list[dict[str, Any]]:
    """Все месячные значения перечисленных рядов по территории — для блока «Что сейчас»."""
    if not series_keys:
        return []
    return fetch_dicts(
        f"""
        SELECT {_ROW}
        FROM fact_month AS f
        JOIN dim_edition AS e ON e.edition_code = f.edition_code
        WHERE f.territory_code = ? AND f.series_key IN ({placeholders(len(series_keys))})
        ORDER BY f.series_key, f.kind, f.year, f.month
        """,  # noqa: S608 - столбцы из константы, значения — параметрами
        [territory_code, *series_keys],
    )


@by_key()
@by_generation("month_observations")
def month_observations(
    series_key: str,
    *,
    territory_code: str | None = None,
    kind: str | None = None,
    first_year: int | None = None,
    last_year: int | None = None,
    limit: int = 500,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """Месячные значения ряда порциями вместе с их общим числом — для API."""
    conditions = ["f.series_key = ?"]
    params: list[Any] = [series_key]
    if territory_code:
        conditions.append("f.territory_code = ?")
        params.append(territory_code)
    else:
        conditions.append("t.level = ? AND NOT t.is_aggregate")
        params.append(LEVEL_REGION)
    if kind:
        conditions.append("f.kind = ?")
        params.append(kind)
    if first_year is not None:
        conditions.append("f.year >= ?")
        params.append(first_year)
    if last_year is not None:
        conditions.append("f.year <= ?")
        params.append(last_year)
    where = " AND ".join(conditions)
    source = (
        "FROM fact_month AS f "
        "JOIN dim_territory AS t ON t.territory_code = f.territory_code "
        "JOIN dim_edition AS e ON e.edition_code = f.edition_code "
        f"WHERE {where}"
    )
    total = fetch_dicts(f"SELECT count(*) AS total {source}", params)
    rows = fetch_dicts(
        f"""
        SELECT {_ROW}
        {source}
        ORDER BY f.territory_code, f.kind, f.year, f.month
        LIMIT ? OFFSET ?
        """,
        [*params, limit, offset],
    )
    return rows, int(total[0]["total"]) if total else 0
