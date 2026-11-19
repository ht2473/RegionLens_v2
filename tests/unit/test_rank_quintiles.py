"""
Квинтили витрины рангов: как у ntile(5), но равные значения — в одном квинтиле.

Без этого порядок внутри равных выбирал движок, и квинтиль региона на границе менялся
от сборки к сборке (проверка данных 18.11.2026: 5854 пары «ряд × год»).
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from apps.warehouse.etl.marts import build_ranks

pytestmark = pytest.mark.unit

SCHEMA = Path(__file__).resolve().parents[2] / "apps" / "warehouse" / "sql" / "schema.sql"


def _ranks(values: dict[str, float]) -> dict[str, tuple[int, int]]:
    connection = duckdb.connect(":memory:")
    try:
        connection.execute(SCHEMA.read_text(encoding="utf-8"))
        connection.executemany(
            "INSERT INTO fact_observation (series_key, indicator_code, section_code, "
            "territory_code, territory_level, year, value, quality) "
            "VALUES ('S:00', 'S', 'sec', ?, 'region', 2024, ?, 0)",
            list(values.items()),
        )
        build_ranks(connection)
        rows = connection.execute(
            "SELECT territory_code, rank_desc, quintile FROM mart_rank"
        ).fetchall()
    finally:
        connection.close()
    return {code: (rank, quintile) for code, rank, quintile in rows}


def _ntile(count: int) -> list[int]:
    """Квинтили ntile(5) по позиции 0..count-1: первые группы на одну строку больше."""
    base, extra = divmod(count, 5)
    return [
        position // (base + 1) + 1
        if position < (base + 1) * extra
        else (position - (base + 1) * extra) // base + extra + 1
        for position in range(count)
    ]


@pytest.mark.parametrize("count", [3, 5, 7, 82, 85])
def test_without_ties_as_ntile(count: int) -> None:
    """Без равных значений квинтили совпадают с ntile(5)."""
    values = {f"R{index:02d}": float(count - index) for index in range(count)}
    ranks = _ranks(values)
    expected = _ntile(count)
    assert [ranks[f"R{index:02d}"][1] for index in range(count)] == expected


def test_ties_share_quintile() -> None:
    """Равные значения на границе квинтилей получают квинтиль первого из них."""
    # Десять регионов: по два в квинтиле; R1 и R2 равны и стоят на границе 1-го и 2-го.
    values = {f"R{index}": float(10 - index) for index in range(10)}
    values["R2"] = values["R1"]
    ranks = _ranks(values)
    assert ranks["R1"] == ranks["R2"] == (2, 1)
    assert ranks["R3"][1] == 2
