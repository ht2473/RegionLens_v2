"""Сводные характеристики набора: объём, покрытие, наполненность по годам, пересмотры."""

from __future__ import annotations

from typing import Any

from django.core.cache import cache
from django.utils.translation import gettext_lazy as _

from ..duckdb_client import fetch_dicts, fetch_row, fetch_scalar, warehouse_generation
from .common import SUMMARY_CACHE_TTL, by_generation

# Подписи полос покрытия: результат запроса кэшируется на оба языка сразу.
COVERAGE_BANDS = {
    "full": _("Полное (100 %)"),
    "high": _("Высокое (80–99 %)"),
    "medium": _("Среднее (50–79 %)"),
    "low": _("Низкое (менее 50 %)"),
    "none": _("Нет данных по субъектам"),
}


def warehouse_summary() -> dict[str, Any]:
    """Получить сводку о содержимом склада; кэшируется до пересборки."""
    key = f"warehouse:summary:{warehouse_generation()}"
    cached = cache.get(key)
    if cached is not None:
        return cached

    row = fetch_row(
        """
        SELECT
            (SELECT count(*) FROM fact_observation)                          AS observations,
            (SELECT count(*) FROM fact_observation WHERE value IS NOT NULL)  AS values_present,
            (SELECT count(*) FROM dim_series)                                AS series,
            (SELECT count(*) FROM mart_series_coverage WHERE is_analysis_ready) AS series_ready,
            (SELECT count(*) FROM dim_indicator)                             AS indicators,
            (SELECT count(*) FROM dim_section)                               AS sections,
            (SELECT count(*) FROM dim_edition)                               AS editions,
            (SELECT count(*) FROM dim_territory WHERE level = 'region'
                 AND NOT is_aggregate)                                       AS regions,
            (SELECT min(year) FROM fact_observation WHERE value IS NOT NULL) AS first_year,
            (SELECT max(year) FROM fact_observation WHERE value IS NOT NULL) AS last_year,
            (SELECT count(*) FROM mart_revision)                             AS revisions
        """
    )

    summary = {
        "observations": row[0],
        "values_present": row[1],
        "series": row[2],
        "series_ready": row[3],
        "indicators": row[4],
        "sections": row[5],
        "editions": row[6],
        "regions": row[7],
        "first_year": row[8],
        "last_year": row[9],
        "revisions": row[10],
    }
    cache.set(key, summary, SUMMARY_CACHE_TTL)
    return summary


def build_metadata() -> dict[str, str]:
    """Прочитать сведения о последней сборке склада."""
    rows = fetch_dicts("SELECT key, value FROM meta_build")
    return {row["key"]: row["value"] for row in rows}


@by_generation("observations_by_year")
def observations_by_year() -> list[dict[str, Any]]:
    """Получить распределение наблюдений по годам для графика наполненности набора."""
    return fetch_dicts(
        """
        SELECT year,
               count(*) FILTER (WHERE value IS NOT NULL) AS observed,
               count(*) FILTER (WHERE quality = 1)       AS no_data,
               count(*) FILTER (WHERE quality = 2)       AS hidden
        FROM fact_observation
        GROUP BY year
        ORDER BY year
        """
    )


@by_generation("coverage_distribution")
def coverage_distribution() -> list[dict[str, Any]]:
    """Получить распределение рядов по доле охваченных субъектов — кодами полос, без подписей."""
    rows = fetch_dicts(
        """
        SELECT
            CASE
                WHEN region_coverage >= 0.99 THEN 'full'
                WHEN region_coverage >= 0.8  THEN 'high'
                WHEN region_coverage >= 0.5  THEN 'medium'
                WHEN region_coverage > 0     THEN 'low'
                ELSE 'none'
            END AS bucket_code,
            count(*) AS series_count
        FROM mart_series_coverage
        GROUP BY 1
        ORDER BY series_count DESC
        """
    )
    for row in rows:
        row["bucket"] = str(COVERAGE_BANDS.get(row["bucket_code"], row["bucket_code"]))
    return rows


def total_observations() -> int:
    """Получить общее число наблюдений в складе."""
    return int(fetch_scalar("SELECT count(*) FROM fact_observation", default=0))
