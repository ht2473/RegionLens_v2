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
from django.core.exceptions import ValidationError
from django.http import HttpRequest
from django.urls import reverse
from django.utils.translation import get_language, gettext_lazy
from django.utils.translation import gettext as _

from apps.analytics.core import correlation, inequality, spatial
from apps.analytics.selectors import cached_result, neighbour_map, region_rows
from apps.core.templatetags.formatting import NBSP, permutation_p, ru_number
from apps.surface.state import SurfaceState

from . import unusual
from .series import FEW_REGIONS

# Регионов в перечнях «больше всего» и «меньше всего»; лет в изменении — как в ответах поиска.
LEADERS = 3
TREND_YEARS = 5
# Связей в ответе «Связь с другими показателями».
RELATED_SHOWN = 3
# Названий в строке «заметно выделяются».
UNUSUAL_SHOWN = 5
# Регионов в перечнях «вырос сильнее» и «меньше всего».
GROWTH_SHOWN = 5
# Графики по регионам: лет в клетке и размер клетки в единицах чертежа.
MULTIPLES_YEARS = 15
SPARK_WIDTH = 120
SPARK_HEIGHT = 36
SPARK_PAD = 3.0
# Лет на графике сравнения двух показателей; регионов, расходящихся сильнее всего.
COMPARE_YEARS = 15
APART_SHOWN = 3
# Редакция содержимого: кэш расчётов не знает о правке кода.
RESULT_REVISION = 1

# Названия карточек — принятые термины анализа: те же в окне «Добавить карточку», на поле
# и в отчёте.
TITLES = {
    "notable": gettext_lazy("Сводка"),
    "leaders": gettext_lazy("Лидеры и отстающие"),
    "mine": gettext_lazy("Положение моего региона"),
    "change": gettext_lazy("Динамика по России"),
    "growth": gettext_lazy("Темпы роста по регионам"),
    "districts": gettext_lazy("Сравнение федеральных округов"),
    "multiples": gettext_lazy("Графики по регионам"),
    "spread": gettext_lazy("Региональное неравенство"),
    "convergence": gettext_lazy("Сближение регионов (конвергенция)"),
    "neighbours": gettext_lazy("Пространственная связь (соседство)"),
    "related": gettext_lazy("Связь с другими показателями"),
    "structure": gettext_lazy("Структура"),
    "contributions": gettext_lazy("Вклад в изменение"),
    "group": gettext_lazy("Группа регионов"),
    "relation": gettext_lazy("Связь двух показателей"),
    "comparison": gettext_lazy("Сравнение двух показателей"),
}
ICONS = {
    "notable": "list",
    "leaders": "ranking",
    "mine": "map-pin",
    "change": "dynamics",
    "growth": "growth",
    "districts": "districts",
    "multiples": "multiples",
    "spread": "inequality",
    "convergence": "convergence",
    "neighbours": "spatial",
    "related": "correlation",
    "structure": "structure",
    "contributions": "waterfall",
    "group": "districts",
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
    summed = False
    if not rows and not state.codes:
        # Строки России в таблице нет: у суммы ход по России — сумма регионов.
        rows = _regions_total(state.series.key, state.year)
        if rows:
            wanted, summed = COUNTRY_CODE, True
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
        "summed": summed,
        "code": wanted,
        "value": ru_number(last["value"], subject.item.precision),
        "change": shift,
        "direction": direction,
        "since": last_year - TREND_YEARS if before else None,
        "spark": sparkline_path([row["value"] for row in rows]),
        "open_url": _url("compare:index", **query),
    }


