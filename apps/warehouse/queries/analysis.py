"""
Выборки для аналитических инструментов и разбора пересмотров статистики.

Наборы «субъекты × показатели за год» и «субъекты × годы по показателю» — одним запросом.
"""

from __future__ import annotations

from typing import Any

from django.utils.translation import gettext_lazy as _

from apps.catalog.constants import ValueQuality

from ..duckdb_client import fetch_dicts, fetch_one, placeholders
from ..routing import by_key, by_keys
from .common import LEVEL_DISTRICT, LEVEL_REGION, MIN_YEAR_COVERAGE, by_generation

# Субъекты без составных территорий, иначе входящие в них учитывались бы дважды.
REGION_CONDITION = "territory_level = ? AND NOT is_aggregate"

# Границы столбиков распределения относительных изменений при пересмотрах, в долях.
REVISION_BUCKETS: tuple[tuple[float, float, Any], ...] = (
    (0.0, 0.001, _("менее 0,1 %")),
    (0.001, 0.01, _("от 0,1 до 1 %")),
    (0.01, 0.05, _("от 1 до 5 %")),
    (0.05, 0.25, _("от 5 до 25 %")),
    (0.25, float("inf"), _("более 25 %")),
)


# ---------------------------------------------------------------------------------------
# Наборы значений для расчётов
# ---------------------------------------------------------------------------------------


@by_key()
@by_generation("region_panel")
def region_panel(series_key: str) -> dict[int, dict[str, float]]:
    """Получить значения ряда по всем субъектам за все годы: «год → территория → значение»."""
    rows = fetch_dicts(
        f"""
        SELECT year, territory_code, value
        FROM fact_observation
        WHERE series_key = ? AND {REGION_CONDITION} AND value IS NOT NULL
        ORDER BY year, territory_code
        """,  # noqa: S608 - условие собрано из констант модуля, значения переданы параметрами
        [series_key, LEVEL_REGION],
    )

    panel: dict[int, dict[str, float]] = {}
    for row in rows:
        panel.setdefault(int(row["year"]), {})[row["territory_code"]] = float(row["value"])
    return panel


@by_key()
@by_generation("hidden_counts")
def hidden_counts(series_key: str) -> dict[int, int]:
    """Получить число субъектов со скрытым источником значением по годам ряда."""
    rows = fetch_dicts(
        f"""
        SELECT year, count(*) AS hidden
        FROM fact_observation
        WHERE series_key = ? AND {REGION_CONDITION} AND quality = ?
        GROUP BY year
        """,  # noqa: S608 - условие собрано из констант модуля, значения переданы параметрами
        [series_key, LEVEL_REGION, int(ValueQuality.HIDDEN)],
    )
    return {int(row["year"]): int(row["hidden"]) for row in rows}


@by_keys()
@by_generation("region_matrix")
def region_matrix(series_keys: list[str], year: int) -> dict[str, dict[str, Any]]:
    """
    Получить значения набора рядов по субъектам за указанный год.

    Для ряда без полных данных за этот год берётся ближайший полный год (``MIN_YEAR_COVERAGE``)
    и возвращается вместе со значениями.
    """
    if not series_keys:
        return {}

    marks = placeholders(len(series_keys))
    rows = fetch_dicts(
        f"""
        WITH counted AS (
            SELECT series_key, year, count(value) AS filled
            FROM fact_observation
            WHERE series_key IN ({marks}) AND {REGION_CONDITION} AND value IS NOT NULL
            GROUP BY 1, 2
        ),
        candidates AS (
            SELECT *
            FROM counted
            QUALIFY filled >= ? * max(filled) OVER (PARTITION BY series_key)
        ),
        chosen AS (
            SELECT
                series_key,
                year,
                filled,
                row_number() OVER (
                    PARTITION BY series_key ORDER BY abs(year - ?), year DESC
                ) AS priority
            FROM candidates
        )
        SELECT c.series_key, c.year, c.filled, f.territory_code, f.value
        FROM chosen           AS c
        JOIN fact_observation AS f
               ON f.series_key = c.series_key
              AND f.year = c.year
              AND f.territory_level = ?
              AND NOT f.is_aggregate
        WHERE c.priority = 1 AND f.value IS NOT NULL
        """,  # noqa: S608 - в текст подставляются только знаки подстановки и константы
        [*series_keys, LEVEL_REGION, MIN_YEAR_COVERAGE, year, LEVEL_REGION],
    )

    matrix: dict[str, dict[str, Any]] = {}
    for row in rows:
        entry = matrix.setdefault(
            row["series_key"],
            {"year": int(row["year"]), "covered": int(row["filled"]), "values": {}},
        )
        entry["values"][row["territory_code"]] = float(row["value"])
    return matrix


