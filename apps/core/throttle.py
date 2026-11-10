"""
Ограничение частоты обращений по адресу посетителя или иному ключу: счётчик в кэше.

Когда хранилище кэша недоступно, счёт ведёт сам процесс: предел остаётся, хотя и считается
на каждый рабочий процесс отдельно. Без этого отказ хранилища снимал бы все пределы разом —
на регистрацию, восстановление пароля, ввод кода.
"""

from __future__ import annotations

import hashlib
import threading
import time

from django.core.cache import cache
from django.http import HttpRequest

from .client_ip import client_ip

# Запасные счётчики процесса: имя — число обращений и момент конца окна.
_local: dict[str, tuple[int, float]] = {}
_local_lock = threading.Lock()
# Сколько запасных счётчиков держит процесс; при переполнении истёкшие убираются.
LOCAL_COUNTERS = 20_000


def allow(request: HttpRequest, scope: str, *, limit: int, window: int) -> bool:
    """Учесть обращение и вернуть, укладывается ли оно в ``limit`` за ``window`` секунд."""
    return allow_key(scope, client_ip(request) or "unknown", limit=limit, window=window)


def _name(scope: str, key: str) -> str:
    """Имя счётчика; ключ может быть адресом почты — в имени только его отпечаток."""
    digest = hashlib.sha256(key.strip().lower().encode()).hexdigest()[:32]
    return f"throttle:{scope}:{digest}"


def _storage_down() -> bool:
    """Хранилище кэша отказало: его ответы сейчас — заглушки, а не счётчики."""
    return bool(getattr(cache, "unavailable", False))


def _local_used(name: str) -> int:
    with _local_lock:
        count, until = _local.get(name, (0, 0.0))
        return count if time.monotonic() < until else 0


def _local_count(name: str, window: int) -> int:
    """Учесть обращение в запасном счётчике процесса и вернуть их число в окне."""
    now = time.monotonic()
    with _local_lock:
        count, until = _local.get(name, (0, 0.0))
        if now >= until:
            count, until = 0, now + window
        if name not in _local and len(_local) >= LOCAL_COUNTERS:
            for stale in [key for key, (_count, end) in _local.items() if now >= end]:
                del _local[stale]
            if len(_local) >= LOCAL_COUNTERS:
                _local.clear()
        _local[name] = (count + 1, until)
        return count + 1


def used(scope: str, key: str) -> int:
    """Сколько обращений учтено в текущем окне, не учитывая нового."""
    name = _name(scope, key)
    count = int(cache.get(name) or 0)
    if not count and _storage_down():
        return _local_used(name)
    return count


def allow_key(scope: str, key: str, *, limit: int, window: int) -> bool:
    """То же по произвольному ключу (адрес почты, учётная запись); окно фиксированное."""
    name = _name(scope, key)
    # add создаёт счётчик только если его нет, и задаёт срок жизни окна;
    # incr увеличивает существующий, не продлевая срока.
    if cache.add(name, 1, timeout=window):
        return True
    try:
        count = cache.incr(name)
    except ValueError:
        if _storage_down():
            return _local_count(name, window) <= limit
        # Счётчик истёк между add и incr — начинается новое окно.
        cache.set(name, 1, timeout=window)
        return True
    return count <= limit
