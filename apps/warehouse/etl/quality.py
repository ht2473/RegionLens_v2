"""
Проверки качества данных после сборки склада, с записью замечаний в PostgreSQL.

Проверки: единицы для России в целом, сходимость суммы регионов с итогом, пересмотры
кратного масштаба, пропущенные годы внутри рядов, значения у рядов с прекращённой публикацией.
"""

from __future__ import annotations

import logging

import duckdb

from apps.catalog.status import all_statuses
from apps.warehouse.models import DataQualityCheck, EtlRun

logger = logging.getLogger(__name__)

# Допустимое расхождение суммы регионов со значением по стране: округления и мелкие
# методические различия.
AGGREGATE_TOLERANCE = 0.05

# Наибольшее число замечаний одного вида.
MAX_FINDINGS_PER_CHECK = 200

# Расхождение, начиная с которого замечание критическое: половина величины.
CRITICAL_DEVIATION = 0.5


def run_checks(connection: duckdb.DuckDBPyConnection, run: EtlRun) -> dict[str, int]:
    """Выполнить все проверки и сохранить найденные замечания."""
    findings: list[DataQualityCheck] = []
    findings.extend(_check_ambiguous_units(connection, run))
    findings.extend(_check_unit_scale_conflicts(connection, run))
    findings.extend(_check_aggregate_consistency(connection, run))
    findings.extend(_check_scale_anomalies(connection, run))
    findings.extend(_check_series_gaps(connection, run))
    findings.extend(_check_discontinued_series(connection, run))

    DataQualityCheck.objects.bulk_create(findings, batch_size=500)

    summary: dict[str, int] = {}
    for finding in findings:
        summary[finding.check_type] = summary.get(finding.check_type, 0) + 1

    logger.info("Проверки качества данных: %s", summary or "замечаний нет")
    return summary


def _check_ambiguous_units(
    connection: duckdb.DuckDBPyConnection, run: EtlRun
) -> list[DataQualityCheck]:
    """
    Найти ряды с составным объявлением единицы, соотношение в котором не определено.

    Значение по России у них остаётся в единице источника и с суммой регионов не сравнимо.
    """
    rows = connection.execute(
        """
        SELECT DISTINCT s.series_key, s.full_title_ru, us.source_unit
        FROM src        AS r
        JOIN map_series AS m
              ON m.indicator_code = r.indicator_code
             AND m.subsection_raw = coalesce(r.subsection, '')
        JOIN dim_series     AS s  ON s.series_key = m.series_key
        JOIN map_unit_scale AS us ON us.source_unit = r.indicator_unit
        WHERE us.country_scale_unknown
        ORDER BY s.series_key
        LIMIT ?
        """,
        [MAX_FINDINGS_PER_CHECK],
    ).fetchall()

    return [
        DataQualityCheck(
            run=run,
            check_type=DataQualityCheck.CheckType.UNIT_INCONSISTENCY,
            severity=DataQualityCheck.Severity.WARNING,
            series_key=series_key,
            message=(
                "Составное объявление единицы измерения: соотношение между единицей "
                "территорий и единицей для России в целом не определено"
            ),
            details={"kind": "compound_unit", "series_title": title, "unit": unit_source},
        )
        for series_key, title, unit_source in rows
    ]


def _check_unit_scale_conflicts(
    connection: duckdb.DuckDBPyConnection, run: EtlRun
) -> list[DataQualityCheck]:
    """
    Найти ряды, у которых выпуски объявляют единицу для России в целом по-разному.

    Значения уже приведены; замечание объясняет тысячекратную разницу в исходных файлах.
    """
    rows = connection.execute(
        """
        SELECT
            s.series_key,
            s.full_title_ru,
            count(DISTINCT us.country_scale) AS scale_variants,
            string_agg(DISTINCT us.source_unit, ' | ') AS units
        FROM src        AS r
        JOIN map_series AS m
              ON m.indicator_code = r.indicator_code
             AND m.subsection_raw = coalesce(r.subsection, '')
        JOIN dim_series     AS s  ON s.series_key = m.series_key
        JOIN map_unit_scale AS us ON us.source_unit = r.indicator_unit
        GROUP BY 1, 2
        HAVING count(DISTINCT us.country_scale) > 1
        ORDER BY s.series_key
        LIMIT ?
        """,
        [MAX_FINDINGS_PER_CHECK],
    ).fetchall()

    return [
        DataQualityCheck(
            run=run,
            check_type=DataQualityCheck.CheckType.UNIT_INCONSISTENCY,
            severity=DataQualityCheck.Severity.INFO,
            series_key=series_key,
            message=(
                "Выпуски издания объявляют единицу измерения для России в целом "
                "по-разному; значения приведены к единой единице при загрузке"
            ),
            details={
                "kind": "scale_variants",
                "series_title": title,
                "scale_variants": variants,
                "units": units,
            },
        )
        for series_key, title, variants, units in rows
    ]


