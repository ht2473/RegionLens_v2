"""
Разбор параметров аналитических страниц, справочные перечни и кэш расчётов.

Непонятный параметр заменяется значением по умолчанию; ключ кэша несёт отпечаток склада.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any, cast

from django.conf import settings
from django.core.cache import cache
from django.http import Http404
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _

from apps.catalog.constants import BreakKind
from apps.catalog.models import Series, SeriesBreak, Territory, TerritoryAdjacency
from apps.catalog.selectors import analysis_ready_series, resolve_series
from apps.warehouse.queries import (
    MIN_YEAR_COVERAGE,
    featured_series,
    region_matrix,
    series_covered_years,
)
from apps.warehouse.routing import generation_of, is_user_key

from .core.composition import Composition, Outsider

# Наибольшее число рядов, одновременно участвующих в расчёте.
MAX_SERIES = 12

# Число рядов, предлагаемых при первом открытии страницы.
DEFAULT_SERIES_COUNT = 6

# Наименьшее число рядов для многомерных инструментов.
MIN_SERIES = 2

# Время жизни кэша справочных перечней приложения.
DIRECTORY_CACHE_TTL = 3600

# Состав субъектов в динамике мер: постоянный — субъекты со значениями во всех годах окна.
COMPOSITION_CONSTANT = "constant"
COMPOSITION_ALL = "all"
COMPOSITIONS: dict[str, Any] = {
    COMPOSITION_CONSTANT: _("постоянный: только субъекты со значениями во всех годах"),
    COMPOSITION_ALL: _("все субъекты, по которым есть значения в каждом году"),
}

# Типы связей матрицы соседства, соответствующие схемам пространственного анализа.
ADJACENCY_SCHEMES: dict[str, tuple[str, ...]] = {
    "land": ("land",),
    "all": ("land", "sea", "bridge"),
}


# ---------------------------------------------------------------------------------------
# Ряды наблюдений
# ---------------------------------------------------------------------------------------


def default_series_keys(count: int = DEFAULT_SERIES_COUNT) -> list[str]:
    """
    Подобрать пригодные ряды основного набора для первого открытия многомерной страницы.

    Ряд с ролью ``default`` — первым: он задаёт первую строку матрицы и первую ось.
    """
    items = featured_series()
    preferred = [item.key for item in items if item.role == "default"]
    keys = preferred + [item.key for item in items if item.key not in preferred]
    available = set(analysis_ready_series().filter(key__in=keys).values_list("key", flat=True))
    ordered = [key for key in keys if key in available]
    if len(ordered) >= MIN_SERIES:
        return ordered[:count]

    return list(analysis_ready_series().values_list("key", flat=True)[:count])


def resolve_many(
    values: list[str],
    *,
    limit: int = MAX_SERIES,
    fallback: int = DEFAULT_SERIES_COUNT,
) -> list[Series]:
    """Отобрать ряды по ключам из параметров запроса в порядке выбора."""
    keys = [value for value in values if value]
    if not keys:
        keys = default_series_keys(fallback)

    found: dict[str, Series] = {
        series.key: series
        for series in Series.objects.filter(key__in=keys).select_related(
            "indicator", "indicator__section", "unit"
        )
    }
    own = [key for key in keys if is_user_key(key)]
    if own:
        from apps.userdata.series import user_series_many

        # Ряд таблицы — только доступной тому, кто спрашивает; чужой — 404, как везде.
        found_own = user_series_many(own)
        if len(found_own) < len(set(own)):
            raise Http404
        found.update(cast(dict[str, Series], found_own))

    ordered: list[Series] = []
    for key in keys:
        series = found.get(key)
        if series is not None and series not in ordered:
            ordered.append(series)
        if len(ordered) >= limit:
            break
    return ordered


def resolve_one(value: str | None) -> Series | None:
    """Отобрать один ряд; при неизвестном ключе берётся ряд по умолчанию, чужая таблица — 404."""
    return resolve_series(value)


def population_series_key() -> str | None:
    """Найти ряд численности населения (роль ``population``) для взвешивания мер неравенства."""
    for item in featured_series():
        if item.role == "population":
            return item.key
    return None


def series_label(series: Series) -> str:
    """Краткая подпись ряда для осей диаграмм и заголовков столбцов."""
    title = series.indicator.name
    return f"{title} — {series.name}" if series.has_subsection and series.name else title


def label_lang(series: Series, label: str) -> str:
    """«ru» у подписи из таблицы пользователя по-русски на странице другого языка."""
    if not getattr(series, "is_user", False):
        return ""
    from apps.userdata.templatetags.userdata import source_lang

    return source_lang(label)


def too_few_regions(series: Series | None) -> bool:
    """Ряд таблицы с малым числом субъектов: неравенство и пространственный анализ не считаются."""
    from apps.userdata.series import FEW_REGIONS

    record = getattr(series, "record", None)
    return record is not None and record.regions_count < FEW_REGIONS


def series_columns(selected: list[Series], year: int) -> list[dict[str, Any]]:
    """
    Собрать значения выбранных рядов по субъектам за год в общем порядке субъектов.

    Для ряда без полных данных за этот год берётся ближайший полный, и год возвращается
    вместе со значениями.
    """
    rows = region_rows()
    codes = [row["code"] for row in rows]
    matrix = region_matrix([series.key for series in selected], year)

    columns: list[dict[str, Any]] = []
    for series in selected:
        entry = matrix.get(series.key, {"year": None, "values": {}})
        values = entry["values"]
        label = series_label(series)
        columns.append(
            {
                "key": series.key,
                "label": label,
                "lang": label_lang(series, label),
                "short": series.indicator.name,
                "unit": series.unit.short_name if series.unit else "",
                "polarity": series.polarity,
                "year": entry["year"],
                "covered": len(values),
                "values": [values.get(code) for code in codes],
                "series": series,
            }
        )
    return columns


# ---------------------------------------------------------------------------------------
# Территории
# ---------------------------------------------------------------------------------------


def region_rows() -> list[dict[str, Any]]:
    """Получить перечень субъектов в порядке показа — общую ось всех расчётов."""
    key = f"analytics:regions:{get_language()}"
    cached = cache.get(key)
    if cached is not None:
        return cached

    districts = {district.code: district.name for district in Territory.objects.federal_districts()}
    rows = [
        {
            "code": territory.code,
            "name": territory.name,
            "slug": territory.slug,
            "abbreviation": territory.short_code,
            "district_code": territory.parent.code if territory.parent else "",
            "district_name": districts.get(territory.parent.code, "") if territory.parent else "",
            "tile_x": territory.tile_x,
            "tile_y": territory.tile_y,
            "latitude": float(territory.latitude) if territory.latitude is not None else None,
            "longitude": float(territory.longitude) if territory.longitude is not None else None,
        }
        for territory in Territory.objects.comparable()
        .with_district()
        .only(
            "code",
            "slug",
            "name_ru",
            "name_en",
            "abbreviation",
            "tile_x",
            "tile_y",
            "latitude",
            "longitude",
            "parent",
        )
    ]

    cache.set(key, rows, DIRECTORY_CACHE_TTL)
    return rows


def district_rows() -> list[dict[str, Any]]:
    """Получить перечень федеральных округов для разложения по группам."""
    return [
        {"code": district.code, "name": district.name, "abbreviation": district.short_code}
        for district in Territory.objects.federal_districts().order_by("display_order")
    ]


def neighbour_map(scheme: str = "all") -> dict[str, list[str]]:
    """Собрать матрицу соседства: ``land`` — сухопутные границы, ``all`` — ещё мосты и море."""
    kinds = ADJACENCY_SCHEMES.get(scheme, ADJACENCY_SCHEMES["all"])
    key = f"analytics:adjacency:{scheme}"
    cached = cache.get(key)
    if cached is not None:
        return cached

    result: dict[str, list[str]] = {}
    pairs = TerritoryAdjacency.objects.filter(kind__in=kinds).values_list(
        "territory__code", "neighbour__code"
    )
    for code, neighbour in pairs:
        result.setdefault(code, []).append(neighbour)

    cache.set(key, result, DIRECTORY_CACHE_TTL)
    return result


def coordinates() -> dict[str, tuple[float, float]]:
    """Получить координаты центров субъектов для схемы соседства по расстоянию."""
    return {
        row["code"]: (row["latitude"], row["longitude"])
        for row in region_rows()
        if row["latitude"] is not None and row["longitude"] is not None
    }


# ---------------------------------------------------------------------------------------
# Параметры запроса
# ---------------------------------------------------------------------------------------


def resolve_choice(value: str | None, allowed: dict[str, Any], default: str) -> str:
    """Выбрать значение из перечня допустимых; при неизвестном берётся значение по умолчанию."""
    return value if value in allowed else default


def resolve_span(
    first: str | None,
    last: str | None,
    years: list[int],
    *,
    default: tuple[int, int],
    min_span: int,
) -> tuple[int, int]:
    """
    Определить границы отрезка по ближайшим годам ряда; перепутанные меняются местами.

    Отрезок короче ``min_span`` лет заменяется отрезком по умолчанию.
    """

    def parse(value: str | None, fallback: int) -> int:
        try:
            candidate = int(value) if value else fallback
        except TypeError, ValueError:
            return fallback
        return min(years, key=lambda year: abs(year - candidate))

    start = parse(first, default[0])
    end = parse(last, default[1])
    if start > end:
        start, end = end, start
    if end - start < min_span:
        return default
    return start, end


def resolve_float(value: str | None, *, default: float, low: float, high: float) -> float:
    """Разобрать вещественный параметр и удержать его в допустимых границах."""
    try:
        number = float(str(value).replace(",", ".")) if value else default
    except TypeError, ValueError:
        return default
    return max(low, min(high, number))


def latest_full_year(series_keys: list[str]) -> int | None:
    """Год по умолчанию для нескольких рядов — самый поздний из последних полных лет рядов."""
    bounds = series_covered_years(series_keys)
    return max((last for _, last in bounds.values()), default=None)


def year_choices(bounds: dict[str, tuple[int, int]]) -> list[int]:
    """Определить годы для среза: объединение диапазонов рядов."""
    if not bounds:
        return []
    first = min(low for low, _ in bounds.values())
    last = max(high for _, high in bounds.values())
    return list(range(first, last + 1))


# ---------------------------------------------------------------------------------------
# Разрывы и состав субъектов
# ---------------------------------------------------------------------------------------


def series_breaks(series: Series, first_year: int, last_year: int) -> list[dict[str, Any]]:
    """
    Разрывы ряда внутри отрезка, общие для всех субъектов: год, вид и пояснение.

    Разрыв в первом году отрезка ничего не разделяет. ``methodological`` — смена правил
    счёта; смена состава территорий (``territory``) учитывается составом субъектов.
    У ряда таблицы — смена методики, отмеченная человеком в описании показателя.
    """
    if getattr(series, "is_user", False):
        label = str(BreakKind.METHODOLOGY.label)
        return [
            {
                "year": item["year"],
                "kind": BreakKind.METHODOLOGY.value,
                "label": label,
                "description": item["note"],
                "methodological": True,
            }
            for item in getattr(series, "breaks", [])
            if first_year < item["year"] <= last_year
        ]
    found = (
        SeriesBreak.objects.filter(
            series=series, territory__isnull=True, year__gt=first_year, year__lte=last_year
        )
        .select_related("note")
        .order_by("year", "kind")
    )
    return [
        {
            "year": item.year,
            "kind": item.kind,
            "label": str(item.get_kind_display()),
            "description": item.description,
            "methodological": item.kind != BreakKind.TERRITORY,
        }
        for item in found
    ]


def comparable_from(breaks: list[dict[str, Any]], last_year: int, min_span: int) -> int | None:
    """Начало отрезка по одним правилам — год последнего разрыва; отрезок не короче ``min_span``."""
    if not breaks:
        return None
    start = max(item["year"] for item in breaks)
    return start if last_year - start >= min_span else None


def break_marks(breaks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Отметки разрывов для оси времени: один год — одна отметка."""
    marks: dict[int, dict[str, Any]] = {}
    for item in breaks:
        marks.setdefault(item["year"], {"year": item["year"], "label": item["label"]})
    return list(marks.values())


