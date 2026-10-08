"""
Ответы-шаблоны на разобранный запрос: числа — из склада и справочников, без сочинённого
текста; у каждого ответа — ссылка на полный вид.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from django.urls import reverse
from django.utils.translation import gettext as _

from apps.search import parse as kinds
from apps.search.parse import Reading

# Сколько регионов в перечнях «больше всего» и «меньше всего».
LEADERS = 3
# За сколько лет изменение в ответе о динамике.
TREND_YEARS = 5
# Наименьшее число регионов с обоими значениями для ответа о связи.
MIN_PAIRS = 30
# Показатели сравнения двух регионов без названного показателя: плитки главной.
COMPARE_HEADLINE = 6
# Границы силы связи по модулю коэффициента и уровень значимости.
WEAK, STRONG = 0.3, 0.6
SIGNIFICANCE = 0.05


def build(reading: Reading, home_code: str | None = None) -> dict[str, Any] | None:
    """Ответ на запрос или ``None``, если вид ответа — «нет в данных» или склад не собран."""
    from apps.warehouse.duckdb_client import WarehouseNotBuiltError

    builders: dict[str, Any] = {
        kinds.RANK: _rank,
        kinds.VALUE: _value,
        kinds.COMPARE: _compare,
        kinds.TREND: _trend,
        kinds.RELATION: _relation,
        kinds.REGION: _region,
        kinds.DEFINE: _define,
        kinds.METHOD: _method,
    }
    builder = builders.get(reading.kind)
    if builder is None:
        return None
    try:
        answer = _rank(reading, home_code) if reading.kind == kinds.RANK else builder(reading)
    except WarehouseNotBuiltError:
        return None
    if answer is not None:
        answer["kind"] = reading.kind
    return answer


# --- Общее ---------------------------------------------------------------------------------


def _item(key: str) -> Any:
    """Показатель основного набора по ключу."""
    from apps.warehouse.queries import featured_set

    return featured_set().by_key()[key]


def _territories(codes: list[str]) -> dict[str, Any]:
    """Территории справочника по кодам."""
    from apps.catalog.models import Territory

    return {territory.code: territory for territory in Territory.objects.filter(code__in=codes)}


def _frames(item: Any) -> dict[str, Any]:
    """Кадры живой карты ряда: значения, места и крайние по годам."""
    from apps.core.showcase import live_frames

    return live_frames(item)


def _year(years: list[int], wanted: int | None) -> tuple[int | None, bool]:
    """Год ответа: запрошенный, если он есть, иначе последний; второе — подменён ли год."""
    if not years:
        return None, False
    if wanted in years:
        return wanted, False
    return years[-1], wanted is not None


def _url(name: str, **params: Any) -> str:
    """Адрес полного вида с параметрами; перечни — повторяющимся параметром."""
    pairs: list[tuple[str, Any]] = []
    for key, value in params.items():
        if value in (None, "", []):
            continue
        pairs += [(key, item) for item in value] if isinstance(value, list) else [(key, value)]
    return reverse(name) + (f"?{urlencode(pairs)}" if pairs else "")


def _timeline(key: str, code: str) -> list[dict[str, Any]]:
    """Значения ряда по годам для территории."""
    from apps.warehouse.queries import series_timeline

    return [row for row in series_timeline(key, code) if row.get("value") is not None]


def _spark(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Малая линия по значениям."""
    from apps.core.charts import sparkline_path

    return sparkline_path([row["value"] for row in rows])


# --- Где больше и где меньше ---------------------------------------------------------------


def _rank(reading: Reading, home_code: str | None) -> dict[str, Any] | None:
    item = _item(reading.series[0])
    frames = _frames(item)
    year, replaced = _year(frames["years"], reading.year)
    if year is None:
        return None
    frame = frames["frames"][str(year)]

    def rows(leaders: list[list[Any]]) -> list[dict[str, Any]]:
        return [
            {
                "name": frames["names"][index],
                "href": frames["links"][index],
                "value": frame["values"][index],
                "bar": bar,
            }
            for index, bar in leaders[:LEADERS]
        ]

    mine = None
    if home_code in frames["codes"]:
        index = frames["codes"].index(home_code)
        if frame["values"][index]:
            mine = {"name": frames["names"][index], "place": frame["places"][index]}
    return {
        "template": "search/answers/_rank.html",
        "item": item,
        "year": year,
        "year_replaced": replaced,
        "top": rows(frame["top"]),
        "bottom": rows(frame["bottom"]),
        "country": frame["country"],
        "mine": mine,
        "link": _url("rankings:index", series=item.key, year=year),
        "link_label": _("Весь рейтинг"),
    }


