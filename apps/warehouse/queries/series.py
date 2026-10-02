"""Запросы к рядам: значения во времени и по территориям, версии по выпускам, статистики."""

from __future__ import annotations

from typing import Any

from ..duckdb_client import fetch_dicts, fetch_scalar, placeholders
from .common import COUNTRY_CODE, LEVEL_REGION, by_generation, featured_series


@by_generation("series_metadata")
def series_metadata(series_key: str) -> dict[str, Any] | None:
    """Получить описание ряда из измерений склада."""
    rows = fetch_dicts(
        """
        SELECT
            s.series_key,
            s.indicator_code,
            s.section_code,
            s.indicator_name_ru,
            s.subsection_ru,
            s.has_subsection,
            s.full_title_ru,
            s.full_title_en,
            s.polarity,
            u.name_ru        AS unit_name_ru,
            u.name_en        AS unit_name_en,
            u.short_name_ru  AS unit_short_ru,
            u.short_name_en  AS unit_short_en,
            u.kind           AS unit_kind,
            c.region_count,
            c.year_count,
            c.first_year,
            c.last_year,
            c.region_coverage,
            c.completeness,
            c.is_analysis_ready
        FROM dim_series               AS s
        LEFT JOIN dim_unit            AS u ON u.unit_code = s.unit_code
        LEFT JOIN mart_series_coverage AS c ON c.series_key = s.series_key
        WHERE s.series_key = ?
        """,
        [series_key],
    )
    return rows[0] if rows else None


@by_generation("series_timeline")
def series_timeline(
    series_key: str,
    territory_code: str = COUNTRY_CODE,
    *,
    first_year: int | None = None,
    last_year: int | None = None,
) -> list[dict[str, Any]]:
    """Получить ряд наблюдений для одной территории; пропуски — ``None`` с признаком качества."""
    conditions = ["series_key = ?", "territory_code = ?"]
    params: list[Any] = [series_key, territory_code]

    if first_year is not None:
        conditions.append("year >= ?")
        params.append(first_year)
    if last_year is not None:
        conditions.append("year <= ?")
        params.append(last_year)

    return fetch_dicts(
        f"""
        SELECT year, value, quality, revision_count
        FROM fact_observation
        WHERE {" AND ".join(conditions)}
        ORDER BY year
        """,  # noqa: S608 - условия собраны из констант модуля, значения переданы параметрами
        params,
    )


