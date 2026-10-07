"""
Ответы карточек исследования — числами, без сочинённого текста: лидеры и отстающие, как
изменился показатель, насколько различаются регионы, похожи ли соседи, с чем связан,
связь двух показателей.

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
from apps.core.templatetags.formatting import ru_number
from apps.surface.state import SurfaceState

from .series import FEW_REGIONS

# Регионов в перечнях «больше всего» и «меньше всего»; лет в изменении — как в ответах поиска.
LEADERS = 3
TREND_YEARS = 5
# Связей в ответе «С чем связан».
RELATED_SHOWN = 3
# Редакция содержимого: кэш расчётов не знает о правке кода.
RESULT_REVISION = 1

TITLES = {
    "leaders": gettext_lazy("Лидеры и отстающие"),
    "change": gettext_lazy("Как изменился"),
    "spread": gettext_lazy("Насколько различаются регионы"),
    "neighbours": gettext_lazy("Похожи ли соседи"),
    "related": gettext_lazy("С чем связан"),
    "relation": gettext_lazy("Связь двух показателей"),
}
ICONS = {
    "leaders": "ranking",
    "change": "dynamics",
    "spread": "inequality",
    "neighbours": "spatial",
    "related": "correlation",
    "relation": "correlation",
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
    """О чём спрошено: запрос (мой регион), выбор холста по карточке и описание ряда."""

    request: HttpRequest
    state: SurfaceState
    subject: Any


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
    common = sorted(set(first) & set(second))
    if not common:
        raise NoAnswerError(_("У показателей нет общего года."))
    # Год исследования, если он есть у обоих; иначе общий год с наибольшим числом пар.
    year = (
        state.year
        if state.year in common
        else max(common, key=lambda item: (len(set(first[item]) & set(second[item])), item))
    )
    names = {row["code"]: row["name"] for row in region_rows()}
    codes = [code for code in names if code in first[year] and code in second[year]]
    if len(codes) < correlation.MIN_PAIRS:
        raise NoAnswerError(_("Пар значений слишком мало для оценки связи."))
    result = correlation.correlate(
        [first[year][code] for code in codes], [second[year][code] for code in codes], "spearman"
    )
    if result.coefficient is None:
        raise NoAnswerError(_("Связь не рассчитать: у одного из показателей значения одинаковы."))
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