# --- Значение в регионе ----------------------------------------------------------------------


def _value(reading: Reading) -> dict[str, Any] | None:
    item = _item(reading.series[0])
    code = reading.codes[0]
    territory = _territories([code]).get(code)
    if territory is None:
        return None
    frames = _frames(item)
    rows = _timeline(item.key, code)
    if not rows:
        return None
    years = [int(row["year"]) for row in rows]
    year, replaced = _year(years, reading.year)
    if year is None:
        return None
    value = next(row["value"] for row in rows if int(row["year"]) == year)
    position: dict[str, Any] = {}
    frame = frames["frames"].get(str(year))
    if frame and code in frames["codes"]:
        index = frames["codes"].index(code)
        position = {
            "place": frame["places"][index],
            "standing": frame["standings"][index],
            "tone": frame["tones"][index] or "neutral",
            "versus": frame["versus"][index],
        }
    return {
        "template": "search/answers/_value.html",
        "item": item,
        "territory": territory,
        "year": year,
        "year_replaced": replaced,
        "value": _number(item, value),
        "country": frame["country"] if frame else "",
        "position": position,
        "spark": _spark([row for row in rows if int(row["year"]) <= year]),
        "first_year": years[0],
        "link": _url("rankings:index", series=item.key, territory=code, year=year),
        "link_label": _("Место среди регионов"),
        "passport": reverse("catalog:territory-detail", kwargs={"slug": territory.slug}),
    }


def _number(item: Any, value: float) -> str:
    """Значение с точностью показателя."""
    from apps.core.templatetags.formatting import ru_number

    return ru_number(value, item.precision)


# --- Сравнение регионов ----------------------------------------------------------------------


def _compare(reading: Reading) -> dict[str, Any] | None:
    from apps.warehouse.queries import featured_set, latest_values_matrix

    codes = reading.codes[:2]
    territories = _territories(codes)
    if len(territories) < len(codes):
        return None
    ordered = [territories[code] for code in codes]
    if reading.series:
        item = _item(reading.series[0])
        lines = []
        for territory in ordered:
            rows = _timeline(item.key, territory.code)
            last = rows[-1] if rows else None
            lines.append(
                {
                    "territory": territory,
                    "value": _number(item, last["value"]) if last else "",
                    "year": int(last["year"]) if last else None,
                    "spark": _spark(rows),
                }
            )
        return {
            "template": "search/answers/_compare.html",
            "item": item,
            "lines": lines,
            "link": _url("compare:index", series=item.key, territory=codes),
            "link_label": _("Динамика рядом"),
        }
    headline = [item for item in featured_set().series if item.headline][:COMPARE_HEADLINE]
    matrix = latest_values_matrix([item.key for item in headline], codes)
    table = []
    for item in headline:
        cells = []
        for code in codes:
            cell = matrix.get((item.key, code))
            cells.append(
                {
                    "value": _number(item, cell["value"])
                    if cell and cell.get("value") is not None
                    else "",
                    "year": cell.get("year") if cell else None,
                }
            )
        table.append({"item": item, "cells": cells})
    return {
        "template": "search/answers/_compare_table.html",
        "territories": ordered,
        "table": table,
        "link": _url("compare:index", territory=codes),
        "link_label": _("Сравнить подробно"),
    }


# --- Как менялся показатель ------------------------------------------------------------------


