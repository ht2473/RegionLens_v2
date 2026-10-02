"""Карта сайта с обеими языковыми версиями каждой страницы."""

from __future__ import annotations

from datetime import datetime

from django.contrib.sitemaps import Sitemap
from django.db.models import QuerySet
from django.urls import reverse

from apps.catalog.models import Indicator, Territory


class StaticViewSitemap(Sitemap):
    """Страницы с постоянным адресом, перечисленные именами маршрутов."""

    i18n = True
    alternates = True
    changefreq = "weekly"
    priority = 0.8

    def items(self) -> list[str]:
        """Имена маршрутов постоянных страниц."""
        return [
            "core:home",
            "core:about",
            "core:terms",
            "core:privacy",
            "core:consent",
            "catalog:indicator-list",
            "catalog:territory-list",
            "catalog:dataset",
            "catalog:sources",
            "maps:choropleth",
            "rankings:index",
            "compare:index",
            "surface:distribution",
            "surface:table",
            "analytics:index",
            "analytics:index-builder",
            "analytics:inequality",
            "analytics:convergence",
            "analytics:correlation",
            "analytics:spatial",
            "analytics:revisions",
            "content:methodology",
            "content:glossary",
            "feedback:create",
        ]

    def location(self, item: str) -> str:
        """Адрес маршрута."""
        return reverse(item)


class TerritorySitemap(Sitemap):
    """Паспорта территорий."""

    i18n = True
    alternates = True
    changefreq = "monthly"
    priority = 0.6

    def items(self) -> QuerySet[Territory]:
        """Действующие территории справочника."""
        return Territory.objects.order_by("display_order", "code")

    def location(self, item: Territory) -> str:
        """Адрес паспорта территории."""
        return reverse("catalog:territory-detail", kwargs={"slug": item.slug})

    def lastmod(self, item: Territory) -> datetime | None:
        """Момент последнего изменения записи справочника."""
        return item.updated_at


class IndicatorSitemap(Sitemap):
    """Карточки показателей, по которым есть наблюдения."""

    i18n = True
    alternates = True
    changefreq = "monthly"
    priority = 0.5
    limit = 2000

    def items(self) -> QuerySet[Indicator]:
        """Действующие показатели, по которым есть наблюдения."""
        return Indicator.objects.filter(observation_count__gt=0).order_by("code")

    def location(self, item: Indicator) -> str:
        """Адрес карточки показателя."""
        return reverse("catalog:series-detail", kwargs={"slug": item.slug})

    def lastmod(self, item: Indicator) -> datetime | None:
        """Момент последнего изменения записи каталога."""
        return item.updated_at


SITEMAPS = {
    "static": StaticViewSitemap,
    "territories": TerritorySitemap,
    "indicators": IndicatorSitemap,
}
