"""
Каталог показателей и перечень регионов, быстрый переход и перечни рядов для выбора.

Без параметров каталог открывает основной набор по темам, с любым — полный перечень рядов.
"""

from __future__ import annotations

import re
from typing import Any

from django.core.cache import cache
from django.db.models import Count, QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.cache import patch_cache_control
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _
from django.views.generic import ListView

from apps.catalog.models import Section, Series, Territory
from apps.catalog.selectors import (
    selected_series_options,
    series_options,
    series_options_total,
    series_options_version,
)
from apps.core.navigation import Crumb, build_breadcrumbs
from apps.core.search import search_q
from apps.core.showcase import country_tiles
from apps.core.templatetags.formatting import ru_number
from apps.core.views import BreadcrumbMixin
from apps.maps.locator import locator_map
from apps.search.constants import EXAMPLES as SEARCH_EXAMPLES
from apps.warehouse.duckdb_client import WarehouseNotBuiltError
from apps.warehouse.queries import featured_set, latest_values_matrix

# Число рядов на странице каталога.
PAGE_SIZE = 25

# Допустимые способы упорядочивания каталога.
SORT_OPTIONS: dict[str, tuple[str, ...]] = {
    "coverage": ("-region_coverage", "-year_count", "indicator__name_ru"),
    "years": ("-year_count", "-region_coverage", "indicator__name_ru"),
    "name": ("indicator__name_ru", "name_ru"),
    "recent": ("-last_year", "-region_coverage"),
}
DEFAULT_SORT = "coverage"

# Наименьшая длина поискового запроса.
MIN_QUERY_LENGTH = 2

# Поля поиска ряда, с английскими названиями для английской версии.
SERIES_SEARCH_FIELDS = (
    "indicator__name_ru",
    "indicator__name_en",
    "name_ru",
    "name_en",
    "indicator__code",
    "indicator__section__name_ru",
    "indicator__section__name_en",
)

# Поля, по которым ищется субъект: название, столица и код.
TERRITORY_SEARCH_FIELDS = ("name_ru", "name_en", "capital_ru", "capital_en", "code")

# Ряд численности хранится в тысячах человек: с тысячи тысяч — «млн».
THOUSANDS_IN_MILLION = 1000

# Сколько самых населённых регионов предлагать быстрыми входами.
LARGEST_REGIONS = 6

# Хвост названия округа, который в оглавлении перечня регионов не нужен.
DISTRICT_SUFFIX = re.compile(r"\s+(федеральный округ|Federal District)$", re.IGNORECASE)