@by_key()
@by_generation("paired_years")
def paired_years(series_key: str, first_year: int, last_year: int) -> list[dict[str, Any]]:
    """Получить значения ряда в двух годах для субъектов, у которых есть оба."""
    return fetch_dicts(
        f"""
        WITH bounds AS (
            SELECT
                territory_code,
                max(value) FILTER (WHERE year = ?) AS start_value,
                max(value) FILTER (WHERE year = ?) AS end_value
            FROM fact_observation
            WHERE series_key = ? AND {REGION_CONDITION} AND year IN (?, ?)
            GROUP BY 1
        )
        SELECT
            b.territory_code,
            t.name_ru,
            t.name_en,
            t.abbreviation,
            t.district_code,
            b.start_value,
            b.end_value
        FROM bounds        AS b
        JOIN dim_territory AS t ON t.territory_code = b.territory_code
        WHERE b.start_value IS NOT NULL AND b.end_value IS NOT NULL
        ORDER BY t.display_order
        """,  # noqa: S608 - условие собрано из констант модуля, значения переданы параметрами
        [first_year, last_year, series_key, LEVEL_REGION, first_year, last_year],
    )


@by_key()
@by_generation("district_totals")
def district_totals(series_key: str, year: int) -> dict[str, dict[str, Any]]:
    """
    Сводка ряда за год по федеральным округам: значение округа, если оно есть в данных,
    и субъекты округа со значениями — число, сумма, медиана, наименьшее и наибольшее.
    """
    rows = fetch_dicts(
        f"""
        SELECT district_code, count(*) AS regions, sum(value) AS total,
               median(value) AS median, min(value) AS low, max(value) AS high
        FROM fact_observation
        WHERE series_key = ? AND year = ? AND {REGION_CONDITION}
          AND value IS NOT NULL AND district_code IS NOT NULL
        GROUP BY district_code
        ORDER BY district_code
        """,  # noqa: S608 - условие собрано из констант модуля, значения переданы параметрами
        [series_key, year, LEVEL_REGION],
    )
    found: dict[str, dict[str, Any]] = {
        str(row["district_code"]): {
            "regions": int(row["regions"]),
            "total": float(row["total"]),
            "median": float(row["median"]),
            "low": float(row["low"]),
            "high": float(row["high"]),
            "value": None,
        }
        for row in rows
    }
    own = fetch_dicts(
        """
        SELECT territory_code, value
        FROM fact_observation
        WHERE series_key = ? AND year = ? AND territory_level = ? AND value IS NOT NULL
        ORDER BY territory_code
        """,
        [series_key, year, LEVEL_DISTRICT],
    )
    for row in own:
        entry = found.setdefault(
            str(row["territory_code"]),
            {"regions": 0, "total": None, "median": None, "low": None, "high": None},
        )
        entry["value"] = float(row["value"])
    return found


@by_generation("region_directory")
def region_directory() -> list[dict[str, Any]]:
    """Получить перечень субъектов из измерения склада: названия, округа, порядок."""
    return fetch_dicts(
        """
        SELECT territory_code, name_ru, name_en, abbreviation, district_code, display_order
        FROM dim_territory
        WHERE level = ? AND NOT is_aggregate
        ORDER BY display_order
        """,
        [LEVEL_REGION],
    )


@by_generation("district_directory")
def district_directory() -> list[dict[str, Any]]:
    """Получить перечень федеральных округов для разложения показателей по группам."""
    return fetch_dicts(
        """
        SELECT territory_code, name_ru, name_en, abbreviation
        FROM dim_territory
        WHERE level = 'federal_district'
        ORDER BY display_order
        """
    )


