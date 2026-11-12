"""
Общий предел частоты обращений, запоминание языка для страниц без языкового префикса
и отказ в запросах с нулевым байтом.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from django.conf import settings
from django.core.exceptions import BadRequest
from django.http import HttpRequest, HttpResponse
from django.urls import Resolver404, resolve
from django.utils.cache import patch_vary_headers
from django.utils.translation import get_language, get_language_from_path, override

from .client_ip import client_ip, is_loopback, rate_subject
from .throttle import allow_key


class RequestRateMiddleware:
    """
    Общий предел обращений с одного адреса в минуту — до любой работы над запросом.

    У расчётов, выгрузок и входа свои, более строгие пределы; этот не даёт одному адресу
    занять все рабочие потоки обычными страницами. Отказ — короткий текст с кодом 429,
    без сборки страницы. Обращения контейнера к самому себе (проверка живости, прогрев)
    не считаются. ``REQUESTS_PER_MINUTE = 0`` предел выключает.
    """

    WINDOW = 60

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Отказать адресу, исчерпавшему предел, иначе передать запрос дальше."""
        limit = settings.REQUESTS_PER_MINUTE
        if limit > 0:
            address = client_ip(request)
            if not is_loopback(address) and not allow_key(
                "requests", rate_subject(address), limit=limit, window=self.WINDOW
            ):
                response = HttpResponse(
                    "Слишком много запросов с вашего адреса. Повторите через минуту.\n"
                    "Too many requests from your address. Try again in a minute.\n",
                    status=429,
                    content_type="text/plain; charset=utf-8",
                )
                response["Retry-After"] = str(self.WINDOW)
                return response
        return self.get_response(request)


class BodyLimitMiddleware:
    """
    Отказать в большом теле запроса до его чтения тому, кому оно не положено.

    Тело запроса читается раньше проверки входа: его разбирают проверка нулевого байта
    и защита от подделки запроса. Без этого слоя любой посетитель заставлял бы сервер
    принять и разобрать десятки мегабайт по любому адресу. Формы посетителя без входа —
    вход, регистрация, обращение — малы, проверка документа — со своим пределом; таблицы
    загружают после входа; выпуск набора — только тот, кто управляет данными. Запрос
    с телом без объявленной длины не принимается:
    браузеры длину объявляют всегда.
    """

    METHODS = frozenset({"POST", "PUT", "PATCH"})

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Сверить объявленную длину тела с пределом отправителя."""
        if request.method in self.METHODS:
            status = self._refusal(request)
            if status:
                return HttpResponse(
                    "Запрос больше, чем принимает сайт. / The request is larger than the "
                    "site accepts.\n",
                    status=status,
                    content_type="text/plain; charset=utf-8",
                )
        return self.get_response(request)

    @staticmethod
    def _refusal(request: HttpRequest) -> int:
        """Код отказа или ноль; пользователь узнаётся по сеансу, только когда тело велико."""
        declared = request.META.get("CONTENT_LENGTH") or ""
        if not declared.isdigit():
            return 411 if request.META.get("HTTP_TRANSFER_ENCODING") else 0
        length = int(declared)
        if length <= settings.ANONYMOUS_BODY_BYTES:
            return 0
        from django.contrib.auth import get_user

        user: Any = get_user(request)
        # Слой аутентификации возьмёт уже найденного пользователя.
        request._cached_user = user  # type: ignore[attr-defined]
        if not user.is_authenticated:
            return 0 if length <= BodyLimitMiddleware._anonymous_limit(request) else 413
        if length > settings.MEMBER_BODY_BYTES and not user.has_perm("accounts.manage_data"):
            return 413
        return 0

    @staticmethod
    def _anonymous_limit(request: HttpRequest) -> int:
        """Предел тела без входа: у страниц из ``ANONYMOUS_BODY_ROUTES`` — свой."""
        routes: dict[str, int] = settings.ANONYMOUS_BODY_ROUTES
        if not routes:
            return int(settings.ANONYMOUS_BODY_BYTES)
        # Язык ещё не выбран (слой языка — дальше), а адрес с префиксом разбирается только
        # на своём языке.
        language = get_language_from_path(request.path_info) or settings.LANGUAGE_CODE
        try:
            with override(language):
                name = resolve(request.path_info).view_name
        except Resolver404:
            return int(settings.ANONYMOUS_BODY_BYTES)
        return routes.get(name, int(settings.ANONYMOUS_BODY_BYTES))


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


class HtmxVaryMiddleware:
    """
    Объявить, что ответ зависит от заголовка ``HX-Request``.

    По одному адресу htmx получает фрагмент, а браузер — полную страницу. Без этого заголовка
    кэш браузера считает их одним ответом, и возврат кнопкой «Назад» показывает голый фрагмент.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Дополнить заголовок ``Vary`` готового ответа."""
        response = self.get_response(request)
        patch_vary_headers(response, ("HX-Request",))
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
