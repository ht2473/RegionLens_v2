"""
Приведение показателей к шкале 0–100: min-max, z-оценка, робастная, ранговая, логарифмическая.

Сто — лучшее положение с учётом направленности: у дестимуляторов шкала разворачивается.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from django.utils.translation import gettext_lazy as _

# Доступные способы приведения и их подписи.
METHODS: dict[str, Any] = {
    "minmax": _("линейное растяжение размаха"),
    "zscore": _("стандартизация (z-оценка)"),
    "robust": _("робастная (медиана и квартили)"),
    "rank": _("ранговая"),
    "log": _("логарифмическая"),
}
DEFAULT_METHOD = "minmax"

# Границы шкалы показа, общей для всех способов.
SCALE_MIN = 0.0
SCALE_MAX = 100.0
SCALE_MIDDLE = 50.0

# Число стандартных отклонений в шкале; значения за ним усекаются, их число сообщается.
SCALE_DEVIATIONS = 3.0

# Минимальное число территорий, при котором приведение к шкале осмысленно.
MIN_VALUES = 3

# Направленность показателя.
POLARITY_POSITIVE = "positive"
POLARITY_NEGATIVE = "negative"


@dataclass(frozen=True, slots=True)
class Normalized:
    """Результат приведения одного показателя к шкале; без исходного значения — ``None``."""

    method: str
    values: list[float | None]
    covered: int
    clipped: int = 0
    note: str = ""

    @property
    def is_available(self) -> bool:
        """Признак того, что показатель удалось привести к шкале."""
        return self.covered >= MIN_VALUES


def normalize(
    values: list[float | None],
    *,
    method: str = DEFAULT_METHOD,
    polarity: str = POLARITY_POSITIVE,
) -> Normalized:
    """Привести значения показателя к шкале от нуля до ста; для ``negative`` — развернуть."""
    array = np.asarray([np.nan if value is None else float(value) for value in values], dtype=float)
    usable = np.isfinite(array)
    covered = int(np.count_nonzero(usable))
    if covered < MIN_VALUES:
        return Normalized(method=method, values=[None] * len(values), covered=covered)

    if method == "zscore":
        scaled, clipped, note = _standard(array[usable], robust=False)
    elif method == "robust":
        scaled, clipped, note = _standard(array[usable], robust=True)
    elif method == "rank":
        scaled, clipped, note = _rank(array[usable])
    elif method == "log":
        scaled, clipped, note = _logarithmic(array[usable])
    else:
        method = "minmax"
        scaled, clipped, note = _min_max(array[usable])

    if polarity == POLARITY_NEGATIVE:
        scaled = SCALE_MAX - scaled

    result: list[float | None] = [None] * len(values)
    position = 0
    for index in range(len(values)):
        if usable[index]:
            result[index] = float(scaled[position])
            position += 1

    return Normalized(method=method, values=result, covered=covered, clipped=clipped, note=note)


# ---------------------------------------------------------------------------------------
# Способы приведения
# ---------------------------------------------------------------------------------------


def _min_max(values: np.ndarray) -> tuple[np.ndarray, int, str]:
    """Линейно растянуть размах значений на шкалу показа."""
    low, high = float(np.min(values)), float(np.max(values))
    if math.isclose(low, high):
        return np.full_like(values, SCALE_MIDDLE), 0, "все территории имеют одинаковое значение"
    return (values - low) / (high - low) * SCALE_MAX, 0, ""


def _standard(values: np.ndarray, *, robust: bool) -> tuple[np.ndarray, int, str]:
    """Отсчитать значения от среднего (или медианы) в долях отклонения (или размаха квартилей)."""
    if robust:
        centre = float(np.median(values))
        spread = float(np.quantile(values, 0.75) - np.quantile(values, 0.25))
        # Межквартильный размах в масштабе стандартного отклонения нормального распределения.
        spread = spread / 1.349
    else:
        centre = float(np.mean(values))
        spread = float(np.std(values, ddof=1))

    if spread <= 0:
        return np.full_like(values, SCALE_MIDDLE), 0, "разброс значений равен нулю"

    scores = (values - centre) / spread
    clipped = int(np.count_nonzero(np.abs(scores) > SCALE_DEVIATIONS))
    scores = np.clip(scores, -SCALE_DEVIATIONS, SCALE_DEVIATIONS)

    note = ""
    if clipped:
        note = f"значений за пределами трёх стандартных отклонений: {clipped}"
    return SCALE_MIDDLE + scores / SCALE_DEVIATIONS * SCALE_MIDDLE, clipped, note


def _rank(values: np.ndarray) -> tuple[np.ndarray, int, str]:
    """Заменить значения их положением в упорядоченном ряду; равные — средним рангом."""
    order = np.argsort(values, kind="stable")
    ranks = np.empty_like(order, dtype=float)
    ranks[order] = np.arange(values.size, dtype=float)

    unique, inverse = np.unique(values, return_inverse=True)
    if unique.size < values.size:
        for index in range(unique.size):
            mask = inverse == index
            ranks[mask] = float(np.mean(ranks[mask]))

    if values.size == 1:
        return np.full_like(values, SCALE_MIDDLE), 0, ""
    return ranks / (values.size - 1) * SCALE_MAX, 0, "учитывается только порядок территорий"


def _logarithmic(values: np.ndarray) -> tuple[np.ndarray, int, str]:
    """Растянуть размах логарифмов значений: применимо к положительным величинам."""
    if np.any(values <= 0):
        return (
            np.full_like(values, SCALE_MIDDLE),
            0,
            "показатель принимает нулевые или отрицательные значения",
        )
    return _min_max(np.log(values))
