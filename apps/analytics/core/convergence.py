"""
Сигма- и бета-конвергенция регионов, скорость сближения и период полусокращения разрыва.

Сигма — разброс логарифмов по годам, бета — регрессия темпа роста на исходный уровень.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from django.utils.translation import gettext

from .regression import Fit, dummy_matrix, ols

# Минимальное число территорий для оценивания регрессии сближения.
MIN_TERRITORIES = 20

# Наименьшее число лет между начальным и конечным годом.
MIN_SPAN_YEARS = 5

# Минимальное число лет для оценки тенденции разброса.
MIN_SIGMA_YEARS = 3

# Период полусокращения дольше этого срока считается бесконечным.
MAX_MEANINGFUL_HALF_LIFE = 200.0


@dataclass(frozen=True, slots=True)
class SigmaPoint:
    """Разброс значений между территориями в одном году."""

    year: int
    count: int
    mean: float
    std_log: float
    coef_variation: float | None


@dataclass(frozen=True, slots=True)
class BetaResult:
    """Результат оценивания бета-конвергенции; ``conditional`` — с переменными округов."""

    fit: Fit
    span: int
    first_year: int
    last_year: int
    conditional: bool
    speed: float | None
    half_life: float | None
    points: list[dict[str, Any]]

    @property
    def beta(self) -> float:
        """Коэффициент при исходном уровне."""
        return self.fit.slope.value

    @property
    def is_converging(self) -> bool:
        """Признак статистически подтверждённого сближения."""
        return self.beta < 0 and self.fit.slope.is_significant


def sigma_series(panel: dict[int, list[float | None]]) -> list[SigmaPoint]:
    """Рассчитать разброс логарифмов по годам; годы с неположительными значениями пропускаются."""
    points: list[SigmaPoint] = []
    for year in sorted(panel):
        array = np.asarray(
            [float(value) for value in panel[year] if value is not None and float(value) > 0],
            dtype=float,
        )
        if array.size < MIN_TERRITORIES:
            continue

        logs = np.log(array)
        mean = float(np.mean(array))
        deviation = float(np.std(array, ddof=1))
        points.append(
            SigmaPoint(
                year=year,
                count=int(array.size),
                mean=mean,
                std_log=float(np.std(logs, ddof=1)),
                coef_variation=(deviation / mean) if mean > 0 else None,
            )
        )
    return points


def sigma_trend(points: list[SigmaPoint]) -> dict[str, Any]:
    """Оценить тенденцию разброса линейным трендом по всем годам."""
    if len(points) < MIN_SIGMA_YEARS:
        return {"available": False}

    years = np.asarray([point.year for point in points], dtype=float)
    deviations = np.asarray([point.std_log for point in points], dtype=float)
    fit = ols(deviations, years.reshape(-1, 1), ["Год"])

    first, last = points[0], points[-1]
    change = last.std_log - first.std_log
    return {
        "available": True,
        "fit": fit,
        "first": first,
        "last": last,
        "change": change,
        "relative_change": change / first.std_log if first.std_log else None,
        "converging": bool(fit is not None and fit.slope.value < 0 and fit.slope.is_significant),
        "diverging": bool(fit is not None and fit.slope.value > 0 and fit.slope.is_significant),
    }


def beta_convergence(
    observations: list[dict[str, Any]],
    *,
    first_year: int,
    last_year: int,
    conditional: bool = False,
    robust: bool = True,
) -> BetaResult | None:
    """
    Оценить бета-конвергенцию по темпам роста территорий.

    Территории без значения в одном из двух годов исключаются, а не достраиваются.
    """
    span = last_year - first_year
    if span < MIN_SPAN_YEARS:
        return None

    usable = [
        item
        for item in observations
        if item.get("start") is not None
        and item.get("end") is not None
        and float(item["start"]) > 0
        and float(item["end"]) > 0
    ]
    if len(usable) < MIN_TERRITORIES:
        return None

    initial = np.asarray([math.log(float(item["start"])) for item in usable], dtype=float)
    growth = np.asarray(
        [(math.log(float(item["end"])) - math.log(float(item["start"]))) / span for item in usable],
        dtype=float,
    )

    names = [gettext("Логарифм исходного уровня")]
    design = initial.reshape(-1, 1)
    if conditional:
        dummies, dummy_names = dummy_matrix([str(item.get("district") or "") for item in usable])
        if dummies.shape[1] > 0:
            design = np.column_stack([design, dummies])
            names = [*names, *dummy_names]

    fit = ols(growth, design, names, robust=robust)
    if fit is None:
        return None

    speed, half_life = convergence_speed(fit.slope.value, span)
    points = [
        {
            "code": item["code"],
            "name": item.get("name", item["code"]),
            "district": item.get("district", ""),
            "x": float(initial[index]),
            "y": float(growth[index]),
            "start": float(item["start"]),
            "end": float(item["end"]),
        }
        for index, item in enumerate(usable)
    ]

    return BetaResult(
        fit=fit,
        span=span,
        first_year=first_year,
        last_year=last_year,
        conditional=conditional,
        speed=speed,
        half_life=half_life,
        points=points,
    )


def convergence_speed(beta: float, span: int) -> tuple[float | None, float | None]:
    """
    Вычислить скорость сближения ``λ = −ln(1 + βT) / T`` и период полусокращения ``ln 2 / λ``.

    При положительном β обе величины не определены.
    """
    if beta >= 0 or span <= 0:
        return None, None

    factor = 1 + beta * span
    if factor <= 0:
        return None, None

    speed = -math.log(factor) / span
    if speed <= 0:
        return None, None

    half_life = math.log(2) / speed
    return speed, (half_life if half_life <= MAX_MEANINGFUL_HALF_LIFE else None)
