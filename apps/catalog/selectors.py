"""
Выбор ряда, года и территорий по параметрам запроса.

Непонятный параметр заменяется значением по умолчанию: старая ссылка открывает страницу.
"""

from __future__ import annotations

from typing import Any

from django.core.cache import cache
from django.db.models import QuerySet
from django.utils.translation import get_language, gettext

from apps.catalog.models import Indicator, Section, Series, Territory
from apps.core.search import words_of
from apps.warehouse.duckdb_client import warehouse_generation
from apps.warehouse.queries import FeaturedSeries, covered_years, featured_series, featured_set

# Время жизни кэша перечня рядов для выпадающих списков.
SERIES_OPTIONS_CACHE_TTL = 3600

# Словарь исправлений меняется только при пересборке справочника.
SEARCH_VOCABULARY_CACHE_TTL = 3600

# Наибольшее число территорий в сравнении: больше восьми линий неразличимы.
MAX_COMPARE = 8


def analysis_ready_series() -> QuerySet[Series]:
    """Ряды, пригодные для картограммы, рейтингов и сравнения."""
    return (
        Series.objects.filter(is_analysis_ready=True)
        .select_related("indicator", "indicator__section", "unit")
        .order_by("indicator__section__name_ru", "indicator__name_ru", "name_ru")
    )


# Редакция разметки перечня в адресе: браузер хранит перечень год.
SERIES_OPTIONS_REVISION = 3


def series_options_version() -> str:
    """Отпечаток перечня рядов: склад, основной набор и редакция разметки."""
    return f"{warehouse_generation()}-{featured_set().digest}-{SERIES_OPTIONS_REVISION}"


def featured_group_title() -> str:
    """Название первой группы перечня — основного набора."""
    return gettext("Основные показатели")


def featured_choice(series: Series) -> dict[str, Any]:
    """
    Подпись ряда в списке выбора: группа, название и пометка абсолютной величины.

    У ряда основного набора пометка берётся из набора, у остальных — по названию.
    """
    item = featured_set().by_key().get(series.key)
    if item is not None:
        return {
            "group": featured_group_title(),
            "title": item.short_title,
            "unnormalised": item.absolute,
        }
    title = series.indicator.name
    if series.has_subsection and series.name:
        title = f"{title} — {series.name}"
    return {
        "group": series.indicator.section.name,
        "title": title,
        "unnormalised": series.is_unnormalised,
    }


def _featured_option(item: FeaturedSeries, series: Series) -> dict[str, Any]:
    """Запись перечня для ряда основного набора."""
    full_title = series.indicator.name
    if series.has_subsection and series.name:
        full_title = f"{full_title} {series.name}"
    return {
        "key": series.key,
        "title": item.short_title,
        "subsection": "",
        "unnormalised": item.absolute,
        # Ряд ищут и по словам сборника, которых нет в коротком названии.
        "search": f"{series.indicator.section.name} {full_title}",
    }


def series_options() -> list[dict[str, Any]]:
    """
    Собрать перечень рядов для списка выбора: основной набор, затем пригодные ряды по разделам.

    Ряд основного набора стоит только в первой группе. Перечень отдаётся целиком и кэшируется.
    """
    key = f"catalog:series-options:{series_options_version()}:{get_language()}"
    cached = cache.get(key)
    if cached is not None:
        return cached

    featured = featured_set().by_key()
    ready: dict[str, Series] = {}
    groups: dict[str, dict[str, Any]] = {}
    for series in analysis_ready_series():
        if series.key in featured:
            ready[series.key] = series
            continue
        section = series.indicator.section
        group = groups.setdefault(section.slug, {"title": section.name, "items": []})
        group["items"].append(
            {
                "key": series.key,
                "title": series.indicator.name,
                "subsection": series.name if series.has_subsection else "",
                "unnormalised": series.is_unnormalised,
                "search": "",
            }
        )

    options = sorted(groups.values(), key=lambda item: item["title"])
    main = [
        _featured_option(item, ready[item.key]) for item in featured_series() if item.key in ready
    ]
    if main:
        options.insert(0, {"title": featured_group_title(), "items": main})

    # Раздел — и в самой записи: в свёрнутом виде групп нет, а отбор ищет и по разделу.
    for group in options:
        for item in group["items"]:
            item["group_title"] = group["title"]

    cache.set(key, options, SERIES_OPTIONS_CACHE_TTL)
    return options


def series_options_total() -> int:
    """Сколько всего рядов предлагается к выбору."""
    return sum(len(group["items"]) for group in series_options())


