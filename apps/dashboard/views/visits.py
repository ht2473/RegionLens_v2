"""Раздел «Посещения»: статистика по журналу Caddy, посчитанная GoAccess, и выгрузка по дням."""

from __future__ import annotations

import csv
import io
from datetime import timedelta
from typing import Any

from django.http import HttpRequest, HttpResponse
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import TemplateView

from apps.core import charts, visits
from apps.core.models import ServiceBeat
from apps.exports.renderers.table_csv import BOM

from ..navigation import AdminViewMixin

# Окно плиток «за последние дни».
RECENT_DAYS = 30
# Сколько систем и браузеров показывать.
SHARE_ROWS = 8


class VisitsView(AdminViewMixin, TemplateView):
    """Посетители по дням и месяцам, страницы, переходы, системы и браузеры."""

    template_name = "dashboard/visits.html"
    section_code = "visits"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Собрать сводку отчёта и график последних дней."""
        context = super().get_context_data(**kwargs)
        summary = visits.summarize(visits.read_report())
        context["summary"] = summary
        context["beat"] = ServiceBeat.objects.filter(service=ServiceBeat.Service.VISITS).first()
        if summary and summary["days"]:
            days = summary["days"]
            shown = days[-visits.CHART_DAYS :]
            context["chart_option"] = charts.timeline_option(
                [row["day"].strftime("%d.%m") for row in shown],
                [
                    {
                        "name": str(_("Посетители")),
                        "values": [row["visitors"] for row in shown],
                        "primary": True,
                    }
                ],
                end_labels=False,
            )
            since = days[-1]["day"] - timedelta(days=RECENT_DAYS - 1)
            recent = [row for row in days if row["day"] >= since]
            context["recent"] = {
                "days": RECENT_DAYS,
                "visitors": sum(row["visitors"] for row in recent),
                "hits": sum(row["hits"] for row in recent),
            }
        if summary:
            context["shares"] = [
                {"title": _("Операционные системы"), "rows": summary["systems"][:SHARE_ROWS]},
                {"title": _("Браузеры"), "rows": summary["browsers"][:SHARE_ROWS]},
            ]
        return context


class VisitsExportView(AdminViewMixin, View):
    """Посетители и запросы по дням в CSV — для таблиц вне сайта."""

    section_code = "visits"

    def get(self, request: HttpRequest) -> HttpResponse:  # noqa: ARG002 — контракт View
        """Отдать файл; пустой отчёт — файл с одной шапкой."""
        summary = visits.summarize(visits.read_report())
        buffer = io.StringIO(newline="")
        buffer.write(BOM)
        writer = csv.writer(buffer, dialect="unix", quoting=csv.QUOTE_MINIMAL)
        writer.writerow(["date", "visitors", "hits"])
        for row in summary["days"] if summary else []:
            writer.writerow([row["day"].isoformat(), row["visitors"], row["hits"]])
        response = HttpResponse(buffer.getvalue().encode("utf-8"), content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="regionlens-visits.csv"'
        return response
