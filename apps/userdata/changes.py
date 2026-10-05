"""
Отчёт о различиях версии таблицы с прежней — средствами «Пересмотров»: выпуски версий
в ``fact_vintage`` файла сборки, размер изменений по тем же ступеням, что у пересмотров
склада, и самые крупные изменения со ссылкой на все версии значения.
"""

from __future__ import annotations

from typing import Any

from apps.warehouse.duckdb_client import dataset_connection
from apps.warehouse.queries.analysis import REVISION_BUCKETS

from .build import edition_code
from .models import DatasetVersion
from .scope import source_of_version

# Сколько изменений показывается списком.
LARGEST_LIMIT = 50

PAIRS_SQL = """
    WITH before AS (
        SELECT series_key, territory_code, year, value FROM fact_vintage WHERE edition_code = ?
    ),
    after AS (
        SELECT series_key, territory_code, year, value FROM fact_vintage WHERE edition_code = ?
    )
    SELECT a.series_key, a.territory_code, CAST(a.year AS INTEGER) AS year,
           b.value AS old, a.value AS new,
           CASE WHEN b.value <> 0 THEN (a.value - b.value) / abs(b.value) END AS rel
    FROM before AS b
    JOIN after AS a USING (series_key, territory_code, year)
    WHERE b.value IS NOT NULL AND a.value IS NOT NULL AND b.value <> a.value
"""
LARGEST_SQL = f"""
    {PAIRS_SQL}
    ORDER BY abs(rel) DESC NULLS LAST, abs(new - old) DESC, 1, 2, 3
    LIMIT ?
"""
BUCKETS_SQL = f"""
    SELECT
        CASE
            WHEN abs(rel) < 0.001 THEN 0
            WHEN abs(rel) < 0.01  THEN 1
            WHEN abs(rel) < 0.05  THEN 2
            WHEN abs(rel) < 0.25  THEN 3
            ELSE 4
        END AS bucket,
        count(*) AS changes
    FROM ({PAIRS_SQL}) AS pairs
    WHERE rel IS NOT NULL
    GROUP BY 1
    ORDER BY 1
"""  # noqa: S608 — текст собран из констант модуля, значения — параметрами


def report(version: DatasetVersion) -> dict[str, Any] | None:
    """Различия версии с прежней: итоги из отчёта сборки и подробности из файла."""
    summary = version.report.get("changes")
    source = source_of_version(version)
    if not summary or source is None:
        return None
    base, edition = edition_code(int(summary["base"])), edition_code(version.number)
    connection = dataset_connection(source)
    columns = ("series_key", "territory_code", "year", "old", "new", "rel")
    largest = [
        dict(zip(columns, row, strict=True))
        for row in connection.execute(LARGEST_SQL, [base, edition, LARGEST_LIMIT]).fetchall()
    ]
    counts = dict(connection.execute(BUCKETS_SQL, [base, edition]).fetchall())
    total = sum(counts.values())
    buckets = [
        {
            "label": str(label),
            "count": int(counts.get(index, 0)),
            "share": counts.get(index, 0) / total if total else 0.0,
        }
        for index, (_low, _high, label) in enumerate(REVISION_BUCKETS)
    ]
    return {**summary, "largest": largest, "buckets": buckets}
