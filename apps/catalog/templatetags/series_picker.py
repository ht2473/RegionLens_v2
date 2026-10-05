"""Адрес перечня рядов и подпись выбранного ряда для списка выбора."""

from __future__ import annotations

from typing import Any

from django import template
from django.urls import reverse
from django.utils.http import urlencode

from apps.catalog.models import Series
from apps.catalog.selectors import featured_choice, series_options_version

register = template.Library()


@register.simple_tag
def series_options_url() -> str:
    """Адрес перечня рядов с отпечатком состава: ответ по нему можно хранить сколь угодно долго."""
    return f"{reverse('catalog:series-options')}?{urlencode({'v': series_options_version()})}"


@register.simple_tag(takes_context=True)
def own_series_options(context: dict[str, Any]) -> list[dict[str, Any]]:
    """Группы рядов своих таблиц того, кто открыл страницу."""
    if "own_groups" in context:
        return list(context["own_groups"])
    from apps.userdata.series import own_option_groups

    return own_option_groups(context.get("request"))


@register.simple_tag
def series_choice(series: Series | str | None) -> dict[str, Any] | None:
    """Группа, название и пометка выбранного ряда — так же, как в полном перечне."""
    if isinstance(series, Series) or getattr(series, "is_user", False):
        return featured_choice(series)  # type: ignore[arg-type]
    return None
