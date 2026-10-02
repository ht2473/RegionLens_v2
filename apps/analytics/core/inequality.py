"""
Меры межрегионального неравенства, кривая Лоренца и разложение Тейла по группам.

Все меры принимают веса; неприменимая мера возвращает ``None`` с причиной, а не ноль.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy.optimize import minimize_scalar

# Параметр неприятия неравенства Аткинсона по умолчанию — умеренный.
DEFAULT_EPSILON = 0.5

# Минимальное число территорий, при котором меры неравенства осмысленны.
MIN_OBSERVATIONS = 5

# Минимальное число территорий в группе для расчёта внутригруппового индекса.
MIN_GROUP_SIZE = 2

# Наибольшее число точек кривой Лоренца в ответе.
MAX_CURVE_POINTS = 200

# Пояснения к отказу в расчёте меры.
NOTE_NEGATIVE = "показатель принимает отрицательные значения"
NOTE_NON_POSITIVE = "показатель принимает нулевые или отрицательные значения"


@dataclass(frozen=True, slots=True)
class Measure:
    """Значение одной меры неравенства; если она неприменима — ``None`` и причина в ``note``."""

    code: str
    value: float | None
    note: str = ""

    @property
    def is_available(self) -> bool:
        """Признак того, что мера рассчитана."""
        return self.value is not None


@dataclass(frozen=True, slots=True)
class Decomposition:
    """Разложение индекса Тейла на межгрупповую и внутригрупповую составляющие."""

    total: float
    between: float
    within: float
    groups: list[dict[str, Any]] = field(default_factory=list)

    @property
    def between_share(self) -> float:
        """Доля различий между группами в общем неравенстве."""
        return self.between / self.total if self.total else 0.0

    @property
    def within_share(self) -> float:
        """Доля различий внутри групп в общем неравенстве."""
        return self.within / self.total if self.total else 0.0


def prepare(
    values: list[float | None],
    weights: list[float | None] | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Отобрать пары «значение — вес»; территория без любого из них исключается."""
    array = np.asarray([np.nan if value is None else float(value) for value in values], dtype=float)

    if weights is None:
        weight_array = np.ones_like(array)
    else:
        weight_array = np.asarray(
            [np.nan if weight is None else float(weight) for weight in weights], dtype=float
        )

    usable = np.isfinite(array) & np.isfinite(weight_array) & (weight_array > 0)
    return array[usable], weight_array[usable]


# ---------------------------------------------------------------------------------------
# Отдельные меры
# ---------------------------------------------------------------------------------------


def weighted_mean(values: np.ndarray, weights: np.ndarray) -> float:
    """Среднее с учётом весов территорий."""
    return float(np.sum(values * weights) / np.sum(weights))


def coefficient_of_variation(values: np.ndarray, weights: np.ndarray) -> float | None:
    """Коэффициент вариации; при неположительном среднем не определён."""
    mean = weighted_mean(values, weights)
    if mean <= 0:
        return None
    variance = float(np.sum(weights * (values - mean) ** 2) / np.sum(weights))
    return math.sqrt(variance) / mean


def gini(values: np.ndarray, weights: np.ndarray) -> float | None:
    """Коэффициент Джини по накопленным долям упорядоченного ряда; значения неотрицательны."""
    if np.any(values < 0):
        return None

    order = np.argsort(values, kind="stable")
    sorted_values = values[order]
    sorted_weights = weights[order]

    shares = sorted_weights / np.sum(sorted_weights)
    weighted = shares * sorted_values
    total = float(np.sum(weighted))
    if total <= 0:
        return None

    cumulative = np.cumsum(weighted)
    previous = np.concatenate(([0.0], cumulative[:-1]))
    return float(1 - np.sum(shares * (previous + cumulative)) / total)


def theil(values: np.ndarray, weights: np.ndarray) -> float | None:
    """Индекс Тейла: раскладывается на части между группами и внутри них; значения положительны."""
    if np.any(values <= 0):
        return None

    mean = weighted_mean(values, weights)
    if mean <= 0:
        return None

    shares = weights / np.sum(weights)
    ratio = values / mean
    return float(np.sum(shares * ratio * np.log(ratio)))