class SeriesListView(BreadcrumbMixin, ListView):
    """Каталог рядов наблюдений."""

    template_name = "catalog/indicator_list.html"
    context_object_name = "series_list"
    paginate_by = PAGE_SIZE

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """
        Открыть основной набор по темам или полный перечень.

        Форма перечня отправляет режим скрытым полем: снятие отборов не возвращает к темам.
        """
        if not request.GET and not request.headers.get("HX-Request"):
            return render(request, "catalog/indicator_themes.html", _themes_context(request))
        return super().get(request, *args, **kwargs)

    def get_crumbs(self) -> tuple[Crumb, ...]:
        """Путь к странице."""
        return (
            Crumb(title=_("Показатели"), url=reverse("catalog:indicator-list")),
            Crumb(title=_("Полный перечень")),
        )

    def get_queryset(self) -> QuerySet[Series]:
        """Собрать выборку с учётом поискового запроса и фасетных фильтров."""
        queryset = Series.objects.select_related("indicator", "indicator__section", "unit")

        query = self.request.GET.get("q", "").strip()
        if len(query) >= MIN_QUERY_LENGTH:
            queryset = queryset.filter(search_q(query, *SERIES_SEARCH_FIELDS))

        sections = self.request.GET.getlist("section")
        if sections:
            queryset = queryset.filter(indicator__section__slug__in=sections)

        units = self.request.GET.getlist("unit")
        if units:
            queryset = queryset.filter(unit__kind__in=units)

        if self.request.GET.get("ready") == "1":
            queryset = queryset.filter(is_analysis_ready=True)

        if self.request.GET.get("comparable") == "1":
            queryset = queryset.filter(break_count=0)

        sort = self.request.GET.get("sort", DEFAULT_SORT)
        ordering = SORT_OPTIONS.get(sort, SORT_OPTIONS[DEFAULT_SORT])
        return queryset.order_by(*ordering)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Дополнить контекст фасетами и текущими параметрами выборки."""
        context = super().get_context_data(**kwargs)
        context["page_title"] = _("Полный перечень рядов")
        context["query"] = self.request.GET.get("q", "").strip()
        context["selected_sections"] = self.request.GET.getlist("section")
        context["selected_units"] = self.request.GET.getlist("unit")
        context["only_ready"] = self.request.GET.get("ready") == "1"
        context["only_comparable"] = self.request.GET.get("comparable") == "1"
        context["sort"] = self.request.GET.get("sort", DEFAULT_SORT)
        context["sections"] = _section_facets()
        context["unit_kinds"] = _unit_facets()
        context["total_series"] = Series.objects.count()
        context["ready_series"] = Series.objects.filter(is_analysis_ready=True).count()
        return context

    def get_template_names(self) -> list[str]:
        """Выбрать шаблон: запросу HTMX — фрагмент таблицы результатов."""
        if self.request.headers.get("HX-Request"):
            return ["catalog/partials/_series_results.html"]
        return [self.template_name]


def headcount(thousands: float | None) -> str:
    """Численность населения из тысяч человек словами: «4,0 млн чел.», «527 тыс. чел.»."""
    if thousands is None:
        return ""
    if thousands >= THOUSANDS_IN_MILLION:
        return _("%(value)s млн чел.") % {"value": ru_number(thousands / THOUSANDS_IN_MILLION, 1)}
    return _("%(value)s тыс. чел.") % {"value": ru_number(thousands, 0)}


def region_population(codes: list[str]) -> dict[str, dict[str, Any]]:
    """Последняя численность населения по субъектам (роль ``population``); без склада — пусто."""
    item = next((row for row in featured_set().series if row.role == "population"), None)
    if item is None or not codes:
        return {}
    try:
        matrix = latest_values_matrix([item.key], codes)
    except WarehouseNotBuiltError:
        return {}
    return {code: row for (_key, code), row in matrix.items()}


class TerritoryListView(BreadcrumbMixin, ListView):
    """Перечень регионов по федеральным округам с картой-указателем и поиском."""

    template_name = "catalog/territory_list.html"
    context_object_name = "territories"

    def get_crumbs(self) -> tuple[Crumb, ...]:
        """Путь к странице."""
        return (Crumb(title=_("Регионы")),)

    def get_queryset(self) -> QuerySet[Territory]:
        """Выбрать субъекты Российской Федерации с их федеральными округами."""
        queryset = Territory.objects.comparable().with_district()

        query = self.request.GET.get("q", "").strip()
        if query:
            queryset = queryset.filter(search_q(query, *TERRITORY_SEARCH_FIELDS))

        district = self.request.GET.get("district", "")
        if district:
            queryset = queryset.filter(parent__code=district)

        return queryset.order_by("parent__display_order", "name_ru")

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Дополнить контекст перечнем округов и группировкой субъектов."""
        context = super().get_context_data(**kwargs)
        context["page_title"] = _("Регионы")
        context["query"] = self.request.GET.get("q", "").strip()
        context["selected_district"] = self.request.GET.get("district", "")

        districts = list(
            Territory.objects.federal_districts()
            .annotate(region_count=Count("children"))
            .order_by("display_order")
        )
        context["districts"] = districts

        territories = list(context["territories"])
        population = region_population([territory.code for territory in territories])

        grouped: dict[str, dict[str, Any]] = {}
        for territory in territories:
            district = territory.parent
            key = district.code if district else "none"
            group = grouped.setdefault(
                key, {"district": district, "territories": [], "rows": [], "people": 0.0}
            )
            people = population.get(territory.code)
            group["territories"].append(territory)
            group["rows"].append(
                {"territory": territory, "people": headcount(people["value"] if people else None)}
            )
            if people:
                group["people"] += people["value"]
        for group in grouped.values():
            group["people_text"] = headcount(group["people"]) if group["people"] else ""
            # В оглавлении — название без «федеральный округ».
            name = group["district"].name if group["district"] else str(_("Без округа"))
            group["short_title"] = DISTRICT_SUFFIX.sub("", name)
        context["grouped"] = list(grouped.values())
        context["total_regions"] = Territory.objects.comparable().count()
        context["population_year"] = max((row["year"] for row in population.values()), default=None)
        # Самые населённые регионы — быстрые входы.
        by_code = {territory.code: territory for territory in territories}
        context["largest"] = [
            by_code[code]
            for code, _row in sorted(
                population.items(), key=lambda item: item[1]["value"], reverse=True
            )[:LARGEST_REGIONS]
            if code in by_code
        ]
        # Карта-указатель без выбранного региона: каждый субъект — ссылка на паспорт.
        context["regions_map"] = locator_map("")
        return context


