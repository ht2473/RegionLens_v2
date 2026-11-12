"""Ограничение числа обращений к API по адресу клиента, одно для всех."""

from __future__ import annotations

from typing import Any

from rest_framework.throttling import AnonRateThrottle

from apps.core.client_ip import client_ip, rate_subject


class AnonymousHourlyThrottle(AnonRateThrottle):
    """Предел числа обращений с одного адреса."""

    scope = "anon"

    def get_ident(self, request: Any) -> str:
        """
        Адрес клиента — тот же, что у остальных ограничителей сайта: из заголовка прокси,
        адрес IPv6 — сетью абонента. Иначе счёт шёл бы по ``X-Forwarded-For`` как есть.
        """
        return rate_subject(client_ip(request))
