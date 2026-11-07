"""Разбор большой таблицы в DuckDB: имена столбцов из файла и закрытое соединение."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from apps.userdata import ingest, tables

pytestmark = pytest.mark.unit


def _parquet(target: Path, columns: dict[str, str]) -> ingest.TableInfo:
    """Записать Parquet в одну строку с заданными именами столбцов и значениями."""
    listed = ", ".join(f"'{value}' AS {tables.identifier(name)}" for name, value in columns.items())
    duckdb.execute(f"COPY (SELECT {listed}) TO ? (FORMAT parquet)", [str(target)])
    return ingest.TableInfo(
        key="data.parquet", name="data.parquet", kind=ingest.PARQUET, size=target.stat().st_size
    )


def test_column_name_is_not_part_of_the_query(tmp_path: Path) -> None:
    """
    Имя столбца с кавычкой остаётся именем: запрос из него не собирается.

    Имена столбцов Parquet задаёт тот, кто прислал файл; без удвоения кавычки имя
    вида «a"), (SELECT …), ("b» выполнялось бы как часть запроса.
    """
    secret = tmp_path / "secret.txt"
    secret.write_text("не для чтения", encoding="utf-8")
    hostile = f"a\"), (SELECT content FROM read_text('{secret.as_posix()}')), (\"b"
    table = _parquet(tmp_path / "data.parquet", {hostile: "RU-MOW", "2024": "1,5"})

    summary = tables.summarize(tmp_path / "data.parquet", table)

    assert summary["values"] == {"0": {"RU-MOW": 1}, "1": {"1,5": 1}}
    assert "не для чтения" not in str(summary)


def test_column_named_v_is_summarized(tmp_path: Path) -> None:
    """Столбец с именем «v» не путается с подписью выражения в запросе сводки."""
    table = _parquet(tmp_path / "data.parquet", {"region": "RU-TA", "v": "7"})

    summary = tables.summarize(tmp_path / "data.parquet", table)

    assert summary["values"] == {"0": {"RU-TA": 1}, "1": {"7": 1}}


def test_connection_is_closed_to_files_after_reading(tmp_path: Path) -> None:
    """Прочитав таблицу, соединение больше не открывает файлы и не снимает запрет."""
    path = tmp_path / "data.parquet"
    table = _parquet(path, {"region": "RU-TA"})
    connection = tables.parse_connection(path)
    try:
        tables.read_into(connection, path, table)
        with pytest.raises(duckdb.PermissionException):
            connection.execute("SELECT * FROM read_text(?)", [str(path)]).fetchall()
        with pytest.raises(duckdb.Error):
            connection.execute("SET enable_external_access = true")
    finally:
        connection.close()
