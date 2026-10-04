"""
Маршрут выборок по ключу ряда: склад или файл набора пользователя.

Ключ «u:<код набора>:<код ряда>» ведёт в файл набора, если набор доступен тому, кто
спрашивает; решает приложение своих данных (``register``). Недоступный набор слой считает
несуществующим: выборка идёт в склад, где такого ряда нет, — даже если представление
забыло проверку. Доступ проверяется до кэша: в ключе кэша — поколение источника.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterable
from functools import wraps
from typing import Any

from .duckdb_client import DataSource, using_source, warehouse_generation

# Ключи рядов пользователей начинаются так.
USER_PREFIX = "u:"

Resolver = Callable[[str], DataSource | None]
_resolvers: list[Resolver] = []


def register(resolver: Resolver) -> None:
    """Задать, как код набора превращается в файл с проверкой доступа."""
    _resolvers[:] = [resolver]


def is_user_key(key: object) -> bool:
    """Ключ ряда пользователя: «u:<код набора>:<код ряда>»."""
    return isinstance(key, str) and key.startswith(USER_PREFIX)


def dataset_code(key: str) -> str:
    """Код набора из ключа ряда пользователя."""
    return key.removeprefix(USER_PREFIX).partition(":")[0]


def source_of(key: str) -> DataSource | None:
    """Файл набора для ключа ряда; ``None`` — склад (ключ склада или набор недоступен)."""
    if not is_user_key(key) or not _resolvers:
        return None
    return _resolvers[0](dataset_code(key))


def generation_of(values: Iterable[Any]) -> str:
    """
    Поколение склада и файлов наборов, упомянутых в параметрах расчёта (ключи рядов,
    перечни ключей через запятую): для ключа кэша расчётов.
    """
    parts = [warehouse_generation()]
    seen: set[str] = set()
    for value in values:
        for item in value if isinstance(value, list | tuple) else str(value).split(","):
            key = str(item).strip()
            if is_user_key(key) and key not in seen:
                seen.add(key)
                source = source_of(key)
                parts.append(source.generation if source else "-")
    return "|".join(parts)


def by_key[**P, R](argument: str = "series_key") -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Выборка по одному ключу ряда идёт в его источник."""

    def decorator(function: Callable[P, R]) -> Callable[P, R]:
        signature = inspect.signature(function)

        @wraps(function)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            key = signature.bind(*args, **kwargs).arguments.get(argument)
            with using_source(source_of(key) if isinstance(key, str) else None):
                return function(*args, **kwargs)

        return wrapper

    return decorator


def by_keys[**P, R](argument: str = "series_keys") -> Callable[[Callable[P, R]], Callable[P, R]]:
    """
    Выборка по перечню ключей делится по источникам; ответы — словари по ключу
    или перечни строк — сливаются в порядке источников.
    """

    def decorator(function: Callable[P, R]) -> Callable[P, R]:
        signature = inspect.signature(function)

        @wraps(function)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            bound = signature.bind(*args, **kwargs)
            keys = list(bound.arguments.get(argument) or [])
            groups = _grouped(keys)
            if len(groups) == 1:
                source, _keys = groups[0]
                with using_source(source):
                    return function(*args, **kwargs)
            merged: Any = None
            for source, part in groups:
                bound.arguments[argument] = part
                with using_source(source):
                    result: Any = function(*bound.args, **bound.kwargs)
                if merged is None:
                    merged = result
                elif isinstance(merged, dict):
                    merged = {**merged, **result}
                else:
                    merged = [*merged, *result]
            return merged

        return wrapper

    return decorator


def _grouped(keys: list[str]) -> list[tuple[DataSource | None, list[str]]]:
    """Ключи по источникам: склад — первым, наборы — в порядке появления."""
    warehouse: list[str] = []
    datasets: dict[str, tuple[DataSource, list[str]]] = {}
    for key in keys:
        source = source_of(key)
        if source is None:
            warehouse.append(key)
        else:
            datasets.setdefault(str(source.path), (source, []))[1].append(key)
    groups: list[tuple[DataSource | None, list[str]]] = []
    if warehouse or not datasets:
        groups.append((None, warehouse))
    groups.extend(datasets.values())
    return groups
