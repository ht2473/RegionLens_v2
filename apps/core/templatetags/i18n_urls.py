"""Теги адресов языковых версий страницы."""

from __future__ import annotations

from django.conf import settings
from django.http import HttpRequest
from django.template import Library
from django.urls import translate_url

register = Library()


@register.simple_tag(takes_context=True)
def switch_language_url(context: dict, language_code: str) -> str:
    """
    Вернуть адрес текущей страницы на указанном языке, сохранив строку запроса.

    Адрес разбирается маршрутом (``translate_url``): у служебных страниц префикса нет.
    """
    request: HttpRequest | None = context.get("request")
    if request is None:  # pragma: no cover - шаблон всегда получает запрос
        return "/"

    translated = translate_url(request.path, language_code)
    query = request.META.get("QUERY_STRING", "")
    return f"{translated}?{query}" if query else translated


@register.simple_tag(takes_context=True)
def alternate_links(context: dict) -> list[dict[str, str]]:
    """Перечень языковых версий текущей страницы для ссылок ``hreflang``; x-default — русская."""
    request: HttpRequest | None = context.get("request")
    if request is None:  # pragma: no cover
        return []

    versions = [
        {"code": code, "url": request.build_absolute_uri(translate_url(request.path, code))}
        for code, _name in settings.LANGUAGES
    ]
    default = next(
        (item["url"] for item in versions if item["code"] == settings.LANGUAGE_CODE),
        versions[0]["url"],
    )
    return [*versions, {"code": "x-default", "url": default}]


# Область языка для og:locale: протокол Open Graph ждёт вид «язык_СТРАНА».
OG_LOCALES = {"ru": "ru_RU", "en": "en_US"}


@register.simple_tag
def og_locale(language_code: str) -> str:
    """Язык страницы в записи Open Graph."""
    return OG_LOCALES.get(language_code, language_code)


@register.simple_tag(takes_context=True)
def canonical_url(context: dict) -> str:
    """Канонический адрес страницы — без строки запроса: расчёты по параметрам не дублируют её."""
    request: HttpRequest | None = context.get("request")
    if request is None:  # pragma: no cover
        return ""
    return request.build_absolute_uri(request.path)
