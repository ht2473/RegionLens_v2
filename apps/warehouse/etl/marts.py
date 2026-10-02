"""
Витрины склада: покрытие рядов, статистики распределения и ранги.

Строятся только по субъектам — без составных территорий, округов и страны.
"""

from __future__ import annotations

import logging

import duckdb
from django.conf import settings

from apps.warehouse.duckdb_client import require_row

logger = logging.getLogger(__name__)

# Условие отбора субъектов: используется во всех витринах.
REGION_FILTER = "territory_level = 'region' AND NOT is_aggregate"

# Доля субъектов, при которой год пригоден для анализа (самый длинный отрезок ряда).
YEAR_COVERAGE_THRESHOLD = 0.5


def build_coverage(connection: duckdb.DuckDBPyConnection) -> int:
    """Рассчитать витрину покрытия рядов; по ней ряд предлагается инструментам анализа."""
    total_regions = require_row(
        connection.execute(
            "SELECT count(*) FROM dim_territory WHERE level = 'region' AND NOT is_aggregate"
        ).fetchone(),
        "total_regions",
    )[0]

    connection.execute(
        f"""
        INSERT INTO mart_series_coverage
        WITH region_facts AS (
            SELECT * FROM fact_observation WHERE {REGION_FILTER}
        ),
        base AS (
            SELECT
                series_key,
                count(*)                                        AS observation_count,
                count(value)                                    AS value_count,
                count(*) FILTER (WHERE quality = 1)             AS no_data_count,
                count(*) FILTER (WHERE quality = 2)             AS hidden_count,
                count(DISTINCT territory_code) FILTER (WHERE value IS NOT NULL) AS region_count,
                count(DISTINCT year)           FILTER (WHERE value IS NOT NULL) AS year_count,
                min(year) FILTER (WHERE value IS NOT NULL)      AS first_year,
                max(year) FILTER (WHERE value IS NOT NULL)      AS last_year
            FROM region_facts
            GROUP BY 1
        ),
        year_coverage AS (
            SELECT series_key, year, count(value) AS filled
            FROM region_facts
            GROUP BY 1, 2
        ),
        year_peak AS (
            SELECT series_key, max(filled) AS peak
            FROM year_coverage
            GROUP BY 1
        ),
        good_years AS (
            SELECT
                yc.series_key,
                yc.year,
                -- Разность года и его порядкового номера постоянна внутри непрерывного
                -- отрезка: это позволяет сгруппировать годы, идущие подряд.
                yc.year - row_number() OVER (
                    PARTITION BY yc.series_key ORDER BY yc.year
                ) AS run_group
            FROM year_coverage AS yc
            JOIN year_peak     AS yp USING (series_key)
            WHERE yc.filled > 0 AND yc.filled >= {YEAR_COVERAGE_THRESHOLD} * yp.peak
        ),
        runs AS (
            SELECT series_key, run_group, count(*) AS span
            FROM good_years
            GROUP BY 1, 2
        ),
        longest AS (
            SELECT series_key, max(span) AS longest_span
            FROM runs
            GROUP BY 1
        )
        SELECT
            b.series_key,
            b.observation_count,
            b.value_count,
            b.no_data_count,
            b.hidden_count,
            CAST(b.region_count AS SMALLINT),
            CAST(b.year_count AS SMALLINT),
            CAST(b.first_year AS SMALLINT),
            CAST(b.last_year AS SMALLINT),
            b.region_count::DOUBLE / {total_regions}                     AS region_coverage,
            CASE
                WHEN b.first_year IS NULL THEN 0
                ELSE b.value_count::DOUBLE
                     / ({total_regions} * (b.last_year - b.first_year + 1))
            END                                                          AS completeness,
            CAST(coalesce(l.longest_span, 0) AS SMALLINT)                AS longest_gap_free_span,
            (
                b.region_count::DOUBLE / {total_regions} >= {settings.SERIES_MIN_REGION_COVERAGE}
                -- Ряды источников начинаются с первого года источника: им хватает меньшего.
                AND b.year_count >= CASE WHEN s.source_code IS NULL
                                         THEN {settings.SERIES_MIN_YEARS}
                                         ELSE {settings.SERIES_MIN_YEARS_SOURCE} END
            )                                                            AS is_analysis_ready
        FROM base    AS b
        JOIN dim_series AS s USING (series_key)
        LEFT JOIN longest AS l USING (series_key)
        -- Упорядочивание задаёт физическое размещение строк: DuckDB пишет группы строк
        -- в порядке вставки, и зонные карты групп начинают отсекать заведомо ненужные.
        ORDER BY b.series_key
        """
    )

    count = require_row(
        connection.execute("SELECT count(*) FROM mart_series_coverage").fetchone(), "coverage"
    )[0]
    ready = require_row(
        connection.execute(
            "SELECT count(*) FROM mart_series_coverage WHERE is_analysis_ready"
        ).fetchone(),
        "analysis_ready",
    )[0]
    logger.info("Витрина покрытия: рядов %d, пригодных для анализа %d", count, ready)
    return count


