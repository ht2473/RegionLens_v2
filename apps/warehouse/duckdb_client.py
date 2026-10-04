"""
Доступ к складу DuckDB: соединения по потокам, ограничения ресурсов, безопасные запросы.

Соединение помнит отпечаток файла и открывается заново после пересборки склада. Выборки
идут в склад или в файл набора пользователя — источник задаёт переменная контекста
(:mod:`apps.warehouse.routing`); вызывающим это незаметно.
"""

from __future__ import annotations

import logging
import os
import threading
from collections import OrderedDict
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
from django.conf import settings
from django.utils.translation import get_language

logger = logging.getLogger(__name__)

# Соединения, привязанные к потоку выполнения.
_local = threading.local()

# Открытых файлов наборов на поток не больше этого: давно не нужные закрываются.
DATASET_CONNECTIONS = 8


@dataclass(frozen=True, slots=True)
class DataSource:
    """Файл набора пользователя: путь и поколение для ключей кэша."""

    path: Path
    generation: str


# Источник выборок текущего контекста; ``None`` — склад.
_source: ContextVar[DataSource | None] = ContextVar("warehouse_source", default=None)


def current_source() -> DataSource | None:
    """Файл набора, в который идут выборки; ``None`` — склад."""
    return _source.get()


@contextmanager
def using_source(source: DataSource | None) -> Iterator[None]:
    """Направить выборки внутри блока в файл набора или (``None``) в склад."""
    token = _source.set(source)
    try:
        yield
    finally:
        _source.reset(token)


def source_generation() -> str:
    """Отпечаток источника текущих выборок: склада или файла набора — для ключей кэша."""
    source = _source.get()
    return source.generation if source is not None else warehouse_generation()


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
    """
    Вернуть соединение с источником текущих выборок для потока; по умолчанию — со складом
    только на чтение.
    """
    source = _source.get()
    if source is not None:
        return _dataset_connection(source)
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


def _dataset_connection(source: DataSource) -> duckdb.DuckDBPyConnection:
    """
    Соединение с файлом набора: только чтение, без доступа к другим файлам и сети,
    с запертыми настройками; до ``DATASET_CONNECTIONS`` на поток.
    """
    pool: OrderedDict[str, tuple[str, duckdb.DuckDBPyConnection]] | None = getattr(
        _local, "datasets", None
    )
    if pool is None:
        pool = OrderedDict()
        _local.datasets = pool
    key = str(source.path)
    held = pool.get(key)
    if held is not None and held[0] == source.generation:
        pool.move_to_end(key)
        return held[1]
    if held is not None:
        held[1].close()
        del pool[key]
    connection = duckdb.connect(
        key,
        read_only=True,
        config={
            "enable_external_access": False,
            "memory_limit": settings.USERDATA_QUERY_MEMORY,
            "threads": 1,
            "temp_directory": str(temp_directory(source.path)),
            "autoinstall_known_extensions": False,
            "autoload_known_extensions": False,
            "lock_configuration": True,
        },
    )
    pool[key] = (source.generation, connection)
    while len(pool) > DATASET_CONNECTIONS:
        _key, (_generation, oldest) = pool.popitem(last=False)
        oldest.close()
    return connection


def dataset_connection(source: DataSource) -> duckdb.DuckDBPyConnection:
    """Соединение потока с файлом набора — для чтения вне переменной контекста (выгрузка)."""
    return _dataset_connection(source)


def close_dataset(path: Path) -> None:
    """Закрыть соединение потока с файлом набора — перед удалением файла."""
    pool = getattr(_local, "datasets", None)
    if pool:
        held = pool.pop(str(path), None)
        if held is not None:
            held[1].close()


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
    """Закрыть соединения текущего потока со складом и с файлами наборов."""
    for key in ("connection_ro", "connection_rw"):
        connection = getattr(_local, key, None)
        if connection is not None:
            connection.close()
            setattr(_local, key, None)
            setattr(_local, f"{key}_generation", None)
    pool = getattr(_local, "datasets", None)
    while pool:
        _key, (_generation, connection) = pool.popitem()
        connection.close()