@by_generation("series_timeline_multi")
def series_timeline_multi(
    series_key: str,
    territory_codes: list[str],
    *,
    first_year: int | None = None,
    last_year: int | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Получить ряды сразу по нескольким территориям одним запросом."""
    if not territory_codes:
        return {}

    conditions = [
        "series_key = ?",
        f"territory_code IN ({placeholders(len(territory_codes))})",
    ]
    params: list[Any] = [series_key, *territory_codes]

    if first_year is not None:
        conditions.append("year >= ?")
        params.append(first_year)
    if last_year is not None:
        conditions.append("year <= ?")
        params.append(last_year)

    rows = fetch_dicts(
        f"""
        SELECT territory_code, year, value, quality
        FROM fact_observation
        WHERE {" AND ".join(conditions)}
        ORDER BY territory_code, year
        """,  # noqa: S608 - в текст запроса подставляется только число знаков подстановки
        params,
    )

    grouped: dict[str, list[dict[str, Any]]] = {code: [] for code in territory_codes}
    for row in rows:
        grouped[row["territory_code"]].append(
            {"year": row["year"], "value": row["value"], "quality": row["quality"]}
        )
    return grouped


# Сколько лет показывает миниатюрный график плитки.
SPARKLINE_YEARS = 12


@by_generation("featured_values")
def _recent_values(territory_code: str, series_keys: list[str]) -> list[dict[str, Any]]:
    """
    Значения рядов за последние годы, по которым у территории есть данные.

    Окно — от последнего непропущенного значения ряда; пропуски внутри сохраняются.
    """
    return fetch_dicts(
        f"""
        WITH observations AS (
            SELECT series_key, year, value, flags
            FROM fact_observation
            WHERE territory_code = ? AND series_key IN ({placeholders(len(series_keys))})
        ),
        latest AS (
            SELECT series_key, max(year) FILTER (WHERE value IS NOT NULL) AS last_year
            FROM observations
            GROUP BY series_key
        )
        SELECT o.series_key, o.year, o.value, o.flags
        FROM observations AS o
        JOIN latest       AS l ON l.series_key = o.series_key
        WHERE l.last_year IS NOT NULL
          AND o.year BETWEEN greatest(l.last_year - ?, 2001) AND l.last_year
        ORDER BY o.series_key, o.year
        """,  # noqa: S608 - в текст запроса подставляется только число знаков подстановки
        [territory_code, *series_keys, SPARKLINE_YEARS - 1],
    )


def featured_snapshot(
    territory_code: str = COUNTRY_CODE,
    *,
    headline_only: bool = True,
) -> list[dict[str, Any]]:
    """
    Собрать плитки основного набора: последнее значение, изменение и короткий ряд.

    По умолчанию — только ``headline``. Кэшируются числа: описание показателя из файла
    набора подставляется после, чтобы правка файла не ждала пересборки склада.
    """
    items = [item for item in featured_series() if item.headline or not headline_only]
    if not items:
        return []

    by_key: dict[str, list[dict[str, Any]]] = {}
    for row in _recent_values(territory_code, [item.key for item in items]):
        by_key.setdefault(row["series_key"], []).append(row)

    snapshot: list[dict[str, Any]] = []
    for item in items:
        points = by_key.get(item.key)
        if not points:
            continue
        known = [point for point in points if point["value"] is not None]
        current = known[-1]
        previous = known[-2] if len(known) > 1 else None

        change_absolute = None
        change_relative = None
        if previous and previous["value"] not in (None, 0):
            change_absolute = current["value"] - previous["value"]
            change_relative = change_absolute / abs(previous["value"])

        snapshot.append(
            {
                "series": item,
                "current": {
                    "year": current["year"],
                    "value": current["value"],
                    "flags": int(current.get("flags") or 0),
                    "previous_year": previous["year"] if previous else None,
                    "previous_value": previous["value"] if previous else None,
                    "change_absolute": change_absolute,
                    "change_relative": change_relative,
                },
                "sparkline": [{"year": point["year"], "value": point["value"]} for point in points],
            }
        )

    return snapshot


@by_generation("series_values_by_territory")
def series_values_by_territory(
    series_key: str,
    year: int,
    *,
    level: str = LEVEL_REGION,
) -> list[dict[str, Any]]:
    """Получить значения ряда по всем территориям уровня за год, без составных."""
    return fetch_dicts(
        """
        SELECT
            f.territory_code,
            t.name_ru,
            t.name_en,
            t.abbreviation,
            t.district_code,
            f.value,
            f.quality,
            r.rank_desc,
            r.rank_asc,
            r.percentile,
            r.quintile,
            r.share_of_total,
            r.ratio_to_country
        FROM fact_observation AS f
        JOIN dim_territory    AS t ON t.territory_code = f.territory_code
        LEFT JOIN mart_rank   AS r
               ON r.series_key = f.series_key
              AND r.territory_code = f.territory_code
              AND r.year = f.year
        WHERE f.series_key = ? AND f.year = ? AND f.territory_level = ? AND NOT f.is_aggregate
        ORDER BY t.display_order
        """,
        [series_key, year, level],
    )


@by_generation("series_leaders")
def series_leaders(
    series_key: str,
    year: int,
    *,
    limit: int = 5,
    ascending: bool = False,
) -> list[dict[str, Any]]:
    """Получить территории с наибольшими или наименьшими значениями ряда."""
    order_column = "rank_asc" if ascending else "rank_desc"
    return fetch_dicts(
        f"""
        SELECT r.territory_code, t.name_ru, t.name_en, t.abbreviation,
               r.value, r.rank_desc, r.rank_asc, r.ratio_to_country
        FROM mart_rank     AS r
        JOIN dim_territory AS t ON t.territory_code = r.territory_code
        WHERE r.series_key = ? AND r.year = ?
        ORDER BY r.{order_column}
        LIMIT ?
        """,  # noqa: S608 - имя столбца выбирается из двух допустимых значений
        [series_key, year, limit],
    )


@by_generation("series_ranked_panel")
def series_ranked_panel(series_key: str) -> list[dict[str, Any]]:
    """Получить места всех субъектов по ряду за все годы — для кадров живой карты."""
    return fetch_dicts(
        """
        SELECT
            year,
            territory_code,
            value,
            rank_desc,
            rank_asc,
            percentile,
            count(*) OVER (PARTITION BY year) AS territories
        FROM mart_rank
        WHERE series_key = ?
        ORDER BY year, territory_code
        """,
        [series_key],
    )


@by_generation("series_statistics")
def series_statistics(series_key: str, year: int) -> dict[str, Any] | None:
    """Получить статистики распределения значений ряда по субъектам за год."""
    rows = fetch_dicts(
        "SELECT * FROM mart_series_stats WHERE series_key = ? AND year = ?",
        [series_key, year],
    )
    return rows[0] if rows else None


@by_generation("series_statistics_timeline")
def series_statistics_timeline(series_key: str) -> list[dict[str, Any]]:
    """Получить статистики распределения по всем годам ряда, с квартилями для коридора."""
    return fetch_dicts(
        """
        SELECT year, observations, mean_value, median_value, min_value, max_value,
               p10_value, p25_value, p75_value, p90_value, stddev_value,
               coef_variation, decile_ratio, country_value
        FROM mart_series_stats
        WHERE series_key = ?
        ORDER BY year
        """,
        [series_key],
    )


@by_generation("available_years")
def available_years(series_key: str) -> list[int]:
    """Получить годы, за которые у ряда есть значения по субъектам."""
    rows = fetch_dicts(
        """
        SELECT DISTINCT year
        FROM fact_observation
        WHERE series_key = ?
          AND territory_level = 'region'
          AND NOT is_aggregate
          AND value IS NOT NULL
        ORDER BY year
        """,
        [series_key],
    )
    return [row["year"] for row in rows]


@by_generation("series_values")
def series_values(
    series_keys: list[str], territory_codes: list[str]
) -> dict[str, dict[str, dict[int, float]]]:
    """Получить значения рядов по территориям за все годы: «ряд → территория → год → значение»."""
    if not series_keys or not territory_codes:
        return {}

    rows = fetch_dicts(
        f"""
        SELECT series_key, territory_code, year, value
        FROM fact_observation
        WHERE series_key IN ({placeholders(len(series_keys))})
          AND territory_code IN ({placeholders(len(territory_codes))})
          AND value IS NOT NULL
        """,  # noqa: S608 - в текст запроса подставляется только число знаков подстановки
        [*series_keys, *territory_codes],
    )
    values: dict[str, dict[str, dict[int, float]]] = {}
    for row in rows:
        by_code = values.setdefault(row["series_key"], {})
        by_code.setdefault(row["territory_code"], {})[int(row["year"])] = float(row["value"])
    return values


@by_generation("year_counts")
def year_counts(series_key: str) -> dict[int, int]:
    """Получить число субъектов со значением по годам ряда."""
    rows = fetch_dicts(
        """
        SELECT year, count(*) AS regions
        FROM fact_observation
        WHERE series_key = ?
          AND territory_level = 'region'
          AND NOT is_aggregate
          AND value IS NOT NULL
        GROUP BY year
        ORDER BY year
        """,
        [series_key],
    )
    return {int(row["year"]): int(row["regions"]) for row in rows}


@by_generation("series_summary_map")
def series_summary_map(series_keys: list[str]) -> dict[str, dict[str, Any]]:
    """Получить характеристики покрытия для набора рядов одним запросом."""
    if not series_keys:
        return {}

    rows = fetch_dicts(
        f"""
        SELECT series_key, region_count, year_count, first_year, last_year,
               region_coverage, completeness, is_analysis_ready
        FROM mart_series_coverage
        WHERE series_key IN ({placeholders(len(series_keys))})
        """,  # noqa: S608 - в текст запроса подставляется только число знаков подстановки
        series_keys,
    )
    return {row["series_key"]: row for row in rows}


@by_generation("series_vintages")
def series_vintages(series_key: str, territory_code: str) -> list[dict[str, Any]]:
    """Получить все версии значений ряда по выпускам изданий."""
    return fetch_dicts(
        """
        SELECT v.year, v.edition_code, v.edition_year, v.value, v.quality, v.flags,
               e.edition_label,
               e.publication_ru,
               e.publication_en
        FROM fact_vintage AS v
        LEFT JOIN dim_edition AS e ON e.edition_code = v.edition_code
        WHERE v.series_key = ? AND v.territory_code = ?
        ORDER BY v.year, e.edition_rank
        """,
        [series_key, territory_code],
    )


@by_generation("series_revisions")
def series_revisions(series_key: str, limit: int = 20) -> list[dict[str, Any]]:
    """Получить пересмотренные наблюдения одного ряда, начиная с наибольших расхождений."""
    return fetch_dicts(
        """
        SELECT m.territory_code,
               t.name_ru AS territory_name_ru, t.name_en AS territory_name_en, m.year,
               m.edition_count, m.first_value, m.last_value, m.abs_change, m.rel_change
        FROM mart_revision  AS m
        JOIN dim_territory  AS t ON t.territory_code = m.territory_code
        WHERE m.series_key = ?
        ORDER BY abs(m.rel_change) DESC
        LIMIT ?
        """,
        [series_key, limit],
    )


@by_generation("series_editions")
def series_editions(series_key: str) -> list[dict[str, Any]]:
    """Получить выпуски изданий, в которых встречаются наблюдения ряда."""
    return fetch_dicts(
        """
        SELECT e.edition_code, e.publication_ru, e.publication_en, e.edition_year,
               e.edition_label, e.source_code, count(*) AS observation_count
        FROM fact_vintage     AS v
        JOIN dim_edition      AS e ON e.edition_code = v.edition_code
        WHERE v.series_key = ?
        GROUP BY e.edition_code, e.publication_ru, e.publication_en, e.edition_year,
                 e.edition_label, e.source_code, e.edition_rank
        ORDER BY e.edition_rank DESC
        """,
        [series_key],
    )


@by_generation("latest_values_matrix")
def latest_values_matrix(
    series_keys: list[str], territory_codes: list[str]
) -> dict[tuple[str, str], dict[str, Any]]:
    """
    Получить последние значения набора рядов по нескольким территориям одним запросом.

    Последний год у рядов разный, поэтому возвращается вместе со значением.
    """
    if not series_keys or not territory_codes:
        return {}

    rows = fetch_dicts(
        f"""
        WITH latest AS (
            SELECT
                f.series_key,
                f.territory_code,
                f.year,
                f.value,
                row_number() OVER (
                    PARTITION BY f.series_key, f.territory_code ORDER BY f.year DESC
                ) AS recency
            FROM fact_observation AS f
            WHERE f.series_key IN ({placeholders(len(series_keys))})
              AND f.territory_code IN ({placeholders(len(territory_codes))})
              AND f.value IS NOT NULL
        )
        SELECT l.series_key, l.territory_code, l.year, l.value,
               r.rank_desc, r.percentile, r.ratio_to_country
        FROM latest         AS l
        LEFT JOIN mart_rank AS r
               ON r.series_key = l.series_key
              AND r.territory_code = l.territory_code
              AND r.year = l.year
        WHERE l.recency = 1
        """,  # noqa: S608 - в текст запроса подставляется только число знаков подстановки
        [*series_keys, *territory_codes],
    )
    return {(row["series_key"], row["territory_code"]): row for row in rows}


def series_observations(
    series_key: str,
    *,
    territory_code: str | None = None,
    first_year: int | None = None,
    last_year: int | None = None,
    limit: int = 500,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """
    Получить наблюдения ряда порциями вместе с их общим числом.

    Число — отдельным запросом: ``count(*) OVER ()`` повторило бы его в каждой строке.
    """
    conditions = ["f.series_key = ?"]
    params: list[Any] = [series_key]

    if territory_code:
        conditions.append("f.territory_code = ?")
        params.append(territory_code)
    else:
        # Без территории — только субъекты, иначе двойной учёт.
        conditions.append("f.territory_level = ? AND NOT f.is_aggregate")
        params.append(LEVEL_REGION)

    if first_year is not None:
        conditions.append("f.year >= ?")
        params.append(first_year)
    if last_year is not None:
        conditions.append("f.year <= ?")
        params.append(last_year)

    where = " AND ".join(conditions)

    total = fetch_scalar(
        f"SELECT count(*) FROM fact_observation AS f WHERE {where}",  # noqa: S608
        params,
        default=0,
    )

    rows = fetch_dicts(
        f"""
        SELECT f.series_key, f.territory_code, f.year, f.value, f.quality, f.flags,
               e.source_code, e.edition_label, e.released_on
        FROM fact_observation AS f
        LEFT JOIN dim_edition AS e ON e.edition_code = f.edition_code
        WHERE {where}
        ORDER BY f.territory_code, f.year
        LIMIT ? OFFSET ?
        """,  # noqa: S608 - условия собраны из констант модуля, значения переданы параметрами
        [*params, limit, offset],
    )
    return rows, int(total or 0)
