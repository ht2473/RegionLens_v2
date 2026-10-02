"""Синтетические выпуски источников для проверок сшивки: разобранные файлы, как от ``collect``."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from apps.sources.base import PARSED_COLUMNS

# Ряды синтетического набора, продолжаемые выпусками, и их показатели в бюллетене.
CPI_KEY = "Y477110111:00"
BASKET_KEY = "Y477110395:00"
UNEMPLOYMENT_KEY = "Y477110418:00"


def dataset_values(warehouse: Path, series_key: str) -> dict[tuple[str, int], float]:
    """Значения ряда в собранном складе без выпусков источников."""
    connection = duckdb.connect(str(warehouse), read_only=True)
    try:
        rows = connection.execute(
            "SELECT territory_code, year, value FROM fact_observation "
            "WHERE series_key = ? AND value IS NOT NULL",
            [series_key],
        ).fetchall()
    finally:
        connection.close()
    return {(code, int(year)): float(value) for code, year, value in rows}


def rows_for(
    measure: str,
    values: dict[tuple[str, int], float],
    *,
    kind: str,
    period: int,
    preliminary: frozenset[int] = frozenset(),
) -> list[dict[str, Any]]:
    """Строки разобранного выпуска: одно значение на территорию и год."""
    return [
        {
            "measure": measure,
            "territory_code": code,
            "year": year,
            "period_kind": kind,
            "period": period,
            "value": value,
            "hidden": False,
            "preliminary": year in preliminary,
        }
        for (code, year), value in values.items()
    ]


def write_release(
    root: Path,
    *,
    code: str,
    reference_year: int,
    published_on: date,
    rows: list[dict[str, Any]],
    source: str = "rosstat_bulletin",
    notes: list[tuple[str, str]] | None = None,
) -> None:
    """Записать разобранный выпуск в каталог ``root`` со сведениями о нём и сносками."""
    from django.test import override_settings

    from apps.sources import parsed

    frame = pd.DataFrame(rows, columns=list(PARSED_COLUMNS))
    info = parsed.ReleaseInfo(
        source=source,
        code=code,
        title=code,
        reference_year=reference_year,
        published_on=published_on.isoformat(),
        fetched_at=f"{published_on.isoformat()}T09:00:00+00:00",
        sha256=(code.replace("-", "") + "0" * 64)[:64],
        url=f"https://rosstat.gov.ru/storage/mediabank/info-stat-{code}.zip",
        parser_version=1,
    )
    with override_settings(SOURCE_PARSED_DIR=root):
        parsed.write(info, frame, notes or [])
