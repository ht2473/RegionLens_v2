"""Общая основа страниц аналитических инструментов: параметры слева, результат справа."""

from __future__ import annotations

from typing import Any

from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView

from apps.catalog.selectors import selected_series_options, series_options_total
from apps.content.selectors import methodology_for_tool
from apps.core.navigation import Crumb
from apps.core.views import BreadcrumbMixin
from apps.userdata.series import own_option_groups
from apps.warehouse.duckdb_client import WarehouseNotBuiltError

from ..selectors import label_lang
from ..tools import TOOLS_BY_CODE, Tool


class AnalyticsView(BreadcrumbMixin, TemplateView):
    """
    Основа страницы аналитического инструмента.

    Наследник задаёт код инструмента и шаблоны и описывает расчёт в ``build_context``.
    """

    tool_code: str = ""
    results_template: str = ""

    @property
    def tool(self) -> Tool:
        """Описание инструмента из общего перечня раздела."""
        return TOOLS_BY_CODE[self.tool_code]

    def get_crumbs(self) -> tuple[Crumb, ...]:
        """Путь к странице: раздел аналитики, основной инструмент и сам инструмент."""
        crumbs = [Crumb(title=_("Анализ"), url=reverse("analytics:index"))]
        if self.tool.parent:
            parent = TOOLS_BY_CODE[self.tool.parent]
            crumbs.append(Crumb(title=parent.title, url=parent.url))
        crumbs.append(Crumb(title=self.tool.title))
        return tuple(crumbs)

    def get_template_names(self) -> list[str]:
        """Выбрать шаблон: запросу HTMX — только область результата."""
        if self.request.headers.get("HX-Request") and self.results_template:
            return [self.results_template]
        if self.template_name is None:
            raise ValueError(f"У представления {type(self).__name__} не задан шаблон")
        return [self.template_name]

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Собрать контекст страницы, отделив отказ склада от прикладных ошибок."""
        context = super().get_context_data(**kwargs)
        context["tool"] = self.tool
        context["page_title"] = self.tool.title
        # Раздел «Методика», связанный с инструментом именем маршрута.
        context["methodology"] = methodology_for_tool(f"analytics:{self.tool.url_name}")

        # Свёрнутый перечень показателей — для инструментов с выбором нескольких рядов;
        # ряды своих таблиц — первыми группами, в разметке страницы.
        context["picker_open"] = bool(self.request.GET.get("picker"))
        context["own_groups"] = own_option_groups(self.request)
        context["series_total"] = series_options_total() + sum(
            len(group["items"]) for group in context["own_groups"]
        )

        try:
            context.update(self.build_context())
            series = context.get("series")
            if series is not None:
                context["series_lang"] = label_lang(series, series.full_title)
            context["selected_options"] = selected_series_options(
                list(context.get("selected_keys") or [])
            )
            context["warehouse_ready"] = True
        except WarehouseNotBuiltError:
            context["warehouse_ready"] = False

        return context

    def build_context(self) -> dict[str, Any]:
        """Выполнить расчёт инструмента. Переопределяется наследником."""
        raise NotImplementedError
