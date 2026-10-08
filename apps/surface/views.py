"""
Рабочая поверхность: один выбор и пять представлений над общим холстом.

Смена параметра в рейле обновляет холст, смена представления — рабочую область с рейлем;
ответ выбирается по заголовку ``HX-Target``.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView

from apps.catalog.selectors import (
    MAX_COMPARE,
    grouped_territories,
    resolve_territories,
    series_options,
)
from apps.core.navigation import Crumb
from apps.core.views import BreadcrumbMixin
from apps.surface import picks
from apps.surface.panels import (
    PANEL_DISTRIBUTION,
    PANEL_TABLE,
    PANELS_BY_CODE,
    Panel,
    build_tabs,
    panel_url,
)
from apps.surface.sharing import share_context
from apps.surface.state import resolve_state, state_context
from apps.surface.subject import describe_subject
from apps.warehouse.duckdb_client import WarehouseNotBuiltError

# Рабочая область: ``id`` в разметке и ``HX-Target`` при смене представления;
# любой другой запрос HTMX — за холстом.
WORKSPACE_ID = "surface"


class SurfaceView(BreadcrumbMixin, TemplateView):
    """Общий каркас рабочей поверхности; представление задаётся полем ``panel_code``."""

    template_name = "surface/index.html"
    panel_code: str = ""

    @property
    def panel(self) -> Panel:
        """Описание текущего представления."""
        return PANELS_BY_CODE[self.panel_code]

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """
        Быстрый вариант выбора регионов (``pick``) дополняет выбор и переводит на адрес
        с регионами словами: ссылка на вид остаётся обычной. HTMX идёт за переходом сам
        и записывает в историю конечный адрес.
        """
        pick = request.GET.get("pick", "")
        if pick in picks.PICKS:
            try:
                state = resolve_state(request)
                codes = picks.apply(request, pick, state.territories)
                chosen = replace(state, territories=resolve_territories(codes))
                return redirect(panel_url(self.panel, chosen, request))
            except WarehouseNotBuiltError:
                pass
        return super().get(request, *args, **kwargs)

    def get_template_names(self) -> list[str]:
        """Выбрать полный экран, рабочую область или один холст."""
        if not self.request.headers.get("HX-Request"):
            return [self.template_name]
        if self.request.headers.get("HX-Target") == WORKSPACE_ID:
            return ["surface/partials/_workspace_response.html"]
        return ["surface/partials/_main_response.html"]

    def get_crumbs(self) -> tuple[Crumb, ...]:
        """Путь к странице: раздел «Исследовать», как в меню, и представление."""
        return (
            Crumb(title=_("Исследовать"), url=reverse("maps:choropleth")),
            Crumb(title=self.panel.tab),
        )

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Собрать общее состояние, холст и переключатель представлений."""
        context = super().get_context_data(**kwargs)
        panel = self.panel
        context["panel"] = panel
        context["page_title"] = panel.tab
        context["max_compare"] = MAX_COMPARE

        try:
            context["series_options"] = series_options
            groups = grouped_territories()
            context["grouped_territories"] = groups
            # Число субъектов — для свёрнутого перечня.
            context["territory_count"] = sum(len(group["items"]) for group in groups)
            state = resolve_state(self.request)
            context.update(state_context(state))
            # На пределе отметок перечень регионов не предлагается.
            context["territories_full"] = len(state.codes) >= MAX_COMPARE
            subject = describe_subject(state)
            context["subject"] = subject
            context["precision"] = subject.item.precision if subject else None
            # Складываемая величина: доля в сумме осмысленна, отношение к России — нет.
            context["additive"] = bool(subject and subject.item.absolute)
            if subject is not None:
                context["dock_title"] = subject.title
            context["tabs"] = build_tabs(panel, state, self.request)
            context["territories_reset_url"] = panel_url(
                panel, state, self.request, with_territories=False
            )
            context["territory_picks"] = picks.offers(
                self.request, state.territories, panel_url(panel, state, self.request)
            )
            context.update(panel.builder(self.request, state))
            # После холста: рейтинг бывает показан не за выбранный год.
            context.update(share_context(panel, state, self.request, context))
            context["warehouse_ready"] = True
        except WarehouseNotBuiltError:
            context["warehouse_ready"] = False

        return context


class DistributionView(SurfaceView):
    """Разброс значений по субъектам: гистограмма, квартили, положение выбранных."""

    panel_code = PANEL_DISTRIBUTION


class TableView(SurfaceView):
    """Полная таблица значений, включая субъекты без данных."""

    panel_code = PANEL_TABLE
