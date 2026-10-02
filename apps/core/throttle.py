"""Ограничение частоты обращений по адресу посетителя или иному ключу: счётчик в кэше."""

from __future__ import annotations

import hashlib

from django.core.cache import cache
from django.http import HttpRequest

from .client_ip import client_ip


def allow(request: HttpRequest, scope: str, *, limit: int, window: int) -> bool:
    """Учесть обращение и вернуть, укладывается ли оно в ``limit`` за ``window`` секунд."""
    return allow_key(scope, client_ip(request) or "unknown", limit=limit, window=window)


def _name(scope: str, key: str) -> str:
    """Имя счётчика; ключ может быть адресом почты — в имени только его отпечаток."""
    digest = hashlib.sha256(key.strip().lower().encode()).hexdigest()[:32]
    return f"throttle:{scope}:{digest}"


def used(scope: str, key: str) -> int:
    """Сколько обращений учтено в текущем окне, не учитывая нового."""
    return int(cache.get(_name(scope, key)) or 0)


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
        # Счётчик истёк между add и incr — начинается новое окно.
        cache.set(name, 1, timeout=window)
        return True
    return count <= limit
