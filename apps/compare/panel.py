"""
Динамика рабочей поверхности: линии ряда, профиль строками точек и таблица различий территорий.

У графика два основания — значения и приведение к базовому году. Разрывы отмечены
на графике, а отрезок после последнего можно показать отдельно (параметр адреса).
"""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest
from django.utils.translation import gettext_lazy as _

from apps.catalog.constants import BreakKind
from apps.catalog.indicator import describe_series
from apps.catalog.models import Series, SeriesBreak, Territory
from apps.catalog.selectors import default_territories
from apps.core import chart_layout as layout
from apps.core.charts import timeline_option
from apps.surface.findings import timeline_finding
from apps.surface.state import SurfaceState, build_query
from apps.warehouse.queries import (
    COUNTRY_CODE,
    featured_series,
    latest_values_matrix,
    series_timeline_multi,
)

# Число ключевых показателей в профиле и таблице различий.
PROFILE_LIMIT = 9

# Основания построения графика динамики.
BASIS_VALUE = "value"
BASIS_INDEX = "index"
BASES: dict[str, Any] = {
    BASIS_VALUE: _("значения"),
    BASIS_INDEX: _("к базовому году"),
}

# Уровень, принимаемый за базовый год.
INDEX_BASE = 100.0

# Показанный отрезок ряда: весь ряд или часть после последнего разрыва сопоставимости.
SPAN_ALL = "all"
SPAN_COMPARABLE = "comparable"

# Сколько разрывов перечисляется в предупреждении; на графике отмечены все.
BREAKS_SHOWN = 6

# Наименьшая длина сопоставимого отрезка в годах.
MIN_COMPARABLE_YEARS = 2


def build(request: HttpRequest, state: SurfaceState) -> dict[str, Any]:
    """Собрать динамику, профиль и таблицу различий."""
    basis = request.GET.get("basis", BASIS_VALUE)
    if basis not in BASES:
        basis = BASIS_VALUE

    span = request.GET.get("span", SPAN_ALL)
    if span != SPAN_COMPARABLE:
        span = SPAN_ALL

    territories = _territories(request, state)
    context: dict[str, Any] = {
        "bases": BASES,
        "basis": basis,
        "span": span,
        "compared": territories,
        # Подставленный набор — не выбор пользователя; об этом сказано на холсте.
        "compared_by_default": not state.territories and bool(territories),
        "view_summary": [
            BASES[basis],
            *([_("сопоставимый отрезок")] if span == SPAN_COMPARABLE else []),
        ],
    }
    if not territories:
        return context

    if state.series is not None:
        context.update(_timeline_context(state, territories, basis, span))
        context.update(_span_links(request, state, basis))
    context.update(_profile_context(territories))
    return context


def _territories(request: HttpRequest, state: SurfaceState) -> list[Territory]:
    """Сравниваемые территории; при пустом выборе — набор по умолчанию, свой регион первым."""
    if state.territories:
        return state.territories
    from apps.accounts.region import my_region

    region = my_region(request)
    return default_territories(preferred=region.code if region is not None else "")


def _profile_context(territories: list[Territory]) -> dict[str, Any]:
    """
    Собрать профиль территорий: строка на показатель, точка территории — её положение среди
    регионов с учётом направленности (слева хуже всех, справа лучше всех). Цвет точки —
    цвет линии территории на графике динамики.
    """
    items = list(featured_series())[:PROFILE_LIMIT]
    keys = [item.key for item in items]
    codes = [territory.code for territory in territories]
    matrix = latest_values_matrix(keys, [*codes, COUNTRY_CODE])

    rows: list[dict[str, Any]] = []
    dots: list[dict[str, Any]] = []

    for item in items:
        country = matrix.get((item.key, COUNTRY_CODE))
        cells = []
        placed = []
        for index, territory in enumerate(territories):
            cell = matrix.get((item.key, territory.code))
            position = _position(cell, item.polarity)
            cells.append({"territory": territory, "cell": cell, "position": position})
            if position is not None:
                placed.append(
                    {
                        "code": territory.code,
                        "name": territory.name,
                        # Строкой: дробь шаблон записал бы с запятой.
                        "at": f"{position / 100:.3f}",
                        "colour": layout.palette_colour(index),
                        "better": round(position),
                    }
                )

        rows.append({"series": item, "cells": cells, "country": country})
        if placed:
            dots.append({"series": item, "dots": placed})

    return {
        "profile_rows": rows,
        "profile_dots": dots,
        "profile_legend": [
            {"code": territory.code, "name": territory.name, "colour": layout.palette_colour(index)}
            for index, territory in enumerate(territories)
        ],
    }


def _position(cell: dict[str, Any] | None, polarity: str) -> float | None:
    """
    Перевести позицию в рейтинге в шкалу «ноль — хуже всех, сто — лучше всех».

    Ряды без направленности не участвуют: направление их шкалы неизвестно.
    """
    if cell is None or cell.get("percentile") is None or polarity not in ("positive", "negative"):
        return None

    percentile = cell["percentile"]
    favourable = percentile if polarity == "positive" else 1 - percentile
    return favourable * 100


