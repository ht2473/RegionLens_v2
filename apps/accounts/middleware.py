"""Отметка «знакомого браузера»: ставится в ответ на удачный вход."""

from __future__ import annotations

from collections.abc import Callable

from django.conf import settings
from django.core import signing
from django.http import HttpRequest, HttpResponse

from .security import DEVICE_COOKIE, DEVICE_SALT


class DeviceCookieMiddleware:
    """
    Поставить подписанную отметку браузеру, из которого только что вошли.

    Отметку готовит обработчик сигнала входа (``security.remember_device``); здесь она
    попадает в ответ. Что она даёт — в ``apps/accounts/security.py``.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Отдать ответ, добавив отметку после входа."""
        response = self.get_response(request)
        mark = getattr(request, "device_mark", "")
        if mark:
            response.set_cookie(
                DEVICE_COOKIE,
                signing.dumps(mark, salt=DEVICE_SALT),
                max_age=settings.DEVICE_COOKIE_AGE,
                secure=settings.SESSION_COOKIE_SECURE,
                httponly=True,
                samesite="Lax",
            )
        return response