def atkinson(values: np.ndarray, weights: np.ndarray, epsilon: float = DEFAULT_EPSILON) -> float:
    """Индекс Аткинсона с неприятием неравенства ``epsilon``."""
    shares = weights / np.sum(weights)
    mean = weighted_mean(values, weights)
    ratio = values / mean

    if math.isclose(epsilon, 1.0):
        equivalent = float(np.exp(np.sum(shares * np.log(ratio))))
    else:
        equivalent = float(np.sum(shares * ratio ** (1 - epsilon)) ** (1 / (1 - epsilon)))
    return 1 - equivalent


def decile_ratio(values: np.ndarray) -> float | None:
    """Децильный коэффициент: отношение девятого дециля к первому."""
    if values.size < MIN_OBSERVATIONS:
        return None
    low = float(np.quantile(values, 0.10))
    high = float(np.quantile(values, 0.90))
    if low <= 0:
        return None
    return high / low


def range_ratio(values: np.ndarray) -> float | None:
    """Отношение максимума к минимуму."""
    low = float(np.min(values))
    if low <= 0:
        return None
    return float(np.max(values)) / low


# ---------------------------------------------------------------------------------------
# Кривая Лоренца
# ---------------------------------------------------------------------------------------


def lorenz_curve(values: np.ndarray, weights: np.ndarray) -> list[dict[str, float]]:
    """Построить точки кривой Лоренца: накопленные доли территорий (или населения) и величины."""
    if values.size == 0 or np.any(values < 0):
        return []

    order = np.argsort(values, kind="stable")
    sorted_values = values[order]
    sorted_weights = weights[order]

    total_weight = float(np.sum(sorted_weights))
    total_value = float(np.sum(sorted_weights * sorted_values))
    if total_weight <= 0 or total_value <= 0:
        return []

    population = np.cumsum(sorted_weights) / total_weight
    value_share = np.cumsum(sorted_weights * sorted_values) / total_value

    points = [{"population": 0.0, "value": 0.0}]
    step = max(1, math.ceil(values.size / MAX_CURVE_POINTS))
    for index in range(0, values.size, step):
        points.append({"population": float(population[index]), "value": float(value_share[index])})
    points.append({"population": 1.0, "value": 1.0})
    return points


# ---------------------------------------------------------------------------------------
# Разложение по группам территорий
# ---------------------------------------------------------------------------------------


def theil_decomposition(
    values: np.ndarray,
    weights: np.ndarray,
    groups: list[str],
    labels: dict[str, str] | None = None,
) -> Decomposition | None:
    """
    Разложить индекс Тейла на составляющие «между группами» и «внутри групп».

    ``T = Σ s_g · T_g + Σ s_g · ln(μ_g / μ)``, ``s_g`` — доля группы в сумме, ``μ_g`` — её среднее.
    """
    if values.size == 0 or np.any(values <= 0) or len(groups) != values.size:
        return None

    group_array = np.asarray(groups, dtype=object)
    total_weight = float(np.sum(weights))
    mean = weighted_mean(values, weights)
    total = theil(values, weights)
    if mean <= 0 or total is None:
        return None

    between = 0.0
    within = 0.0
    details: list[dict[str, Any]] = []

    for code in sorted({str(code) for code in group_array if code}):
        mask = group_array == code
        group_values = values[mask]
        if group_values.size == 0:
            continue

        group_weights = weights[mask]
        group_mean = weighted_mean(group_values, group_weights)
        share = float(np.sum(group_weights * group_values) / (total_weight * mean))

        inner = theil(group_values, group_weights) if group_values.size >= MIN_GROUP_SIZE else 0.0
        inner = inner if inner is not None else 0.0

        between += share * math.log(group_mean / mean)
        within += share * inner

        details.append(
            {
                "code": code,
                "name": (labels or {}).get(code, code),
                "size": int(group_values.size),
                "mean": group_mean,
                "share": share,
                "theil": inner,
                "ratio_to_mean": group_mean / mean,
                "contribution": share * inner,
            }
        )

    details.sort(key=lambda item: item["mean"], reverse=True)
    return Decomposition(total=total, between=between, within=within, groups=details)


# ---------------------------------------------------------------------------------------
# Границы мер при скрытых значениях
# ---------------------------------------------------------------------------------------

# Меры, для которых строятся границы: все три квазивыпуклы и выпуклы по Шуру.
BOUNDED_MEASURES = ("gini", "theil", "cv")

