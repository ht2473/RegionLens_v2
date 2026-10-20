"""Запросы рейтингов и движения позиций."""

from __future__ import annotations

from typing import Any

from ..duckdb_client import fetch_dicts, placeholders, validate_identifier
from ..routing import by_key
from .common import by_generation

# Допустимые столбцы упорядочивания рейтинга.
ORDER_COLUMNS = frozenset({"rank_desc", "rank_asc"})


# Новый вид строки — новый ключ кэша («-2»): прежние ответы хранятся до суток.
@by_key()
@by_generation("ranking_table-2")
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
            d.abbreviation  AS district_abbreviation,
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
@by_generation("rank_movers-2")
def rank_movers(
    series_key: str,
    year: int,
    previous_year: int,
    *,
    limit: int = 5,
    ascending: bool = False,
    district: str = "",
) -> dict[str, Any]:
    """
    Найти поднявшиеся и опустившиеся сильнее всего территории — в том же порядке мест,
    что и таблица рейтинга (``ascending`` — первое место у наименьшего), и в своём округе.

    ``year`` — поздний год, ``previous_year`` — ранний. Территории без ранга в одном из годов
    не участвуют. Если у последнего показанного тот же сдвиг, что у следующих, их число —
    в ``risen_more``/``fallen_more``: граница списка проходит внутри равных.
    """
    column = validate_identifier("rank_asc" if ascending else "rank_desc", ORDER_COLUMNS)
    where = "r.series_key = ? AND r.year = ?"
    params: list[Any] = [previous_year, series_key, year]
    if district:
        where += " AND t.district_code = ?"
        params.append(district)
    rows = fetch_dicts(
        f"""
        SELECT
            r.territory_code,
            t.name_ru,
            t.name_en,
            t.abbreviation,
            r.value,
            p.value          AS previous_value,
            r.{column}       AS rank,
            p.{column}       AS previous_rank,
            p.{column} - r.{column} AS movement
        FROM mart_rank     AS r
        JOIN mart_rank     AS p
             ON p.series_key = r.series_key
            AND p.territory_code = r.territory_code
            AND p.year = ?
        JOIN dim_territory AS t ON t.territory_code = r.territory_code
        WHERE {where}
        ORDER BY movement DESC, r.{column}
        """,  # noqa: S608 - имя столбца проверено по белому списку
        params,
    )

    # Направление показывает значок, поэтому в подписи — модуль: не «↓ −24».
    for row in rows:
        row["movement_abs"] = abs(row["movement"])
    risen = [row for row in rows if row["movement"] > 0]
    fallen = sorted(
        (row for row in rows if row["movement"] < 0),
        key=lambda row: (row["movement"], row["rank"]),
    )

    def tied_after(items: list[dict[str, Any]]) -> int:
        if len(items) <= limit:
            return 0
        edge = items[limit - 1]["movement"]
        return sum(1 for item in items[limit:] if item["movement"] == edge)

    return {
        "risen": risen[:limit],
        "fallen": fallen[:limit],
        "risen_more": tied_after(risen),
        "fallen_more": tied_after(fallen),
        "moved": len(risen) + len(fallen),
        "compared": len(rows),
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
