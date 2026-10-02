"""
Загрузка фактов: версии наблюдений по выпускам, каноническая таблица и пересмотры.

Заглушки источника становятся ``NULL`` с признаком качества; из версий побеждает самое
позднее непропущенное значение (правило источника); значение по России приводится
к единице территорий, иначе оно в тысячу раз меньше суммы регионов.
"""

from __future__ import annotations

import logging

import duckdb

from apps.catalog.constants import SENTINEL_HIDDEN, SENTINEL_NO_DATA, ValueQuality
from apps.warehouse.duckdb_client import require_row

logger = logging.getLogger(__name__)


def load_vintages(connection: duckdb.DuckDBPyConnection) -> int:
    """
    Заполнить таблицу версий наблюдений — по строке на выпуск издания.

    Повторы внутри выпуска сводятся с приоритетом непропущенного значения.
    """
    connection.execute(
        f"""
        INSERT INTO fact_vintage
        SELECT
            m.series_key,
            t.territory_code,
            CAST(s.year AS SMALLINT)                    AS year,
            e.edition_code,
            CAST(e.edition_year AS SMALLINT)            AS edition_year,
            -- Значение сохраняется только для действительных наблюдений и приводится
            -- к единице измерения территорий: для страны в целом источник может
            -- объявлять укрупнённую единицу, и без приведения сумма регионов
            -- расходится с итогом по России в тысячу раз.
            max(CASE
                    WHEN s.indicator_value IN ({SENTINEL_NO_DATA}, {SENTINEL_HIDDEN}) THEN NULL
                    WHEN t.level = 'country'
                        THEN s.indicator_value * coalesce(us.country_scale, 1)
                    ELSE s.indicator_value
                END)                                    AS value,
            -- Признак качества: наименьший код соответствует наиболее полному сведению.
            CAST(min(CASE
                    WHEN s.indicator_value = {SENTINEL_NO_DATA} THEN {ValueQuality.NO_DATA}
                    WHEN s.indicator_value = {SENTINEL_HIDDEN}  THEN {ValueQuality.HIDDEN}
                    ELSE {ValueQuality.OBSERVED}
                END) AS TINYINT)                        AS quality,
            CAST(0 AS TINYINT)                          AS flags
        FROM src           AS s
        JOIN dim_territory AS t ON t.source_name = s.object_name
        JOIN dim_edition   AS e ON e.source_name = s.source
        JOIN map_series    AS m
              ON m.indicator_code = s.indicator_code
             AND m.subsection_raw = coalesce(s.subsection, '')
        -- Коэффициент берётся по единице конкретного наблюдения, а не по единице ряда:
        -- выпуски одного издания объявляют единицу для России в целом по-разному.
        LEFT JOIN map_unit_scale AS us ON us.source_unit = s.indicator_unit
        GROUP BY 1, 2, 3, 4, 5
        -- Упорядочивание задаёт физическое размещение строк в группах: страница
        -- пересмотров запрашивает все версии одного наблюдения, и они должны лежать рядом.
        ORDER BY 1, 2, 3, 5
        """
    )

    count = require_row(
        connection.execute("SELECT count(*) FROM fact_vintage").fetchone(), "count"
    )[0]
    logger.info("Загружено версий наблюдений: %d", count)
    return count


def rank_editions(connection: duckdb.DuckDBPyConnection) -> int:
    """
    Упорядочить выпуски по времени выхода: больший номер — более поздний.

    У сборников набора дата неизвестна — конец года издания; из сборников одного года
    первым идёт издание с меньшим кодом, как было до появления внешних источников.
    """
    connection.execute(
        """
        UPDATE dim_edition AS e
        SET edition_rank = r.edition_rank
        FROM (
            SELECT
                edition_code,
                CAST(row_number() OVER (
                    ORDER BY edition_year,
                             coalesce(released_on, make_date(edition_year, 12, 31)),
                             edition_code DESC
                ) AS SMALLINT) AS edition_rank
            FROM dim_edition
        ) AS r
        WHERE r.edition_code = e.edition_code
        """
    )
    return require_row(connection.execute("SELECT count(*) FROM dim_edition").fetchone(), "count")[
        0
    ]


