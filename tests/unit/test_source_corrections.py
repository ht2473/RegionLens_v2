"""Поправки к исходному набору: файл согласован, сборка меняет только записанную ошибку."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb
import pytest

from apps.catalog.constants import ValueFlag, ValueQuality
from apps.warehouse.etl import facts

REFERENCE = Path(__file__).resolve().parents[2] / "data" / "reference"
ITEMS: list[dict[str, Any]] = json.loads(
    (REFERENCE / "source_corrections.json").read_text(encoding="utf-8")
)["corrections"]


def test_file_is_consistent() -> None:
    """Каждая поправка меняет значение, названа её причина и источник, повторов нет."""
    territories = json.loads((REFERENCE / "territories.json").read_text(encoding="utf-8"))
    codes = {territories["country"]["code"]} | {
        item["code"]
        for group in ("regions", "federal_districts", "aggregates")
        for item in territories[group]
    }
    keys = [(item["series"], item["territory"], item["year"]) for item in ITEMS]
    assert len(keys) == len(set(keys))
    for item in ITEMS:
        assert item["territory"] in codes
        assert item["value"] != item["was"]
        assert item["source"].startswith("Регионы России")
        assert item["reason"]


@pytest.fixture
def warehouse(tmp_path: Path, settings: Any) -> duckdb.DuckDBPyConnection:
    """Две версии значения, скрытое значение и файл поправок к ним."""
    settings.REFERENCE_DIR = tmp_path
    corrections = [
        {"series": "S:00", "territory": "RU", "year": 2010, "was": 4.5, "value": 59.5},
        {"series": "S:00", "territory": "RU-PSK", "year": 2024, "was": None, "value": 1.7},
        {"series": "S:00", "territory": "RU-MUR", "year": 2020, "was": 9.0, "value": 3.0},
    ]
    (tmp_path / facts.CORRECTIONS_FILE).write_text(
        json.dumps({"corrections": corrections}), encoding="utf-8"
    )
    connection = duckdb.connect()
    connection.execute(
        "CREATE TABLE fact_vintage (series_key VARCHAR, territory_code VARCHAR, year SMALLINT, "
        "edition_code VARCHAR, edition_year SMALLINT, value DOUBLE, quality TINYINT, flags TINYINT)"
    )
    connection.executemany(
        "INSERT INTO fact_vintage VALUES (?, ?, ?, 'e', ?, ?, ?, 0)",
        [
            ("S:00", "RU", 2010, 2023, 59.0, ValueQuality.OBSERVED),
            ("S:00", "RU", 2010, 2025, 4.5, ValueQuality.OBSERVED),
            ("S:00", "RU-PSK", 2024, 2025, None, ValueQuality.HIDDEN),
            ("S:00", "RU-MUR", 2020, 2025, 3.0, ValueQuality.OBSERVED),
        ],
    )
    return connection


def _row(connection: duckdb.DuckDBPyConnection, territory: str, year: int) -> tuple[Any, ...]:
    found = connection.execute(
        "SELECT value, quality, flags FROM fact_vintage "
        "WHERE territory_code = ? AND year = ? ORDER BY edition_year DESC",
        [territory, year],
    ).fetchone()
    assert found is not None
    return found


def test_apply_changes_only_recorded_error(warehouse: duckdb.DuckDBPyConnection) -> None:
    """Ошибочная версия исправлена с признаком, соседняя версия того же года цела."""
    assert facts.apply_corrections(warehouse) == {"applied": 2, "stale": 1}
    assert _row(warehouse, "RU", 2010) == (59.5, ValueQuality.OBSERVED, ValueFlag.CORRECTED)
    earlier = warehouse.execute(
        "SELECT value, flags FROM fact_vintage WHERE territory_code = 'RU' AND edition_year = 2023"
    ).fetchone()
    assert earlier == (59.0, 0)


def test_hidden_value_becomes_observed(warehouse: duckdb.DuckDBPyConnection) -> None:
    """Значение, потерянное при сдвиге строк, — обычное наблюдение с признаком поправки."""
    facts.apply_corrections(warehouse)
    assert _row(warehouse, "RU-PSK", 2024) == (1.7, ValueQuality.OBSERVED, ValueFlag.CORRECTED)


def test_fixed_by_supplier_is_stale(warehouse: duckdb.DuckDBPyConnection) -> None:
    """Поставщик исправил значение сам: поправка не применяется и попадает в отчёт."""
    facts.apply_corrections(warehouse)
    assert _row(warehouse, "RU-MUR", 2020) == (3.0, ValueQuality.OBSERVED, 0)
