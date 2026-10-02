"""Ограничение числа обращений к API по адресу клиента, одно для всех."""

from __future__ import annotations

from rest_framework.throttling import AnonRateThrottle


class AnonymousHourlyThrottle(AnonRateThrottle):
    """Предел числа обращений с одного адреса."""

    scope = "anon"