@by_keys()
@by_generation("series_years")
def series_years(series_keys: list[str]) -> dict[str, tuple[int, int]]:
    """Получить первый и последний год с данными для набора рядов."""
    if not series_keys:
        return {}

    rows = fetch_dicts(
        f"""
        SELECT series_key, first_year, last_year
        FROM mart_series_coverage
        WHERE series_key IN ({placeholders(len(series_keys))})
        """,  # noqa: S608 - в текст запроса подставляется только число знаков подстановки
        series_keys,
    )
    return {
        row["series_key"]: (int(row["first_year"]), int(row["last_year"]))
        for row in rows
        if row["first_year"] is not None and row["last_year"] is not None
    }


@by_keys()
@by_generation("series_covered_years")
def series_covered_years(series_keys: list[str]) -> dict[str, tuple[int, int]]:
    """Получить первый и последний полный год (``MIN_YEAR_COVERAGE``) для набора рядов."""
    if not series_keys:
        return {}

    rows = fetch_dicts(
        f"""
        WITH counted AS (
            SELECT series_key, year, count(*) AS filled
            FROM fact_observation
            WHERE series_key IN ({placeholders(len(series_keys))})
              AND {REGION_CONDITION} AND value IS NOT NULL
            GROUP BY 1, 2
        )
        SELECT series_key, min(year) AS first_year, max(year) AS last_year
        FROM (
            SELECT *
            FROM counted
            QUALIFY filled >= ? * max(filled) OVER (PARTITION BY series_key)
        )
        GROUP BY series_key
        """,  # noqa: S608 - в текст подставляются только знаки подстановки и константы
        [*series_keys, LEVEL_REGION, MIN_YEAR_COVERAGE],
    )
    return {row["series_key"]: (int(row["first_year"]), int(row["last_year"])) for row in rows}


# ---------------------------------------------------------------------------------------
# Пересмотры статистики
# ---------------------------------------------------------------------------------------


@by_generation("revision_summary")
def revision_summary() -> dict[str, Any]:
    """Получить сводку по пересмотрам: число наблюдений, рядов, территорий и размер расхождений."""
    row = fetch_one(
        """
        SELECT
            count(*)                                    AS observations,
            count(DISTINCT series_key)                  AS series,
            count(DISTINCT territory_code)              AS territories,
            median(abs(rel_change))                     AS median_change,
            avg(abs(rel_change))                        AS mean_change,
            max(abs(rel_change))                        AS max_change,
            min(year)                                   AS first_year,
            max(year)                                   AS last_year,
            count(*) FILTER (WHERE abs(rel_change) >= 0.05) AS large_count
        FROM mart_revision
        WHERE rel_change IS NOT NULL
        """
    )
    if row is None:
        return {"observations": 0}

    return {
        "observations": row[0],
        "series": row[1],
        "territories": row[2],
        "median_change": row[3],
        "mean_change": row[4],
        "max_change": row[5],
        "first_year": row[6],
        "last_year": row[7],
        "large_count": row[8],
    }


@by_generation("revision_distribution")
def revision_distribution() -> list[dict[str, Any]]:
    """Получить распределение пересмотров по величине расхождения."""
    rows = fetch_dicts(
        """
        SELECT
            CASE
                WHEN abs(rel_change) < 0.001 THEN 0
                WHEN abs(rel_change) < 0.01  THEN 1
                WHEN abs(rel_change) < 0.05  THEN 2
                WHEN abs(rel_change) < 0.25  THEN 3
                ELSE 4
            END      AS bucket,
            count(*) AS revision_count
        FROM mart_revision
        WHERE rel_change IS NOT NULL
        GROUP BY 1
        ORDER BY 1
        """
    )
    counts = {int(row["bucket"]): int(row["revision_count"]) for row in rows}
    total = sum(counts.values())

    return [
        {
            "label": str(label),
            "low": low,
            "high": high,
            "count": counts.get(index, 0),
            "share": counts.get(index, 0) / total if total else 0.0,
        }
        for index, (low, high, label) in enumerate(REVISION_BUCKETS)
    ]


