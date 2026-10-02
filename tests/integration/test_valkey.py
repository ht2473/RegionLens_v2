"""
Проверки с настоящим Valkey: счётчики ограничителей и сроки ключей.

Адрес — ``VALKEY_TEST_URL`` (без него проверки пропускаются), база 15.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest
import redis

from apps.core.cache import ResilientValkeyCache

pytestmark = pytest.mark.integration

TEST_DATABASE = 15


@pytest.fixture(scope="module")
def valkey_url() -> str:
    """Адрес Valkey для проверок или пропуск модуля."""
    url = os.environ.get("VALKEY_TEST_URL", "").rstrip("/")
    if not url:
        pytest.skip("VALKEY_TEST_URL не задан")
    try:
        redis.Redis.from_url(f"{url}/{TEST_DATABASE}", socket_connect_timeout=0.5).ping()
    except redis.RedisError:
        pytest.skip("Valkey недоступен")
    return url


@pytest.fixture
def valkey_cache(valkey_url: str) -> Iterator[ResilientValkeyCache]:
    """Кэш на отдельной базе Valkey со своим префиксом ключей."""
    cache = ResilientValkeyCache(
        f"{valkey_url}/{TEST_DATABASE}",
        {"KEY_PREFIX": f"test-{uuid.uuid4().hex[:8]}", "TIMEOUT": 60},
    )
    yield cache
    redis.Redis.from_url(f"{valkey_url}/{TEST_DATABASE}").flushdb()


def test_counter_is_atomic(valkey_cache: ResilientValkeyCache) -> None:
    """Счётчик ограничителя создаётся один раз и растёт на хранилище, а не в процессе."""
    assert valkey_cache.add("hits", 1, timeout=30) is True
    assert valkey_cache.add("hits", 1, timeout=30) is False
    assert valkey_cache.incr("hits") == 2
    assert valkey_cache.get("hits") == 2


def test_keys_have_expiry(valkey_url: str, valkey_cache: ResilientValkeyCache) -> None:
    """
    У каждого ключа кэша есть срок жизни.

    Ключ без срока жил бы до вытеснения при нехватке памяти, и сводка по складу,
    собранному год назад, отдавалась бы и после пересборки.
    """
    valkey_cache.set("summary", {"observations": 1})
    client = redis.Redis.from_url(f"{valkey_url}/{TEST_DATABASE}")
    key = valkey_cache.make_key("summary")

    assert 0 < client.ttl(key) <= 60