def _timeline_context(
    state: SurfaceState, territories: list[Territory], basis: str, span: str
) -> dict[str, Any]:
    """Построить график динамики выбранного ряда по сравниваемым территориям."""
    series = state.series
    if series is None:
        return {"timeline_option": None}

    codes = [territory.code for territory in territories]
    timelines = series_timeline_multi(series.key, [*codes, COUNTRY_CODE])
    years = sorted({point["year"] for points in timelines.values() for point in points})
    if not years:
        return {"timeline_option": None}

    breaks = _breaks(series, codes, years)
    comparable_from = _comparable_from(breaks, years)
    trimmed = span == SPAN_COMPARABLE and comparable_from is not None
    if trimmed and comparable_from is not None:
        years = [year for year in years if year >= comparable_from]

    lines = []
    for territory in territories:
        values = {point["year"]: point["value"] for point in timelines.get(territory.code, [])}
        lines.append(
            {
                "name": territory.name,
                "values": [values.get(year) for year in years],
                # Код — для связи линии со строкой таблицы.
                "code": territory.code,
            }
        )

    country = {point["year"]: point["value"] for point in timelines.get(COUNTRY_CODE, [])}
    country_line: dict[str, Any] = {
        "name": str(_("Российская Федерация")),
        "values": [country.get(year) for year in years],
        "country": True,
    }
    lines.append(country_line)

    # Единица — из описания ряда: ``series.unit`` врёт у 642 рядов.
    item = describe_series(series)
    unit = item.unit_label
    # Вывод — по значениям, до приведения к базовому году.
    finding = timeline_finding(years, lines, item)
    base_year = _base_year(years, lines) if basis == BASIS_INDEX else None
    if base_year is not None:
        lines = [_rebased(line, years, base_year) for line in lines]
        unit = str(_("%(year)s = 100") % {"year": base_year})

    year_mark = state.year if state.year in years else None

    # Разрыв в первом показанном году ничего не отделяет и отметки не получает.
    marks = [
        {"year": item["year"], "label": item["kind"]}
        for item in breaks
        if item["year"] in years and item["year"] > years[0]
    ]

    return {
        "timeline_option": timeline_option(
            years, lines, unit=unit, breaks=marks, year_mark=year_mark
        ),
        "timeline_years": years,
        "finding": finding,
        "timeline_base_year": base_year,
        # Приведение просили, но общего базового года нет.
        "timeline_base_missing": basis == BASIS_INDEX and base_year is None,
        "timeline_breaks": breaks,
        # Последние разрывы: они определяют начало сопоставимого отрезка.
        "timeline_breaks_shown": breaks[-BREAKS_SHOWN:],
        "timeline_breaks_hidden": max(len(breaks) - BREAKS_SHOWN, 0),
        "comparable_from": comparable_from,
        "timeline_trimmed": trimmed,
    }


def _breaks(series: Series, codes: list[str], years: list[int]) -> list[dict[str, Any]]:
    """
    Собрать разрывы, относящиеся к графику: всего ряда и показанных территорий, в его годах.
    """
    if not years:
        return []
    if getattr(series, "is_user", False):
        # Смена методики, отмеченная в описании показателя таблицы: для всех территорий.
        label = str(BreakKind.METHODOLOGY.label)
        return [
            {"year": item["year"], "kind": label, "description": item["note"], "territory": ""}
            for item in getattr(series, "breaks", [])
            if years[0] <= item["year"] <= years[-1]
        ]

    found = (
        SeriesBreak.objects.filter(series=series, year__gte=years[0], year__lte=years[-1])
        .select_related("territory", "note")
        .order_by("year")
    )
    chosen = set(codes)
    return [
        {
            "year": item.year,
            "kind": str(item.get_kind_display()),
            "description": item.description,
            "territory": item.territory.name if item.territory else "",
        }
        for item in found
        if item.territory_id is None or (item.territory and item.territory.code in chosen)
    ]


def _comparable_from(breaks: list[dict[str, Any]], years: list[int]) -> int | None:
    """
    Определить начало сопоставимого отрезка — год последнего разрыва.

    Отрезок короче двух лет или совпадающий со всем рядом не предлагается.
    """
    if not breaks or not years:
        return None
    start = max(item["year"] for item in breaks)
    remaining = [year for year in years if year >= start]
    if len(remaining) < MIN_COMPARABLE_YEARS or start <= years[0]:
        return None
    return start


def _span_links(request: HttpRequest, state: SurfaceState, basis: str) -> dict[str, Any]:
    """
    Собрать ссылки «показать только сопоставимый отрезок» и «показать весь ряд».

    Адрес — от текущего пути: описание представления лежит в ``surface``, а оно зависит отсюда.
    """

    def link(span: str) -> str:
        query = build_query([*state.query, ("basis", basis), ("span", span)])
        return f"{request.path}?{query}" if query else request.path

    return {
        "span_comparable_url": link(SPAN_COMPARABLE),
        # Пустое значение убирает параметр из адреса.
        "span_all_url": link(""),
    }


def _base_year(years: list[int], lines: list[dict[str, Any]]) -> int | None:
    """Найти самый ранний год, в котором значение есть у всех показанных линий."""
    for index, year in enumerate(years):
        if all(line["values"][index] not in (None, 0) for line in lines):
            return year
    return None


def _rebased(line: dict[str, Any], years: list[int], base_year: int) -> dict[str, Any]:
    """Привести линию к базовому году, принятому за сто."""
    base = line["values"][years.index(base_year)]
    return {
        **line,
        "values": [
            round(value / base * INDEX_BASE, 1) if value is not None else None
            for value in line["values"]
        ],
    }
