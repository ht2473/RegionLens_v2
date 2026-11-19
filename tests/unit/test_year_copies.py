"""
Копии лет в сборниках набора (проверка данных 19.11.2026).

У рядов о малом и среднем предпринимательстве строки «2011» повторяют «2023» по всем
субъектам; медленно меняющийся ряд с равными соседними годами копией не считается.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from apps.warehouse.etl.facts import drop_year_copies

pytestmark = pytest.mark.unit

SCHEMA = Path(__file__).resolve().parents[2] / "apps" / "warehouse" / "sql" / "schema.sql"
REGIONS = [f"R{index:02d}" for index in range(12)]


def _warehouse(
    series: dict[int, list[float]], editions: dict[int, str] | None = None
) -> duckdb.DuckDBPyConnection:
    """Склад с одним рядом: год → значения по 12 субъектам и стране, год → выпуск."""
    editions = editions or {}
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
    rows = []
    for year, values in series.items():
        edition = editions.get(year, "ed")
        rows += [
            ("S:00", code, year, edition, value)
            for code, value in zip(REGIONS, values, strict=True)
        ]
        rows.append(("S:00", "RU", year, edition, sum(values)))
    connection.executemany(
        "INSERT INTO fact_vintage (series_key, territory_code, year, edition_code, edition_year, "
        "value, quality) VALUES (?, ?, ?, ?, 2024, ?, 0)",
        rows,
    )
    return connection


def _years(connection: duckdb.DuckDBPyConnection) -> list[int]:
    return [
        year
        for (year,) in connection.execute(
            "SELECT DISTINCT year FROM fact_vintage ORDER BY 1"
        ).fetchall()
    ]


def _values(year: int) -> list[float]:
    """Разные значения по субъектам, свои у каждого года."""
    return [float(year * 10 + index * 7 % 11) for index in range(len(REGIONS))]


def test_detached_old_year_is_removed() -> None:
    """«2011» = «2023» при блоке 2019–2023: убирается 2011 вместе со страной."""
    series = {year: _values(year) for year in range(2019, 2024)}
    series[2011] = series[2023]
    connection = _warehouse(series)
    assert drop_year_copies(connection) == {"series": 1, "years": 1, "rows": 13}
    assert _years(connection) == [2019, 2020, 2021, 2022, 2023]


def test_copy_between_editions_is_removed() -> None:
    """«2011» сборника 2022 года = «2021» сборника 2024-го: убирается 2011 из своего выпуска."""
    series = {year: _values(year) for year in range(2019, 2024)}
    series[2011] = series[2021]
    editions = {2011: "ed_2022", 2019: "ed_2022", 2020: "ed_2022"}
    connection = _warehouse(series, editions)
    assert drop_year_copies(connection) == {"series": 1, "years": 1, "rows": 13}
    assert _years(connection) == [2019, 2020, 2021, 2022, 2023]


def test_detached_year_leaves_no_rest_in_other_editions() -> None:
    """Под оторванным годом в другом выпуске одни нули: они уходят вместе с копией."""
    series = {year: _values(year) for year in range(2019, 2024)}
    series[2011] = series[2023]
    connection = _warehouse(series)
    connection.executemany(
        "INSERT INTO fact_vintage (series_key, territory_code, year, edition_code, edition_year, "
        "value, quality) VALUES ('S:00', ?, 2011, 'ed_old', 2022, 0, 0)",
        [(code,) for code in REGIONS],
    )
    drop_year_copies(connection)
    assert _years(connection) == [2019, 2020, 2021, 2022, 2023]


def test_copy_inside_block_removes_both() -> None:
    """Копия внутри сплошного ряда лет: неизвестно, какой год верен, — убираются оба."""
    series = {year: _values(year) for year in range(2001, 2016)}
    series[2001] = series[2014]
    connection = _warehouse(series)
    assert drop_year_copies(connection)["years"] == 2
    assert 2001 not in _years(connection)
    assert 2014 not in _years(connection)


def test_steady_series_is_kept() -> None:
    """Ряд, у которого соседние годы совпадают (плотность путей), — не копия."""
    constant = _values(2000)
    series = dict.fromkeys(range(2005, 2016), constant)
    connection = _warehouse(series)
    assert drop_year_copies(connection) == {"series": 0, "years": 0, "rows": 0}
    assert len(_years(connection)) == 11


def test_close_years_are_kept() -> None:
    """Совпадение лет ближе пяти лет друг к другу не трогается."""
    series = {year: _values(year) for year in range(2019, 2024)}
    series[2017] = series[2020]
    connection = _warehouse(series)
    assert drop_year_copies(connection)["rows"] == 0
