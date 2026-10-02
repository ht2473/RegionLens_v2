"""
Разбор пересмотров статистики: объём, разделы, ряды, годы, издания и версии наблюдения.

Типичный размер пересмотра — медиана: часть расхождений — смена единицы между изданиями.
"""

from __future__ import annotations

from typing import Any

from django.utils.translation import gettext_lazy as _

from apps.warehouse.queries import (
    edition_activity,
    largest_revisions,
    revision_distribution,
    revision_summary,
    revision_trace,
    revision_triangle,
    revisions_by_section,
    revisions_by_series,
    revisions_by_year,
)

from ..selectors import region_rows
from .base import AnalyticsView

# Число строк в перечнях страницы.
SECTIONS_LIMIT = 10
SERIES_LIMIT = 15
EXAMPLES_LIMIT = 20

# Граница, начиная с которой пересмотр считается крупным.
LARGE_REVISION = 0.05


class RevisionsView(AnalyticsView):
    """Сводка и разбор расхождений значений между выпусками изданий."""

    template_name = "analytics/revisions.html"
    results_template = "analytics/partials/_revisions_results.html"
    tool_code = "revisions"

    def build_context(self) -> dict[str, Any]:
        """Собрать сводку пересмотров, распределения и перечни."""
        request = self.request
        section = request.GET.get("section", "")
        sections = revisions_by_section(limit=SECTIONS_LIMIT)
        known = {row["section_code"] for row in sections}
        if section not in known:
            section = ""

        distribution = revision_distribution()
        by_year = revisions_by_year()

        return {
            "summary": revision_summary(),
            "distribution": distribution,
            "distribution_option": _distribution_option(distribution),
            "by_year": by_year,
            "year_option": _year_option(by_year),
            "sections": sections,
            "section": section,
            "series_rows": revisions_by_series(limit=SERIES_LIMIT, section_code=section),
            "examples": _examples(section),
            "editions": edition_activity(),
            "large_revision": LARGE_REVISION,
        }


class RevisionTraceView(AnalyticsView):
    """Перечень версий одного наблюдения — фрагментом по запросу из таблицы."""

    template_name = "analytics/partials/_revision_trace.html"
    results_template = "analytics/partials/_revision_trace.html"
    tool_code = "revisions"

    def build_context(self) -> dict[str, Any]:
        """Получить все опубликованные версии наблюдения."""
        request = self.request
        series_key = request.GET.get("series", "")
        territory_code = request.GET.get("territory", "")
        try:
            year = int(request.GET.get("year", ""))
        except TypeError, ValueError:
            return {"versions": []}

        if not series_key or not territory_code:
            return {"versions": []}

        names = {row["code"]: row["name"] for row in region_rows()}
        versions = revision_trace(series_key, territory_code, year)
        return {
            "versions": versions,
            "trace_year": year,
            "trace_territory": names.get(territory_code, territory_code),
            "triangle": revision_triangle(series_key, territory_code),
        }


def _distribution_option(buckets: list[dict[str, Any]]) -> dict[str, Any]:
    """Построить распределение пересмотров по величине расхождения."""
    return {
        "grid": {"left": 8, "right": 24, "top": 12, "bottom": 8, "containLabel": True},
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
        "xAxis": {
            "type": "category",
            "data": [str(bucket["label"]) for bucket in buckets],
            "axisLabel": {"fontSize": 11},
        },
        "yAxis": {"type": "value", "name": str(_("наблюдений"))},
        "series": [
            {
                "type": "bar",
                "barMaxWidth": 40,
                "data": [bucket["count"] for bucket in buckets],
            }
        ],
    }


def _year_option(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Построить распределение пересмотров по отчётным годам."""
    if not rows:
        return None

    return {
        "grid": {"left": 8, "right": 24, "top": 32, "bottom": 8, "containLabel": True},
        "legend": {"show": True, "top": 0, "icon": "roundRect", "itemWidth": 14, "itemHeight": 8},
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
        "xAxis": {"type": "category", "data": [str(row["year"]) for row in rows]},
        "yAxis": {"type": "value", "name": str(_("наблюдений"))},
        "series": [
            {
                "name": str(_("все пересмотры")),
                "type": "bar",
                "stack": "revisions",
                "data": [row["revision_count"] - row["large_count"] for row in rows],
            },
            {
                "name": str(_("крупные (свыше 5 %)")),
                "type": "bar",
                "stack": "revisions",
                "data": [row["large_count"] for row in rows],
            },
        ],
    }


def _examples(section: str) -> list[dict[str, Any]]:
    """Отобрать наиболее крупные пересмотры для разбора."""
    return largest_revisions(limit=EXAMPLES_LIMIT, section_code=section)
