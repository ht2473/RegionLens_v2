"""Запросы по территориям: сведения, полнота данных, положение по рядам, сводка округа."""

from __future__ import annotations

from typing import Any

from ..duckdb_client import fetch_dicts, fetch_one, placeholders
from ..routing import by_key, by_keys
from .common import COUNTRY_CODE, by_generation

# Наименьшее число ранжированных территорий, при котором место сопоставимо с другими.
MIN_RANKED_TERRITORIES = 60

# На сколько лет назад смотрит вывод об изменении показателя.
TREND_YEARS = 5


@by_generation("territory_profile")
def territory_profile(territory_code: str) -> dict[str, Any] | None:
    """Получить сведения о территории из измерения склада."""
    rows = fetch_dicts(
        "SELECT * FROM dim_territory WHERE territory_code = ?",
        [territory_code],
    )
    return rows[0] if rows else None


@by_generation("territory_coverage")
def territory_coverage(territory_code: str) -> dict[str, Any]:
    """Оценить полноту данных по территории: долю пригодных рядов, по которым есть значения."""
    row = fetch_one(
        """
        SELECT
            count(DISTINCT f.series_key)                                     AS series_total,
            count(DISTINCT f.series_key) FILTER (WHERE f.value IS NOT NULL)  AS series_with_values,
            min(f.year) FILTER (WHERE f.value IS NOT NULL)                   AS first_year,
            max(f.year) FILTER (WHERE f.value IS NOT NULL)                   AS last_year
        FROM fact_observation AS f
        JOIN mart_series_coverage AS c ON c.series_key = f.series_key
        WHERE f.territory_code = ? AND c.is_analysis_ready
        """,
        [territory_code],
    )

    total, with_values, first_year, last_year = row or (0, 0, None, None)
    return {
        "series_total": total,
        "series_with_values": with_values,
        "share": (with_values / total) if total else 0.0,
        "first_year": first_year,
        "last_year": last_year,
    }


@by_keys()
@by_generation("territory_positions")
def territory_positions(territory_code: str, series_keys: list[str]) -> list[dict[str, Any]]:
    """
    Получить положение территории по перечисленным рядам одним запросом.

    По каждому ряду — последний год с рангом, число ранжированных, значения страны
    и значения на ``TREND_YEARS`` раньше; ряды с малым числом ранжированных отбрасываются.
    """
    if not series_keys:
        return []

    return fetch_dicts(
        f"""
        WITH ranked AS (
            SELECT
                r.series_key,
                r.territory_code,
                r.year,
                r.value,
                r.rank_desc,
                r.rank_asc,
                r.percentile,
                r.ratio_to_country,
                row_number() OVER (PARTITION BY r.series_key ORDER BY r.year DESC) AS recency
            FROM mart_rank AS r
            WHERE r.territory_code = ? AND r.series_key IN ({placeholders(len(series_keys))})
        ),
        latest AS (
            SELECT * FROM ranked WHERE recency = 1
        ),
        sizes AS (
            SELECT r.series_key, r.year, count(*) AS territories
            FROM mart_rank AS r
            JOIN latest    AS l ON l.series_key = r.series_key AND l.year = r.year
            GROUP BY r.series_key, r.year
        )
        SELECT
            l.series_key,
            l.year,
            l.value,
            l.rank_desc,
            l.rank_asc,
            l.percentile,
            l.ratio_to_country,
            s.territories,
            country.value AS country_value,
            past.year     AS past_year,
            past.value    AS past_value,
            country_past.value AS country_past_value,
            coalesce(own.flags, 0) AS flags
        FROM latest AS l
        JOIN sizes  AS s ON s.series_key = l.series_key AND s.year = l.year
        LEFT JOIN fact_observation AS own
               ON own.series_key = l.series_key
              AND own.year = l.year
              AND own.territory_code = l.territory_code
        LEFT JOIN fact_observation AS country
               ON country.series_key = l.series_key
              AND country.year = l.year
              AND country.territory_code = ?
        LEFT JOIN fact_observation AS past
               ON past.series_key = l.series_key
              AND past.year = l.year - ?
              AND past.territory_code = l.territory_code
        LEFT JOIN fact_observation AS country_past
               ON country_past.series_key = l.series_key
              AND country_past.year = l.year - ?
              AND country_past.territory_code = ?
        WHERE s.territories >= ?
        """,  # noqa: S608 - в текст запроса подставляется только число знаков подстановки
        [
            territory_code,
            *series_keys,
            COUNTRY_CODE,
            TREND_YEARS,
            TREND_YEARS,
            COUNTRY_CODE,
            MIN_RANKED_TERRITORIES,
        ],
    )


@by_key()
@by_generation("territory_values_for_series")
def territory_values_for_series(
    series_key: str,
    territory_codes: list[str],
    year: int,
) -> dict[str, dict[str, Any]]:
    """Получить значения ряда за год для перечисленных территорий."""
    if not territory_codes:
        return {}

    rows = fetch_dicts(
        f"""
        SELECT f.territory_code, f.value, f.quality, r.rank_desc, r.percentile
        FROM fact_observation AS f
        LEFT JOIN mart_rank   AS r
               ON r.series_key = f.series_key
              AND r.territory_code = f.territory_code
              AND r.year = f.year
        WHERE f.series_key = ? AND f.year = ?
          AND f.territory_code IN ({placeholders(len(territory_codes))})
        """,  # noqa: S608 - в текст запроса подставляется только число знаков подстановки
        [series_key, year, *territory_codes],
    )
    return {row["territory_code"]: row for row in rows}


@by_key()
@by_generation("district_summary")
def district_summary(district_code: str, series_key: str, year: int) -> dict[str, Any] | None:
    """Получить сводку по субъектам одного федерального округа."""
    row = fetch_one(
        """
        SELECT count(*), avg(f.value), median(f.value), min(f.value), max(f.value)
        FROM fact_observation AS f
        WHERE f.series_key = ? AND f.year = ? AND f.district_code = ?
          AND f.territory_level = 'region' AND NOT f.is_aggregate AND f.value IS NOT NULL
        """,
        [series_key, year, district_code],
    )
    if not row or not row[0]:
        return None
    return {
        "count": row[0],
        "mean_value": row[1],
        "median_value": row[2],
        "min_value": row[3],
        "max_value": row[4],
    }