def describe_composition(composition: Composition) -> dict[str, Any]:
    """Состав субъектов словами для шаблона: названия и годы вне постоянного состава."""
    names = {row["code"]: row["name"] for row in region_rows()}

    def rows(items: tuple[Outsider, ...]) -> list[dict[str, Any]]:
        return [
            {
                "name": names.get(item.code, item.code),
                "first_year": item.first_year,
                "last_year": item.last_year,
                "gaps": item.gaps,
            }
            for item in items
        ]

    return {
        "constant": len(composition.constant),
        "fullest": composition.fullest,
        "years": composition.years,
        "sparse": composition.sparse,
        "entered": rows(composition.entered),
        "left": rows(composition.left),
        "gapped": rows(composition.gapped),
        "outsiders": len(composition.outsiders),
        "changes": composition.changes,
        "usable": composition.is_usable,
        "share": MIN_YEAR_COVERAGE,
    }


# ---------------------------------------------------------------------------------------
# Кэширование расчётов
# ---------------------------------------------------------------------------------------


def cached_result(prefix: str, parameters: dict[str, Any], builder: Callable[[], Any]) -> Any:
    """Вернуть результат расчёта из кэша или вычислить его."""
    key = build_cache_key(prefix, parameters)
    stored = cache.get(key)
    if stored is not None:
        return stored

    result = builder()
    cache.set(key, result, settings.ANALYTICS_CACHE_TTL)
    return result


def build_cache_key(prefix: str, parameters: dict[str, Any]) -> str:
    """
    Построить ключ кэша: отпечаток параметров, склада и наборов в них, версии приложения.

    Версия нужна, потому что состав сохранённого результата меняется вместе с кодом.
    """
    built_at = generation_of(parameters.values())
    payload = "|".join(f"{name}={parameters[name]}" for name in sorted(parameters))
    source = f"{settings.PROJECT_VERSION}|{built_at}|{payload}"
    digest = hashlib.sha256(source.encode()).hexdigest()[:20]
    return f"analytics:{prefix}:{get_language()}:{digest}"
