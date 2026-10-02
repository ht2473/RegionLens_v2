"""
Доступ к складу DuckDB: соединения по потокам, ограничения ресурсов, безопасные запросы.

Соединение помнит отпечаток файла и открывается заново после пересборки склада.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import duckdb
from django.conf import settings
from django.utils.translation import get_language

logger = logging.getLogger(__name__)

# Соединения, привязанные к потоку выполнения.
_local = threading.local()


class WarehouseError(RuntimeError):
    """Ошибка обращения к аналитическому складу."""


class WarehouseNotBuiltError(WarehouseError):
    """Склад ещё не собран: файл базы отсутствует."""

    def __init__(self, path: Path) -> None:
        super().__init__(
            f"Аналитический склад не найден: {path}. "
            "Выполните команду: python manage.py etl_build --full"
        )


def _configure(connection: duckdb.DuckDBPyConnection) -> None:
    """Применить к соединению ограничения ресурсов из настроек проекта."""
    connection.execute(f"SET memory_limit = '{settings.DUCKDB_MEMORY_LIMIT}'")
    connection.execute(f"SET threads = {int(settings.DUCKDB_THREADS)}")
    # Без автозагрузки расширений: она обращалась бы к сети.
    connection.execute("SET autoinstall_known_extensions = false")
    connection.execute("SET autoload_known_extensions = false")


def temp_directory(path: Path) -> Path:
    """
    Каталог выгрузки на диск для текущего процесса.

    По умолчанию все процессы, открывшие файл склада, выгружают в общий ``<файл>.tmp``
    файлы с одинаковыми именами и при одновременной выгрузке падают в DuckDB
    (нарушение доступа). DuckDB удаляет свой каталог при закрытии базы.
    """
    root = path.parent / f"{path.name}.tmp"
    root.mkdir(parents=True, exist_ok=True)
    return root / str(os.getpid())


def warehouse_generation(path: Path | None = None) -> str:
    """
    Отпечаток текущего файла склада: время изменения и размер.

    Одинаков во всех процессах и меняется ровно при пересборке; им помечаются соединения
    и ключи кэша.
    """
    target = path or settings.DUCKDB_PATH
    try:
        stat = target.stat()
    except FileNotFoundError:
        return "absent"
    return f"{stat.st_mtime_ns:x}-{stat.st_size:x}"


def get_connection(*, read_only: bool | None = None) -> duckdb.DuckDBPyConnection:
    """Вернуть соединение со складом для текущего потока; по умолчанию — только на чтение."""
    path: Path = settings.DUCKDB_PATH
    if not path.exists():
        raise WarehouseNotBuiltError(path)

    readonly = settings.DUCKDB_READ_ONLY if read_only is None else read_only
    cache_key = "connection_ro" if readonly else "connection_rw"
    generation = warehouse_generation(path)

    connection = getattr(_local, cache_key, None)
    # На Linux заменённый файл живёт, пока его держат открытым.
    if connection is not None and getattr(_local, f"{cache_key}_generation", None) != generation:
        connection.close()
        connection = None
    if connection is None:
        logger.debug("Открытие соединения DuckDB", extra={"path": str(path), "read_only": readonly})
        connection = duckdb.connect(
            str(path),
            read_only=readonly,
            config={"temp_directory": str(temp_directory(path))},
        )
        _configure(connection)
        setattr(_local, cache_key, connection)
        setattr(_local, f"{cache_key}_generation", generation)
    return connection


@contextmanager
def writable_connection(path: Path | None = None) -> Iterator[duckdb.DuckDBPyConnection]:
    """Открыть отдельное некэшируемое соединение на запись с пределом памяти сборки."""
    target = path or settings.DUCKDB_PATH
    target.parent.mkdir(parents=True, exist_ok=True)

    connection = duckdb.connect(str(target), read_only=False)
    try:
        _configure(connection)
        connection.execute(f"SET memory_limit = '{settings.DUCKDB_BUILD_MEMORY_LIMIT}'")
        yield connection
    finally:
        connection.close()


def fetch_dicts(sql: str, params: Sequence[Any] | None = None) -> list[dict[str, Any]]:
    """
    Выполнить запрос и вернуть строки в виде словарей.

    При паре столбцов ``X_ru`` и ``X_en`` добавляется ``X`` на языке запроса.
    """
    connection = get_connection()
    cursor = connection.execute(sql, params or [])
    columns = [column[0] for column in cursor.description]
    rows = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]

    bilingual = [
        column[: -len("_ru")]
        for column in columns
        if column.endswith("_ru") and f"{column[: -len('_ru')]}_en" in columns
    ]
    if not bilingual:
        return rows

    prefer_english = get_language() == "en"
    for item in rows:
        for base in bilingual:
            # Пустой перевод не подменяет оригинал.
            translated = item.get(f"{base}_en") if prefer_english else None
            item[base] = translated or item.get(f"{base}_ru")
    return rows


def fetch_one(sql: str, params: Sequence[Any] | None = None) -> tuple[Any, ...] | None:
    """Выполнить запрос и вернуть первую строку результата или ``None``."""
    connection = get_connection()
    return connection.execute(sql, params or []).fetchone()


def require_row(row: tuple[Any, ...] | None, context: str = "") -> tuple[Any, ...]:
    """Вернуть строку запроса; её отсутствие — ошибка с понятным сообщением."""
    if row is None:
        raise WarehouseError(f"Запрос не вернул ни одной строки{': ' + context if context else ''}")
    return row


def fetch_row(sql: str, params: Sequence[Any] | None = None) -> tuple[Any, ...]:
    """Выполнить запрос, который обязан вернуть ровно одну строку."""
    return require_row(fetch_one(sql, params), sql.strip().splitlines()[0].strip())


def fetch_scalar(sql: str, params: Sequence[Any] | None = None, default: Any = None) -> Any:
    """Выполнить запрос и вернуть первое значение первой строки."""
    row = fetch_one(sql, params)
    if row is None or row[0] is None:
        return default
    return row[0]


def placeholders(count: int) -> str:
    """Построить строку подстановок вида ``?, ?, ?`` для оператора IN."""
    if count < 1:
        raise ValueError("Список значений для оператора IN не может быть пустым")
    return ", ".join(["?"] * count)


def validate_identifier(name: str, allowed: frozenset[str]) -> str:
    """Проверить имя столбца или таблицы по белому списку: параметром его не передать."""
    if name not in allowed:
        raise WarehouseError(f"Недопустимый идентификатор: {name!r}")
    return name


def table_exists(name: str) -> bool:
    """Проверить наличие таблицы или представления в складе."""
    result = fetch_one(
        "SELECT 1 FROM information_schema.tables WHERE table_name = ? LIMIT 1",
        [name],
    )
    return result is not None


def close_connections() -> None:
    """Закрыть соединения текущего потока."""
    for key in ("connection_ro", "connection_rw"):
        connection = getattr(_local, key, None)
        if connection is not None:
            connection.close()
            setattr(_local, key, None)
            setattr(_local, f"{key}_generation", None)
