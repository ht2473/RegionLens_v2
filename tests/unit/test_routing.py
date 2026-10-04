"""
Слой рядов: выборка по ключу «u:» идёт в файл набора, недоступный набор — в склад,
перечни ключей делятся по источникам, кэш помнит поколение источника.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import duckdb
import pytest

from apps.warehouse import duckdb_client, routing
from apps.warehouse.duckdb_client import DataSource
from apps.warehouse.queries import series_values, year_counts

SCHEMA = """
CREATE TABLE fact_observation (
    series_key VARCHAR, territory_code VARCHAR, territory_level VARCHAR,
    is_aggregate BOOLEAN, year SMALLINT, value DOUBLE
)
"""


def _file(path: Path, key: str, value: float) -> Path:
    connection = duckdb.connect(str(path))
    connection.execute(SCHEMA)
    connection.execute(
        "INSERT INTO fact_observation VALUES (?, 'RU-TA', 'region', false, 2020, ?)",
        [key, value],
    )
    connection.close()
    return path


@pytest.fixture
def sources(tmp_path: Path, settings: Any) -> Iterator[dict[str, DataSource | None]]:
    """Склад и два файла наборов; доступность набора задаёт словарь."""
    settings.DUCKDB_PATH = _file(tmp_path / "warehouse.duckdb", "W:00", 1.0)
    available: dict[str, DataSource | None] = {
        "aaa": DataSource(_file(tmp_path / "a.duckdb", "u:aaa:1", 2.0), "a1"),
        "bbb": DataSource(_file(tmp_path / "b.duckdb", "u:bbb:1", 3.0), "b1"),
    }
    previous = list(routing._resolvers)
    routing.register(available.get)
    duckdb_client.close_connections()
    yield available
    duckdb_client.close_connections()
    routing._resolvers[:] = previous


class TestRouting:
    def test_key_parts(self) -> None:
        assert routing.is_user_key("u:abc:def")
        assert not routing.is_user_key("Y477110461:00")
        assert routing.dataset_code("u:abc:def-p100k") == "abc"

    def test_single_key_goes_to_its_file(self, sources: dict[str, Any]) -> None:
        assert year_counts("u:aaa:1") == {2020: 1}
        assert year_counts("W:00") == {2020: 1}

    def test_unavailable_dataset_looks_absent(self, sources: dict[str, Any]) -> None:
        sources["aaa"] = None
        assert year_counts("u:aaa:1") == {}

    def test_mixed_keys_are_merged(self, sources: dict[str, Any]) -> None:
        values = series_values(["W:00", "u:aaa:1", "u:bbb:1"], ["RU-TA"])
        assert values == {
            "W:00": {"RU-TA": {2020: 1.0}},
            "u:aaa:1": {"RU-TA": {2020: 2.0}},
            "u:bbb:1": {"RU-TA": {2020: 3.0}},
        }

    def test_cache_key_carries_source(self, sources: dict[str, Any], tmp_path: Path) -> None:
        assert series_values(["u:aaa:1"], ["RU-TA"])["u:aaa:1"]["RU-TA"][2020] == 2.0
        # Новая сборка набора — новое поколение: прежний ответ кэша не подставляется.
        sources["aaa"] = DataSource(_file(tmp_path / "a2.duckdb", "u:aaa:1", 5.0), "a2")
        assert series_values(["u:aaa:1"], ["RU-TA"])["u:aaa:1"]["RU-TA"][2020] == 5.0

    def test_generation_of_parameters(self, sources: dict[str, Any]) -> None:
        plain = routing.generation_of(["W:00"])
        mixed = routing.generation_of(["W:00,u:aaa:1"])
        assert mixed.startswith(plain)
        assert mixed.endswith("|a1")

    def test_dataset_connection_is_locked(self, sources: dict[str, Any], tmp_path: Path) -> None:
        (tmp_path / "other.csv").write_text("a\n1\n")
        connection = duckdb_client.dataset_connection(sources["aaa"])
        with pytest.raises(duckdb.Error):
            connection.execute(f"SELECT * FROM read_csv('{tmp_path / 'other.csv'}')")
        with pytest.raises(duckdb.Error):
            connection.execute("SET enable_external_access = true")
        with pytest.raises(duckdb.Error):
            connection.execute("CREATE TABLE x AS SELECT 1")
