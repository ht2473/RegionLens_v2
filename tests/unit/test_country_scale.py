"""
Значение по России при ненадёжном объявлении единицы (проверка данных 19.11.2026).

«Торговля в России 2023» даёт Россию в млрд руб., «Торговля в России 2025» при том же
объявлении «для значений в целом по России: млрд руб» — в млн; субъекты — в млн.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from apps.warehouse.etl.facts import reconcile_country_scale

pytestmark = pytest.mark.unit

SCHEMA = Path(__file__).resolve().parents[2] / "apps" / "warehouse" / "sql" / "schema.sql"
REGIONS = [f"R{index:02d}" for index in range(80)]
DECLARATION = "; для значений в целом по России: млрд руб"


def _warehouse(country: dict[str, float], kind: str = "currency") -> duckdb.DuckDBPyConnection:
    """Ряд с 80 субъектами по 10 млн руб. и значением России по выпускам."""
    connection = duckdb.connect(":memory:")
    connection.execute(SCHEMA.read_text(encoding="utf-8"))
    connection.executemany(
        "INSERT INTO dim_territory (territory_code, source_name, name_ru, level) "
        "VALUES (?, ?, ?, 'region')",
        [(code, code, code) for code in REGIONS],
    )
    connection.execute(
        "INSERT INTO dim_territory (territory_code, source_name, name_ru, level) "
        "VALUES ('RU', 'RU', 'Россия', 'country')"
    )
    connection.execute(
        "INSERT INTO dim_unit (unit_code, source_name, name_ru, kind) "
        "VALUES ('u', ?, 'Млн руб.', ?)",
        [DECLARATION, kind],
    )
    connection.execute(
        "INSERT INTO dim_series (series_key, indicator_code, section_code, unit_code, "
        "indicator_name_ru, full_title_ru) VALUES ('S:00', 'S', 'sec', 'u', 'Оборот', 'Оборот')"
    )
    connection.execute(
        "CREATE TABLE src AS SELECT 'S' AS indicator_code, 'Оборот: млн руб.' AS subsection, "
        "? AS indicator_unit",
        [DECLARATION],
    )
    connection.execute(
        "CREATE TABLE map_series AS SELECT 'S' AS indicator_code, "
        "'Оборот: млн руб.' AS subsection_raw, 'S:00' AS series_key"
    )
    connection.execute(
        "CREATE TABLE map_unit_scale AS SELECT ? AS source_unit, TRUE AS country_scale_unknown",
        [DECLARATION],
    )
    rows = []
    for edition, value in country.items():
        rows += [("S:00", code, 2022, edition, 10.0) for code in REGIONS]
        rows.append(("S:00", "RU", 2022, edition, value))
    connection.executemany(
        "INSERT INTO fact_vintage (series_key, territory_code, year, edition_code, edition_year, "
        "value, quality) VALUES (?, ?, ?, ?, 2023, ?, 0)",
        rows,
    )
    return connection


def _country(connection: duckdb.DuckDBPyConnection) -> dict[str, float]:
    rows = connection.execute(
        "SELECT edition_code, value FROM fact_vintage WHERE territory_code = 'RU'"
    ).fetchall()
    return dict(rows)


def test_billions_become_millions_only_where_sum_agrees() -> None:
    """Выпуск в млрд приводится к млн, выпуск, уже данный в млн, не трогается."""
    connection = _warehouse({"ed_2023": 0.8, "ed_2025": 800.0})
    assert reconcile_country_scale(connection) == 1
    assert _country(connection) == {"ed_2023": pytest.approx(800.0), "ed_2025": 800.0}


def test_mismatch_without_factor_is_left() -> None:
    """Расхождение, которое множитель не объясняет, остаётся для проверки качества."""
    connection = _warehouse({"ed_2023": 650.0})
    assert reconcile_country_scale(connection) == 0
    assert _country(connection) == {"ed_2023": 650.0}


def test_relative_series_is_not_checked() -> None:
    """Проценты не складываются по субъектам: ряд не проверяется."""
    connection = _warehouse({"ed_2023": 0.8}, kind="share")
    assert reconcile_country_scale(connection) == 0