def _trend(reading: Reading) -> dict[str, Any] | None:
    from apps.core.showcase import change_between

    item = _item(reading.series[0])
    code = reading.codes[0] if reading.codes else "RU"
    territory = _territories([code]).get(code)
    rows = _timeline(item.key, code)
    if not rows or territory is None:
        return None
    last = rows[-1]
    last_year = int(last["year"])
    before = next((row for row in rows if int(row["year"]) == last_year - TREND_YEARS), None)
    change, direction = change_between(item, last["value"], before["value"] if before else None)
    breaks = [
        mark["year"]
        for mark in _frames(item)["breaks"]
        if last_year - TREND_YEARS < int(mark["year"]) <= last_year
    ]
    return {
        "template": "search/answers/_trend.html",
        "item": item,
        "territory": territory,
        "is_country": code == "RU",
        "value": _number(item, last["value"]),
        "year": last_year,
        "first_year": int(rows[0]["year"]),
        "change": change,
        "direction": direction,
        "since": last_year - TREND_YEARS if before else None,
        "breaks": breaks,
        "spark": _spark(rows),
        "link": _url("compare:index", series=item.key, territory=[] if code == "RU" else [code]),
        "link_label": _("Динамика полностью"),
    }


# --- Связь двух показателей -----------------------------------------------------------------


def _relation(reading: Reading) -> dict[str, Any] | None:
    from apps.analytics.core.correlation import correlate, scatter_points
    from apps.core.charts import scatter_option
    from apps.warehouse.queries import series_ranked_panel

    first, second = (_item(key) for key in reading.series[:2])
    panels = []
    for item in (first, second):
        by_year: dict[int, dict[str, float]] = {}
        for row in series_ranked_panel(item.key):
            if row["value"] is not None:
                by_year.setdefault(int(row["year"]), {})[row["territory_code"]] = row["value"]
        panels.append(by_year)
    common = sorted(set(panels[0]) & set(panels[1]), reverse=True)
    year = next(
        (year for year in common if len(set(panels[0][year]) & set(panels[1][year])) >= MIN_PAIRS),
        None,
    )
    if year is None:
        return None
    codes = sorted(set(panels[0][year]) & set(panels[1][year]))
    territories = _territories(codes)
    x: list[float | None] = [panels[0][year][code] for code in codes]
    y: list[float | None] = [panels[1][year][code] for code in codes]
    result = correlate(x, y)
    if result.coefficient is None:
        return None
    labels = [territories[code].name if code in territories else code for code in codes]
    strength = abs(result.coefficient)
    return {
        "template": "search/answers/_relation.html",
        "first": first,
        "second": second,
        "year": year,
        "coefficient": f"{result.coefficient:+.2f}".replace(".", ",").replace("-", "−"),
        "strength": "weak" if strength < WEAK else ("moderate" if strength < STRONG else "strong"),
        "positive": result.coefficient > 0,
        "significant": result.p_value is not None and result.p_value < SIGNIFICANCE,
        "pairs": result.pairs,
        "chart": scatter_option(
            scatter_points(x, y, labels, codes),
            x_name=first.short_title,
            y_name=second.short_title,
            labels={code: territories[code].short_code for code in codes if code in territories},
        ),
        "link": _url("analytics:correlation", series=[first.key, second.key], year=year),
        "link_label": _("Связи показателей"),
    }


# --- Регион, термин, методика -----------------------------------------------------------------


def _region(reading: Reading) -> dict[str, Any] | None:
    from apps.catalog.brief import region_brief

    territory = _territories([reading.codes[0]]).get(reading.codes[0])
    if territory is None:
        return None
    brief = region_brief(territory, metrics=4, now=0)
    return {
        "template": "search/answers/_region.html",
        "brief": brief,
        "link": brief["href"],
        "link_label": _("Паспорт региона"),
    }


def _define(reading: Reading) -> dict[str, Any] | None:
    from apps.content.models import GlossaryTerm

    term = GlossaryTerm.objects.filter(slug=reading.target, is_published=True).first()
    if term is None:
        return None
    return {
        "template": "search/answers/_define.html",
        "term": term,
        "link": reverse("content:glossary") + f"#{term.anchor}",
        "link_label": _("В глоссарии"),
    }


def _method(reading: Reading) -> dict[str, Any] | None:
    from apps.content.models import MethodologySection

    section = MethodologySection.objects.filter(code=reading.target, is_published=True).first()
    if section is None:
        return None
    return {
        "template": "search/answers/_method.html",
        "section": section,
        "link": reverse("content:methodology") + f"#{section.anchor}",
        "link_label": _("Раздел методики"),
    }
