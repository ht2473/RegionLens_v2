"""Кэш на Valkey, переживающий недоступность хранилища."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable
from functools import partial
from typing import Any

from django.core.cache.backends.base import DEFAULT_TIMEOUT
from django.core.cache.backends.redis import RedisCache
from redis.exceptions import ConnectionError as ValkeyConnectionError
from redis.exceptions import TimeoutError as ValkeyTimeoutError

logger = logging.getLogger(__name__)

# Сколько секунд после отказа кэш не обращается к хранилищу.
PAUSE_AFTER_FAILURE = 10.0

FAILURES = (ValkeyConnectionError, ValkeyTimeoutError, OSError)


class ResilientValkeyCache(RedisCache):
    """
    Встроенный кэш Redis/Valkey, у которого отказ хранилища — это промах.

    Содержимое кэша вычисляется заново, поэтому недоступный Valkey не даёт ошибку 500.
    """

    def __init__(self, server: str, params: dict[str, Any]) -> None:
        super().__init__(server, params)
        self._paused_until = 0.0

    def _guard[T](self, action: Callable[[], T], fallback: T) -> T:
        """Выполнить обращение к хранилищу, заменив отказ запасным значением."""
        if time.monotonic() < self._paused_until:
            return fallback
        try:
            return action()
        except FAILURES as error:
            self._paused_until = time.monotonic() + PAUSE_AFTER_FAILURE
            logger.warning("Кэш недоступен, работа продолжается без него: %s", error)
            return fallback

    def add(
        self,
        key: str,
        value: Any,
        timeout: float | None = DEFAULT_TIMEOUT,
        version: int | None = None,
    ) -> bool:
        return self._guard(partial(RedisCache.add, self, key, value, timeout, version), False)

    def get(self, key: str, default: Any = None, version: int | None = None) -> Any:
        return self._guard(partial(RedisCache.get, self, key, default, version), default)

    def set(
        self,
        key: str,
        value: Any,
        timeout: float | None = DEFAULT_TIMEOUT,
        version: int | None = None,
    ) -> None:
        self._guard(partial(RedisCache.set, self, key, value, timeout, version), None)

    def touch(
        self, key: str, timeout: float | None = DEFAULT_TIMEOUT, version: int | None = None
    ) -> bool:
        return self._guard(partial(RedisCache.touch, self, key, timeout, version), False)

    def delete(self, key: str, version: int | None = None) -> bool:
        return self._guard(partial(RedisCache.delete, self, key, version), False)

    def get_many(self, keys: Iterable[str], version: int | None = None) -> dict[str, Any]:
        return self._guard(partial(RedisCache.get_many, self, keys, version), {})

    def has_key(self, key: str, version: int | None = None) -> bool:
        return self._guard(partial(RedisCache.has_key, self, key, version), False)

    def incr(self, key: str, delta: int = 1, version: int | None = None) -> int:
        # Недоступный счётчик — то же, что отсутствующий.
        missing = object()
        result = self._guard(partial(RedisCache.incr, self, key, delta, version), missing)
        if result is missing:
            raise ValueError(f"Ключ {key!r} недоступен")
        return result  # type: ignore[return-value]

    def set_many(
        self,
        data: dict[str, Any],
        timeout: float | None = DEFAULT_TIMEOUT,
        version: int | None = None,
    ) -> list[str]:
        return self._guard(partial(RedisCache.set_many, self, data, timeout, version), list(data))

    def delete_many(self, keys: Iterable[str], version: int | None = None) -> None:
        self._guard(partial(RedisCache.delete_many, self, keys, version), None)

    def clear(self) -> None:
        self._guard(partial(RedisCache.clear, self), None)
