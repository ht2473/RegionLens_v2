"""Страница о наборе данных."""

from __future__ import annotations

from typing import Any

from django.db.models import Count, QuerySet
from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView

from apps.catalog.models import Indicator, Section
from apps.core import structured_data
from apps.core.navigation import Crumb
from apps.core.templatetags.formatting import ru_number
from apps.core.views import BreadcrumbMixin
from apps.warehouse.duckdb_client import WarehouseNotBuiltError
from apps.warehouse.models import DataQualityCheck
from apps.warehouse.queries import (
    coverage_distribution,
    observations_by_year,
    warehouse_summary,
)


class DatasetView(BreadcrumbMixin, TemplateView):
    """
    Страница о наборе данных: источник, период, полнота, итог проверок, цитирование.

    Итог проверок — сводкой по видам; отдельные замечания разбирают в панели управления.
    """

    template_name = "catalog/dataset.html"

    def get_crumbs(self) -> tuple[Crumb, ...]:
        """Путь к странице."""
        return (Crumb(title=_("О наборе данных")),)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Собрать характеристики набора и его покрытия."""
        context = super().get_context_data(**kwargs)
        context["page_title"] = _("О наборе данных")

        try:
            context["summary"] = warehouse_summary()
            context["fill_by_year"] = observations_by_year()
            context["coverage"] = _coverage_bars(coverage_distribution())
            context["warehouse_ready"] = True
        except WarehouseNotBuiltError:
            context["warehouse_ready"] = False

        # Описание набора для поиска по данным; период — только из собранного склада.
        summary = context.get("summary") or {}
        context["structured_data"] = structured_data.collection(
            self.request,
            first_year=summary.get("first_year"),
            last_year=summary.get("last_year"),
            observations=summary.get("observations"),
        )

        checks = DataQualityCheck.objects.all()
        latest_run_id = checks.order_by("-created_at").values_list("run_id", flat=True).first()
        if latest_run_id:
            checks = checks.filter(run_id=latest_run_id)
        context["quality_summary"] = _summary_by_type(checks)

        context["sections"] = Section.objects.order_by("-series_count")[:12]
        context["editions_count"] = Indicator.objects.count()
        return context


# Доля меньше половины десятой процента округлилась бы до «0,0 %».
SMALLEST_SHOWN_SHARE = 0.05

# Порядок полос покрытия: от полного к отсутствующему.
COVERAGE_ORDER = ("full", "high", "medium", "low", "none")


def _coverage_bars(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Полосы покрытия с долей от всех рядов набора."""
    total = sum(row["series_count"] for row in rows) or 1
    order = {code: index for index, code in enumerate(COVERAGE_ORDER)}
    bars = sorted(rows, key=lambda row: order.get(row["bucket_code"], len(order)))
    result = []
    for row in bars:
        share = 100 * row["series_count"] / total
        text = f"< {ru_number(0.1, 1)}" if 0 < share < SMALLEST_SHOWN_SHARE else ru_number(share, 1)
        result.append({**row, "share": round(share, 1), "share_text": text})
    return result


# Уровни замечаний от слабого к сильному; плитка вида проверки показывает наибольший.
SEVERITY_WEIGHT: dict[str, int] = {
    DataQualityCheck.Severity.INFO.value: 0,
    DataQualityCheck.Severity.WARNING.value: 1,
    DataQualityCheck.Severity.ERROR.value: 2,
}


def _summary_by_type(checks: QuerySet[DataQualityCheck]) -> list[dict[str, Any]]:
    """Собрать сводку замечаний по видам проверок с подписью вида и наибольшим уровнем."""
    labels = dict(DataQualityCheck.CheckType.choices)
    grouped: dict[str, dict[str, Any]] = {}

    for row in checks.values("check_type", "severity").annotate(count=Count("id")):
        code = row["check_type"]
        item = grouped.setdefault(
            code,
            {"code": code, "title": labels.get(code, code), "count": 0, "severity": ""},
        )
        item["count"] += row["count"]
        if SEVERITY_WEIGHT.get(row["severity"], 0) >= SEVERITY_WEIGHT.get(item["severity"], -1):
            item["severity"] = row["severity"]

    severity_labels = dict(DataQualityCheck.Severity.choices)
    for item in grouped.values():
        item["severity_title"] = severity_labels.get(item["severity"], item["severity"])

    return sorted(grouped.values(), key=lambda item: -item["count"])