def _check_aggregate_consistency(
    connection: duckdb.DuckDBPyConnection, run: EtlRun
) -> list[DataQualityCheck]:
    """
    Сравнить сумму значений по субъектам со значением по России.

    Аддитивность определяется по медианному отношению суммы к итогу за все годы: около
    единицы — годы с расхождением отмечаются, около степени десяти — разные единицы,
    иначе показатель не аддитивен (единица «рубли» этого не различает).
    """
    rows = connection.execute(
        f"""
        WITH candidate_series AS (
            SELECT s.series_key, s.full_title_ru
            FROM dim_series AS s
            JOIN dim_unit   AS u ON u.unit_code = s.unit_code
            JOIN mart_series_coverage AS c ON c.series_key = s.series_key
            WHERE u.kind IN ('absolute', 'currency', 'physical')
              AND c.region_coverage >= 0.99
        ),
        region_totals AS (
            SELECT f.series_key, f.year, sum(f.value) AS regions_sum, count(*) AS regions
            FROM fact_observation AS f
            JOIN candidate_series AS a USING (series_key)
            WHERE f.territory_level = 'region'
              AND NOT f.is_aggregate
              AND f.value IS NOT NULL
            GROUP BY 1, 2
        ),
        country_totals AS (
            SELECT series_key, year, any_value(value) AS country_value
            FROM fact_observation
            WHERE territory_level = 'country' AND value IS NOT NULL AND value <> 0
            GROUP BY 1, 2
        ),
        paired AS (
            SELECT r.series_key, r.year, r.regions_sum, r.regions, c.country_value,
                   r.regions_sum / c.country_value AS ratio
            FROM region_totals  AS r
            JOIN country_totals AS c USING (series_key, year)
            WHERE r.regions >= 84
        ),
        profile AS (
            SELECT series_key, median(ratio) AS median_ratio, count(*) AS years
            FROM paired
            GROUP BY 1
            HAVING count(*) >= 3
        )
        SELECT
            p.series_key,
            a.full_title_ru,
            p.year,
            p.regions_sum,
            p.country_value,
            p.ratio,
            pr.median_ratio,
            -- Классификация расхождения определяет и формулировку замечания, и его уровень.
            CASE
                WHEN pr.median_ratio BETWEEN 0.9 AND 1.1 THEN 'year_deviation'
                WHEN pr.median_ratio BETWEEN 900 AND 1100 THEN 'scale_mismatch'
                WHEN pr.median_ratio BETWEEN 0.0009 AND 0.0011 THEN 'scale_mismatch'
                ELSE 'not_additive'
            END AS verdict
        FROM paired          AS p
        JOIN profile         AS pr USING (series_key)
        JOIN candidate_series AS a USING (series_key)
        WHERE (
                pr.median_ratio BETWEEN 0.9 AND 1.1
                AND abs(p.ratio - 1) > {AGGREGATE_TOLERANCE}
              )
           OR pr.median_ratio BETWEEN 900 AND 1100
           OR pr.median_ratio BETWEEN 0.0009 AND 0.0011
        ORDER BY abs(p.ratio - 1) DESC
        LIMIT {MAX_FINDINGS_PER_CHECK}
        """
    ).fetchall()

    findings: list[DataQualityCheck] = []
    for series_key, title, year, regions_sum, country_value, ratio, median_ratio, verdict in rows:
        deviation = abs(ratio - 1)
        if verdict == "scale_mismatch":
            message = (
                "Значения по субъектам и по России в целом приведены в разных единицах "
                f"измерения: их отношение устойчиво равно {median_ratio:.0f}"
            )
            severity = DataQualityCheck.Severity.ERROR
        else:
            message = (
                f"Сумма по субъектам отличается от значения по России на {deviation * 100:.1f} %"
            )
            severity = (
                DataQualityCheck.Severity.ERROR
                if deviation > CRITICAL_DEVIATION
                else DataQualityCheck.Severity.WARNING
            )

        findings.append(
            DataQualityCheck(
                run=run,
                check_type=DataQualityCheck.CheckType.AGGREGATE_MISMATCH,
                severity=severity,
                series_key=series_key,
                territory_code="RU",
                year=year,
                message=message,
                details={
                    "kind": verdict,
                    "deviation": deviation,
                    "series_title": title,
                    "regions_sum": regions_sum,
                    "country_value": country_value,
                    "ratio": ratio,
                    "median_ratio": median_ratio,
                    "verdict": verdict,
                },
            )
        )
    return findings


