"""Адрес посетителя за обратным прокси."""

from __future__ import annotations

import ipaddress

from django.conf import settings
from django.http import HttpRequest

# Сеть IPv6 одного абонента: провайдер выдаёт ему /64, и адресов в ней — 2⁶⁴.
IPV6_SUBSCRIBER_PREFIX = 64


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


def rate_subject(address: str | None) -> str:
    """
    За кого считать обращения с этого адреса: адрес IPv4 или сеть /64 адреса IPv6.

    Счёт по отдельным адресам IPv6 ничего не ограничивает: абонент меняет адрес внутри
    своей сети в каждом запросе.
    """
    if not address:
        return "unknown"
    try:
        parsed = ipaddress.ip_address(address.strip().strip("[]"))
    except ValueError:
        return address
    if isinstance(parsed, ipaddress.IPv6Address):
        mapped = parsed.ipv4_mapped
        if mapped is not None:
            return str(mapped)
        network = ipaddress.ip_network((parsed, IPV6_SUBSCRIBER_PREFIX), strict=False)
        return str(network)
    return str(parsed)


def is_loopback(address: str | None) -> bool:
    """Обращение контейнера к самому себе: проверка живости, прогрев кэша."""
    if not address:
        return False
    try:
        return ipaddress.ip_address(address.strip().strip("[]")).is_loopback
    except ValueError:
        return False
