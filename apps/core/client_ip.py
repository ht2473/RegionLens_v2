"""Адрес посетителя за обратным прокси."""

from __future__ import annotations

from django.conf import settings
from django.http import HttpRequest


def client_ip(request: HttpRequest | None) -> str | None:
    """
    Адрес отправителя запроса или ``None``, если он неизвестен.

    Заголовок ``CLIENT_IP_HEADER`` читается, только если задан: без прокси его присылает
    сам клиент. Годится ``X-Real-IP`` — прокси его перезаписывает, а не дописывает.
    """
    if request is None:
        return None

    header = getattr(settings, "CLIENT_IP_HEADER", "")
    if header:
        forwarded = request.META.get(header, "")
        # При нескольких прокси заголовок приходит цепочкой; клиент в ней первый.
        value = forwarded.split(",")[0].strip()
        if value:
            return value

    return request.META.get("REMOTE_ADDR") or None