def _regions_total(key: str, until: int) -> list[dict[str, Any]]:
    """
    Сумма регионов по годам вместо строки России, которой в таблице нет: только у сумм (поток,
    запас) и в годы, где значения есть не меньше чем у 80 субъектов. Состав субъектов — общий
    для этих лет: иначе сумма росла бы от одного появления новых субъектов в таблице. Доли
    и средние не складываются — у них ответа нет.
    """
    from apps.sources.territories import region_codes
    from apps.warehouse.queries import series_values

    from . import consistency, operations, semantics

    info = operations.info_of(key)
    if info is None or not semantics.is_additive(info.type):
        return []
    by_year: dict[int, dict[str, float]] = {}
    for code, years in series_values([key], list(region_codes())).get(key, {}).items():
        for year, value in years.items():
            if year <= until:
                by_year.setdefault(year, {})[code] = value
    full = [
        set(values)
        for values in by_year.values()
        if consistency.regions_total(values)[1] >= consistency.SUM_REGIONS
    ]
    common = set.intersection(*full) if full else set()
    rows = []
    for year in sorted(by_year):
        values = {code: value for code, value in by_year[year].items() if code in common}
        total, count = consistency.regions_total(values)
        if total is not None and count >= consistency.SUM_REGIONS:
            rows.append({"year": year, "value": total})
    return rows


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


# --- Темпы роста по регионам -----------------------------------------------------------------


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


