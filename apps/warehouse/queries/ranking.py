"""Запросы рейтингов и движения позиций."""

from __future__ import annotations

from typing import Any

from ..duckdb_client import fetch_dicts, placeholders, validate_identifier
from ..routing import by_key
from .common import by_generation

# Допустимые столбцы упорядочивания рейтинга.
ORDER_COLUMNS = frozenset({"rank_desc", "rank_asc"})


@by_key()
@by_generation("ranking_table")
def ranking_table(
    series_key: str,
    year: int,
    *,
    previous_year: int | None = None,
    ascending: bool = False,
) -> list[dict[str, Any]]:
    """Получить рейтинг субъектов за год с рангом года сравнения — одним запросом."""
    order_column = validate_identifier("rank_asc" if ascending else "rank_desc", ORDER_COLUMNS)

    return fetch_dicts(
        f"""
        SELECT
            r.territory_code,
            t.name_ru,
            t.name_en,
            t.abbreviation,
            t.district_code,
            d.name_ru       AS district_name_ru,
            d.name_en       AS district_name_en,
            r.value,
            r.rank_desc,
            r.rank_asc,
            r.percentile,
            r.quintile,
            r.share_of_total,
            r.ratio_to_country,
            p.value         AS previous_value,
            p.rank_desc     AS previous_rank_desc,
            p.rank_asc      AS previous_rank_asc
        FROM mart_rank          AS r
        JOIN dim_territory      AS t ON t.territory_code = r.territory_code
        LEFT JOIN dim_territory AS d ON d.territory_code = t.district_code
        LEFT JOIN mart_rank     AS p
               ON p.series_key = r.series_key
              AND p.territory_code = r.territory_code
              AND p.year = ?
        WHERE r.series_key = ? AND r.year = ?
        ORDER BY r.{order_column}
        """,  # noqa: S608 - имя столбца проверено по белому списку
        [previous_year, series_key, year],
    )


@by_key()
@by_generation("rank_history")
def rank_history(
    series_key: str,
    territory_codes: list[str],
    *,
    first_year: int | None = None,
    last_year: int | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Получить историю позиций перечисленных территорий для графика движения рангов."""
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
        SELECT territory_code, year, value, rank_desc, rank_asc, percentile
        FROM mart_rank
        WHERE {" AND ".join(conditions)}
        ORDER BY territory_code, year
        """,  # noqa: S608 - условия собраны из констант модуля, значения переданы параметрами
        params,
    )

    grouped: dict[str, list[dict[str, Any]]] = {code: [] for code in territory_codes}
    for row in rows:
        grouped[row["territory_code"]].append(row)
    return grouped


@by_key()
@by_generation("rank_movers")
def rank_movers(
    series_key: str,
    year: int,
    previous_year: int,
    *,
    limit: int = 5,
) -> dict[str, list[dict[str, Any]]]:
    """
    Найти поднявшиеся и опустившиеся сильнее всего территории.

    Территории без ранга в одном из годов не участвуют.
    """
    rows = fetch_dicts(
        """
        SELECT
            r.territory_code,
            t.name_ru,
            t.name_en,
            t.abbreviation,
            r.value,
            r.rank_desc,
            p.rank_desc AS previous_rank_desc,
            p.rank_desc - r.rank_desc AS movement
        FROM mart_rank     AS r
        JOIN mart_rank     AS p
             ON p.series_key = r.series_key
            AND p.territory_code = r.territory_code
            AND p.year = ?
        JOIN dim_territory AS t ON t.territory_code = r.territory_code
        WHERE r.series_key = ? AND r.year = ?
        ORDER BY movement DESC
        """,
        [previous_year, series_key, year],
    )

    moved = [row for row in rows if row["movement"]]
    # Направление показывает значок, поэтому в подписи — модуль: не «↓ −24».
    for row in moved:
        row["movement_abs"] = abs(row["movement"])
    return {
        "risen": moved[:limit],
        "fallen": list(reversed(moved[-limit:])) if len(moved) > limit else [],
    }


@by_key()
@by_generation("ranked_years")
def ranked_years(series_key: str) -> list[int]:
    """Получить годы, за которые рассчитаны ранги ряда."""
    rows = fetch_dicts(
        "SELECT DISTINCT year FROM mart_rank WHERE series_key = ? ORDER BY year",
        [series_key],
    )
    return [row["year"] for row in rows]
