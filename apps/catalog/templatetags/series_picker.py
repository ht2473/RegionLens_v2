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


@register.simple_tag
def series_choice(series: Series | str | None) -> dict[str, Any] | None:
    """Группа, название и пометка выбранного ряда — так же, как в полном перечне."""
    return featured_choice(series) if isinstance(series, Series) else None