def selected_series_options(keys: list[str]) -> list[dict[str, Any]]:
    """Записи перечня для отмеченных рядов — чтобы свёрнутый выбор их не сбрасывал."""
    chosen = set(keys)
    return [item for group in series_options() for item in group["items"] if item["key"] in chosen]


def default_series_key() -> str | None:
    """
    Ряд для первого открытия страницы — с ролью ``default`` в основном наборе.

    Первый в наборе — численность населения, а по ней субъекты упорядочиваются по размеру.
    Если помеченного ряда в складе нет, берётся первый по порядку набора.
    """
    items = featured_series()
    keys = [item.key for item in items]
    if not keys:
        return analysis_ready_series().values_list("key", flat=True).first()

    existing = set(Series.objects.filter(key__in=keys).values_list("key", flat=True))

    preferred = [item.key for item in items if item.role == "default"]
    for key in [*preferred, *keys]:
        if key in existing:
            return key
    return analysis_ready_series().values_list("key", flat=True).first()


def resolve_series(value: str | None) -> Series | None:
    """Найти ряд по ключу из параметра запроса; при отсутствии или ошибке — ряд по умолчанию."""
    queryset = Series.objects.select_related("indicator", "indicator__section", "unit")

    if value:
        series = queryset.filter(key=value).first()
        if series is not None:
            return series

    key = default_series_key()
    return queryset.filter(key=key).first() if key else None


def resolve_year(value: str | None, years: list[int], *, default: int | None = None) -> int | None:
    """
    Выбрать год из числа доступных; по умолчанию — ``default`` или последний.

    Если запрошенного года нет, берётся ближайший: у рядов бывают пропуски целых лет.
    """
    if not years:
        return None

    if value:
        try:
            candidate = int(value)
        except TypeError, ValueError:
            candidate = None
        if candidate is not None:
            if candidate in years:
                return candidate
            return min(years, key=lambda year: (abs(year - candidate), year))

    return default if default in years else years[-1]


def default_year(counts: dict[int, int]) -> int | None:
    """Последний полный год ряда: значения не меньше чем у ``MIN_YEAR_COVERAGE`` субъектов."""
    full = covered_years(counts)
    return full[-1] if full else None


def resolve_territories(codes: list[str], *, limit: int = MAX_COMPARE) -> list[Territory]:
    """Отобрать территории по кодам в заданном порядке: от него зависят цвета линий."""
    if not codes:
        return []

    found = {
        territory.code: territory
        for territory in Territory.objects.comparable().with_district().filter(code__in=codes)
    }

    ordered: list[Territory] = []
    for code in codes:
        territory = found.get(code)
        if territory is not None and territory not in ordered:
            ordered.append(territory)
        if len(ordered) >= limit:
            break
    return ordered


def default_territories(limit: int = 4, *, preferred: str = "") -> list[Territory]:
    """Территории для первого открытия сравнения: из разных округов, свой регион — первым."""
    codes = ["RU-MOW", "RU-SPE", "RU-TA", "RU-SVE", "RU-NVS", "RU-KDA"]
    if preferred:
        codes = [preferred] + [code for code in codes if code != preferred]
    return resolve_territories(codes[:limit], limit=limit)


def grouped_territories() -> list[dict[str, Any]]:
    """Сгруппировать субъекты по федеральным округам; территории вне округов — отдельно."""
    key = f"catalog:grouped-territories:{warehouse_generation()}"
    cached = cache.get(key)
    if cached is not None:
        return cached

    groups: dict[str, dict[str, Any]] = {}
    for territory in (
        Territory.objects.comparable().with_district().order_by("parent__display_order", "name_ru")
    ):
        district = territory.parent
        group_key = district.code if district else "none"
        groups.setdefault(group_key, {"district": district, "items": []})["items"].append(territory)
    result = list(groups.values())
    cache.set(key, result, SERIES_OPTIONS_CACHE_TTL)
    return result


def search_vocabulary() -> list[str]:
    """Словарь слов из названий справочника на обоих языках — для исправления опечаток."""
    key = "catalog:search-vocabulary:1"
    cached = cache.get(key)
    if cached is not None:
        return cached

    texts: list[str] = []
    for model, fields in (
        (Indicator, ("name_ru", "name_en")),
        (Series, ("name_ru", "name_en")),
        (Territory, ("name_ru", "name_en", "capital_ru", "capital_en")),
        (Section, ("name_ru", "name_en")),
    ):
        for row in model.objects.values_list(*fields):
            texts.extend(value for value in row if value)

    vocabulary = sorted(words_of(*texts))
    cache.set(key, vocabulary, SEARCH_VOCABULARY_CACHE_TTL)
    return vocabulary
