"""Паспорт территории и карточка региона для живой карты."""

from __future__ import annotations

from typing import Any

from django.shortcuts import get_object_or_404
from django.utils.translation import gettext_lazy as _
from django.views.generic import DetailView

from apps.analytics.similar import similar_territories
from apps.catalog.indicator import series_descriptor
from apps.catalog.models import Territory
from apps.catalog.monthly import now_rows
from apps.catalog.passport import build_passport, position_for
from apps.catalog.selectors import resolve_series, series_options
from apps.core.charts import sparkline_path, timeline_option
from apps.core.navigation import Crumb
from apps.core.views import BreadcrumbMixin
from apps.maps.locator import locator_map
from apps.warehouse.duckdb_client import WarehouseNotBuiltError
from apps.warehouse.queries import (
    COUNTRY_CODE,
    featured_snapshot,
    series_statistics_timeline,
    series_timeline_multi,
    territory_coverage,
)
from apps.warehouse.routing import is_user_key

# Сколько сильных и слабых сторон показывать сразу; остальные — под «ещё N».
STRENGTH_LIMIT = 6

# Блок похожих регионов: ``id`` в разметке и значение заголовка ``HX-Target``.
SIMILAR_ID = "territory-similar"

# Блок динамики — второй фрагмент страницы.
TIMELINE_ID = "territory-timeline"

# Признак запроса похожих регионов: при открытии страницы они не считаются.
SIMILAR_PARAM = "similar"

# Глубина ряда на графике паспорта.
TIMELINE_YEARS = 25


class TerritoryDetailView(BreadcrumbMixin, DetailView):
    """Паспорт субъекта Российской Федерации."""

    template_name = "catalog/territory_detail.html"
    context_object_name = "territory"

    def get_object(self, queryset: Any = None) -> Territory:  # noqa: ARG002
        """Найти территорию по адресному идентификатору."""
        return get_object_or_404(
            Territory.objects.select_related("parent"),
            slug=self.kwargs["slug"],
        )

    def get_crumbs(self) -> tuple[Crumb, ...]:
        """Путь к странице."""
        from django.urls import reverse

        return (
            Crumb(title=_("Регионы"), url=reverse("catalog:territory-list")),
            Crumb(title=self.object.name),
        )

    def fragment(self) -> str:
        """Блок, запрошенный HTMX (по ``HX-Target``); для целой страницы — пустая строка."""
        if not self.request.headers.get("HX-Request"):
            return ""
        return self.request.headers.get("HX-Target", TIMELINE_ID)

    def get_template_names(self) -> list[str]:
        """Выбрать полную страницу или один из двух фрагментов."""
        fragment = self.fragment()
        if not fragment:
            return [self.template_name]
        if fragment == SIMILAR_ID:
            return ["catalog/partials/_similar.html"]
        return ["catalog/partials/_territory_timeline.html"]

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """
        Собрать паспорт территории — или только то, о чём спросил фрагмент.

        Весь паспорт собирается около четверти секунды; похожим регионам он не нужен.
        """
        context = super().get_context_data(**kwargs)
        territory: Territory = self.object
        fragment = self.fragment()

        if fragment == SIMILAR_ID:
            context.update(_similar_context(self.request, territory))
            return context

        if not fragment:
            context["page_title"] = territory.name
            context["neighbours"] = _neighbours(territory)
            context["locator"] = locator_map(territory.code) if territory.is_region else None
            context.update(_similar_context(self.request, territory))

        try:
            if fragment == TIMELINE_ID:
                context.update(_timeline_context(self.request, territory))
            else:
                context.update(self._warehouse_context(territory))
            context["warehouse_ready"] = True
        except WarehouseNotBuiltError:
            context["warehouse_ready"] = False

        return context

    def _warehouse_context(self, territory: Territory) -> dict[str, Any]:
        """Собрать данные, читаемые из аналитического склада."""
        passport = build_passport(territory.code)
        positions = passport.by_key()

        metrics = [
            {
                **entry,
                "position": positions.get(entry["series"].key),
                "spark": sparkline_path([point["value"] for point in entry["sparkline"]]),
            }
            for entry in featured_snapshot(territory.code)
        ]

        return {
            "coverage": territory_coverage(territory.code),
            "now": now_rows(territory.code),
            "metrics": metrics,
            "passport": passport,
            "passport_columns": [
                {
                    "title": _("В числе лучших"),
                    "modifier": "strong",
                    "items": passport.strengths[:STRENGTH_LIMIT],
                    "more": passport.strengths[STRENGTH_LIMIT:],
                    "count": len(passport.strengths),
                    "empty": _("Ни по одному основному показателю регион не входит в число лучших"),
                },
                {
                    "title": _("В числе худших"),
                    "modifier": "weak",
                    "items": passport.weaknesses[:STRENGTH_LIMIT],
                    "more": passport.weaknesses[STRENGTH_LIMIT:],
                    "count": len(passport.weaknesses),
                    "empty": _("Ни по одному основному показателю регион не входит в число худших"),
                },
            ],
            "series_options": series_options,
            **_timeline_context(self.request, territory),
        }