def build_stats(connection: duckdb.DuckDBPyConnection) -> int:
    """
    Рассчитать статистики распределения значений по субъектам за каждый год.

    Индексы Джини, Тейла и Аткинсона считает аналитический слой: им нужен весь вектор.
    """
    connection.execute(
        f"""
        INSERT INTO mart_series_stats
        WITH region_values AS (
            SELECT series_key, year, value
            FROM fact_observation
            WHERE {REGION_FILTER} AND value IS NOT NULL
        ),
        country_values AS (
            SELECT series_key, year, any_value(value) AS country_value
            FROM fact_observation
            WHERE territory_level = 'country' AND value IS NOT NULL
            GROUP BY 1, 2
        ),
        aggregated AS (
            SELECT
                series_key,
                year,
                CAST(count(*) AS SMALLINT) AS observations,
                avg(value)                 AS mean_value,
                median(value)              AS median_value,
                min(value)                 AS min_value,
                max(value)                 AS max_value,
                quantile_cont(value, 0.10) AS p10_value,
                quantile_cont(value, 0.25) AS p25_value,
                quantile_cont(value, 0.75) AS p75_value,
                quantile_cont(value, 0.90) AS p90_value,
                stddev_samp(value)         AS stddev_value,
                sum(value)                 AS sum_value
            FROM region_values
            GROUP BY 1, 2
            HAVING count(*) >= 2
        )
        SELECT
            a.series_key,
            a.year,
            a.observations,
            a.mean_value,
            a.median_value,
            a.min_value,
            a.max_value,
            a.p10_value,
            a.p25_value,
            a.p75_value,
            a.p90_value,
            a.stddev_value,
            a.sum_value,
            -- Коэффициент вариации определён только для положительного среднего:
            -- для показателей, принимающих значения разных знаков, он лишён смысла.
            CASE
                WHEN a.mean_value IS NULL OR a.mean_value <= 0 THEN NULL
                ELSE a.stddev_value / a.mean_value
            END AS coef_variation,
            CASE
                WHEN a.p10_value IS NULL OR a.p10_value <= 0 THEN NULL
                ELSE a.p90_value / a.p10_value
            END AS decile_ratio,
            CASE
                WHEN a.min_value IS NULL OR a.min_value <= 0 THEN NULL
                ELSE a.max_value / a.min_value
            END AS range_ratio,
            c.country_value
        FROM aggregated       AS a
        LEFT JOIN country_values AS c USING (series_key, year)
        -- Порядок совпадает с порядком обращения: ряд целиком, годы подряд.
        ORDER BY a.series_key, a.year
        """
    )

    count = require_row(
        connection.execute("SELECT count(*) FROM mart_series_stats").fetchone(), "stats"
    )[0]
    logger.info("Витрина статистик: строк %d", count)
    return count


def build_ranks(connection: duckdb.DuckDBPyConnection) -> int:
    """Рассчитать ранги субъектов по ряду и году в обоих направлениях."""
    connection.execute(
        f"""
        INSERT INTO mart_rank
        WITH region_values AS (
            SELECT series_key, year, territory_code, value
            FROM fact_observation
            WHERE {REGION_FILTER} AND value IS NOT NULL
        ),
        totals AS (
            SELECT series_key, year, sum(value) AS total_value
            FROM region_values
            -- Доля в сумме имеет смысл только для неотрицательных величин.
            WHERE value >= 0
            GROUP BY 1, 2
        ),
        country_values AS (
            SELECT series_key, year, any_value(value) AS country_value
            FROM fact_observation
            WHERE territory_level = 'country' AND value IS NOT NULL
            GROUP BY 1, 2
        )
        SELECT
            r.series_key,
            r.year,
            r.territory_code,
            r.value,
            CAST(rank() OVER (PARTITION BY r.series_key, r.year ORDER BY r.value DESC)
                 AS SMALLINT) AS rank_desc,
            CAST(rank() OVER (PARTITION BY r.series_key, r.year ORDER BY r.value ASC)
                 AS SMALLINT) AS rank_asc,
            percent_rank() OVER (PARTITION BY r.series_key, r.year ORDER BY r.value ASC)
                           AS percentile,
            CAST(ntile(5) OVER (PARTITION BY r.series_key, r.year ORDER BY r.value DESC)
                 AS TINYINT) AS quintile,
            CASE
                WHEN t.total_value IS NULL OR t.total_value = 0 OR r.value < 0 THEN NULL
                ELSE r.value / t.total_value
            END AS share_of_total,
            CASE
                WHEN c.country_value IS NULL OR c.country_value = 0 THEN NULL
                ELSE r.value / c.country_value
            END AS ratio_to_country
        FROM region_values     AS r
        LEFT JOIN totals         AS t USING (series_key, year)
        LEFT JOIN country_values AS c USING (series_key, year)
        -- Рейтинг запрашивается как «ряд + год целиком», поэтому строки одного года
        -- одного ряда должны лежать рядом: это самая частая выборка в интерфейсе.
        ORDER BY r.series_key, r.year, r.territory_code
        """
    )

    count = require_row(connection.execute("SELECT count(*) FROM mart_rank").fetchone(), "ranks")[0]
    logger.info("Витрина рангов: строк %d", count)
    return count


# Индексов склад не содержит: планировщик DuckDB их не выбирает, отбор ускоряет
# упорядоченная вставка (ORDER BY здесь и в facts.py).
