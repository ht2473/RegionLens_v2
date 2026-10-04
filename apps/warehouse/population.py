"""
Среднегодовая численность населения: ВРП, делённый на ВРП на душу, из поздних версий.

Это знаменатель Росстата для ВРП на душу. Им делятся ряды проекта на жителя при сборке
склада и суммы своих данных пользователей; единицы сводит множитель справочника.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import duckdb

if TYPE_CHECKING:  # pragma: no cover - только для проверки типов
    import pandas as pd

    from apps.sources.registry import Registry

POPULATION_SQL = """
    WITH ranked AS (
        SELECT v.series_key, v.territory_code, v.year, v.value,
               row_number() OVER (
                   PARTITION BY v.series_key, v.territory_code, v.year
                   ORDER BY (v.value IS NULL) ASC, e.edition_rank DESC
               ) AS priority
        FROM fact_vintage AS v
        JOIN dim_edition  AS e ON e.edition_code = v.edition_code
        WHERE v.series_key IN (?, ?)
    )
    SELECT territory_code, CAST(year AS INTEGER) AS year,
           max(value) FILTER (WHERE series_key = ?) AS total,
           max(value) FILTER (WHERE series_key = ?) AS per_capita
    FROM ranked
    WHERE priority = 1
    GROUP BY 1, 2
    ORDER BY 1, 2
"""


def population_frame(connection: duckdb.DuckDBPyConnection, book: Registry) -> pd.DataFrame:
    """Численность по территориям и годам: ``territory_code``, ``year``, ``population``."""
    frame = connection.execute(
        POPULATION_SQL,
        [
            book.population_total,
            book.population_per_capita,
            book.population_total,
            book.population_per_capita,
        ],
    ).df()
    frame = frame.dropna(subset=["total", "per_capita"])
    frame = frame[frame["per_capita"] != 0]
    frame["population"] = frame["total"] / frame["per_capita"] * book.population_scale
    return frame[["territory_code", "year", "population"]].reset_index(drop=True)