def build_observations(connection: duckdb.DuckDBPyConnection) -> int:
    """Построить каноническую таблицу наблюдений с числом различающихся версий."""
    connection.execute(
        """
        INSERT INTO fact_observation
        WITH ranked AS (
            SELECT
                v.series_key,
                v.territory_code,
                v.year,
                v.edition_code,
                v.edition_year,
                v.value,
                v.quality,
                v.flags,
                row_number() OVER (
                    PARTITION BY v.series_key, v.territory_code, v.year
                    -- Приоритет: сначала непропущенные значения, затем более поздние выпуски.
                    ORDER BY (v.value IS NULL) ASC, e.edition_rank DESC
                ) AS priority,
                -- Число различных значений среди версий: показывает факт пересмотра.
                count(DISTINCT v.value) OVER (
                    PARTITION BY v.series_key, v.territory_code, v.year
                ) AS distinct_values
            FROM fact_vintage AS v
            JOIN dim_edition  AS e ON e.edition_code = v.edition_code
        )
        SELECT
            r.series_key,
            ds.indicator_code,
            ds.section_code,
            r.territory_code,
            dt.level                                        AS territory_level,
            dt.district_code,
            dt.is_aggregate,
            r.year,
            r.value,
            r.quality,
            r.edition_code,
            r.edition_year,
            CAST(greatest(r.distinct_values - 1, 0) AS TINYINT) AS revision_count,
            r.flags
        FROM ranked        AS r
        JOIN dim_series    AS ds ON ds.series_key = r.series_key
        JOIN dim_territory AS dt ON dt.territory_code = r.territory_code
        WHERE r.priority = 1
        -- Ключ ряда — ведущим столбцом: почти каждый запрос отбирает строки одного ряда,
        -- и от порядка размещения зависит, сколько групп строк придётся прочитать.
        ORDER BY r.series_key, r.territory_code, r.year
        """
    )

    count = require_row(
        connection.execute("SELECT count(*) FROM fact_observation").fetchone(), "count"
    )[0]
    logger.info("Канонических наблюдений: %d", count)
    return count


def build_revisions(connection: duckdb.DuckDBPyConnection) -> int:
    """Построить витрину пересмотров: только различающиеся непропущенные значения."""
    connection.execute(
        """
        INSERT INTO mart_revision
        WITH versions AS (
            SELECT v.series_key, v.territory_code, v.year, e.edition_rank, v.value
            FROM fact_vintage AS v
            JOIN dim_edition  AS e ON e.edition_code = v.edition_code
            WHERE v.value IS NOT NULL
        ),
        aggregated AS (
            SELECT
                series_key,
                territory_code,
                year,
                count(*)                  AS edition_count,
                count(DISTINCT value)     AS distinct_values,
                -- arg_min и arg_max возвращают значение, соответствующее крайним выпускам.
                arg_min(value, edition_rank) AS first_value,
                arg_max(value, edition_rank) AS last_value
            FROM versions
            GROUP BY 1, 2, 3
        )
        SELECT
            series_key,
            territory_code,
            year,
            CAST(edition_count AS TINYINT) AS edition_count,
            first_value,
            last_value,
            last_value - first_value       AS abs_change,
            CASE
                WHEN first_value IS NULL OR first_value = 0 THEN NULL
                ELSE (last_value - first_value) / abs(first_value)
            END                            AS rel_change
        FROM aggregated
        WHERE distinct_values > 1
        ORDER BY series_key, territory_code, year
        """
    )

    count = require_row(
        connection.execute("SELECT count(*) FROM mart_revision").fetchone(), "count"
    )[0]
    logger.info("Пересмотренных наблюдений: %d", count)
    return count


def observation_quality_summary(connection: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """Собрать сводку по качеству загруженных наблюдений для журнала запуска."""
    row = require_row(
        connection.execute(
            f"""
        SELECT
            count(*)                                                      AS total,
            count(*) FILTER (WHERE quality = {ValueQuality.OBSERVED})     AS observed,
            count(*) FILTER (WHERE quality = {ValueQuality.NO_DATA})      AS no_data,
            count(*) FILTER (WHERE quality = {ValueQuality.HIDDEN})       AS hidden,
            count(*) FILTER (WHERE value < 0)                             AS negative
        FROM fact_observation
        """
        ).fetchone(),
        "observation_quality_summary",
    )

    return {
        "total": row[0],
        "observed": row[1],
        "no_data": row[2],
        "hidden": row[3],
        "negative_values": row[4],
    }