@by_generation("revisions_by_section")
def revisions_by_section(limit: int = 12) -> list[dict[str, Any]]:
    """Получить разделы сборников, значения которых пересматривались чаще прочих."""
    return fetch_dicts(
        """
        SELECT
            sec.section_code,
            sec.name_ru,
            sec.name_en,
            count(*)                  AS revision_count,
            count(DISTINCT m.series_key) AS series_count,
            median(abs(m.rel_change)) AS median_change,
            max(abs(m.rel_change))    AS max_change
        FROM mart_revision AS m
        JOIN dim_series    AS s   ON s.series_key = m.series_key
        JOIN dim_section   AS sec ON sec.section_code = s.section_code
        WHERE m.rel_change IS NOT NULL
        GROUP BY 1, 2, 3
        ORDER BY revision_count DESC
        LIMIT ?
        """,
        [limit],
    )


@by_generation("revisions_by_series")
def revisions_by_series(limit: int = 15, section_code: str = "") -> list[dict[str, Any]]:
    """Получить ряды с наибольшим числом пересмотренных наблюдений."""
    conditions = ["m.rel_change IS NOT NULL"]
    params: list[Any] = []
    if section_code:
        conditions.append("s.section_code = ?")
        params.append(section_code)

    params.append(limit)
    return fetch_dicts(
        f"""
        SELECT
            m.series_key,
            s.full_title_ru,
            s.full_title_en,
            s.indicator_code,
            sec.name_ru               AS section_name_ru,
            sec.name_en               AS section_name_en,
            count(*)                  AS revision_count,
            count(DISTINCT m.territory_code) AS territory_count,
            median(abs(m.rel_change)) AS median_change,
            max(abs(m.rel_change))    AS max_change,
            min(m.year)               AS first_year,
            max(m.year)               AS last_year
        FROM mart_revision AS m
        JOIN dim_series    AS s   ON s.series_key = m.series_key
        JOIN dim_section   AS sec ON sec.section_code = s.section_code
        WHERE {" AND ".join(conditions)}
        GROUP BY 1, 2, 3, 4, 5, 6
        ORDER BY revision_count DESC, max_change DESC
        LIMIT ?
        """,  # noqa: S608 - условия собраны из констант модуля, значения переданы параметрами
        params,
    )


@by_generation("revisions_by_year")
def revisions_by_year() -> list[dict[str, Any]]:
    """Получить распределение пересмотров по отчётным годам."""
    return fetch_dicts(
        """
        SELECT
            year,
            count(*)                  AS revision_count,
            median(abs(rel_change))   AS median_change,
            count(*) FILTER (WHERE abs(rel_change) >= 0.05) AS large_count
        FROM mart_revision
        WHERE rel_change IS NOT NULL
        GROUP BY 1
        ORDER BY 1
        """
    )


@by_generation("largest_revisions")
def largest_revisions(limit: int = 20, section_code: str = "") -> list[dict[str, Any]]:
    """
    Получить наблюдения с наибольшим расхождением между выпусками.

    Расхождения свыше пятикратных отсеиваются: это смена единицы, а не пересмотр.
    """
    conditions = ["m.rel_change IS NOT NULL", "abs(m.rel_change) BETWEEN 0.01 AND 5"]
    params: list[Any] = []
    if section_code:
        conditions.append("s.section_code = ?")
        params.append(section_code)

    params.append(limit)
    return fetch_dicts(
        f"""
        SELECT
            m.series_key,
            s.full_title_ru,
            s.full_title_en,
            t.name_ru AS territory_name_ru,
            t.name_en AS territory_name_en,
            t.territory_code,
            m.year,
            m.edition_count,
            m.first_value,
            m.last_value,
            m.abs_change,
            m.rel_change
        FROM mart_revision AS m
        JOIN dim_series    AS s ON s.series_key = m.series_key
        JOIN dim_territory AS t ON t.territory_code = m.territory_code
        WHERE {" AND ".join(conditions)}
        ORDER BY abs(m.rel_change) DESC
        LIMIT ?
        """,  # noqa: S608 - условия собраны из констант модуля, значения переданы параметрами
        params,
    )


