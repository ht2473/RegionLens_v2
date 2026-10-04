"""
Выбранная таблица для распознавания: небольшая — строками в памяти, большая — началом
строк и сводкой по каждому столбцу всей таблицы (DuckDB в памяти процесса).

Сводка большой таблицы считается один раз и хранится в отчёте версии.
"""

from __future__ import annotations

import csv
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb
from django.conf import settings

from . import ingest

# Таблица меньше этого читается в память целиком; больше — сводкой DuckDB.
SMALL_TABLE_BYTES = 8 * 1024 * 1024
# Сколько строк начала большой таблицы нужно для шапки, ролей и образца.
HEAD_ROWS = 400
# Значения столбца перечисляются, если их не больше этого: территории, разрезы, показатели.
DISTINCT_LIMIT = 5000
BOM = chr(0xFEFF)


@dataclass(slots=True)
class Loaded:
    """Строки таблицы и, у большой таблицы, сводка столбцов по всем строкам."""

    rows: list[list[Any]]
    complete: bool
    total: int
    # Номер столбца → значение → число строк; у столбца с множеством значений — пусто.
    values: dict[int, dict[str, int]] = field(default_factory=dict)
    distinct: dict[int, int] = field(default_factory=dict)
    _counted: dict[tuple[int, int], dict[str, int]] = field(default_factory=dict, repr=False)

    def column_values(self, column: int, start: int = 0) -> dict[str, int]:
        """Значения столбца с числом строк: по всей таблице или по строкам в памяти."""
        key = (column, start)
        if not self.complete and column in self.values:
            if key not in self._counted:
                # Сводка считает и строки шапки: их значения вычитаются.
                summary = dict(self.values[column])
                for row in self.rows[:start]:
                    text = ingest.cell_text(row[column]).strip() if column < len(row) else ""
                    if summary.get(text, 0) > 1:
                        summary[text] -= 1
                    else:
                        summary.pop(text, None)
                self._counted[key] = summary
            return self._counted[key]
        if key not in self._counted:
            counted: dict[str, int] = {}
            for row in self.rows[start:]:
                if column < len(row):
                    text = ingest.cell_text(row[column]).strip()
                    if text:
                        counted[text] = counted.get(text, 0) + 1
            self._counted[key] = counted
        return self._counted[key]


def is_large(path: Path, table: ingest.TableInfo) -> bool:
    """Таблица разбирается сводкой DuckDB, а не строками в памяти."""
    if table.kind == ingest.CSV:
        return path.stat().st_size > SMALL_TABLE_BYTES
    return table.kind == ingest.PARQUET and (table.rows or 0) > HEAD_ROWS * 50


def load(path: Path, table: ingest.TableInfo, profile: dict[str, Any] | None = None) -> Loaded:
    """Таблица для распознавания; ``profile`` — сводка большой таблицы, посчитанная раньше."""
    if not is_large(path, table):
        rows = [list(row) for row in ingest.iter_rows(path, table)]
        return Loaded(rows=rows, complete=True, total=len(rows))
    head = [list(row) for row in ingest.iter_rows(path, table, limit=HEAD_ROWS)]
    profile = profile or summarize(path, table)
    return Loaded(
        rows=head,
        complete=False,
        total=int(profile["rows"]),
        values={int(key): value for key, value in profile["values"].items()},
        distinct={int(key): int(value) for key, value in profile["distinct"].items()},
    )


def summarize(path: Path, table: ingest.TableInfo) -> dict[str, Any]:
    """Сводка большой таблицы: число строк, разных значений и сами значения по столбцам."""
    started = time.perf_counter()
    connection = parse_connection(path)
    try:
        columns = read_into(connection, path, table)
        quoted = [f'"{name}"' for name in columns]
        approximate = ", ".join(f"approx_count_distinct({name})" for name in quoted)
        counts = connection.execute(f"SELECT count(*), {approximate} FROM t").fetchone()  # noqa: S608
        assert counts is not None
        values: dict[str, dict[str, int]] = {}
        distinct: dict[str, int] = {}
        for index, name in enumerate(quoted):
            distinct[str(index)] = int(counts[index + 1])
            if counts[index + 1] <= DISTINCT_LIMIT * 1.1:
                rows = connection.execute(
                    f"SELECT CAST({name} AS VARCHAR) AS v, count(*) FROM t "  # noqa: S608
                    f"WHERE {name} IS NOT NULL GROUP BY v ORDER BY v"
                ).fetchall()
                if len(rows) <= DISTINCT_LIMIT:
                    values[str(index)] = {str(value).strip(): int(count) for value, count in rows}
                    distinct[str(index)] = len(rows)
        # Шапка Parquet — названия столбцов, а не строка таблицы.
        total = int(counts[0]) + (table.kind == ingest.PARQUET)
    finally:
        connection.close()
    return {
        "rows": total,
        "values": values,
        "distinct": distinct,
        "seconds": round(time.perf_counter() - started, 2),
    }


def parse_connection(path: Path) -> duckdb.DuckDBPyConnection:
    """Соединение DuckDB в памяти для разбора таблицы: пределы памяти и свой каталог выгрузки."""
    connection = duckdb.connect()
    connection.execute(f"SET memory_limit = '{settings.USERDATA_PARSE_MEMORY}'")
    connection.execute("SET threads = 2")
    connection.execute("SET enable_progress_bar = false")
    connection.execute("SET preserve_insertion_order = false")
    connection.execute("SET temp_directory = ?", [str(_temp_directory(path))])
    return connection


def read_into(
    connection: duckdb.DuckDBPyConnection, path: Path, table: ingest.TableInfo
) -> list[str]:
    """
    Прочитать таблицу в таблицу ``t`` соединения: CSV — текстом без шапки, Parquet — как есть.
    Возвращает имена столбцов ``t``.
    """
    if table.kind == ingest.PARQUET:
        connection.execute("CREATE TABLE t AS SELECT * FROM read_parquet(?)", [str(path)])
    else:
        reader = (
            "CREATE TABLE t AS SELECT * FROM read_csv(?, header = false, all_varchar = true, "
            "delim = ?, quote = '\"', escape = '\"', null_padding = true, strict_mode = false"
        )
        arguments = [str(path), table.delimiter or ";"]
        try:
            connection.execute(reader + ")", arguments)
        except duckdb.Error:
            # Перевод строки внутри кавычек параллельный разбор не принимает.
            connection.execute(reader + ", parallel = false)", arguments)
    return [row[0] for row in connection.execute("DESCRIBE t").fetchall()]


def transcode(source: Path, encoding: str, target: Path) -> Path:
    """Текст таблицы в UTF-8 без метки порядка байтов: так его читают и DuckDB, и сборка."""
    with (
        source.open("r", encoding=encoding, errors="replace", newline="") as reader,
        target.open("w", encoding="utf-8", newline="") as writer,
    ):
        first = True
        while chunk := reader.read(1 << 20):
            writer.write(chunk.removeprefix(BOM) if first else chunk)
            first = False
    return target


def _temp_directory(path: Path) -> Path:
    """Свой каталог выгрузки на процесс: общий роняет DuckDB при одновременной работе."""
    directory = path.parent / ".duckdb-tmp" / str(os.getpid())
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def write_csv(rows: list[list[Any]], target: Path) -> None:
    """Записать строки таблицей CSV в UTF-8 с «;»."""
    with target.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle, delimiter=";").writerows(rows)