class TerritoryCardView(DetailView):
    """
    Карточка региона во всплывающей панели живой карты: главное и показатели карты.

    Показатель, открытый на карте, стоит первым.
    """

    template_name = "catalog/partials/_territory_card.html"
    context_object_name = "territory"

    def get_object(self, queryset: Any = None) -> Territory:  # noqa: ARG002
        """Найти субъект по адресному идентификатору; округа и страна карточки не имеют."""
        return get_object_or_404(
            Territory.objects.comparable().select_related("parent"),
            slug=self.kwargs["slug"],
        )

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Собрать главное о регионе и положение по показателям живой карты."""
        from apps.core.showcase import live_series

        context = super().get_context_data(**kwargs)
        territory: Territory = self.object
        try:
            passport = build_passport(territory.code)
        except WarehouseNotBuiltError:
            context["passport"] = None
            return context

        positions = passport.by_key()
        chosen = self.request.GET.get("series", "")
        items = sorted(live_series(), key=lambda item: item.key != chosen)
        rows = [positions[item.key] for item in items if item.key in positions]
        # Открытый ряд стоит первым и тогда, когда его нет среди рядов витрины.
        if chosen and not any(position.series.key == chosen for position in rows):
            found = positions.get(chosen)
            if found is None:
                item = series_descriptor(chosen)
                found = position_for(item, territory.code) if item is not None else None
            if found is not None:
                rows.insert(0, found)
        context["passport"] = passport
        # Без строки о полноте сведений: она о паспорте, а не о регионе.
        context["summary"] = passport.summary[:3]
        context["positions"] = rows
        context["chosen"] = chosen
        return context


def _neighbours(territory: Territory) -> list[Territory]:
    """Сопредельные субъекты по той же матрице соседства, что у пространственного анализа."""
    return list(
        Territory.objects.filter(
            reverse_adjacencies__territory=territory,
        )
        .order_by("name_ru")
        .distinct()
    )


def _similar_context(request: Any, territory: Territory) -> dict[str, Any]:
    """
    Собрать перечень похожих регионов, если о нём попросили.

    Кнопка — обычная ссылка с признаком в адресе: ответ адресуем и работает без сценариев.
    """
    requested = bool(request.GET.get(SIMILAR_PARAM))
    parameters = request.GET.copy()
    parameters[SIMILAR_PARAM] = "1"

    context: dict[str, Any] = {
        "similar_requested": requested,
        "similar_url": f"{request.path}?{parameters.urlencode()}",
        "similar_id": SIMILAR_ID,
    }
    if not requested:
        return context

    try:
        context["similar"] = similar_territories(territory.code)
    except WarehouseNotBuiltError:
        # О несобранном складе уже сказано на странице.
        context["similar"] = None
    return context


def _timeline_context(request: Any, territory: Territory) -> dict[str, Any]:
    """
    Построить график динамики ряда: субъект на фоне округа, страны и коридора квартилей.

    Коридор — половина субъектов страны: по нему видно, обычен ли уровень региона.
    """
    # Паспорт показывает ряды склада: ключ ряда своей таблицы — непонятный параметр.
    value = request.GET.get("series")
    series = resolve_series(None if is_user_key(value) else value)
    if series is None:
        return {"timeline_series": None}

    codes = [territory.code]
    district = territory.parent if territory.is_region else None
    if district is not None:
        codes.append(district.code)
    codes.append(COUNTRY_CODE)

    timelines = series_timeline_multi(series.key, codes)
    years = sorted({point["year"] for points in timelines.values() for point in points})
    if not years:
        return {"timeline_series": series, "timeline_option": None}

    def line(
        code: str,
        name: str,
        *,
        country: bool = False,
        primary: bool = False,
        muted: bool = False,
    ) -> dict:
        values = {point["year"]: point["value"] for point in timelines.get(code, [])}
        return {
            "name": name,
            "values": [values.get(year) for year in years],
            "country": country,
            "primary": primary,
            "muted": muted,
        }

    lines = [line(territory.code, territory.name, primary=True)]
    if district is not None:
        lines.append(line(district.code, district.name, muted=True))
    lines.append(line(COUNTRY_CODE, str(_("Российская Федерация")), country=True, muted=True))

    unit = series.unit.short_name if series.unit else ""
    corridor = _corridor(series.key, years)
    return {
        "timeline_series": series,
        "timeline_option": timeline_option(years, lines, unit=unit, corridor=corridor),
        "timeline_years": years,
        "timeline_corridor": bool(corridor),
    }


def _corridor(series_key: str, years: list[int]) -> dict[str, list[Any]] | None:
    """
    Собрать коридор между первым и третьим квартилем по годам из витрины статистик.

    Годы без квартилей остаются пропусками.
    """
    statistics = {row["year"]: row for row in series_statistics_timeline(series_key)}
    if not statistics:
        return None

    lower: list[Any] = []
    band: list[Any] = []
    for year in years:
        row = statistics.get(year) or {}
        bottom, top = row.get("p25_value"), row.get("p75_value")
        if bottom is None or top is None:
            lower.append(None)
            band.append(None)
            continue
        lower.append(bottom)
        band.append(top - bottom)

    return {"lower": lower, "band": band} if any(value is not None for value in band) else None
