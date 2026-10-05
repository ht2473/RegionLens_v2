"""
«С чем связан показатель»: связь ряда таблицы с рядами основного набора сайта за общий год —
коэффициент Спирмена по субъектам, уровни значимости с поправкой Бенджамини — Хохберга
на число сравнений. Облако точек пары — в инструменте «Корреляции».
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import urlencode

from django.urls import reverse
from scipy import stats

from apps.analytics.core import correlation
from apps.analytics.selectors import cached_result, region_rows
from apps.catalog.selectors import default_year
from apps.warehouse.queries import FeaturedSeries, featured_series, region_matrix, region_panel

from .series import UserSeries

METHOD = "spearman"
# Редакция содержимого: кэш расчётов не знает о правке кода.
RESULT_REVISION = 1


@dataclass(frozen=True, slots=True)
class Link:
    """Связь ряда таблицы с рядом основного набора."""

    item: FeaturedSeries
    year: int | None
    result: correlation.Correlation
    url: str


def related(series: UserSeries) -> dict[str, Any]:
    """Связи ряда с рядами основного набора: значимые — по силе, остальные — отдельно."""
    panel = region_panel(series.key)
    year = default_year({key: len(values) for key, values in panel.items()})
    if year is None:
        return {"year": None, "significant": [], "other": [], "pairs": 0}
    return cached_result(
        "userdata-related",
        {"series": series.key, "year": year, "revision": RESULT_REVISION},
        lambda: _compute(series, year, panel[year]),
    )


def _compute(series: UserSeries, year: int, values: dict[str, float]) -> dict[str, Any]:
    items = featured_series()
    codes = [row["code"] for row in region_rows()]
    matrix = region_matrix([item.key for item in items], year)
    own = [values.get(code) for code in codes]
    links: list[Link] = []
    for item in items:
        entry = matrix.get(item.key)
        if entry is None:
            continue
        other = [entry["values"].get(code) for code in codes]
        result = correlation.correlate(own, other, METHOD)
        if result.coefficient is None:
            continue
        query = urlencode(
            {"series": [series.key, item.key], "x": series.key, "y": item.key, "year": year},
            doseq=True,
        )
        links.append(
            Link(
                item=item,
                year=entry["year"],
                result=result,
                url=f"{reverse('analytics:correlation')}?{query}",
            )
        )
    if links:
        adjusted = stats.false_discovery_control(
            [link.result.p_value or 1.0 for link in links], method="bh"
        )
        links = [
            replace(link, result=replace(link.result, p_adjusted=float(value)))
            for link, value in zip(links, adjusted, strict=True)
        ]
    links.sort(key=lambda link: -abs(link.result.coefficient or 0))
    return {
        "year": year,
        "pairs": sum(1 for value in own if value is not None),
        "significant": [link for link in links if link.result.is_significant],
        "other": [link for link in links if not link.result.is_significant],
    }
