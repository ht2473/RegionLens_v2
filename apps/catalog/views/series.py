"""Страница показателя; выводы словами собирает :mod:`apps.catalog.indicator`."""

from __future__ import annotations

from contextlib import suppress
from typing import Any

from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _
from django.views.generic import DetailView

from apps.catalog.indicator import (
    build_lead,
    country_break_years,
    country_tile,
    describe_series,
    theme_neighbours,
    theme_of,
    unit_name,
)
from apps.catalog.models import Indicator, Series, Territory
from apps.catalog.monthly import month_block
from apps.catalog.status import series_status
from apps.core import showcase, structured_data
from apps.core.charts import timeline_option
from apps.core.navigation import Crumb
from apps.core.views import BreadcrumbMixin
from apps.sources.registry import registry
from apps.warehouse.duckdb_client import WarehouseNotBuiltError
from apps.warehouse.queries import (
    COUNTRY_CODE,
    FeaturedSeries,
    series_editions,
    series_leaders,
    series_revisions,
    series_statistics_timeline,
    series_timeline,
)

# Число пересмотренных наблюдений в таблице.
REVISIONS_LIMIT = 12

# Сколько методических примечаний источника показывается под раскрытием.
NOTES_LIMIT = 8

# До скольких разрезов выбор — пилюлями, дальше — списком (у показателя их бывает до 46).
VARIANT_CHIPS_LIMIT = 8

# Опознаватель живой карты на странице показателя.
INDICATOR_MAP_ID = "indicator-map"

# Число регионов в перечнях крайних, когда живой карты нет.
FALLBACK_LEADERS = 5

# Цвет середины регионов на графике — как у подписи под ним и хода на плитках.
MEDIAN_COLOUR = "var(--accent)"
MEDIAN_INK = "var(--accent-ink)"


