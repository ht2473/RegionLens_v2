"""
Изменение денежных рядов в реальном выражении — по индексам Росстата и индексу цен.

Способ пересчёта задан у ряда основного набора (поле ``real``); без него ряд не пересчитывается.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping

from apps.warehouse.queries import (
    REAL_BY_INDEX,
    REAL_BY_PRICES,
    REAL_BY_VOLUME,
    FeaturedSeries,
    RealBasis,
    series_values,
)

# «ряд → территория → год → значение»
Values = Mapping[str, Mapping[str, Mapping[int, float]]]


def real_ratio(
    basis: RealBasis,
    nominal_ratio: float,
    *,
    index: Mapping[int, float],
    total: Mapping[int, float],
    start: int,
    end: int,
) -> float | None:
    """
    Отношение конечного значения к начальному в неизменных ценах; ``None`` — не хватает лет.

    Индексы — в процентах к прошлому году и перемножаются цепочкой за годы после ``start``.
    Индекс физического объёма относится к итогу, поэтому душевой ряд поправляется
    на изменение численности: ``nominal_ratio / total_ratio`` — её обратное отношение.
    """
    years = range(start + 1, end + 1)
    if not years or any(index.get(year) is None or index[year] <= 0 for year in years):
        return None
    chain = math.prod(index[year] / 100 for year in years)

    if basis.method == REAL_BY_INDEX:
        return chain
    if basis.method == REAL_BY_PRICES:
        return nominal_ratio / chain
    if basis.method == REAL_BY_VOLUME:
        before, after = total.get(start), total.get(end)
        if before is None or after is None or before <= 0 or after <= 0:
            return None
        return nominal_ratio * chain / (after / before)
    return None


def deflators(items: Iterable[FeaturedSeries], codes: list[str]) -> Values:
    """Значения индексов и итогов, нужных для пересчёта рядов, по территориям — одним запросом."""
    keys = sorted(
        {
            key
            for item in items
            if item.real is not None
            for key in (item.real.index, item.real.total)
            if key
        }
    )
    return series_values(keys, codes) if keys else {}


def real_between(
    item: FeaturedSeries,
    values: Values,
    code: str,
    *,
    start: int,
    end: int,
    nominal_ratio: float,
) -> float | None:
    """Реальное отношение значений ряда ``item`` территории ``code`` между двумя годами."""
    basis = item.real
    if basis is None or nominal_ratio <= 0:
        return None
    index = values.get(basis.index, {}).get(code, {})
    total = values.get(basis.total, {}).get(code, {}) if basis.total else {}
    return real_ratio(basis, nominal_ratio, index=index, total=total, start=start, end=end)
