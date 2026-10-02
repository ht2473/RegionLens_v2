"""Обзорная страница аналитического раздела: инструменты по вопросам, на которые они отвечают."""

from __future__ import annotations

from typing import Any

from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView

from apps.core.navigation import Crumb
from apps.core.views import BreadcrumbMixin
from apps.warehouse.duckdb_client import WarehouseNotBuiltError
from apps.warehouse.queries import warehouse_summary

from ..tools import SECTION_TOOLS


class AnalyticsIndexView(BreadcrumbMixin, TemplateView):
    """Перечень аналитических инструментов раздела."""

    template_name = "analytics/index.html"

    def get_crumbs(self) -> tuple[Crumb, ...]:
        """Путь к странице."""
        return (Crumb(title=_("Анализ")),)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Дополнить контекст перечнем инструментов и сводкой о данных."""
        context = super().get_context_data(**kwargs)
        context["page_title"] = _("Анализ")
        context["tools"] = SECTION_TOOLS

        try:
            context["summary"] = warehouse_summary()
            context["warehouse_ready"] = True
        except WarehouseNotBuiltError:
            context["warehouse_ready"] = False

        return context
