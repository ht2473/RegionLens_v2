"""
Выводы страницы показателя: описание ряда, главное словами, плитка «Россия», соседи по теме.

Вне основного набора направленность не оценивается: положение — «выше» и «ниже».
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.urls import reverse
from django.utils.translation import get_language, gettext

from apps.catalog.constants import is_unnormalised_title
from apps.catalog.models import Series
from apps.catalog.passport import times_phrase
from apps.catalog.provenance import Origin, origin_of_year
from apps.catalog.real_terms import deflators
from apps.core.charts import sparkline_path
from apps.core.showcase import change_between, change_tone, real_year_change
from apps.core.templatetags.formatting import (
    LARGE_VALUE_THRESHOLD,
    NBSP,
    SMALL_VALUE_THRESHOLD,
    ru_number,
)
from apps.warehouse.queries import (
    COUNTRY_CODE,
    FeaturedSeries,
    FeaturedTheme,
    featured_set,
    series_metadata,
    series_ranked_panel,
    series_statistics_timeline,
    series_timeline,
)
from apps.warehouse.routing import is_user_key

# Направленность рядов вне основного набора: оценки нет (см. описание модуля).
UNASSESSED = "neutral"

# С какой кратности разница между крайними регионами называется словами «в N раз».
GAP_RATIO_FROM = 1.1

# Сколько соседей по теме основного набора называет страница.
THEME_NEIGHBOURS = 8

# Единица, которую ставит сборка склада, когда её не удалось восстановить.
UNIT_NOT_GIVEN = "Не указана"


# ---------------------------------------------------------------------------------------
# Описание ряда
# ---------------------------------------------------------------------------------------


def _precision(reference: float | None) -> int:
    """
    Знаков после запятой по порядку величины — одна точность на весь ряд.

    Порог как у ``ru_number``, но по середине ряда, чтобы значения печатались одинаково.
    """
    if reference is None:
        return 1
    magnitude = abs(reference)
    if magnitude >= LARGE_VALUE_THRESHOLD:
        return 0
    return 1 if magnitude >= SMALL_VALUE_THRESHOLD else 2


def _integral(key: str) -> bool:
    """Все значения ряда — целые числа: численность, штуки, места, посещения."""
    return all(
        row["value"] is None or float(row["value"]).is_integer() for row in series_ranked_panel(key)
    )


def describe_series(series: Series) -> FeaturedSeries:
    """
    Описание ряда для выводов словами: из основного набора или из источника.

    Единица — из склада: справочник сводит восстановленные единицы в одну запись «ND».
    Ряд набора пользователя описывает сам набор.
    """
    if getattr(series, "is_user", False):
        return series.describe()  # type: ignore[attr-defined]
    featured = featured_set().by_key().get(series.key)
    if featured is not None:
        return featured

    statistics = series_statistics_timeline(series.key)
    reference = next(
        (row["median_value"] for row in reversed(statistics) if row["median_value"] is not None),
        None,
    )
    metadata = series_metadata(series.key) or {}
    unit_ru = metadata.get("unit_short_ru") or ""
    unit_en = metadata.get("unit_short_en") or ""
    indicator = series.indicator
    title_ru = indicator.name_ru
    title_en = indicator.name_en or ""
    if series.has_subsection and series.name_ru:
        title_ru = f"{title_ru} — {series.name_ru}"
        if title_en and series.name_en:
            title_en = f"{title_en} — {series.name_en}"
    absolute = is_unnormalised_title(
        f"{indicator.name_ru} {series.name_ru or ''} {unit_ru}",
        metadata.get("unit_kind") or "unknown",
    )
    return FeaturedSeries(
        key=series.key,
        short_title_ru=title_ru,
        short_title_en=title_en,
        unit_label_ru=unit_ru,
        unit_label_en=unit_en,
        polarity=UNASSESSED,
        theme="",
        precision=0 if _integral(series.key) else _precision(reference),
        order=len(featured_set().series) + 1,
        absolute=absolute,
    )


def unit_name(series: Series) -> str:
    """Полное название единицы по складу: «рублей», «в процентах к итогу»; без заглушки."""
    metadata = series_metadata(series.key) or {}
    russian = metadata.get("unit_name_ru") or ""
    if russian in ("", UNIT_NOT_GIVEN):
        return ""
    if get_language() == "en" and metadata.get("unit_name_en"):
        return str(metadata["unit_name_en"])
    return russian


def series_descriptor(key: str | None) -> FeaturedSeries | None:
    """Описание ряда по ключу из адреса; неизвестный или скрытый ряд — ничего."""
    if not key:
        return None
    featured = featured_set().by_key().get(key)
    if featured is not None:
        return featured
    if is_user_key(key):
        from apps.userdata.series import user_series

        found = user_series(key)
        return found.describe() if found is not None else None
    series = Series.objects.filter(key=key).select_related("indicator", "unit").first()
    return describe_series(series) if series is not None else None


# ---------------------------------------------------------------------------------------
# Выводы словами
# ---------------------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class Extreme:
    """Крайний регион года: название, значение и адрес паспорта."""

    name: str
    value: str
    href: str


@dataclass(slots=True)
class Lead:
    """Главное о показателе: числа и выводы готовит сервер, предложения собирает шаблон."""

    year: int
    highest: Extreme
    lowest: Extreme
    # Разница между крайними: «в 4,6 раза» или «на 29,8 п. п.»; пусто, если
    # кратность ничего не добавляет или величина проходит через ноль.
    gap: str
    median: str
    # Подпись единицы с неразрывным пробелом впереди; пусто, если единица неизвестна.
    unit: str
    regions: int
    total: int
    first_year: int
    last_year: int
    break_years: list[int]


@dataclass(slots=True)
class CountryTile:
    """Плитка «Россия»: значение последнего года, изменение за год и ход."""

    value: str
    year: int
    previous_year: int | None
    change: str
    tone: str
    # Разрыв между прошлым и последним годом: изменение через него не считается.
    break_year: int | None
    spark: dict[str, Any] | None
    # У денежного ряда ``change`` — реальное изменение, это — в рублях.
    nominal: str = ""
    # Откуда значение года, если не из набора.
    origin: Origin | None = None


def _extreme(frame: dict[str, Any], state: dict[str, Any], side: str) -> Extreme | None:
    """Первый регион перечня «больше всего» или «меньше всего» из кадра живой карты."""
    rows = frame.get(side) or []
    if not rows:
        return None
    index = rows[0][0]
    return Extreme(
        name=state["names"][index],
        value=frame["values"][index],
        href=state["links"][index],
    )


def _gap(item: FeaturedSeries, high: float, low: float) -> str:
    """Разница между крайними значениями словами."""
    if item.is_percentage:
        digits = max(item.precision, 1)
        return _points(high - low, digits)
    if low <= 0 or high <= 0:
        return ""
    ratio = high / low
    if ratio < GAP_RATIO_FROM:
        return ""
    return times_phrase(ratio)


def _points(amount: float, digits: int) -> str:
    """Разность процентов в процентных пунктах: «29,8 п. п.»."""
    return gettext("%(points)s п. п.") % {"points": ru_number(abs(amount), digits)}


def build_lead(
    item: FeaturedSeries,
    live: dict[str, Any],
    statistics: list[dict[str, Any]],
    break_years: list[int],
) -> Lead | None:
    """Собрать главное о показателе за год живой карты — из того же кадра, что рисует карта."""
    frame = live["frame"]
    state = live["state"]
    highest = _extreme(frame, state, "top")
    lowest = _extreme(frame, state, "bottom")
    row = next((row for row in statistics if row["year"] == live["year"]), None)
    if highest is None or lowest is None or row is None:
        return None
    years = [entry["year"] for entry in statistics if entry["observations"]]
    return Lead(
        year=live["year"],
        highest=highest,
        lowest=lowest,
        gap=_gap(item, row["max_value"], row["min_value"]),
        median=ru_number(row["median_value"], item.precision),
        unit=f"{NBSP}{item.unit_label}" if item.unit_label else "",
        regions=int(row["observations"] or 0),
        total=len(state["codes"]),
        first_year=years[0] if years else live["first_year"],
        last_year=years[-1] if years else live["last_year"],
        break_years=break_years,
    )


def country_tile(item: FeaturedSeries, break_years: list[int]) -> CountryTile | None:
    """
    Плитка «Россия»: значение последнего года, изменение к прошлому и ход за двенадцать лет.

    Изменение через разрыв сопоставимости не называется; у денежных рядов — реальное.
    """
    rows = [row for row in series_timeline(item.key, COUNTRY_CODE) if row["value"] is not None]
    if not rows:
        return None
    current = rows[-1]
    year = int(current["year"])
    previous = next((row for row in rows if int(row["year"]) == year - 1), None)
    change, tone, broken, nominal = "", "neutral", None, ""
    if previous is not None and not item.is_growth_index:
        if year in break_years:
            broken = year
        else:
            change, direction = change_between(item, current["value"], previous["value"])
            real = real_year_change(
                item,
                deflators([item], [COUNTRY_CODE]),
                COUNTRY_CODE,
                year=year,
                value=current["value"],
                before=previous["value"],
            )
            if real is not None:
                nominal = change
                change, direction = real
            tone = change_tone(item, direction)
    values = {int(row["year"]): row["value"] for row in rows}
    span = range(int(rows[0]["year"]), year + 1)
    return CountryTile(
        value=ru_number(current["value"], item.precision),
        year=year,
        previous_year=year - 1 if previous is not None else None,
        change=change,
        tone=tone,
        break_year=broken,
        spark=sparkline_path([values.get(point) for point in span]),
        nominal=nominal,
        origin=origin_of_year(item.key, year),
    )


def country_break_years(breaks: list[Any]) -> list[int]:
    """Годы разрывов, прерывающих сравнение по стране: без отнесённых к одному субъекту."""
    return sorted({item.year for item in breaks if item.territory_id is None})


# ---------------------------------------------------------------------------------------
# Соседи по теме
# ---------------------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class Neighbour:
    """Показатель той же темы основного набора: короткое название и адрес страницы."""

    title: str
    href: str


def theme_of(item: FeaturedSeries) -> FeaturedTheme | None:
    """Тема основного набора, к которой относится ряд; вне набора — ничего."""
    return featured_set().theme(item.theme) if item.theme else None


def theme_neighbours(item: FeaturedSeries) -> list[Neighbour]:
    """Другие показатели той же темы основного набора, одним запросом по ключам рядов."""
    if not item.theme:
        return []
    items = [
        other
        for other in featured_set().series
        if other.theme == item.theme and other.key != item.key
    ][:THEME_NEIGHBOURS]
    slugs = dict(
        Series.objects.filter(key__in=[other.key for other in items]).values_list(
            "key", "indicator__slug"
        )
    )
    return [
        Neighbour(
            title=other.short_title,
            href=reverse("catalog:series-detail", kwargs={"slug": slugs[other.key]})
            + f"?series={other.key}",
        )
        for other in items
        if other.key in slugs
    ]
