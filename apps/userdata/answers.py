"""
Ответы карточек исследования — числами, без сочинённого текста: лидеры и отстающие, мой
регион, как изменился показатель, кто вырос сильнее, итоги по округам, насколько различаются
регионы, сближаются ли они, похожи ли соседи, с чем связан; тепловая таблица «регионы × годы»
и малые графики по округам; связь двух показателей и их сравнение.

Ответ строится заново по ряду (своему или сайта) и общему году исследования; у каждого —
адрес полного вида или инструмента. Ответа нет — ``NoAnswerError`` с причиной словами.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import numpy as np
from django.http import HttpRequest
from django.urls import reverse
from django.utils.translation import get_language, gettext_lazy
from django.utils.translation import gettext as _

from apps.analytics.core import correlation, inequality, spatial
from apps.analytics.selectors import cached_result, neighbour_map, region_rows
from apps.core.templatetags.formatting import NBSP, ru_number
from apps.surface.state import SurfaceState

from .series import FEW_REGIONS

# Регионов в перечнях «больше всего» и «меньше всего»; лет в изменении — как в ответах поиска.
LEADERS = 3
TREND_YEARS = 5
# Связей в ответе «С чем связан».
RELATED_SHOWN = 3
# Регионов в перечнях «вырос сильнее» и «меньше всего».
GROWTH_SHOWN = 5
# Лет в тепловой таблице и классов её шкалы.
HEAT_YEARS = 10
HEAT_CLASSES = 5
# Малые графики: лет в клетке и размер клетки в единицах чертежа.
MULTIPLES_YEARS = 15
SPARK_WIDTH = 120
SPARK_HEIGHT = 36
SPARK_PAD = 3.0
# Лет на графике сравнения двух показателей; регионов, расходящихся сильнее всего.
COMPARE_YEARS = 15
APART_SHOWN = 3
# Редакция содержимого: кэш расчётов не знает о правке кода.
RESULT_REVISION = 1

TITLES = {
    "leaders": gettext_lazy("Лидеры и отстающие"),
    "mine": gettext_lazy("Мой регион"),
    "change": gettext_lazy("Как изменился"),
    "growth": gettext_lazy("Кто вырос сильнее"),
    "districts": gettext_lazy("Итоги по округам"),
    "heat": gettext_lazy("Тепловая таблица"),
    "multiples": gettext_lazy("Малые графики"),
    "spread": gettext_lazy("Насколько различаются регионы"),
    "convergence": gettext_lazy("Сближаются ли регионы"),
    "neighbours": gettext_lazy("Похожи ли соседи"),
    "related": gettext_lazy("С чем связан"),
    "relation": gettext_lazy("Связь двух показателей"),
    "comparison": gettext_lazy("Сравнение двух показателей"),
}
ICONS = {
    "leaders": "ranking",
    "mine": "map-pin",
    "change": "dynamics",
    "growth": "growth",
    "districts": "districts",
    "heat": "heat",
    "multiples": "multiples",
    "spread": "inequality",
    "convergence": "convergence",
    "neighbours": "spatial",
    "related": "correlation",
    "relation": "correlation",
    "comparison": "compare",
}
DIRECTIONS = {
    "clustered": gettext_lazy("соседние регионы похожи"),
    "dispersed": gettext_lazy("соседние регионы различаются"),
    "random": gettext_lazy("размещение неотличимо от случайного"),
}


class NoAnswerError(Exception):
    """Ответа нет: мало регионов, нет значений за год; причина — словами."""


@dataclass(frozen=True, slots=True)
class Asked:
    """
    О чём спрошено: запрос (мой регион), выбор холста по карточке, описание ряда и сама
    карточка (её настройки, например шкала малых графиков).
    """

    request: HttpRequest
    state: SurfaceState
    subject: Any
    block: dict[str, Any] | None = None


def _url(name: str, **params: Any) -> str:
    return f"{reverse(name)}?{urlencode(params, doseq=True)}"


def _values(state: SurfaceState) -> dict[str, float]:
    """Значения регионов за год исследования."""
    from apps.warehouse.queries import region_panel

    assert state.series is not None and state.year is not None
    values = region_panel(state.series.key).get(state.year) or {}
    if not values:
        raise NoAnswerError(_("За этот год значений по регионам нет."))
    return values


def _enough(values: dict[str, float]) -> None:
    if len(values) < FEW_REGIONS:
        raise NoAnswerError(
            _("Нужны значения хотя бы по %(count)s регионам, есть по %(found)s.")
            % {"count": FEW_REGIONS, "found": len(values)}
        )


# --- Лидеры и отстающие --------------------------------------------------------------------


def leaders(asked: Asked) -> dict[str, Any]:
    """Первые и последние три региона года, Россия и мой регион с местом."""
    from apps.accounts.region import my_region
    from apps.warehouse.queries import ranking_table

    request, state, subject = asked.request, asked.state, asked.subject

    assert state.series is not None and state.year is not None
    rows = ranking_table(state.series.key, state.year)
    if not rows:
        raise NoAnswerError(_("Рейтинг за этот год не рассчитан."))
    precision = subject.item.precision
    english = get_language() != "ru"

    def line(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "code": row["territory_code"],
            "name": row["name_en"] if english else row["name_ru"],
            "value": ru_number(row["value"], precision),
            "place": row["rank_desc"],
        }

    region = my_region(request)
    mine = next(
        (line(row) for row in rows if region is not None and row["territory_code"] == region.code),
        None,
    )
    return {
        "year": state.year,
        "top": [line(row) for row in rows[:LEADERS]],
        "bottom": [line(row) for row in reversed(rows[-LEADERS:])],
        "count": len(rows),
        "country": subject.country,
        "mine": mine,
        "open_url": _url("rankings:index", series=state.series.key, year=state.year),
    }


# --- Как изменился -------------------------------------------------------------------------


def change(asked: Asked) -> dict[str, Any]:
    """Значение выбранного региона (первого общего, моего или России), изменение и ход."""
    from apps.accounts.region import my_region
    from apps.catalog.models import Territory
    from apps.core.charts import sparkline_path
    from apps.core.showcase import change_between
    from apps.warehouse.queries import COUNTRY_CODE, series_timeline

    request, state, subject = asked.request, asked.state, asked.subject

    assert state.series is not None and state.year is not None
    region = my_region(request)
    wanted = state.codes[0] if state.codes else (region.code if region else COUNTRY_CODE)
    rows: list[dict[str, Any]] = []
    for code in dict.fromkeys((wanted, COUNTRY_CODE)):
        rows = [
            row
            for row in series_timeline(state.series.key, code)
            if row.get("value") is not None and int(row["year"]) <= state.year
        ]
        if rows:
            wanted = code
            break
    if not rows:
        raise NoAnswerError(_("Значений по годам нет."))
    last = rows[-1]
    last_year = int(last["year"])
    before = next((row for row in rows if int(row["year"]) == last_year - TREND_YEARS), None)
    shift, direction = change_between(
        subject.item, last["value"], before["value"] if before else None
    )
    query: dict[str, Any] = {"series": state.series.key}
    if wanted != COUNTRY_CODE:
        query["territory"] = wanted
    return {
        "year": last_year,
        "territory": Territory.objects.filter(code=wanted).first(),
        "is_country": wanted == COUNTRY_CODE,
        "code": wanted,
        "value": ru_number(last["value"], subject.item.precision),
        "change": shift,
        "direction": direction,
        "since": last_year - TREND_YEARS if before else None,
        "spark": sparkline_path([row["value"] for row in rows]),
        "open_url": _url("compare:index", **query),
    }


# --- Мой регион -----------------------------------------------------------------------------


def mine(asked: Asked) -> dict[str, Any]:
    """
    Мой регион: значение, место среди регионов и в округе, отношение к России (у сумм — доля
    в сумме регионов), соседи по рейтингу, изменение за пять лет и ход.
    """
    from apps.accounts.region import my_region
    from apps.core.charts import sparkline_path
    from apps.core.showcase import change_between
    from apps.warehouse.queries import ranking_table, series_timeline

    request, state, subject = asked.request, asked.state, asked.subject

    assert state.series is not None and state.year is not None
    region = my_region(request)
    if region is None:
        raise NoAnswerError(_("Мой регион не выбран: его выбирают в шапке сайта."))
    rows = ranking_table(state.series.key, state.year)
    index = next((at for at, row in enumerate(rows) if row["territory_code"] == region.code), None)
    if index is None:
        raise NoAnswerError(_("За этот год значения по моему региону нет."))
    precision = subject.item.precision
    english = get_language() != "ru"
    row = rows[index]
    district = [item for item in rows if item["district_code"] == row["district_code"]]

    def line(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "code": item["territory_code"],
            "name": item["name_en"] if english else item["name_ru"],
            "value": ru_number(item["value"], precision),
            "place": item["rank_desc"],
        }

    timeline = [
        item
        for item in series_timeline(state.series.key, region.code)
        if item.get("value") is not None and int(item["year"]) <= state.year
    ]
    before = next(
        (item for item in timeline if int(item["year"]) == state.year - TREND_YEARS), None
    )
    shift, direction = change_between(
        subject.item, row["value"], before["value"] if before else None
    )
    absolute = subject.item.absolute
    return {
        "year": state.year,
        "code": region.code,
        "name": region.name,
        "value": ru_number(row["value"], precision),
        "place": row["rank_desc"],
        "count": len(rows),
        "district_name": row["district_name_en" if english else "district_name_ru"] or "",
        "district_place": 1 + sum(1 for item in district if item["rank_desc"] < row["rank_desc"]),
        "district_count": len(district),
        "share": row["share_of_total"] if absolute else None,
        "ratio": row["ratio_to_country"] if not absolute else None,
        "country": subject.country,
        "around": [line(item) for item in rows[max(index - 1, 0) : index + 2]],
        "change": shift,
        "direction": direction,
        "since": state.year - TREND_YEARS if before else None,
        "spark": sparkline_path([item["value"] for item in timeline]),
        "open_url": _url(
            "rankings:index", series=state.series.key, year=state.year, territory=region.code
        ),
    }


# --- Кто вырос сильнее ----------------------------------------------------------------------


POINTS, PERCENT, DIFFERENCE = "points", "percent", "difference"
MEASURES = {
    POINTS: gettext_lazy("в процентных пунктах"),
    PERCENT: gettext_lazy("в процентах"),
    DIFFERENCE: gettext_lazy("разностью значений"),
}


def change_mode(item: Any, pairs: list[tuple[float, float]]) -> str:
    """
    Мера изменения, общая для всех регионов карточки: проценты — в пунктах; положительные
    величины — в процентах; если хоть одно значение не больше нуля — разностью.
    """
    if item.is_percentage:
        return POINTS
    if all(before > 0 and value > 0 for before, value in pairs):
        return PERCENT
    return DIFFERENCE


def shift_of(mode: str, value: float, before: float) -> float:
    """Изменение от ``before`` до ``value`` в мере ``mode``."""
    if mode == PERCENT:
        return (value - before) / before * 100
    return value - before


def signed(mode: str, amount: float, precision: int) -> str:
    """Изменение словами со знаком: «+3,4 %», «−0,8 п. п.», «+150»."""
    digits = 1 if mode == PERCENT else max(precision, 1) if mode == POINTS else precision
    text = ru_number(abs(amount), digits)
    if round(amount, digits):
        text = ("+" if amount > 0 else "−") + text
    if mode == PERCENT:
        return f"{text}{NBSP}%"
    if mode == POINTS:
        return _("%(points)s п. п.") % {"points": text}
    return text


def growth(asked: Asked) -> dict[str, Any]:
    """
    Кто вырос сильнее за пять лет до года исследования (или с первого года, если ряд
    короче): первые и последние по изменению, Россия и мой регион с местом.
    """
    from apps.accounts.region import my_region
    from apps.warehouse.queries import COUNTRY_CODE, paired_years, series_values

    request, state, subject = asked.request, asked.state, asked.subject

    assert state.series is not None and state.year is not None
    end = state.year
    earlier = [year for year in _years(state) if year < end]
    if not earlier:
        raise NoAnswerError(_("Для изменения нужны хотя бы два года."))
    # Самый ранний год не дальше пяти лет; если таких нет — ближайший более ранний.
    within = [year for year in earlier if end - year <= TREND_YEARS]
    start = min(within) if within else max(earlier)
    english = get_language() != "ru"
    precision = subject.item.precision
    rows = paired_years(state.series.key, start, end)
    if len(rows) < LEADERS:
        raise NoAnswerError(_("Значений в обоих годах слишком мало для сравнения."))
    country = series_values([state.series.key], [COUNTRY_CODE]).get(state.series.key, {})
    years = country.get(COUNTRY_CODE, {})
    pairs = [(row["start_value"], row["end_value"]) for row in rows]
    if start in years and end in years:
        pairs.append((years[start], years[end]))
    mode = change_mode(subject.item, pairs)
    moved = []
    for row in rows:
        amount = shift_of(mode, row["end_value"], row["start_value"])
        moved.append(
            {
                "code": row["territory_code"],
                "name": row["name_en"] if english else row["name_ru"],
                "amount": amount,
                "change": signed(mode, amount, precision),
            }
        )
    moved.sort(key=lambda entry: entry["amount"], reverse=True)
    for place, entry in enumerate(moved, start=1):
        entry["place"] = place
    shown = min(GROWTH_SHOWN, len(moved) // 2)
    region = my_region(request)
    country_change = ""
    if start in years and end in years:
        country_change = signed(mode, shift_of(mode, years[end], years[start]), precision)
    return {
        "year": end,
        "since": start,
        "top": moved[:shown],
        "bottom": list(reversed(moved[-shown:])),
        "count": len(moved),
        "country": country_change,
        "mine": next(
            (entry for entry in moved if region is not None and entry["code"] == region.code),
            None,
        ),
        "measure": MEASURES[mode],
        "open_url": _url("maps:choropleth", series=state.series.key, year=end, compare=start),
    }


def _years(state: SurfaceState) -> list[int]:
    """Годы ряда со значениями по регионам."""
    from apps.warehouse.queries import year_counts

    assert state.series is not None
    return sorted(int(year) for year in year_counts(state.series.key))


# --- Итоги по округам -----------------------------------------------------------------------


def districts(asked: Asked) -> dict[str, Any]:
    """
    Федеральные округа за год: значение округа из данных, иначе сумма его регионов (у сумм)
    или медиана регионов с размахом (у относительных величин). Мой округ отмечен.
    """
    from apps.accounts.region import my_region
    from apps.analytics.selectors import region_rows
    from apps.catalog.models import Territory
    from apps.warehouse.queries import district_totals

    request, state, subject = asked.request, asked.state, asked.subject

    assert state.series is not None and state.year is not None
    found = district_totals(state.series.key, state.year)
    if not any(entry["regions"] or entry["value"] is not None for entry in found.values()):
        raise NoAnswerError(_("За этот год значений по регионам нет."))
    members: dict[str, int] = {}
    for row in region_rows():
        members[row["district_code"]] = members.get(row["district_code"], 0) + 1
    present = [entry for entry in found.values() if entry["regions"] or entry["value"] is not None]
    if all(entry["value"] is not None for entry in present):
        basis = "data"
    elif subject.item.absolute:
        basis = "sum"
    else:
        basis = "median"
    region = my_region(request)
    mine = ""
    if region is not None:
        mine = next(
            (row["district_code"] for row in region_rows() if row["code"] == region.code), ""
        )
    precision = subject.item.precision
    rows = []
    for district in Territory.objects.federal_districts().order_by("display_order"):
        entry = found.get(district.code)
        if entry is None:
            continue
        value = {"data": entry["value"], "sum": entry["total"], "median": entry["median"]}[basis]
        if value is None:
            continue
        rows.append(
            {
                "code": district.code,
                "name": district.name,
                "number": value,
                "value": ru_number(value, precision),
                "regions": entry["regions"],
                "members": members.get(district.code, 0),
                "low": entry["low"],
                "high": entry["high"],
                "mine": district.code == mine,
            }
        )
    if not rows:
        raise NoAnswerError(_("За этот год значений по округам нет."))
    _bars(rows, basis)
    total = sum(row["number"] for row in rows) if basis == "sum" else None
    for row in rows:
        row["share"] = row["number"] / total if total else None
    return {
        "year": state.year,
        "basis": basis,
        "rows": rows,
        "incomplete": basis == "sum" and any(row["regions"] < row["members"] for row in rows),
        "open_url": _url("rankings:index", series=state.series.key, year=state.year),
    }


def _bars(rows: list[dict[str, Any]], basis: str) -> None:
    """
    Отрезки строк в долях общей шкалы — строками, иначе шаблон записал бы дробь с запятой:
    у медианы — размах регионов и точка медианы, у прочих — полоса от нуля.
    """
    if basis == "median":
        low = min(row["low"] for row in rows)
        high = max(row["high"] for row in rows)
        spread = (high - low) or 1.0
        for row in rows:
            row["from"] = f"{(row['low'] - low) / spread:.3f}"
            row["to"] = f"{(row['high'] - low) / spread:.3f}"
            row["at"] = f"{(row['number'] - low) / spread:.3f}"
        return
    highest = max(abs(row["number"]) for row in rows) or 1.0
    scaled = all(row["number"] >= 0 for row in rows)
    for row in rows:
        row["bar"] = f"{abs(row['number']) / highest:.3f}" if scaled else ""


# --- Тепловая таблица ----------------------------------------------------------------------


def heat(asked: Asked) -> dict[str, Any]:
    """
    Регионы × годы: последние годы до года исследования, цвет — класс общей шкалы всех
    клеток (квантили). Регионы — общие регионы исследования, иначе все; по убыванию
    значения последнего года. Строка России — без цвета: у сумм она равна сумме регионов.
    """
    from apps.analytics.selectors import region_rows
    from apps.maps.classification import classify, sequential_palette
    from apps.warehouse.queries import COUNTRY_CODE, region_panel, series_values

    state, subject = asked.state, asked.subject

    assert state.series is not None and state.year is not None
    panel = region_panel(state.series.key)
    years = [year for year in sorted(panel) if year <= state.year][-HEAT_YEARS:]
    if len(years) < 2:  # noqa: PLR2004 — таблица по годам, а не по одному году
        raise NoAnswerError(_("Для тепловой таблицы нужны хотя бы два года."))
    names = {row["code"]: row["name"] for row in region_rows()}
    chosen = [code for code in state.codes if code in names]
    codes = chosen or [code for code in names if any(code in panel[year] for year in years)]
    values = [panel[year][code] for year in years for code in codes if code in panel[year]]
    classification = classify(values, class_count=HEAT_CLASSES)
    if classification is None:
        raise NoAnswerError(_("Значения одинаковы: шкалы не построить."))
    palette = sequential_palette(classification.class_count)
    count = classification.class_count
    precision = subject.item.precision
    last = years[-1]

    def cell(value: float | None) -> dict[str, Any]:
        if value is None:
            return {"value": "", "step": "", "light": False}
        index = classification.class_of(value)
        assert index is not None
        return {
            "value": ru_number(value, precision),
            # Ступень шкалы (--scale-seq-N): цвет клетки задаёт правило оформления.
            "step": palette[index].rsplit("-", 1)[1],
            # Две верхние ступени тёмные: подпись светлая.
            "light": index >= count - 2,
        }

    rows = [
        {
            "code": code,
            "name": names[code],
            "sort": panel[last].get(code),
            "cells": [cell(panel[year].get(code)) for year in years],
        }
        for code in codes
    ]
    rows.sort(key=lambda row: (row["sort"] is None, -(row["sort"] or 0.0)))
    country = series_values([state.series.key], [COUNTRY_CODE]).get(state.series.key, {})
    by_year = country.get(COUNTRY_CODE, {})
    return {
        "year": last,
        "years": years,
        "rows": rows,
        "country": [
            ru_number(by_year[year], precision) if year in by_year else "" for year in years
        ],
        "has_country": bool(by_year),
        "legend": [
            {
                "step": palette[index].rsplit("-", 1)[1],
                "lower": ru_number(item["lower"], precision),
                "upper": ru_number(item["upper"], precision),
            }
            for index, item in enumerate(classification.intervals())
        ],
        "open_url": _url("surface:table", series=state.series.key, year=last),
    }


# --- Малые графики -------------------------------------------------------------------------


def multiples(asked: Asked) -> dict[str, Any]:
    """
    Малые графики: клетка региона — ход показателя за последние годы до года исследования,
    регионы — по федеральным округам, с полными названиями. Шкала по выбору карточки: общая
    (высота линии сравнима между регионами) или своя у каждой клетки (виден ход внутри
    региона). Точка — год исследования; мой регион отмечен.
    """
    from apps.accounts.region import my_region
    from apps.analytics.selectors import region_rows
    from apps.warehouse.queries import region_panel

    request, state, subject = asked.request, asked.state, asked.subject
    assert state.series is not None and state.year is not None
    panel = region_panel(state.series.key)
    years = [year for year in sorted(panel) if year <= state.year][-MULTIPLES_YEARS:]
    if len(years) < 2:  # noqa: PLR2004 — ход — это хотя бы два года
        raise NoAnswerError(_("Для малых графиков нужны хотя бы два года."))
    own = (asked.block or {}).get("scale") == "own"
    rows = region_rows()
    chosen = set(state.codes)
    if chosen:
        rows = [row for row in rows if row["code"] in chosen]
    series_of = {row["code"]: [panel[year].get(row["code"]) for year in years] for row in rows}
    known = [value for values in series_of.values() for value in values if value is not None]
    if not known:
        raise NoAnswerError(_("За эти годы значений по регионам нет."))
    region = my_region(request)
    mine = region.code if region is not None else ""
    precision = subject.item.precision

    groups: list[dict[str, Any]] = []
    for row in rows:
        values = series_of[row["code"]]
        present = [value for value in values if value is not None]
        if not present:
            continue
        low, high = (min(present), max(present)) if own else (min(known), max(known))
        cell = {
            "code": row["code"],
            "name": row["name"],
            "mine": row["code"] == mine,
            "value": ru_number(values[-1], precision) if values[-1] is not None else "",
            **_spark(values, low, high),
        }
        if not groups or groups[-1]["code"] != row["district_code"]:
            groups.append({"code": row["district_code"], "name": row["district_name"], "cells": []})
        groups[-1]["cells"].append(cell)
    return {
        "year": years[-1],
        "first_year": years[0],
        "groups": groups,
        "scale": "own" if own else "common",
        "low": ru_number(min(known), precision),
        "high": ru_number(max(known), precision),
        "width": SPARK_WIDTH,
        "height": SPARK_HEIGHT,
        "open_url": _url("surface:table", series=state.series.key, year=years[-1]),
    }


def _spark(values: list[float | None], low: float, high: float) -> dict[str, str]:
    """
    Линия клетки в заданной шкале: путь SVG с разрывами на пропусках и точка последнего
    года; координаты — строками (дробь шаблон записал бы с запятой).
    """
    spread = (high - low) or 1.0
    step = (SPARK_WIDTH - 2 * SPARK_PAD) / (len(values) - 1)
    segments: list[str] = []
    pen = False
    end = ("", "")
    for index, value in enumerate(values):
        if value is None:
            pen = False
            continue
        x = SPARK_PAD + index * step
        y = SPARK_PAD + (1 - (value - low) / spread) * (SPARK_HEIGHT - 2 * SPARK_PAD)
        segments.append(f"{'L' if pen else 'M'}{x:.1f} {y:.1f}")
        pen = True
        if index == len(values) - 1:
            end = (f"{x:.1f}", f"{y:.1f}")
    return {"path": " ".join(segments), "end_x": end[0], "end_y": end[1]}


# --- Насколько различаются регионы --------------------------------------------------------


def spread(asked: Asked) -> dict[str, Any]:
    """Разрыв между крайними десятыми долями регионов, Джини, вариация и ход разрыва."""
    from apps.core.charts import sparkline_path
    from apps.warehouse.queries import series_statistics_timeline

    state = asked.state

    assert state.series is not None and state.year is not None
    values = _values(state)
    _enough(values)
    array, weights = inequality.prepare(list(values.values()), None)
    gini = inequality.gini(array, weights)
    decile = inequality.decile_ratio(array)
    ranged = inequality.range_ratio(array)
    variation = inequality.coefficient_of_variation(array, weights)
    timeline = [
        row
        for row in series_statistics_timeline(state.series.key)
        if int(row["year"]) <= state.year
    ]
    return {
        "year": state.year,
        "decile": ru_number(decile, 1) if decile else "",
        "range": ru_number(ranged, 1) if ranged else "",
        "gini": ru_number(gini, 2) if gini is not None else "",
        "variation": ru_number(variation * 100, 0) if variation is not None else "",
        "regions": len(values),
        "spark": sparkline_path([row.get("decile_ratio") for row in timeline]),
        "first_year": int(timeline[0]["year"]) if timeline else None,
        "open_url": _url("analytics:inequality", series=state.series.key, year=state.year),
    }


# --- Похожи ли соседи ----------------------------------------------------------------------


def neighbours(asked: Asked) -> dict[str, Any]:
    """Глобальный индекс Морана по соседству с мостами и морем: значение и вывод правилом."""
    state = asked.state
    assert state.series is not None and state.year is not None
    values = _values(state)
    _enough(values)
    found = cached_result(
        "study-moran",
        {"series": state.series.key, "year": state.year, "revision": RESULT_REVISION},
        lambda: _moran(values),
    )
    if found is None:
        raise NoAnswerError(_("Индекс не рассчитать: значения регионов одинаковы."))
    return {
        "year": state.year,
        "value": ru_number(found["value"], 2),
        "direction": DIRECTIONS[found["direction"]],
        "p_value": ru_number(found["p_value"], 3),
        "regions": found["observations"],
        "open_url": _url("analytics:spatial", series=state.series.key, year=state.year),
    }


def _moran(values: dict[str, float]) -> dict[str, Any] | None:
    codes = sorted(values)
    adjacency = neighbour_map("all")
    weights = spatial.build_matrix(codes, {code: adjacency.get(code, []) for code in codes})
    found = spatial.global_moran(np.asarray([values[code] for code in codes], dtype=float), weights)
    if found is None:
        return None
    return {
        "value": found.value,
        "direction": found.direction,
        "p_value": found.p_value,
        "observations": found.observations,
    }


# --- Сближаются ли регионы -------------------------------------------------------------------


def convergence(asked: Asked) -> dict[str, Any]:
    """
    Сигма и бета за полный отрезок ряда — с умолчаниями инструмента «Конвергенция»
    (постоянный состав, абсолютная постановка) и проверкой устойчивости к составу.
    """
    from apps.analytics import sigma_beta
    from apps.analytics.core import convergence as core
    from apps.analytics.selectors import COMPOSITION_CONSTANT
    from apps.warehouse.queries import region_panel

    state = asked.state
    assert state.series is not None
    key = state.series.key
    panel = region_panel(key)
    years = sorted(panel)
    span = sigma_beta.default_span(panel, years) if years else (0, 0)
    if span[1] - span[0] < core.MIN_SPAN_YEARS:
        raise NoAnswerError(
            _("Нужно не меньше %(count)s лет между первым и последним годом.")
            % {"count": core.MIN_SPAN_YEARS}
        )

    def build() -> dict[str, Any]:
        points = sigma_beta.sigma(panel, *span, COMPOSITION_CONSTANT)["points"]
        trend = core.sigma_trend(points)
        result = sigma_beta.beta(sigma_beta.observations(key, *span), *span)
        checked = sigma_beta.robustness(
            panel, key, span, COMPOSITION_CONSTANT, sigma_beta.DEFAULT_MODE
        )
        return {
            "change": trend.get("relative_change") if trend.get("available") else None,
            "sigma": sigma_beta.sigma_verdict(trend),
            "beta": sigma_beta.beta_verdict(result),
            "beta_value": result.beta if result else None,
            "p_value": result.fit.slope.p_value if result else None,
            "regions": result.fit.observations if result else (points[-1].count if points else 0),
            "stable": checked["stable"],
        }

    found = cached_result("study-convergence", {"series": key, "revision": RESULT_REVISION}, build)
    if found["sigma"] == sigma_beta.VERDICT_UNAVAILABLE:
        raise NoAnswerError(
            _("Нужны положительные значения хотя бы по %(count)s регионам каждый год.")
            % {"count": core.MIN_TERRITORIES}
        )
    return {
        **found,
        "beta_value": ru_number(found["beta_value"], 4) if found["beta_value"] is not None else "",
        "first_year": span[0],
        "last_year": span[1],
        "open_url": _url("analytics:convergence", series=key, first=span[0], last=span[1]),
    }


# --- С чем связан ---------------------------------------------------------------------------


def related(asked: Asked) -> dict[str, Any]:
    """Самые тесные значимые связи с рядами основного набора за общий год."""
    from . import related as relations

    state = asked.state

    assert state.series is not None
    _enough(_values(state))
    found = relations.related(state.series)  # type: ignore[arg-type]
    links = [link for link in found["significant"] if link.item.key != state.series.key]
    dataset = getattr(state.series, "dataset", None)
    if dataset is not None:
        base = reverse("userdata:related", args=[dataset.public_id])
        more = f"{base}?{urlencode({'series': state.series.key})}"
    else:
        more = _url("analytics:correlation", series=state.series.key)
    return {
        "year": found["year"],
        "links": [
            {
                "title": link.item.short_title,
                "coefficient": ru_number(link.result.coefficient, 2),
                "strength": link.result.strength,
                "direct": (link.result.coefficient or 0) > 0,
                "url": link.url,
            }
            for link in links[:RELATED_SHOWN]
        ],
        "significant": len(links),
        "pairs": found["pairs"],
        "open_url": more,
    }


# --- Связь двух показателей ----------------------------------------------------------------


def relation(asked: Asked, other: SurfaceState, partner: Any) -> dict[str, Any]:
    """Коэффициент Спирмена двух рядов по регионам за общий год и облако точек."""
    from apps.analytics.charts import scatter_fit_option
    from apps.warehouse.queries import region_panel

    state, subject = asked.state, asked.subject

    assert state.series is not None and other.series is not None
    first = region_panel(state.series.key)
    second = region_panel(other.series.key)
    year, codes, result = _paired(first, second, state.year)
    names = {row["code"]: row["name"] for row in region_rows()}
    points = [
        {"code": code, "name": names[code], "x": first[year][code], "y": second[year][code]}
        for code in codes
    ]
    option = scatter_fit_option(points, x_name=subject.title, y_name=partner.title)
    return {
        "year": year,
        "coefficient": ru_number(result.coefficient, 2),
        "strength": result.strength,
        "direct": result.coefficient > 0,
        "significant": result.is_significant,
        "pairs": result.pairs,
        "other_title": partner.title,
        "chart": option,
        "open_url": _url(
            "analytics:correlation",
            series=[state.series.key, other.series.key],
            x=state.series.key,
            y=other.series.key,
            year=year,
        ),
    }


def _paired(
    first: dict[int, dict[str, float]], second: dict[int, dict[str, float]], wanted: int | None
) -> tuple[int, list[str], Any]:
    """
    Общий год двух рядов (год исследования, если он есть у обоих; иначе год с наибольшим
    числом пар), регионы с обоими значениями и коэффициент Спирмена между ними.
    """
    from apps.analytics.selectors import region_rows

    common = sorted(set(first) & set(second))
    if not common:
        raise NoAnswerError(_("У показателей нет общего года."))
    year = (
        wanted
        if wanted in common
        else max(common, key=lambda item: (len(set(first[item]) & set(second[item])), item))
    )
    codes = [
        row["code"]
        for row in region_rows()
        if row["code"] in first[year] and row["code"] in second[year]
    ]
    if len(codes) < correlation.MIN_PAIRS:
        raise NoAnswerError(_("Пар значений слишком мало для оценки связи."))
    result = correlation.correlate(
        [first[year][code] for code in codes], [second[year][code] for code in codes], "spearman"
    )
    if result.coefficient is None:
        raise NoAnswerError(_("Связь не рассчитать: у одного из показателей значения одинаковы."))
    return year, codes, result


# --- Сравнение двух показателей ------------------------------------------------------------


def comparison(asked: Asked, other: SurfaceState, partner: Any) -> dict[str, Any]:
    """
    Два показателя рядом: линии по годам для одной территории (общего региона, моего или
    России) — в своих единицах, если единицы совпадают, иначе первый общий год = 100;
    связь по регионам за общий год и регионы, места которых расходятся сильнее всего.
    """
    from apps.accounts.region import my_region
    from apps.catalog.models import Territory
    from apps.core.charts import timeline_option
    from apps.warehouse.queries import COUNTRY_CODE, series_values

    request, state, subject = asked.request, asked.state, asked.subject

    assert state.series is not None and other.series is not None
    keys = [state.series.key, other.series.key]
    region = my_region(request)
    candidates = list(
        dict.fromkeys(
            [*state.codes[:1], *([region.code] if region is not None else []), COUNTRY_CODE]
        )
    )
    values = series_values(keys, candidates)
    focus, years = "", []
    for code in candidates:
        mine = values.get(keys[0], {}).get(code, {})
        theirs = values.get(keys[1], {}).get(code, {})
        shared = sorted(
            year for year in set(mine) & set(theirs) if state.year is None or year <= state.year
        )[-COMPARE_YEARS:]
        if len(shared) >= 2:  # noqa: PLR2004 — линия — хотя бы из двух лет
            focus, years = code, shared
            break
    chart: dict[str, Any] | None = None
    base_year = None
    same_unit = subject.unit.strip().casefold() == partner.unit.strip().casefold()
    if focus:
        mine = values[keys[0]][focus]
        theirs = values[keys[1]][focus]
        if same_unit:
            lines = [[mine[year] for year in years], [theirs[year] for year in years]]
        elif mine[years[0]] > 0 and theirs[years[0]] > 0:
            base_year = years[0]
            lines = [
                [round(mine[year] / mine[base_year] * 100, 1) for year in years],
                [round(theirs[year] / theirs[base_year] * 100, 1) for year in years],
            ]
        else:
            lines = []
        if lines:
            unit = subject.unit if same_unit else _("%(year)s = 100") % {"year": base_year}
            chart = timeline_option(
                years,
                [
                    {"name": subject.title, "values": lines[0], "primary": True},
                    {"name": partner.title, "values": lines[1]},
                ],
                unit=unit,
                end_labels=False,
            )
    territory = Territory.objects.filter(code=focus).first() if focus else None
    found: dict[str, Any] = {
        "year": years[-1] if years else state.year,
        "focus": focus,
        "focus_name": territory.name if territory is not None else "",
        "is_country": focus == COUNTRY_CODE,
        "base_year": base_year,
        "same_unit": same_unit,
        # В своих единицах линии — только при общей единице, иначе первый год = 100.
        "unit": subject.unit if same_unit else "",
        "chart": chart,
        "titles": [subject.title, partner.title],
        "other_title": partner.title,
        "coefficient": "",
        "apart": [],
        "open_url": _url("compare:index", series=keys[0]),
    }
    try:
        found.update(_ranks(keys, state.year))
    except NoAnswerError:
        # Связь по регионам не считается (мало пар): остаётся график по годам.
        if chart is None:
            raise
    return found


def _ranks(keys: list[str], wanted: int | None) -> dict[str, Any]:
    """Связь двух рядов по регионам за общий год и регионы, места которых дальше всего."""
    from apps.analytics.selectors import region_rows
    from apps.warehouse.queries import region_panel

    year, codes, result = _paired(region_panel(keys[0]), region_panel(keys[1]), wanted)
    first = region_panel(keys[0])[year]
    second = region_panel(keys[1])[year]
    rank_first = _places({code: first[code] for code in codes})
    rank_second = _places({code: second[code] for code in codes})
    names = {row["code"]: row["name"] for row in region_rows()}
    apart = sorted(codes, key=lambda code: -abs(rank_first[code] - rank_second[code]))
    return {
        "year": year,
        "coefficient": ru_number(result.coefficient, 2),
        "strength": result.strength,
        "direct": result.coefficient > 0,
        "significant": result.is_significant,
        "pairs": result.pairs,
        "apart": [
            {
                "code": code,
                "name": names[code],
                "first": rank_first[code],
                "second": rank_second[code],
            }
            for code in apart[:APART_SHOWN]
            if rank_first[code] != rank_second[code]
        ],
        "open_url": _url("analytics:correlation", series=keys, x=keys[0], y=keys[1], year=year),
    }


def _places(values: dict[str, float]) -> dict[str, int]:
    """Места регионов по убыванию значения; равным — одно место."""
    ordered = sorted(values.values(), reverse=True)
    first: dict[float, int] = {}
    for place, value in enumerate(ordered, start=1):
        first.setdefault(value, place)
    return {code: first[value] for code, value in values.items()}