def coefficient(value: float | None) -> str:
    """Коэффициент связи: два знака и настоящий минус — «−0,45», а не дефис."""
    return ru_number(value, 2).replace("-", "−")


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
    Темпы роста по регионам за пять лет до года исследования (или с первого года, если ряд
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


# --- Структура и вклады в изменение ---------------------------------------------------------

# Частей структуры со своим цветом (палитра рядов); остальные — вместе, «прочие».
STRUCTURE_PARTS = 8
# Регионов-вкладов отдельными столбцами водопада; остальные — одним.
CONTRIBUTORS = 8
# Размах водопада меньше этой доли его наибольшего столбца — ось начинается не с нуля.
WATERFALL_SPAN = 0.5
# Поле оси ниже наименьшего столбца — доля размаха.
WATERFALL_MARGIN = 0.15


def _part_titles(keys: list[str]) -> tuple[dict[str, str], str]:
    """
    Названия частей и целого: у разрезов одного показателя — название разреза («Женщины»)
    и показателя, у разных показателей таблицы — их короткие названия и перечень первых.
    """
    from .series import user_series_many

    found = user_series_many(keys)
    if len(found) < len(set(keys)):
        raise NoAnswerError(_("Часть структуры удалена из таблицы."))
    english = get_language() != "ru"
    titles = list(dict.fromkeys(found[key].record.title for key in keys))
    named = {}
    for key, series in found.items():
        name = series.name if english else series.name_ru
        if len(titles) == 1 and name:
            named[key] = name
        else:
            named[key] = f"{series.record.title}, {name}" if name else series.record.title
    if len(titles) == 1:
        whole = titles[0]
    elif len(titles) == 2:  # noqa: PLR2004
        whole = _("%(first)s и %(second)s") % {"first": titles[0], "second": titles[1]}
    else:
        whole = _("%(first)s и другие (%(count)s)") % {
            "first": titles[0],
            "count": len(titles) - 1,
        }
    return named, whole


def structure(asked: Asked) -> dict[str, Any]:
    """
    Структура по регионам: доли частей (разрезов одного показателя или сумм одной таблицы
    с одной единицей) в их сумме за год исследования — столбец региона в 100 %. Сверху —
    Россия как сумма регионов; регионы — по доле самой крупной части. Частей больше восьми —
    мелкие вместе как «прочие». Регион без значения какой-либо части пропускается.
    """
    from apps.analytics.selectors import region_rows
    from apps.core.charts import STRUCTURE_PAD, STRUCTURE_ROW, structure_option
    from apps.warehouse.queries import region_panel

    state = asked.state
    assert state.year is not None and asked.block is not None
    keys = [key for key in asked.block.get("parts") or [] if isinstance(key, str)]
    if len(keys) < 2:  # noqa: PLR2004
        raise NoAnswerError(_("Для структуры нужны хотя бы две части."))
    titles, heading = _part_titles(keys)
    panels = {key: region_panel(key).get(state.year) or {} for key in keys}
    codes = sorted(set.intersection(*(set(values) for values in panels.values())))
    totals = {code: sum(panels[key][code] for key in keys) for code in codes}
    codes = [code for code in codes if totals[code] > 0]
    if any(panels[key][code] < 0 for key in keys for code in codes):
        raise NoAnswerError(_("У частей есть отрицательные значения — долей у них нет."))
    if len(codes) < LEADERS:
        raise NoAnswerError(_("За этот год значений всех частей по регионам слишком мало."))
    # Части по доле в сумме регионов: крупные — своим цветом, мелкие — «прочими».
    country = {key: sum(panels[key][code] for code in codes) for key in keys}
    whole = sum(country.values())
    ordered = sorted(keys, key=lambda key: -country[key])
    shown = ordered if len(ordered) <= STRUCTURE_PARTS else ordered[: STRUCTURE_PARTS - 1]
    rest = [key for key in ordered if key not in shown]
    lead = ordered[0]
    codes.sort(key=lambda code: -panels[lead][code] / totals[code])
    names = {row["code"]: row["name"] for row in region_rows()}
    labels = [_("Россия (сумма регионов)"), *(names.get(code, code) for code in codes)]

    parts: list[dict[str, Any]] = []
    groups = [(titles[key], [key]) for key in shown]
    if rest:
        groups.append((_("прочие"), rest))
    for title, members in groups:
        column = [sum(country[key] for key in members) / whole * 100]
        column += [sum(panels[key][code] for key in members) / totals[code] * 100 for code in codes]
        parts.append({"name": title, "shares": [round(value, 1) for value in column]})
    leader = parts[0]["shares"][1:]
    high = max(range(len(codes)), key=lambda index: leader[index])
    low = min(range(len(codes)), key=lambda index: leader[index])
    return {
        "year": state.year,
        # Название карточки — целое, а не первая часть.
        "title": heading,
        "parts": [
            {**part, "country": ru_number(part["shares"][0], 1), "colour": index}
            for index, part in enumerate(parts, start=1)
        ],
        "lead": parts[0]["name"],
        "high": {"name": names.get(codes[high], codes[high]), "share": ru_number(leader[high], 1)},
        "low": {"name": names.get(codes[low], codes[low]), "share": ru_number(leader[low], 1)},
        "rows": [
            {"name": label, "shares": [ru_number(part["shares"][index], 1) for part in parts]}
            for index, label in enumerate(labels)
        ],
        "count": len(codes),
        "chart": structure_option(labels, parts),
        "chart_height": STRUCTURE_PAD + STRUCTURE_ROW * len(labels),
        "unit": "%",
    }


def contributions(asked: Asked) -> dict[str, Any]:
    """
    Вклад в изменение: изменение суммы между двумя годами разложено на вклады — водопад
    от итога начального года к итогу конечного. Без частей вклады — регионов; с частями
    (``parts`` карточки) — составляющих: каждая часть — сумма по одним и тем же регионам.
    Год конечный — исследования, начальный — как у «Темпов роста по регионам» (не дальше пяти лет).
    Вклады складываются только у сумм; регионы без значения в одном из лет (у частей —
    хотя бы одной части) не входят в итог.
    """
    from apps.core.charts import STRUCTURE_PAD, STRUCTURE_ROW, waterfall_option

    state, subject = asked.state, asked.subject
    assert state.series is not None and state.year is not None
    if not subject.item.absolute:
        raise NoAnswerError(
            _(
                "Вклады регионов складываются только у сумм. У относительной величины "
                "смотрите «Темпы роста по регионам»."
            )
        )
    end = state.year
    earlier = [year for year in _years(state) if year < end]
    if not earlier:
        raise NoAnswerError(_("Для изменения нужны хотя бы два года."))
    within = [year for year in earlier if end - year <= TREND_YEARS]
    start = min(within) if within else max(earlier)
    keys = [key for key in (asked.block or {}).get("parts") or [] if isinstance(key, str)]
    shifts = (
        _part_shifts(keys, start, end) if keys else _region_shifts(state.series.key, start, end)
    )
    first, last = shifts.first, shifts.last
    precision = subject.item.precision
    ranked = sorted(shifts.found, key=lambda entry: -abs(entry[1]))
    shown, folded = ranked[:CONTRIBUTORS], ranked[CONTRIBUTORS:]
    steps: list[dict[str, Any]] = [{"name": str(start), "start": 0, "end": first, "total": True}]
    level = first
    entries = list(shown)
    if folded:
        entries.append(
            (shifts.rest % {"count": len(folded)}, sum(shift for _name, shift in folded))
        )
    for name, shift in entries:
        steps.append({"name": name, "start": level, "end": level + shift, "total": False})
        level += shift
    steps.append({"name": str(end), "start": 0, "end": last, "total": True})
    for step in steps:
        amount = step["end"] - step["start"]
        step["label"] = (
            ru_number(amount, precision) if step["total"] else signed(DIFFERENCE, amount, precision)
        )
    moving: list[float] = [
        value for step in steps if not step["total"] for value in (step["start"], step["end"])
    ]
    low = None
    high = max(first, last, *moving)
    bottom = min(moving or [first])
    if bottom > 0 and high - bottom < WATERFALL_SPAN * high:
        low = _round_axis(bottom - (high - bottom) * WATERFALL_MARGIN)
    rising = sorted((entry for entry in entries[: len(shown)] if entry[1] > 0), key=lambda e: -e[1])
    falling = sorted((entry for entry in entries[: len(shown)] if entry[1] < 0), key=lambda e: e[1])
    return {
        **({"title": shifts.heading} if shifts.heading else {}),
        "by_parts": bool(keys),
        "year": end,
        "since": start,
        "first": ru_number(first, precision),
        "last": ru_number(last, precision),
        "change": signed(DIFFERENCE, last - first, precision),
        "relative": signed(PERCENT, (last - first) / first * 100, 1) if first > 0 else "",
        "count": shifts.count,
        "rising": [
            {"name": name, "change": signed(DIFFERENCE, shift, precision)}
            for name, shift in rising[:3]
        ],
        "falling": [
            {"name": name, "change": signed(DIFFERENCE, shift, precision)}
            for name, shift in falling[:3]
        ],
        "steps": [
            {"name": step["name"], "label": step["label"], "total": step["total"]} for step in steps
        ],
        "chart": waterfall_option(steps, low=low),
        "chart_height": STRUCTURE_PAD + 2 * STRUCTURE_ROW * len(steps),
        "open_url": _url("maps:choropleth", series=state.series.key, year=end, compare=start),
    }


@dataclass(frozen=True, slots=True)
class _Shifts:
    """
    Вклады в изменение суммы: пары «название, изменение», итоги начального и конечного года
    по регионам со значениями в обоих, их число, подпись свёрнутых вкладов, название целого.
    """

    found: list[tuple[str, float]]
    first: float
    last: float
    count: int
    rest: str
    heading: str = ""


def _region_shifts(key: str, start: int, end: int) -> _Shifts:
    """Вклады регионов в изменение суммы ряда."""
    from apps.analytics.selectors import region_rows
    from apps.warehouse.queries import region_panel

    panel = region_panel(key)
    before, after = panel.get(start) or {}, panel.get(end) or {}
    codes = sorted(set(before) & set(after))
    if len(codes) < LEADERS:
        raise NoAnswerError(_("Значений в обоих годах слишком мало для сравнения."))
    names = {row["code"]: row["name"] for row in region_rows()}
    return _Shifts(
        found=[(names.get(code, code), after[code] - before[code]) for code in codes],
        first=sum(before[code] for code in codes),
        last=sum(after[code] for code in codes),
        count=len(codes),
        rest=_("остальные регионы (%(count)s)"),
    )


def _part_shifts(keys: list[str], start: int, end: int) -> _Shifts:
    """Вклады составляющих: каждая часть — сумма по одним и тем же регионам в обоих годах."""
    from apps.warehouse.queries import region_panel

    titles, heading = _part_titles(keys)
    panels = {key: region_panel(key) for key in keys}
    codes = sorted(
        set.intersection(
            *(set(panels[key].get(start) or {}) & set(panels[key].get(end) or {}) for key in keys)
        )
    )
    if len(codes) < LEADERS:
        raise NoAnswerError(_("Значений всех частей в обоих годах слишком мало для сравнения."))
    totals = {
        key: (
            sum(panels[key][start][code] for code in codes),
            sum(panels[key][end][code] for code in codes),
        )
        for key in keys
    }
    return _Shifts(
        found=[(titles[key], after - before) for key, (before, after) in totals.items()],
        first=sum(before for before, _after in totals.values()),
        last=sum(after for _before, after in totals.values()),
        count=len(codes),
        rest=_("остальные части (%(count)s)"),
        heading=heading,
    )


def group(asked: Asked) -> dict[str, Any]:
    """
    Группа регионов против остальных: итог группы и остальных регионов по годам — у суммы
    сумма и доля группы в сумме регионов, у относительной величины среднее, взвешенное
    по численности, и разница. Год — исследования, иначе последний с итогом обеих частей.
    """
    from apps.core.charts import timeline_option

    from . import groups, operations, semantics
    from .models import RegionGroup

    state, subject = asked.state, asked.subject
    assert state.series is not None
    try:
        found = RegionGroup.objects.filter(
            public_id=str((asked.block or {}).get("group") or "")
        ).first()
    except ValueError, ValidationError:
        found = None
    if found is None:
        raise NoAnswerError(_("Группа регионов удалена."))
    info = operations.info_of(state.series.key)
    kind = info.type if info is not None else ""
    is_sum = semantics.is_additive(kind) if info is not None else bool(subject.item.absolute)
    median = kind == semantics.INDEX
    years = [
        item
        for item in groups.totals(state.series.key, found.codes, is_sum=is_sum, median=median)
        if item.group is not None and item.rest is not None
    ]
    if not years:
        raise NoAnswerError(_("У регионов группы нет значений этого показателя."))
    chosen = next((item for item in years if item.year == state.year), years[-1])
    precision = subject.item.precision

    def share(item: groups.Year) -> float:
        whole = (item.group or 0.0) + (item.rest or 0.0)
        return (item.group or 0.0) / whole * 100 if whole else 0.0

    labels = [str(item.year) for item in years]
    if is_sum:
        chart = timeline_option(
            labels,
            [
                {
                    "name": _("доля группы в сумме регионов"),
                    "values": [round(share(item), 1) for item in years],
                    "primary": True,
                }
            ],
            unit="%",
            end_labels=False,
        )
    else:
        chart = timeline_option(
            labels,
            [
                {"name": found.title, "values": [item.group for item in years], "primary": True},
                {"name": _("остальные регионы"), "values": [item.rest for item in years]},
            ],
            unit=subject.unit,
            end_labels=False,
        )
    assert chosen.group is not None and chosen.rest is not None
    return {
        "title": f"{subject.title} — {found.title}",
        "year": chosen.year,
        "group_title": found.title,
        "is_sum": is_sum,
        "median": median,
        "group_value": ru_number(chosen.group, precision),
        "rest_value": ru_number(chosen.rest, precision),
        "share": ru_number(share(chosen), 1),
        "difference": signed(DIFFERENCE, chosen.group - chosen.rest, precision),
        "ratio": ru_number(chosen.group / chosen.rest * 100, 0) if chosen.rest else "",
        "members": chosen.members,
        "size": len(found.codes),
        "rows": [
            {
                "year": item.year,
                "group": ru_number(item.group, precision),
                "rest": ru_number(item.rest, precision),
                "share": ru_number(share(item), 1) if is_sum else "",
            }
            for item in reversed(years)
        ],
        "chart": chart,
    }


def _round_axis(value: float) -> float:
    """Начало оси водопада — круглое число не больше данного."""
    import math

    if value <= 0:
        return 0.0
    power = 10.0 ** math.floor(math.log10(value))
    return math.floor(value / power) * power


def _years(state: SurfaceState) -> list[int]:
    """Годы ряда со значениями по регионам."""
    from apps.warehouse.queries import year_counts

    assert state.series is not None
    return sorted(int(year) for year in year_counts(state.series.key))


# --- Сравнение федеральных округов --------------------------------------------------------


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


# --- Сводка ---------------------------------------------------------------------------------


def notable(asked: Asked) -> dict[str, Any]:
    """
    Сводка по ряду, числами и без оценок: наибольшее и наименьшее значение года и их
    отношение (у положительных), в скольких регионах значение выросло с первого года ряда,
    у скольких значения за год нет — из регионов, что есть в ряду хоть за один год: таблица
    по двадцати регионам не «теряет» остальные шестьдесят пять. Ещё — кто заметно
    выделяется (``unusual``, не у сумм).
    """
    from apps.analytics.selectors import region_rows
    from apps.warehouse.queries import region_panel

    state, subject = asked.state, asked.subject
    assert state.series is not None and state.year is not None
    names = {row["code"]: row["name"] for row in region_rows()}
    panel = region_panel(state.series.key)
    values = {code: value for code, value in (panel.get(state.year) or {}).items() if code in names}
    if not values:
        raise NoAnswerError(_("За этот год значений по регионам нет."))
    precision = subject.item.precision
    high = max(values, key=values.__getitem__)
    low = min(values, key=values.__getitem__)

    def point(code: str) -> dict[str, str]:
        return {"code": code, "name": names[code], "value": ru_number(values[code], precision)}

    ratio = values[high] / values[low] if values[low] > 0 and high != low else None
    covered = {code for row in panel.values() for code in row if code in names}
    growth = None
    earlier = [year for year in panel if year < state.year]
    if earlier:
        first = min(earlier)
        both = [code for code in values if code in panel[first]]
        if both:
            growth = {
                "first": first,
                "up": sum(1 for code in both if values[code] > panel[first][code]),
                "total": len(both),
            }
    standing = [] if subject.item.absolute else unusual.standing_out(values)
    return {
        "year": state.year,
        "high": point(high),
        "low": point(low),
        "ratio": ru_number(ratio, 1 if ratio < 10 else 0) if ratio else "",  # noqa: PLR2004
        "growth": growth,
        "missing": len(covered - set(values)),
        "standing": [names[code] for code in standing[:UNUSUAL_SHOWN]],
        "standing_more": max(len(standing) - UNUSUAL_SHOWN, 0),
        "open_url": _url("rankings:index", series=state.series.key, year=state.year),
    }


# --- Графики по регионам ---------------------------------------------------------------------


def multiples(asked: Asked) -> dict[str, Any]:
    """
    Графики по регионам: клетка региона — ход показателя за последние годы до года исследования,
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


# --- Региональное неравенство ----------------------------------------------------------------


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


# --- Пространственная связь ------------------------------------------------------------------


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
        "p_value": permutation_p(found["p_value"], found.get("permutations", 999)),
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
        "permutations": found.permutations,
        "observations": found.observations,
    }


# --- Сближение регионов ----------------------------------------------------------------------


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


# --- Связь с другими показателями ------------------------------------------------------------


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
                "coefficient": coefficient(link.result.coefficient),
                "strength": link.result.strength,
                "direct": (link.result.coefficient or 0) > 0,
                "url": link.url,
                # Год показателя сайта, если у него нет значений за год таблицы.
                "other_year": link.year if link.year != found["year"] else None,
            }
            for link in links[:RELATED_SHOWN]
        ],
        "significant": len(links),
        "pairs": found["pairs"],
        "other_years": any(link.year != found["year"] for link in links[:RELATED_SHOWN]),
        "open_url": more,
    }


# --- Связь двух показателей ----------------------------------------------------------------


def _pair_names(state: SurfaceState, other: SurfaceState, subject: Any, partner: Any) -> list[str]:
    """
    Подписи двух показателей на графике: при одинаковых названиях — откуда каждый (своя
    таблица или данные сайта), иначе две линии и две оси неразличимы.
    """
    from apps.warehouse.routing import is_user_key

    names = [str(subject.title), str(partner.title)]
    if names[0].strip().casefold() != names[1].strip().casefold():
        return names
    assert state.series is not None and other.series is not None
    kinds = [is_user_key(state.series.key), is_user_key(other.series.key)]
    if kinds[0] == kinds[1]:
        return [f"{names[0]} (1)", f"{names[1]} (2)"]
    return [
        _("%(title)s — своя таблица") % {"title": name}
        if own
        else _("%(title)s — данные сайта") % {"title": name}
        for name, own in zip(names, kinds, strict=True)
    ]


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
    x_name, y_name = _pair_names(state, other, subject, partner)
    option = scatter_fit_option(points, x_name=x_name, y_name=y_name)
    return {
        "year": year,
        "coefficient": coefficient(result.coefficient),
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
    names = _pair_names(state, other, subject, partner)
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
                    {"name": names[0], "values": lines[0], "primary": True},
                    {"name": names[1], "values": lines[1]},
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
        "titles": names,
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
        "coefficient": coefficient(result.coefficient),
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
