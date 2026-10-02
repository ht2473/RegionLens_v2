"""Витрина: живая карта основного показателя, вопросы с готовым ответом и главное о стране."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.core.cache import cache
from django.urls import reverse
from django.utils.translation import get_language, gettext

from apps.catalog.constants import BreakKind
from apps.catalog.models import SeriesBreak, Territory
from apps.catalog.passport import Position
from apps.catalog.provenance import origin_of_year
from apps.catalog.real_terms import Values, deflators, real_between
from apps.core.charts import sparkline_path
from apps.core.templatetags.formatting import NBSP, ru_number
from apps.maps.cartogram import build_map
from apps.maps.classification import classify, sequential_palette
from apps.warehouse.duckdb_client import warehouse_generation
from apps.warehouse.queries import (
    COUNTRY_CODE,
    FeaturedSeries,
    featured_series,
    featured_set,
    featured_snapshot,
    ranked_years,
    series_leaders,
    series_ranked_panel,
    series_statistics_timeline,
)
from apps.warehouse.queries.common import GENERATION_CACHE_TTL
from apps.warehouse.queries.territory import MIN_RANKED_TERRITORIES

# Шкала живой карты — пять квантильных классов: при переборе лет с инфляцией и сменой
# методик равные доли показывают место региона, равные интервалы — нет.
LIVE_METHOD = "quantile"
LIVE_CLASSES = 5

# Блок живой карты: смена показателя заменяет только его (заголовок ``HX-Target``).
LIVE_MAP_ID = "live-map"

# Срок хранения кадров в браузере; адрес кадров несёт отпечаток склада и набора.
FRAMES_MAX_AGE = 24 * 3600

# Редакция устройства кадров в ключе кэша: при правке их состава старые не находятся.
FRAMES_LAYOUT = 4

# Сколько регионов в перечнях «больше всего» и «меньше всего» рядом с картой.
FRAME_LEADERS = 5

# Сколько регионов называет ответ на вопрос-вход.
ANSWER_TOP = 3

# Разрывы на шкале годов; перекройка округов на карте субъектов не видна.
TIMELINE_BREAKS = tuple(kind for kind in BreakKind.values if kind != BreakKind.TERRITORY)


# ---------------------------------------------------------------------------------------
# Живая карта
# ---------------------------------------------------------------------------------------


def live_series() -> list[FeaturedSeries]:
    """Показатели основного набора с вопросом-входом — те, что открываются на живой карте."""
    return [item for item in featured_series() if item.headline and item.question_ru]


def resolve_live_series(key: str | None) -> FeaturedSeries | None:
    """Показатель живой карты по ключу из адреса (только основной набор); иначе — ``home``."""
    items = live_series()
    by_key = {item.key: item for item in items}
    if key and key in by_key:
        return by_key[key]
    return next((item for item in items if item.role == "home"), items[0] if items else None)


@dataclass(slots=True, frozen=True)
class _Place:
    """Субъект живой карты: код, название и адрес паспорта."""

    code: str
    name: str
    abbreviation: str
    href: str
    card: str


def _places() -> list[_Place]:
    """Субъекты карты в порядке справочника — тот же порядок у всех кадров."""
    return [
        _Place(
            code=territory.code,
            name=territory.name,
            abbreviation=territory.short_code,
            href=reverse("catalog:territory-detail", kwargs={"slug": territory.slug}),
            card=reverse("catalog:territory-card", kwargs={"slug": territory.slug}),
        )
        for territory in Territory.objects.comparable().only(
            "code", "slug", "name_ru", "name_en", "abbreviation", "display_order"
        )
    ]


def live_frames(item: FeaturedSeries) -> dict[str, Any]:
    """
    Кадры живой карты по всем годам ряда: классы, значения, места, легенда и крайние.

    Годы, где ранжировано меньше ``MIN_RANKED_TERRITORIES`` субъектов, пропускаются.
    Ключ кэша — склад, файл набора, язык и ``FRAMES_LAYOUT``.
    """
    key = (
        f"showcase:frames:{FRAMES_LAYOUT}:{warehouse_generation()}:{featured_set().digest}:"
        f"{get_language()}:{item.key}"
    )
    stored = cache.get(key)
    if stored is not None:
        return stored

    places = _places()
    country = {row["year"]: row["country_value"] for row in series_statistics_timeline(item.key)}
    by_year: dict[int, dict[str, dict[str, Any]]] = {}
    for row in series_ranked_panel(item.key):
        by_year.setdefault(int(row["year"]), {})[row["territory_code"]] = row

    frames = {
        year: _frame(item, year, rows, country.get(year), places)
        for year, rows in sorted(by_year.items())
        if len(rows) >= MIN_RANKED_TERRITORIES
    }
    years = list(frames)
    result = {
        "series": item.key,
        "title": item.short_title,
        "unit": item.unit_label,
        "codes": [place.code for place in places],
        "names": [place.name for place in places],
        "abbreviations": [place.abbreviation for place in places],
        "links": [place.href for place in places],
        "cards": [place.card for place in places],
        "years": years,
        "breaks": _timeline_breaks(item.key, years),
        "frames": {str(year): frame for year, frame in frames.items()},
    }
    cache.set(key, result, GENERATION_CACHE_TTL)
    return result


def _frame(
    item: FeaturedSeries,
    year: int,
    rows: dict[str, dict[str, Any]],
    country_value: float | None,
    places: list[_Place],
) -> dict[str, Any]:
    """Собрать кадр одного года."""
    classification = classify(
        [row["value"] for row in rows.values()], method=LIVE_METHOD, class_count=LIVE_CLASSES
    )
    palette = sequential_palette(classification.class_count) if classification else []

    classes: list[int | None] = []
    values: list[str] = []
    places_text: list[str] = []
    standings: list[str] = []
    tones: list[str] = []
    versus: list[str] = []
    for place in places:
        row = rows.get(place.code)
        if row is None or row["value"] is None:
            classes.append(None)
            values.append("")
            places_text.append("")
            standings.append("")
            tones.append("")
            versus.append("")
            continue
        position = Position(
            series=item,
            year=year,
            value=row["value"],
            rank_desc=row["rank_desc"],
            rank_asc=row["rank_asc"],
            percentile=row["percentile"] or 0.0,
            territories=row["territories"],
            country_value=country_value,
        )
        classes.append(classification.class_of(row["value"]) if classification else None)
        values.append(ru_number(row["value"], item.precision))
        places_text.append(position.place_text)
        standings.append(position.standing)
        tones.append(position.tone)
        versus.append(position.versus_country)

    names = {place.code: place.name for place in places}
    ranked = sorted(
        (
            row
            for row in rows.values()
            if row["value"] is not None and row["territory_code"] in names
        ),
        key=lambda row: row["value"],
    )
    order = {place.code: index for index, place in enumerate(places)}
    return {
        "year": year,
        "palette": palette,
        "classes": classes,
        "values": values,
        "places": places_text,
        "standings": standings,
        "tones": tones,
        "versus": versus,
        "legend": scale_legend(classification, palette, item.precision),
        "highest": _extreme(ranked[-1], names, item.precision) if ranked else None,
        "lowest": _extreme(ranked[0], names, item.precision) if ranked else None,
        "country": "" if country_value is None else ru_number(country_value, item.precision),
        "missing": sum(1 for value in classes if value is None),
        "top": _leaders(list(reversed(ranked[-FRAME_LEADERS:])), ranked, order),
        "bottom": _leaders(ranked[:FRAME_LEADERS], ranked, order),
    }


def _leaders(
    rows: list[dict[str, Any]], ranked: list[dict[str, Any]], order: dict[str, int]
) -> list[list[Any]]:
    """
    Регионы перечня «больше всего» или «меньше всего»: номер в справочнике и полоска.

    Полоска — только у величин одного знака. Длина строкой: шаблон записал бы дробь с запятой.
    """
    if not ranked:
        return []
    low, high = ranked[0]["value"], ranked[-1]["value"]
    scaled = low >= 0 and high > 0
    return [
        [order[row["territory_code"]], f"{row['value'] / high:.3f}" if scaled else ""]
        for row in rows
    ]


def scale_legend(classification: Any, palette: list[str], precision: int) -> list[dict[str, Any]]:
    """Классы шкалы: цвет, границы (парой и порознь, для стыков) и число субъектов."""
    if classification is None:
        return []
    legend = []
    for interval in classification.intervals():
        lower = ru_number(interval["lower"], precision)
        upper = ru_number(interval["upper"], precision)
        legend.append(
            {
                "colour": palette[interval["index"]],
                "range": f"{lower}{NBSP}–{NBSP}{upper}",
                "lower": lower,
                "upper": upper,
                "count": interval["count"],
            }
        )
    return legend


def _extreme(row: dict[str, Any], names: dict[str, str], precision: int) -> dict[str, str]:
    """Крайнее значение года: кто и сколько."""
    return {"name": names[row["territory_code"]], "value": ru_number(row["value"], precision)}


def _timeline_breaks(series_key: str, years: list[int]) -> list[dict[str, Any]]:
    """Разрывы сопоставимости на шкале годов, кроме отнесённых к одному субъекту."""
    if not years:
        return []
    span = years[-1] - years[0] or 1
    found: dict[int, list[str]] = {}
    rows = SeriesBreak.objects.filter(
        series__key=series_key,
        territory__isnull=True,
        kind__in=TIMELINE_BREAKS,
        year__gte=years[0],
        year__lte=years[-1],
    ).values_list("year", "kind")
    labels = dict(BreakKind.choices)
    for year, kind in rows:
        text = str(labels.get(kind, kind)).lower()
        if text not in found.setdefault(year, []):
            found[year].append(text)
    return [
        {
            "year": year,
            # Доля длины шкалы строкой: шаблон записал бы число с запятой.
            "offset": f"{(year - years[0]) / span:.4f}",
            "title": gettext("%(year)s: %(what)s") % {"year": year, "what": "; ".join(kinds)},
        }
        for year, kinds in sorted(found.items())
    ]


def live_map(item: FeaturedSeries, year_param: str | None) -> dict[str, Any] | None:
    """
    Контекст живой карты: чертёж, кадр выбранного года и органы управления.

    Остальные кадры сценарий забирает отдельным ответом, когда читатель тронет ползунок.
    """
    frames = live_frames(item)
    years: list[int] = frames["years"]
    if not years:
        return None
    year = _resolve_year(year_param, years)
    frame = frames["frames"][str(year)]

    rows = []
    for index, code in enumerate(frames["codes"]):
        class_index = frame["classes"][index]
        name = frames["names"][index]
        value = frame["values"][index]
        rows.append(
            {
                "code": code,
                "name": name,
                # Сокращение подписывает субъект во врезке: «МСК», а не код «RU-MOW».
                "abbreviation": frames["abbreviations"][index],
                "href": frames["links"][index],
                "title": f"{name}: {value} {item.unit_label}" if value else name,
                "colour": frame["palette"][class_index] if class_index is not None else "",
                "class_index": class_index,
                "mapped": None,
            }
        )
    geo_map = build_map(rows)
    if geo_map is None:
        return None

    return {
        "series": item,
        "year": year,
        "years": years,
        "first_year": years[0],
        "last_year": years[-1],
        "frame": frame,
        "geo_map": geo_map,
        "ends": scale_ends(item),
        "breaks": frames["breaks"],
        "break_years": ", ".join(str(mark["year"]) for mark in frames["breaks"]),
        # Перечни за выбранный год; за другие годы их переписывает сценарий.
        "top": _leader_rows(frames, frame, "top"),
        "bottom": _leader_rows(frames, frame, "bottom"),
        # Кадр выбранного года уходит в разметку целиком, остальные — по запросу.
        "state": {
            "series": item.key,
            "year": year,
            "years": years,
            "unit": item.unit_label,
            "codes": frames["codes"],
            "names": frames["names"],
            "links": frames["links"],
            "cards": frames["cards"],
            "frame": frame,
            "noData": f"url(#{geo_map['no_data_pattern']})",
            "framesUrl": reverse("core:home-frames")
            + f"?series={item.key}&v={FRAMES_LAYOUT}.{warehouse_generation()}"
            f".{featured_set().digest}",
        },
    }


def _leader_rows(frames: dict[str, Any], frame: dict[str, Any], side: str) -> list[dict[str, str]]:
    """Строки перечня крайних регионов за год: название, значение, ссылка, полоска класса."""
    rows = []
    for index, share in frame[side]:
        class_index = frame["classes"][index]
        rows.append(
            {
                "code": frames["codes"][index],
                "name": frames["names"][index],
                "href": frames["links"][index],
                "value": frame["values"][index],
                "share": share,
                "colour": frame["palette"][class_index] if class_index is not None else "",
            }
        )
    return rows


def scale_ends(item: FeaturedSeries) -> dict[str, str]:
    """
    Подписи концов шкалы: где меньше и где больше, у оцениваемых показателей — где лучше.

    У безработицы самый зелёный класс — больше безработных, поэтому при проверенной
    направленности концы подписаны оценкой.
    """
    ends = {"low": "", "low_tone": "", "high": "", "high_tone": ""}
    if item.absolute or item.polarity not in ("positive", "negative"):
        return ends
    better_high = item.polarity == "positive"
    ends["low"] = gettext("хуже") if better_high else gettext("лучше")
    ends["low_tone"] = "bad" if better_high else "good"
    ends["high"] = gettext("лучше") if better_high else gettext("хуже")
    ends["high_tone"] = "good" if better_high else "bad"
    return ends


def _resolve_year(value: str | None, years: list[int]) -> int:
    """Год из адреса, если он есть среди кадров; иначе последний."""
    try:
        year = int(value) if value else years[-1]
    except TypeError, ValueError:
        return years[-1]
    return year if year in years else years[-1]


# ---------------------------------------------------------------------------------------
# Вопросы с готовым ответом
# ---------------------------------------------------------------------------------------


def answered_questions() -> list[dict[str, Any]]:
    """
    Вопросы-входы, не занятые живой картой, и первые три региона в ответе.

    Все вопросы спрашивают о наибольшем значении, поэтому ответ — начало рейтинга.
    """
    on_map = {item.key for item in live_series()}
    cards = []
    for item in featured_series():
        if not item.question_ru or item.key in on_map:
            continue
        years = ranked_years(item.key)
        if not years:
            continue
        year = years[-1]
        leaders = series_leaders(item.key, year, limit=ANSWER_TOP)
        if not leaders:
            continue
        english = get_language() == "en"
        cards.append(
            {
                "series": item,
                "year": year,
                "leaders": [
                    {
                        "name": (row["name_en"] if english and row["name_en"] else row["name_ru"]),
                        "value": ru_number(row["value"], item.precision),
                    }
                    for row in leaders
                ],
                "href": reverse("rankings:index") + f"?series={item.key}&year={year}",
            }
        )
    return cards


# ---------------------------------------------------------------------------------------
# Россия: главное
# ---------------------------------------------------------------------------------------


def country_tiles(*, headline_only: bool = True) -> list[dict[str, Any]]:
    """
    Показатели страны: значение, изменение к прошлому году и ход за двенадцать лет.

    ``headline_only`` — только восемь главных (витрина), иначе весь основной набор.
    """
    tiles = []
    metrics = featured_snapshot(COUNTRY_CODE, headline_only=headline_only)
    values = deflators([metric["series"] for metric in metrics], [COUNTRY_CODE])
    broken = _country_breaks([metric["series"].key for metric in metrics])
    for metric in metrics:
        item: FeaturedSeries = metric["series"]
        current = metric["current"]
        break_year = current["year"] if current["year"] in broken.get(item.key, ()) else None
        change, direction, nominal = "", 0, ""
        if break_year is None:
            change, direction = _change(item, current)
            real = real_year_change(
                item,
                values,
                COUNTRY_CODE,
                year=current["year"],
                value=current["value"],
                before=current["previous_value"],
            )
            if real is not None:
                nominal = change
                change, direction = real
        tiles.append(
            {
                "series": item,
                "value": ru_number(current["value"], item.precision),
                "year": current["year"],
                "previous_year": current["previous_year"],
                "change": change,
                # У денежного ряда ``change`` — реальное изменение, это — в рублях.
                "nominal": nominal,
                "tone": change_tone(item, direction),
                "break_year": break_year,
                "origin": (
                    origin_of_year(item.key, current["year"]) if current.get("flags") else None
                ),
                "spark": sparkline_path([point["value"] for point in metric["sparkline"]]),
                "href": reverse("maps:choropleth") + f"?series={item.key}",
            }
        )
    return tiles


def _country_breaks(keys: list[str]) -> dict[str, set[int]]:
    """Годы разрывов, прерывающих сравнение по стране, по рядам."""
    found: dict[str, set[int]] = {}
    rows = SeriesBreak.objects.filter(series__key__in=keys, territory__isnull=True).values_list(
        "series__key", "year"
    )
    for key, year in rows:
        found.setdefault(key, set()).add(year)
    return found


def _change(item: FeaturedSeries, current: dict[str, Any]) -> tuple[str, int]:
    """Изменение к прошлому году со знаком и его направление."""
    return change_between(item, current["value"], current["previous_value"])


def change_between(item: FeaturedSeries, value: float, before: float | None) -> tuple[str, int]:
    """
    Изменение от ``before`` до ``value`` со знаком и его направление.

    Проценты — в процентных пунктах, положительные величины — в процентах, знакопеременные —
    разностью: с −4,0 до −3,5 — «+0,5», а не «+12,5 %».
    """
    if before is None:
        return "", 0
    if item.is_percentage:
        digits = max(item.precision, 1)
        amount = round(value - before, digits)
        text = gettext("%(points)s п. п.") % {"points": _signed(amount, digits)}
    elif before > 0 and value > 0:
        amount = round((value - before) / before * 100, 1)
        text = f"{_signed(amount, 1)}{NBSP}%"
    else:
        amount = round(value - before, item.precision)
        text = _signed(amount, item.precision)
    return text, (amount > 0) - (amount < 0)


def real_year_change(
    item: FeaturedSeries,
    values: Values,
    code: str,
    *,
    year: int,
    value: float,
    before: float | None,
) -> tuple[str, int] | None:
    """
    Изменение денежного ряда к прошлому году в неизменных ценах, со знаком, и его направление.

    ``None`` — ряд не пересчитывается или индекса за год нет.
    """
    if item.real is None or not before or before <= 0 or value <= 0:
        return None
    ratio = real_between(item, values, code, start=year - 1, end=year, nominal_ratio=value / before)
    if ratio is None:
        return None
    amount = round((ratio - 1) * 100, 1)
    return f"{_signed(amount, 1)}{NBSP}%", (amount > 0) - (amount < 0)


def _signed(number: float, digits: int) -> str:
    """Число со знаком: «+1,9», «−0,7», «0»."""
    if number == 0:
        return ru_number(0, digits)
    sign = "+" if number > 0 else "−"
    return f"{sign}{ru_number(abs(number), digits)}"


def change_tone(item: FeaturedSeries, direction: int) -> str:
    """Оценка изменения: рост дестимулятора — ухудшение; у нейтральных оценки нет."""
    if direction == 0 or item.polarity not in ("positive", "negative"):
        return "neutral"
    better = direction > 0 if item.polarity == "positive" else direction < 0
    return "good" if better else "bad"
