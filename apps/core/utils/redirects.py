"""Проверка адреса возврата, пришедшего от клиента."""

from __future__ import annotations

from django.http import HttpRequest
from django.utils.http import url_has_allowed_host_and_scheme


def safe_back(request: HttpRequest, fallback: str, field: str = "back") -> str:
    """
    Вернуть адрес возврата из поля ``field``, если он ведёт на этот же сайт, иначе ``fallback``.

    Без проверки ссылка с ``?back=https://чужой.сайт`` делала бы сайт перенаправителем.
    """
    candidate = request.POST.get(field) or request.GET.get(field) or ""
    if candidate and url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return fallback
