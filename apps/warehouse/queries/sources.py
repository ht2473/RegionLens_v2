"""Происхождение значений: связи рядов с внешними источниками и признаки значений года."""

from __future__ import annotations

from typing import Any

from ..duckdb_client import fetch_dicts, fetch_scalar, get_connection, placeholders
from ..routing import by_key, by_keys
from .common import COUNTRY_CODE, by_generation


@by_generation("source_links")
def source_links() -> dict[str, dict[str, Any]]:
    """Связи рядов с источниками вместе с последним выпуском источника, по ключу ряда."""
    rows = fetch_dicts(
        """
        SELECT l.*, e.publication_ru, e.publication_en, e.edition_label, e.released_on
        FROM mart_source_link AS l
        LEFT JOIN (
            SELECT source_code, publication_ru, publication_en, edition_label, released_on,
                   row_number() OVER (PARTITION BY source_code ORDER BY edition_rank DESC) AS n
            FROM dim_edition
            WHERE source_code IS NOT NULL
        ) AS e ON e.source_code = l.source_code AND e.n = 1
        ORDER BY l.series_key
        """
    )
    return {row["series_key"]: row for row in rows}


@by_key()
@by_generation("year_origin")
def year_origin(series_key: str, year: int) -> dict[str, Any] | None:
    """
    Откуда значения ряда за год: выпуск источника и признаки значений.

    ``None`` — все значения года из набора.
    """
    rows = fetch_dicts(
        """
        SELECT
            e.source_code,
            e.edition_label,
            e.released_on,
            count(*)                              AS values,
            bit_or(f.flags)                       AS flags,
            count(*) FILTER (WHERE f.flags & 1 > 0) AS preliminary
        FROM fact_observation AS f
        JOIN dim_edition      AS e ON e.edition_code = f.edition_code
        WHERE f.series_key = ? AND f.year = ? AND e.source_code IS NOT NULL
        GROUP BY 1, 2, 3
        ORDER BY count(*) DESC
        LIMIT 1
        """,
        [series_key, year],
    )
    return rows[0] if rows else None


@by_key()
@by_generation("source_years")
def source_years(series_key: str) -> list[dict[str, Any]]:
    """Годы ряда, значения субъектов в которых взяты из выпусков источников, с признаками."""
    return fetch_dicts(
        """
        SELECT CAST(f.year AS INTEGER) AS year, e.source_code, e.edition_label, e.released_on,
               count(*) AS values, bit_or(f.flags) AS flags
        FROM fact_observation AS f
        JOIN dim_edition      AS e ON e.edition_code = f.edition_code
        WHERE f.series_key = ? AND e.source_code IS NOT NULL AND f.value IS NOT NULL
          AND f.territory_level = 'region' AND NOT f.is_aggregate
        GROUP BY 1, 2, 3, 4
        ORDER BY 1
        """,
        [series_key],
    )


@by_generation("population_last_year")
def population_last_year() -> int | None:
    """Последний год численности, на которую делятся ряды проекта на жителя."""
    from apps.sources.registry import registry

    value = fetch_scalar(
        "SELECT max(year) FROM fact_observation "
        "WHERE series_key = ? AND territory_code = ? AND value IS NOT NULL",
        [registry().population_per_capita, COUNTRY_CODE],
    )
    return int(value) if value is not None else None


@by_generation("population_table")
def population_table() -> dict[str, dict[int, float]]:
    """Среднегодовая численность по территориям и годам — знаменатель сумм своих данных."""
    from apps.sources.registry import registry

    from ..population import population_frame

    frame = population_frame(get_connection(), registry())
    table: dict[str, dict[int, float]] = {}
    for code, year, population in frame.itertuples(index=False):
        table.setdefault(str(code), {})[int(year)] = float(population)
    return table


@by_generation("release_editions")
def release_editions() -> list[dict[str, Any]]:
    """Выпуски источников в складе: издание, дата, число версий значений и годы."""
    return fetch_dicts(
        """
        SELECT edition_code, source_code, edition_label, released_on, publication_ru,
               publication_en, observation_count, first_year, last_year
        FROM dim_edition
        WHERE source_code IS NOT NULL
        ORDER BY edition_rank DESC
        """
    )


