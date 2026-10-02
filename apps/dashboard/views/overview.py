"""Обзорная страница панели: сначала то, что требует вмешательства."""

from __future__ import annotations

from typing import Any

from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView

from apps.accounts.constants import MANAGE_CONTENT, MANAGE_DATA, MANAGE_TICKETS, MANAGE_USERS

from .. import selectors
from ..navigation import AdminViewMixin

# Раздел, на который ведёт пункт «Требует внимания», — по имени маршрута.
ATTENTION_PERMISSIONS: dict[str, str] = {
    "dashboard:ticket-list": MANAGE_TICKETS,
    "dashboard:quality": MANAGE_DATA,
    "dashboard:data": MANAGE_DATA,
    "dashboard:sources": MANAGE_DATA,
}


class IndexView(AdminViewMixin, TemplateView):
    """Обзор состояния системы: блоки тех разделов, на которые есть права."""

    template_name = "dashboard/index.html"
    section_code = "overview"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Собрать сводку по доступным подсистемам."""
        context = super().get_context_data(**kwargs)
        granted = self.current_user.panel_permissions
        context["page_title"] = _("Обзор системы")
        context["can"] = {
            "users": MANAGE_USERS in granted,
            "data": MANAGE_DATA in granted,
            "tickets": MANAGE_TICKETS in granted,
            "content": MANAGE_CONTENT in granted,
        }
        context["metrics"] = selectors.overview_metrics()
        context["attention"] = [
            item
            for item in selectors.attention_items()
            if ATTENTION_PERMISSIONS.get(item["url_name"], MANAGE_DATA) in granted
        ]
        context["roles"] = selectors.role_distribution()
        context["warehouse"] = selectors.warehouse_state()
        context["tickets"] = selectors.ticket_statistics()
        context["content"] = selectors.content_counts()
        context["health"] = list(selectors.system_health().values())
        return context