class SeriesDetailView(BreadcrumbMixin, DetailView):
    """Страница показателя с выбором разреза."""

    template_name = "catalog/series_detail.html"
    context_object_name = "indicator"

    def get_object(self, queryset: Any = None) -> Indicator:  # noqa: ARG002
        """Найти показатель по адресному идентификатору."""
        return get_object_or_404(
            Indicator.objects.select_related("section"),
            slug=self.kwargs["slug"],
        )

    def get_crumbs(self) -> tuple[Crumb, ...]:
        """Путь к странице: у показателя основного набора — через его тему."""
        crumbs = [Crumb(title=_("Показатели"), url=reverse("catalog:indicator-list"))]
        theme = getattr(self, "theme", None)
        if theme is not None:
            crumbs.append(
                Crumb(
                    title=theme.title,
                    url=f"{reverse('catalog:indicator-list')}#theme-{theme.slug}",
                )
            )
        crumbs.append(Crumb(title=getattr(self, "page_heading", self.object.name)))
        return tuple(crumbs)

    def get_template_names(self) -> list[str]:
        """Смена разреза заменяет страницу под шапкой сайта целиком."""
        if self.request.headers.get("HX-Request"):
            return ["catalog/partials/_indicator_page.html"]
        return [self.template_name]

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Собрать страницу показателя."""
        indicator: Indicator = self.object
        variants = list(
            Series.objects.filter(indicator=indicator)
            .select_related("unit", "indicator")
            .order_by("-region_coverage", "name_ru")
        )
        series = _resolve_variant(self.request.GET.get("series"), variants)

        self.theme = None
        self.page_heading = indicator.name
        item: FeaturedSeries | None = None
        if series is not None:
            # Без склада страница показывает только сведения справочника.
            with suppress(WarehouseNotBuiltError):
                item = describe_series(series)
        if item is not None and item.theme:
            self.theme = theme_of(item)
            self.page_heading = item.short_title

        context = super().get_context_data(**kwargs)
        context["page_title"] = self.page_heading
        context["heading"] = self.page_heading
        context["variants"] = variants
        context["variant_chips"] = len(variants) <= VARIANT_CHIPS_LIMIT
        context["series"] = series
        context["item"] = item
        context["theme"] = self.theme
        context["live_map_id"] = INDICATOR_MAP_ID

        if series is None:
            context["warehouse_ready"] = False
            return context

        context.update(_series_context(self.request, series, item, self.page_heading))
        breaks = context["breaks"]

        if item is None:
            context["warehouse_ready"] = False
            return context
        try:
            context.update(_warehouse_context(self.request, series, item, breaks))
            context["warehouse_ready"] = True
        except WarehouseNotBuiltError:
            context["warehouse_ready"] = False
        return context


def _resolve_variant(value: str | None, variants: list[Series]) -> Series | None:
    """Выбрать разрез показателя; по умолчанию — с наибольшим покрытием субъектов."""
    if value:
        for series in variants:
            if series.key == value:
                return series
    return variants[0] if variants else None


def _series_context(
    request: Any, series: Series, item: FeaturedSeries | None, heading: str
) -> dict[str, Any]:
    """Сведения о ряде из справочника: формулировка, разрывы, примечания, переходы."""
    context: dict[str, Any] = {}
    # Формулировка сборника — мелко под коротким названием.
    if series.full_title != heading:
        context["source_title"] = series.full_title
    # Единица сборника — только вне основного набора: у рядов набора она выверена вручную.
    if item is not None and not item.theme:
        context["unit_name"] = unit_name(series)
    context["glossary_terms"] = list(series.indicator.glossary_terms.all()[:6])
    context["status"] = series_status(series.key)
    # Ряд проекта из выпусков Банка России или ФНС, а не из сборника.
    context["collected"] = series.key in registry().by_key

    # Описание ряда для поиска по данным; адрес — с выбранным разрезом.
    context["structured_data"] = structured_data.series_dataset(
        request,
        series,
        f"{request.path}?series={series.key}",
    )
    context["page_description"] = series.full_title

    breaks = list(series.breaks.select_related("note", "territory").order_by("year"))
    context["breaks"] = breaks
    context["break_years"] = sorted({entry.year for entry in breaks})
    notes = series.methodology_notes.filter(affects_comparability=True).order_by(
        "-occurrence_count"
    )
    context["notes"] = list(notes[:NOTES_LIMIT])
    context["notes_total"] = notes.count()
    context["links"] = _deep_links(series)
    context["territories_total"] = Territory.objects.comparable().count()
    return context


def _warehouse_context(
    request: Any, series: Series, item: FeaturedSeries, breaks: list[Any]
) -> dict[str, Any]:
    """Собрать данные ряда из аналитического склада."""
    statistics = series_statistics_timeline(series.key)
    country = series_timeline(series.key, COUNTRY_CODE)
    broken = country_break_years(breaks)

    live = showcase.live_map(item, request.GET.get("year"))
    context: dict[str, Any] = {
        "live": live,
        "lead": build_lead(item, live, statistics, broken) if live else None,
        "country": country_tile(item, broken),
        "timeline_option": _timeline_chart(item, country, statistics, breaks),
        "timeline_years": _span(statistics, country),
        "editions": series_editions(series.key),
        "revisions": series_revisions(series.key, limit=REVISIONS_LIMIT),
        "neighbours": theme_neighbours(item),
        "month": month_block(series.key),
    }
    if live is None:
        # Живой карты нет, если ни за один год не ранжировано 60 субъектов.
        context.update(_fallback_leaders(series, statistics))
    return context


def _span(
    statistics: list[dict[str, Any]], country: list[dict[str, Any]]
) -> tuple[int, int] | None:
    """Первый и последний год графика динамики."""
    years = sorted({row["year"] for row in statistics} | {row["year"] for row in country})
    return (years[0], years[-1]) if years else None


def _fallback_leaders(series: Series, statistics: list[dict[str, Any]]) -> dict[str, Any]:
    """Крайние регионы последнего года, когда живой карты нет."""
    years = [row["year"] for row in statistics if row["observations"]]
    if not years:
        return {}
    year = years[-1]
    english = get_language() == "en"

    def rows(ascending: bool) -> list[dict[str, Any]]:
        return [
            {
                "name": row["name_en"] if english and row["name_en"] else row["name_ru"],
                "value": row["value"],
            }
            for row in series_leaders(series.key, year, limit=FALLBACK_LEADERS, ascending=ascending)
        ]

    return {
        "fallback_year": year,
        "fallback_top": rows(ascending=False),
        "fallback_bottom": rows(ascending=True),
    }


def _deep_links(series: Series) -> list[dict[str, Any]]:
    """Переходы с этим рядом в пять представлений рабочей поверхности и инструменты анализа."""
    query = f"?series={series.key}"
    links = [
        {
            "href": reverse("maps:choropleth") + query,
            "icon": "map",
            "title": _("Карта"),
            "text": _("Способ разбиения шкалы, округа, подписи значений"),
        },
        {
            "href": reverse("compare:index") + query,
            "icon": "dynamics",
            "title": _("Динамика"),
            "text": _("Ход величины в выбранных регионах рядом со страной"),
        },
        {
            "href": reverse("rankings:index") + query,
            "icon": "ranking",
            "title": _("Рейтинг"),
            "text": _("Все регионы по местам и кто поднялся или опустился"),
        },
        {
            "href": reverse("surface:distribution") + query,
            "icon": "distribution",
            "title": _("Распределение"),
            "text": _("Как значения регионов расположены на шкале"),
        },
        {
            "href": reverse("surface:table") + query,
            "icon": "table",
            "title": _("Таблица"),
            "text": _("Регионы по строкам, годы по столбцам"),
        },
        {
            "href": reverse("analytics:inequality") + query,
            "icon": "inequality",
            "title": _("Неравенство"),
            "text": _("Джини, децильный коэффициент и сближение во времени"),
        },
        {
            "href": reverse("analytics:spatial") + query,
            "icon": "spatial",
            "title": _("Пространственный анализ"),
            "text": _("Похожи ли значения соседних регионов"),
        },
    ]
    if series.revision_count:
        links.append(
            {
                "href": reverse("analytics:revisions") + query,
                "icon": "revisions",
                "title": _("Пересмотры статистики"),
                "text": _("Как Росстат уточнял значения от выпуска к выпуску"),
            }
        )
    return links


def _timeline_chart(
    item: FeaturedSeries,
    country: list[dict[str, Any]],
    statistics: list[dict[str, Any]],
    breaks: list[Any],
) -> dict[str, Any] | None:
    """Построить график динамики: линия страны на фоне коридора квартилей по субъектам."""
    years = sorted({row["year"] for row in statistics} | {row["year"] for row in country})
    if not years:
        return None

    country_values = {row["year"]: row["value"] for row in country}
    lower = {row["year"]: row["p25_value"] for row in statistics}
    upper = {row["year"]: row["p75_value"] for row in statistics}
    median = {row["year"]: row["median_value"] for row in statistics}

    corridor = {
        "lower": [lower.get(year) for year in years],
        "band": [
            (upper[year] - lower[year])
            if lower.get(year) is not None and upper.get(year) is not None
            else None
            for year in years
        ],
    }

    lines = [
        {
            "name": str(_("Россия")),
            "values": [country_values.get(year) for year in years],
            "country": True,
        },
        {
            "name": str(_("Середина регионов")),
            "values": [median.get(year) for year in years],
            "primary": True,
            "colour": MEDIAN_COLOUR,
            "ink": MEDIAN_INK,
        },
    ]

    marks = [
        {"year": entry.year, "label": str(entry.get_kind_display())}
        for entry in breaks
        if entry.territory_id is None and entry.year in years
    ]
    return timeline_option(years, lines, unit=item.unit_label, breaks=marks, corridor=corridor)