@by_keys()
@by_generation("edition_contents")
def edition_contents(edition_codes: list[str], series_keys: list[str]) -> list[dict[str, Any]]:
    """
    Что принесли выпуски по рядам: последний период и число значений.

    Годовые версии — из ``fact_vintage``, месячные — из ``fact_month``; у годовых месяц пуст.
    """
    if not edition_codes or not series_keys:
        return []
    editions = placeholders(len(edition_codes))
    keys = placeholders(len(series_keys))
    return fetch_dicts(
        f"""
        SELECT edition_code, series_key, max(year) AS last_year,
               NULL::TINYINT AS last_month, count(*) AS values
        FROM fact_vintage
        WHERE edition_code IN ({editions}) AND series_key IN ({keys}) AND value IS NOT NULL
        GROUP BY 1, 2
        UNION ALL
        SELECT edition_code, series_key, arg_max(year, year::INTEGER * 100 + month) AS last_year,
               arg_max(month, year::INTEGER * 100 + month) AS last_month, count(*) AS values
        FROM fact_month
        WHERE edition_code IN ({editions}) AND series_key IN ({keys}) AND value IS NOT NULL
        GROUP BY 1, 2
        """,  # noqa: S608 - в текст запроса подставляется только число знаков подстановки
        [*edition_codes, *series_keys, *edition_codes, *series_keys],
    )


@by_keys()
@by_generation("edition_territories")
def edition_territories(
    edition_codes: list[str], series_keys: list[str], territory_codes: list[str]
) -> dict[str, int]:
    """Сколько рядов из ``series_keys`` получили в выпусках значения по каждой территории."""
    if not edition_codes or not series_keys or not territory_codes:
        return {}
    editions = placeholders(len(edition_codes))
    keys = placeholders(len(series_keys))
    codes = placeholders(len(territory_codes))
    rows = fetch_dicts(
        f"""
        SELECT territory_code, count(DISTINCT series_key) AS series
        FROM (
            SELECT territory_code, series_key FROM fact_vintage
            WHERE edition_code IN ({editions}) AND series_key IN ({keys})
              AND territory_code IN ({codes}) AND value IS NOT NULL
            UNION ALL
            SELECT territory_code, series_key FROM fact_month
            WHERE edition_code IN ({editions}) AND series_key IN ({keys})
              AND territory_code IN ({codes}) AND value IS NOT NULL
        )
        GROUP BY 1
        """,  # noqa: S608 - в текст запроса подставляется только число знаков подстановки
        [*edition_codes, *series_keys, *territory_codes] * 2,
    )
    return {row["territory_code"]: int(row["series"]) for row in rows}


@by_keys()
@by_generation("dataset_last_years")
def dataset_last_years(series_keys: list[str]) -> dict[str, int]:
    """Последний год значений набора (не внешних источников) по каждому ряду."""
    if not series_keys:
        return {}
    rows = fetch_dicts(
        f"""
        SELECT o.series_key, max(o.year) AS last_year
        FROM fact_observation AS o
        JOIN dim_edition      AS e ON e.edition_code = o.edition_code
        WHERE e.source_code IS NULL AND o.value IS NOT NULL
          AND o.series_key IN ({placeholders(len(series_keys))})
        GROUP BY 1
        """,  # noqa: S608 - в текст запроса подставляется только число знаков подстановки
        series_keys,
    )
    return {row["series_key"]: int(row["last_year"]) for row in rows}


@by_keys()
@by_generation("dataset_territories")
def dataset_territories(series_keys: list[str], territory_codes: list[str]) -> dict[str, int]:
    """Число рядов ``series_keys`` со значением набора за последний год ряда, по территориям."""
    if not series_keys or not territory_codes:
        return {}
    keys = placeholders(len(series_keys))
    rows = fetch_dicts(
        f"""
        WITH dataset AS (
            SELECT o.series_key, o.territory_code, o.year
            FROM fact_observation AS o
            JOIN dim_edition      AS e ON e.edition_code = o.edition_code
            WHERE e.source_code IS NULL AND o.value IS NOT NULL AND o.series_key IN ({keys})
        ),
        last AS (SELECT series_key, max(year) AS year FROM dataset GROUP BY 1)
        SELECT d.territory_code, count(DISTINCT d.series_key) AS series
        FROM dataset AS d
        JOIN last    AS l ON l.series_key = d.series_key AND l.year = d.year
        WHERE d.territory_code IN ({placeholders(len(territory_codes))})
        GROUP BY 1
        """,  # noqa: S608 - в текст запроса подставляется только число знаков подстановки
        [*series_keys, *territory_codes],
    )
    return {row["territory_code"]: int(row["series"]) for row in rows}
