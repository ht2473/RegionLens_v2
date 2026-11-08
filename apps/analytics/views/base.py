"""Общая основа страниц аналитических инструментов: параметры слева, результат справа."""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView

from apps.catalog.selectors import selected_series_options, series_options_total
from apps.content.selectors import methodology_for_tool
from apps.core.navigation import Crumb
from apps.core.throttle import allow
from apps.core.views import BreadcrumbMixin
from apps.userdata.series import chosen_option_groups
from apps.warehouse.duckdb_client import WarehouseNotBuiltError

from ..selectors import ComputationLimitError, computation_budget, label_lang
from ..tools import TOOLS_BY_CODE, Tool


class AnalyticsView(BreadcrumbMixin, TemplateView):
    """
    Основа страницы аналитического инструмента.

    Наследник задаёт код инструмента и шаблоны и описывает расчёт в ``build_context``.
    """

    tool_code: str = ""
    results_template: str = ""
    # Исчерпан предел свежих расчётов с адреса: вместо результата — просьба подождать.
    busy: bool = False

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """Страница инструмента; свежие расчёты учитываются по адресу посетителя."""

        def allowed() -> bool:
            return allow(request, "analysis", limit=settings.ANALYTICS_COMPUTATIONS, window=600)

        with computation_budget(allowed):
            response = super().get(request, *args, **kwargs)
        # Ответ 4xx htmx в область результата не вставляет: фрагменту — 200, странице — 429.
        if self.busy and not request.headers.get("HX-Request"):
            response.status_code = 429
        return response

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
        if self.busy and self.request.headers.get("HX-Request"):
            return ["analytics/partials/_busy.html"]
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
        # свои ряды живут в лаборатории: здесь — только уже выбранные, первой группой.
        context["picker_open"] = bool(self.request.GET.get("picker"))
        context["own_groups"] = chosen_option_groups(self.request.GET.getlist("series"))
        context["series_total"] = series_options_total()

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
        except ComputationLimitError:
            self.busy = True
            context["warehouse_ready"] = True
            context["busy"] = True

        return context

    def build_context(self) -> dict[str, Any]:
        """Выполнить расчёт инструмента. Переопределяется наследником."""
        raise NotImplementedError