@by_generation("edition_activity")
def edition_activity() -> list[dict[str, Any]]:
    """Получить выпуски изданий с числом опубликованных в них версий значений."""
    return fetch_dicts(
        """
        SELECT
            e.edition_code,
            e.publication_ru,
            e.publication_en,
            e.edition_year,
            e.edition_label,
            e.observation_count,
            e.first_year,
            e.last_year
        FROM dim_edition AS e
        ORDER BY e.edition_rank DESC
        """
    )


@by_key()
@by_generation("revision_trace")
def revision_trace(series_key: str, territory_code: str, year: int) -> list[dict[str, Any]]:
    """Получить все опубликованные версии одного наблюдения."""
    return fetch_dicts(
        """
        SELECT
            v.edition_code,
            v.edition_year,
            e.edition_label,
            v.value,
            v.quality,
            v.flags,
            e.publication_ru,
            e.publication_en
        FROM fact_vintage     AS v
        LEFT JOIN dim_edition AS e ON e.edition_code = v.edition_code
        WHERE v.series_key = ? AND v.territory_code = ? AND v.year = ?
        ORDER BY e.edition_rank
        """,
        [series_key, territory_code, year],
    )


@by_key()
@by_generation("revision_triangle")
def revision_triangle(series_key: str, territory_code: str) -> dict[str, Any]:
    """
    Собрать треугольник пересмотров: год наблюдения × выпуск сборника (как ALFRED).

    Строки — годы со значениями по выпускам и отклонением от первой публикации;
    сводка — среднее изменение через один, два и более выпусков после неё.
    """
    rows = fetch_dicts(
        """
        SELECT v.year, v.edition_code, e.edition_rank, e.edition_label, v.value, v.quality
        FROM fact_vintage AS v
        JOIN dim_edition  AS e ON e.edition_code = v.edition_code
        WHERE v.series_key = ? AND v.territory_code = ?
        ORDER BY v.year, e.edition_rank
        """,
        [series_key, territory_code],
    )
    if not rows:
        return {"available": False}

    # Столбец — выпуск: у сборников это год (сборники одного года — один столбец, берётся
    # более поздний), у внешних источников — код выпуска. Порядок — порядок выхода.
    order: dict[str, int] = {}
    by_year: dict[int, dict[str, dict[str, Any]]] = {}
    for row in rows:
        label = str(row["edition_label"])
        order[label] = max(order.get(label, 0), int(row["edition_rank"]))
        by_year.setdefault(int(row["year"]), {})[label] = row
    editions = sorted(order, key=order.__getitem__)

    table: list[dict[str, Any]] = []
    # Отклонения по горизонту: ключ — сколько выпусков прошло после первой публикации.
    horizons: dict[int, list[float]] = {}

    for year in sorted(by_year):
        published = by_year[year]
        known = [published[edition] for edition in editions if edition in published]
        first = next((item["value"] for item in known if item["value"] is not None), None)

        cells: list[dict[str, Any]] = []
        for step, edition in enumerate(editions):
            item = published.get(edition)
            if item is None:
                # Выпуск этого года не печатал.
                cells.append({"known": False})
                continue

            value = item["value"]
            change = None
            if first not in (None, 0) and value is not None:
                change = (value - first) / abs(first)
                # Горизонт — от первого выпуска со значением, а не от первого столбца.
                position = step - next(
                    index for index, code in enumerate(editions) if code in published
                )
                if position > 0:
                    horizons.setdefault(position, []).append(abs(change))

            cells.append(
                {
                    "known": True,
                    "value": value,
                    "quality": item["quality"],
                    "change": change,
                    "revised": change is not None and abs(change) > 0,
                }
            )

        table.append({"year": year, "cells": cells, "first": first})

    return {
        "available": True,
        "editions": editions,
        "rows": table,
        "horizons": [
            {
                "step": step,
                "mean": sum(values) / len(values),
                "max": max(values),
                "count": len(values),
            }
            for step, values in sorted(horizons.items())
        ],
        "revised_count": sum(1 for row in table for cell in row["cells"] if cell.get("revised")),
    }
