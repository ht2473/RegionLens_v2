"""Раздел «Поиск»: запросы без ответа — по ним пополняется словарь синонимов."""

from __future__ import annotations

from typing import Any

from django.db.models import Count, Max
from django.db.models.functions import Lower
from django.views.generic import TemplateView

from apps.search.models import RETENTION_DAYS, SearchMiss

from ..navigation import AdminViewMixin

# Сколько разных запросов показывать.
SHOWN = 200


class SearchLogView(AdminViewMixin, TemplateView):
    """Запросы без ответа за срок хранения: сколько раз и когда последний."""

    template_name = "dashboard/search.html"
    section_code = "search"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Сгруппировать одинаковые запросы без учёта регистра."""
        context = super().get_context_data(**kwargs)
        rows = (
            SearchMiss.objects.annotate(folded=Lower("text"))
            .values("folded", "language")
            .annotate(count=Count("id"), last=Max("created_at"))
            .order_by("-count", "-last")[:SHOWN]
        )
        context["rows"] = list(rows)
        context["total"] = SearchMiss.objects.count()
        context["retention_days"] = RETENTION_DAYS
        return context
