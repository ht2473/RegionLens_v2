"""Кнопка выгрузки с форматами из описания вида документа и право выгружать ряд."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from django import template
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from ..constants import ExportFormat
from ..services import available_formats

register = template.Library()

# Пояснения к форматам: чем один отличается от другого для того, кто выбирает.
FORMAT_HINTS: dict[str, Any] = {
    ExportFormat.CSV: _("для расчётов: разделитель — запятая, десятичный — точка"),
    ExportFormat.XLSX: _("таблица для Excel"),
    ExportFormat.PDF: _("готовый к печати документ"),
}


@register.inclusion_tag("exports/partials/_export_button.html")
def export_button(
    kind: str,
    *,
    series: str = "",
    territory: str = "",
    year: Any = "",
    title: str = "",
    ascending: Any = False,
) -> dict[str, Any]:
    """
    Меню выгрузки по параметрам текущей страницы: каждый пункт — ссылка на готовый файл.

    ``ascending`` — обратный порядок рейтинга, как на холсте.
    """
    query: dict[str, str] = {"kind": kind, "series": series, "territory": territory}
    if year not in (None, ""):
        query["year"] = str(int(year))
    if ascending:
        query["ascending"] = "1"
    base = {name: value for name, value in query.items() if value}

    options = [
        {
            "format": code,
            "label": label,
            "hint": FORMAT_HINTS.get(code, ""),
            "url": reverse("exports:document") + "?" + urlencode({**base, "format": code}),
        }
        for code, label in available_formats(kind)
    ]
    return {"export_options": options, "export_title": title}


@register.filter
def exportable(series: Any) -> bool:
    """Ряд можно выгрузить: склада — всегда, своей таблицы — владельцу или по разрешению ссылки."""
    from apps.warehouse.routing import exportable as allowed

    key = getattr(series, "key", series)
    return bool(key) and allowed(str(key))
