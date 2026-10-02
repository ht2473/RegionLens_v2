"""Страница «Источники данных»: издания, последние выпуски и судьба рядов."""

from __future__ import annotations

from typing import Any

from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView

from apps.catalog.indicator import series_descriptor
from apps.catalog.models import Series
from apps.catalog.monthly import annual_method_text, latest_period
from apps.catalog.provenance import SourceLink, source_title
from apps.catalog.status import all_statuses
from apps.core.navigation import Crumb
from apps.core.views import BreadcrumbMixin
from apps.sources.models import Release, Source
from apps.sources.registry import registry
from apps.warehouse.duckdb_client import WarehouseNotBuiltError
from apps.warehouse.queries.sources import source_links


class DataSourcesView(BreadcrumbMixin, TemplateView):
    """Откуда данные: набор, издания Росстата, продолжающиеся и прекращённые ряды."""

    template_name = "catalog/sources.html"

    def get_crumbs(self) -> tuple[Crumb, ...]:
        """Путь к странице."""
        return (
            Crumb(title=_("О наборе данных"), url=reverse("catalog:dataset")),
            Crumb(title=_("Источники данных")),
        )

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Собрать издания, связи рядов и прекращённые ряды."""
        context = super().get_context_data(**kwargs)
        context["page_title"] = _("Источники данных")
        context["sources"] = [
            {"source": source, "latest": source.latest_release()} for source in Source.objects.all()
        ]
        try:
            rows = source_links()
        except WarehouseNotBuiltError:
            rows = {}
        links = [SourceLink.from_row(row) for row in rows.values()]
        slugs = dict(
            Series.objects.filter(key__in=[link.series_key for link in links]).values_list(
                "key", "indicator__slug"
            )
        )
        described = [(link, series_descriptor(link.series_key)) for link in links]
        described.sort(key=lambda pair: pair[1].short_title if pair[1] else pair[0].series_key)
        # Ряды по изданиям: название издания — заголовком группы, а не в каждой строке.
        groups: dict[str, dict[str, Any]] = {}
        for link, item in described:
            group = groups.setdefault(link.source_code, {"title": link.publication, "rows": []})
            group["rows"].append({"link": link, "item": item, "slug": slugs.get(link.series_key)})
        context["continued"] = [groups[code] for code in sorted(groups)]
        stopped = [status for status in all_statuses() if status.is_discontinued]
        stopped_slugs = dict(
            Series.objects.filter(key__in=[status.key for status in stopped]).values_list(
                "key", "indicator__slug"
            )
        )
        context["discontinued"] = [
            {
                "status": status,
                "item": series_descriptor(status.key),
                "slug": stopped_slugs.get(status.key),
            }
            for status in stopped
        ]
        context["collected"] = _collected_groups()
        context["release_count"] = Release.objects.filter(status=Release.Status.PARSED).count()
        return context


def _collected_groups() -> list[dict[str, Any]]:
    """Ряды проекта по источникам: годы, последний месяц и способ годового значения."""
    book = registry()
    found = {
        series.key: series
        for series in Series.objects.filter(key__in=book.by_key).select_related("indicator")
    }
    groups: dict[str, dict[str, Any]] = {}
    for item in book.series:
        series = found.get(item.key)
        if series is None:
            continue
        try:
            period = latest_period(item.key)
        except WarehouseNotBuiltError:
            period = ""
        group = groups.setdefault(item.source, {"title": source_title(item.source), "rows": []})
        group["rows"].append(
            {
                "title": item.short_title,
                "key": item.key,
                "slug": series.indicator.slug,
                "first_year": series.first_year,
                "last_year": series.last_year,
                "period": period,
                "method": annual_method_text(item),
            }
        )
    return list(groups.values())
