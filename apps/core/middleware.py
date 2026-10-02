"""Запоминание языка для страниц без языкового префикса и отказ в запросах с нулевым байтом."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from django.conf import settings
from django.core.exceptions import BadRequest
from django.http import HttpRequest, HttpResponse
from django.utils.translation import get_language


class LanguageMemoryMiddleware:
    """
    Запомнить язык, заданный префиксом адреса, для страниц без префикса.

    Язык пишется в стандартный языковой cookie, который читает ``LocaleMiddleware``.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response
        self.codes = {code for code, _name in settings.LANGUAGES}

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Отдать ответ, сохранив в нём язык страницы с префиксом."""
        language = get_language()
        prefixed = bool(language) and any(
            request.path_info.startswith(f"/{code}/") for code in self.codes
        )

        response = self.get_response(request)
        if not prefixed or language is None:
            return response

        if request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME) != language:
            # mypy не знает, что в настройке только допустимые значения.
            samesite: Any = settings.LANGUAGE_COOKIE_SAMESITE
            response.set_cookie(
                settings.LANGUAGE_COOKIE_NAME,
                language,
                max_age=settings.LANGUAGE_COOKIE_AGE,
                path=settings.LANGUAGE_COOKIE_PATH,
                domain=settings.LANGUAGE_COOKIE_DOMAIN,
                secure=settings.LANGUAGE_COOKIE_SECURE,
                httponly=settings.LANGUAGE_COOKIE_HTTPONLY,
                samesite=samesite,
            )
        return response


class NullCharacterMiddleware:
    """
    Ответить 400 на запрос с нулевым байтом в адресе, параметрах, полях формы или cookie.

    PostgreSQL не принимает этот знак в текстовых полях, а параметры доходят до выборок
    напрямую (ряд, поиск, регион): без проверки такой запрос заканчивался бы ошибкой 500.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Отказать в запросе с нулевым байтом, иначе передать его дальше."""
        values = [request.path_info, *request.COOKIES.values()]
        for data in (request.GET, request.POST):
            for key, items in data.lists():
                values.append(key)
                values.extend(items)
        if any("\x00" in value for value in values):
            raise BadRequest("Нулевой байт в запросе")
        return self.get_response(request)