def series_picker(request: HttpRequest) -> HttpResponse:
    """
    Перечень рядов для выбора нескольких показателей — отдельным фрагментом.

    Развёрнутый перечень — около 400 КБ разметки. Без сценариев страница открывается
    с параметром ``picker``; отмеченные ряды приходят параметром ``series``.
    """
    from apps.userdata.series import chosen_option_groups

    # Ряд, названный дважды, отмечается один раз; свои ряды — только уже выбранные.
    keys = list(dict.fromkeys(request.GET.getlist("series")))
    own_groups = chosen_option_groups(keys)
    return render(
        request,
        "partials/_series_picker.html",
        {
            "series_options": series_options,
            "own_groups": own_groups,
            "selected_options": selected_series_options(keys),
            "selected_keys": keys,
            "series_total": series_options_total(),
            "picker_open": bool(request.GET.get("picker")),
        },
    )


# Сколько браузер хранит перечень рядов, запрошенный с действующим отпечатком склада.
SERIES_OPTIONS_MAX_AGE = 365 * 24 * 3600


def series_options_markup(request: HttpRequest) -> HttpResponse:
    """
    Пункты списка выбора ряда — основной набор и все пригодные ряды по разделам.

    Адрес несёт отпечаток перечня и язык, поэтому браузер хранит ответ год;
    запрос с устаревшим отпечатком получает свежий перечень без права хранения.
    """
    version = series_options_version()
    key = f"catalog:series-options-markup:{version}:{get_language()}"
    markup = cache.get(key)
    if markup is None:
        markup = render_to_string(
            "partials/_series_options.html", {"series_options": series_options()}
        ).strip()
        cache.set(key, markup, SERIES_OPTIONS_MAX_AGE)

    response = HttpResponse(markup, content_type="text/html; charset=utf-8")
    if request.GET.get("v") == version:
        patch_cache_control(response, public=True, max_age=SERIES_OPTIONS_MAX_AGE, immutable=True)
    else:
        patch_cache_control(response, no_cache=True)
    return response


# ---------------------------------------------------------------------------------------
# Основной набор по темам
# ---------------------------------------------------------------------------------------


def _themes_context(request: HttpRequest) -> dict[str, Any]:
    """
    Собрать каталог основного набора: темы, показатели, значения по России, вопросы.

    Ряд, которого нет в справочнике, пропускается.
    """
    featured = featured_set()
    keys = [item.key for item in featured.series]
    known = {
        series.key: series
        for series in Series.objects.filter(key__in=keys).select_related("indicator")
    }

    context: dict[str, Any] = {"warehouse_ready": True}
    try:
        # Плитки — те же, что на главной.
        tiles = {tile["series"].key: tile for tile in country_tiles(headline_only=False)}
    except WarehouseNotBuiltError:
        tiles = {}
        context["warehouse_ready"] = False

    themes: list[dict[str, Any]] = []
    for theme, items in featured.grouped():
        rows = [
            {"item": item, "series": known[item.key], "tile": tiles.get(item.key)}
            for item in items
            if item.key in known
        ]
        if rows:
            themes.append({"theme": theme, "rows": rows})

    rows_in_order = sorted(
        (row for block in themes for row in block["rows"]), key=lambda row: row["item"].order
    )
    context.update(
        page_title=_("Показатели"),
        breadcrumbs=build_breadcrumbs(request, Crumb(title=_("Показатели"))),
        themes=themes,
        search_examples=SEARCH_EXAMPLES,
        featured_total=len(rows_in_order),
        total_series=Series.objects.count(),
    )
    return context


# ---------------------------------------------------------------------------------------
# Фасеты
# ---------------------------------------------------------------------------------------


def _section_facets() -> list[dict[str, Any]]:
    """Собрать перечень разделов с числом рядов; название — на языке запроса."""
    sections = (
        Section.objects.annotate(count=Count("indicators__series"))
        .filter(count__gt=0)
        .order_by("-count")
    )
    return [
        {"slug": section.slug, "name": section.name, "count": section.count} for section in sections
    ]


def _unit_facets() -> list[dict[str, Any]]:
    """Собрать перечень категорий единиц измерения."""
    from apps.catalog.constants import UnitKind

    counts = dict(
        Series.objects.filter(unit__isnull=False)
        .values_list("unit__kind")
        .annotate(count=Count("id"))
    )

    return [
        {"code": code, "title": title, "count": counts.get(code, 0)}
        for code, title in UnitKind.choices
        if counts.get(code, 0) > 0
    ]