# Точность поиска нижней границы по общему значению скрытых.
BOUNDS_TOLERANCE = 1e-9


@dataclass(frozen=True, slots=True)
class Bounds:
    """Границы меры при скрытых значениях и оценка по одним наблюдаемым."""

    code: str
    observed: float
    low: float
    high: float

    @property
    def width(self) -> float:
        """Ширина интервала относительно оценки по наблюдаемым."""
        return (self.high - self.low) / self.observed if self.observed else 0.0


def _unweighted(code: str, values: np.ndarray) -> float | None:
    """Мера по значениям без весов."""
    weights = np.ones_like(values)
    if code == "gini":
        return gini(values, weights)
    if code == "theil":
        return theil(values, weights)
    return coefficient_of_variation(values, weights)


def hidden_bounds(values: list[float], hidden: int) -> dict[str, Bounds]:
    """
    Границы мер неравенства, если ``hidden`` скрытых значений лежат в наблюдаемом размахе.

    Допущение Манского об ограниченном носителе: скрытое значение не выходит за минимум
    и максимум наблюдаемых. Меры квазивыпуклы, поэтому наибольшее значение достигается
    в вершине — часть скрытых в минимуме, остальные в максимуме; выпуклы по Шуру, поэтому
    наименьшее — при равных скрытых значениях. Территории равнозначны.
    """
    observed = np.asarray(values, dtype=float)
    if hidden <= 0 or observed.size < MIN_OBSERVATIONS:
        return {}
    low_end, high_end = float(np.min(observed)), float(np.max(observed))

    result: dict[str, Bounds] = {}
    for code in BOUNDED_MEASURES:
        point = _unweighted(code, observed)
        if point is None:
            continue

        corners = [
            _unweighted(
                code,
                np.concatenate(
                    (observed, np.full(at_min, low_end), np.full(hidden - at_min, high_end))
                ),
            )
            for at_min in range(hidden + 1)
        ]
        if any(corner is None for corner in corners):
            continue

        def filled(level: float, measure: str = code) -> float:
            value = _unweighted(measure, np.concatenate((observed, np.full(hidden, level))))
            return math.inf if value is None else value

        if math.isclose(low_end, high_end):
            low = filled(low_end)
        else:
            search = minimize_scalar(
                filled,
                bounds=(low_end, high_end),
                method="bounded",
                options={"xatol": BOUNDS_TOLERANCE * max(1.0, abs(high_end))},
            )
            low = min(float(search.fun), filled(low_end), filled(high_end))

        result[code] = Bounds(
            code=code,
            observed=point,
            low=low,
            high=max(float(corner) for corner in corners if corner is not None),
        )
    return result


# ---------------------------------------------------------------------------------------
# Сводный расчёт
# ---------------------------------------------------------------------------------------


def measure_set(
    values: list[float | None],
    weights: list[float | None] | None = None,
    *,
    epsilon: float = DEFAULT_EPSILON,
) -> dict[str, Any]:
    """Рассчитать набор мер неравенства по одному срезу с пометками о применимости."""
    array, weight_array = prepare(values, weights)
    if array.size < MIN_OBSERVATIONS:
        return {"available": False, "count": int(array.size), "measures": []}

    negative = bool(np.any(array < 0))
    non_positive = bool(np.any(array <= 0))

    measures = [
        Measure("cv", coefficient_of_variation(array, weight_array)),
        Measure("gini", gini(array, weight_array), note=NOTE_NEGATIVE if negative else ""),
        Measure(
            "theil",
            theil(array, weight_array),
            note=NOTE_NON_POSITIVE if non_positive else "",
        ),
        Measure(
            "atkinson",
            None if non_positive else atkinson(array, weight_array, epsilon),
            note=NOTE_NON_POSITIVE if non_positive else "",
        ),
        Measure("decile", decile_ratio(array)),
        Measure("range", range_ratio(array)),
    ]

    return {
        "available": True,
        "count": int(array.size),
        "mean": weighted_mean(array, weight_array),
        "median": float(np.median(array)),
        "min": float(np.min(array)),
        "max": float(np.max(array)),
        "epsilon": epsilon,
        "measures": measures,
        "by_code": {measure.code: measure for measure in measures},
        "lorenz": lorenz_curve(array, weight_array),
    }
