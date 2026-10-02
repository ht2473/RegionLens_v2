"""
Карта-указатель паспорта: где находится регион.

Та же серверная картограмма без данных: суша одним цветом, регион — цветом выбранного.
"""

from __future__ import annotations

from typing import Any

from django.urls import reverse

from apps.catalog.models import Territory

from .boundaries import boundaries_available
from .cartogram import build_map

# Маркеры оформления: суша без данных и выбранный регион.
LAND = "--map-land"
FOCUS = "--map-focus"


def locator_map(territory_code: str) -> dict[str, Any] | None:
    """Чертёж указателя для субъекта или ничего, если файла границ нет."""
    if not boundaries_available():
        return None
    rows = [
        {
            "code": territory.code,
            "name": territory.name,
            "abbreviation": territory.short_code,
            "href": reverse("catalog:territory-detail", kwargs={"slug": territory.slug}),
            "title": territory.name,
            "colour": FOCUS if territory.code == territory_code else LAND,
            "selected": territory.code == territory_code,
            "class_index": None,
            "mapped": None,
        }
        for territory in Territory.objects.comparable().only(
            "code", "slug", "name_ru", "name_en", "abbreviation", "display_order"
        )
    ]
    return build_map(rows, selected=[territory_code])