def _check_scale_anomalies(
    connection: duckdb.DuckDBPyConnection, run: EtlRun
) -> list[DataQualityCheck]:
    """Найти пересмотры примерно в 10, 100 или 1000 раз — признак ошибки подписи единицы."""
    rows = connection.execute(
        """
        SELECT m.series_key, s.full_title_ru, m.territory_code, m.year,
               m.first_value, m.last_value,
               m.last_value / nullif(m.first_value, 0) AS factor
        FROM mart_revision m
        JOIN dim_series    s USING (series_key)
        WHERE m.first_value <> 0
          AND abs(m.last_value / m.first_value) > 9
        ORDER BY abs(m.last_value / m.first_value) DESC
        LIMIT ?
        """,
        [MAX_FINDINGS_PER_CHECK],
    ).fetchall()

    return [
        DataQualityCheck(
            run=run,
            check_type=DataQualityCheck.CheckType.SUDDEN_JUMP,
            severity=DataQualityCheck.Severity.ERROR,
            series_key=series_key,
            territory_code=territory_code,
            year=year,
            message=(
                f"Значение изменилось между выпусками в {abs(factor):.0f} раз — "
                "вероятна ошибка единицы измерения в источнике"
            ),
            details={
                "kind": "unit_jump",
                "series_title": title,
                "first_value": first_value,
                "last_value": last_value,
                "factor": factor,
            },
        )
        for series_key, title, territory_code, year, first_value, last_value, factor in rows
    ]


def _check_series_gaps(
    connection: duckdb.DuckDBPyConnection, run: EtlRun
) -> list[DataQualityCheck]:
    """Найти пропущенные годы внутри периода наблюдения ряда."""
    rows = connection.execute(
        """
        WITH observed_years AS (
            SELECT DISTINCT f.series_key, f.year
            FROM fact_observation AS f
            JOIN mart_series_coverage AS c USING (series_key)
            WHERE f.territory_level = 'region'
              AND NOT f.is_aggregate
              AND f.value IS NOT NULL
              AND c.is_analysis_ready
        ),
        bounds AS (
            SELECT series_key, min(year) AS first_year, max(year) AS last_year,
                   count(*) AS observed
            FROM observed_years
            GROUP BY 1
        )
        SELECT
            b.series_key,
            s.full_title_ru,
            b.first_year,
            b.last_year,
            (b.last_year - b.first_year + 1) - b.observed AS missing_years
        FROM bounds     AS b
        JOIN dim_series AS s USING (series_key)
        WHERE (b.last_year - b.first_year + 1) - b.observed > 0
        ORDER BY missing_years DESC
        LIMIT ?
        """,
        [MAX_FINDINGS_PER_CHECK],
    ).fetchall()

    return [
        DataQualityCheck(
            run=run,
            check_type=DataQualityCheck.CheckType.GAP,
            severity=DataQualityCheck.Severity.INFO,
            series_key=series_key,
            message=(f"В периоде {first_year}–{last_year} отсутствует наблюдений: {missing_years}"),
            details={
                "kind": "gap",
                "series_title": title,
                "first_year": first_year,
                "last_year": last_year,
                "missing_years": missing_years,
            },
        )
        for series_key, title, first_year, last_year, missing_years in rows
    ]


def _check_discontinued_series(
    connection: duckdb.DuckDBPyConnection, run: EtlRun
) -> list[DataQualityCheck]:
    """
    Найти ряды с пометкой «публикация прекращена», у которых есть значения с года прекращения.

    Пометка задана вручную в ``series_status.json``: новые значения значат, что источник
    возобновил публикацию или год указан неверно. Снимает пометку человек.
    """
    stopped = {
        item.key: item.since for item in all_statuses() if item.is_discontinued and item.since
    }
    if not stopped:
        return []
    rows = connection.execute(
        f"""
        SELECT o.series_key, s.full_title_ru, max(o.year) AS last_year
        FROM fact_observation AS o
        JOIN dim_series       AS s ON s.series_key = o.series_key
        WHERE o.series_key IN ({", ".join("?" * len(stopped))}) AND o.value IS NOT NULL
        GROUP BY 1, 2
        ORDER BY 1
        """,
        list(stopped),
    ).fetchall()
    return [
        DataQualityCheck(
            run=run,
            check_type=DataQualityCheck.CheckType.STATUS_OUTDATED,
            severity=DataQualityCheck.Severity.WARNING,
            series_key=series_key,
            year=last_year,
            message=(
                f"Ряд отмечен как прекращённый с {stopped[series_key]} года, но в складе "
                f"есть значения за {last_year} год"
            ),
            details={
                "kind": "discontinued_has_values",
                "series_title": title,
                "since": stopped[series_key],
                "year": last_year,
            },
        )
        for series_key, title, last_year in rows
        if last_year >= stopped[series_key]
    ]
