"""
Меры связи показателей: Пирсон, Спирмен, Кендалл, частная корреляция, запаздывание.

Коэффициент возвращается с уровнем значимости и числом пар; в матрице — поправка
Бенджамини — Хохберга по всем парам.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

import numpy as np
from django.utils.translation import gettext_lazy as _
from scipy import stats

# Доступные коэффициенты и их подписи.
METHODS: dict[str, Any] = {
    "spearman": _("Спирмена (ранговый)"),
    "pearson": _("Пирсона (линейный)"),
    "kendall": _("Кендалла (порядковый)"),
}
DEFAULT_METHOD = "spearman"

# Минимальное число пар наблюдений, при котором коэффициент вообще рассчитывается.
MIN_PAIRS = 8

# Уровень значимости для проверки гипотезы об отсутствии связи.
SIGNIFICANCE_LEVEL = 0.05

# Границы словесной оценки тесноты связи по модулю коэффициента.
STRENGTH_BOUNDS: tuple[tuple[float, Any], ...] = (
    (0.9, _("очень тесная")),
    (0.7, _("тесная")),
    (0.5, _("заметная")),
    (0.3, _("умеренная")),
    (0.1, _("слабая")),
)

# Наибольший сдвиг в годах при поиске запаздывающей связи.
MAX_LAG = 5


@dataclass(frozen=True, slots=True)
class Correlation:
    """Коэффициент связи двух рядов вместе с оценкой его надёжности."""

    coefficient: float | None
    p_value: float | None
    pairs: int
    method: str = DEFAULT_METHOD
    # Уровень с поправкой Бенджамини — Хохберга; у пары вне матрицы — ``None``.
    p_adjusted: float | None = None

    @property
    def is_available(self) -> bool:
        """Признак того, что коэффициент рассчитан."""
        return self.coefficient is not None

    @property
    def is_significant(self) -> bool:
        """Признак статистической значимости связи; у пары из матрицы — по поправленному уровню."""
        p_value = self.p_adjusted if self.p_adjusted is not None else self.p_value
        return p_value is not None and p_value < SIGNIFICANCE_LEVEL

    @property
    def strength(self) -> Any:
        """Словесная оценка тесноты связи."""
        if self.coefficient is None:
            return ""
        magnitude = abs(self.coefficient)
        for bound, label in STRENGTH_BOUNDS:
            if magnitude >= bound:
                return label
        return _("практически отсутствует")

    @property
    def direction(self) -> str:
        """Направление связи: прямая или обратная."""
        if self.coefficient is None:
            return ""
        return "positive" if self.coefficient > 0 else "negative"


def correlate(
    x: list[float | None],
    y: list[float | None],
    method: str = DEFAULT_METHOD,
) -> Correlation:
    """Рассчитать коэффициент связи двух рядов по полным парам значений."""
    first, second = _aligned(x, y)
    if first.size < MIN_PAIRS:
        return Correlation(coefficient=None, p_value=None, pairs=int(first.size), method=method)

    if np.std(first) == 0 or np.std(second) == 0:
        return Correlation(coefficient=None, p_value=None, pairs=int(first.size), method=method)

    if method == "pearson":
        result = stats.pearsonr(first, second)
    elif method == "kendall":
        result = stats.kendalltau(first, second)
    else:
        method = "spearman"
        result = stats.spearmanr(first, second)

    return Correlation(
        coefficient=float(result[0]),
        p_value=float(result[1]),
        pairs=int(first.size),
        method=method,
    )


def correlation_matrix(
    columns: list[dict[str, Any]],
    method: str = DEFAULT_METHOD,
) -> dict[str, Any]:
    """
    Построить матрицу парных связей с числом пар в каждой ячейке.

    Значения всех столбцов относятся к одним территориям в одном порядке.
    """
    size = len(columns)
    cells: list[list[Correlation]] = []
    for row in range(size):
        line: list[Correlation] = []
        for column in range(size):
            if row == column:
                line.append(Correlation(coefficient=1.0, p_value=0.0, pairs=0, method=method))
            elif column < row:
                line.append(cells[column][row])
            else:
                line.append(correlate(columns[row]["values"], columns[column]["values"], method))
        cells.append(line)

    _adjust_for_multiplicity(cells)

    return {
        "labels": [column["label"] for column in columns],
        "keys": [column["key"] for column in columns],
        "cells": cells,
        "method": method,
        "size": size,
    }


def _adjust_for_multiplicity(cells: list[list[Correlation]]) -> None:
    """
    Поправить уровни значимости матрицы по Бенджамини — Хохбергу над диагональю.

    Бонферрони при 45 парах требовал бы p < 0,0011 и гасил бы почти все умеренные связи.
    """
    size = len(cells)
    places = [
        (row, column)
        for row in range(size)
        for column in range(row + 1, size)
        if cells[row][column].p_value is not None
    ]
    if not places:
        return
    adjusted = stats.false_discovery_control(
        [cells[row][column].p_value for row, column in places], method="bh"
    )
    for (row, column), value in zip(places, adjusted, strict=True):
        cell = replace(cells[row][column], p_adjusted=float(value))
        cells[row][column] = cell
        cells[column][row] = cell


def strongest_pairs(matrix: dict[str, Any], limit: int = 10) -> list[dict[str, Any]]:
    """Отобрать наиболее тесные связи матрицы, значимые после поправки."""
    pairs: list[dict[str, Any]] = []
    labels = matrix["labels"]
    keys = matrix["keys"]

    for row in range(matrix["size"]):
        for column in range(row + 1, matrix["size"]):
            cell: Correlation = matrix["cells"][row][column]
            if not cell.is_available or not cell.is_significant:
                continue
            pairs.append(
                {
                    "first_label": labels[row],
                    "second_label": labels[column],
                    "first_key": keys[row],
                    "second_key": keys[column],
                    "correlation": cell,
                }
            )

    pairs.sort(key=lambda item: abs(item["correlation"].coefficient), reverse=True)
    return pairs[:limit]


def partial_correlation(columns: list[dict[str, Any]]) -> dict[str, Any] | None:
    """
    Рассчитать частную корреляцию через обратную корреляционную матрицу.

    Территории без какого-либо показателя исключаются, чтобы все ячейки опирались
    на одни наблюдения.
    """
    size = len(columns)
    if size < 3:  # noqa: PLR2004 - частная корреляция требует хотя бы одного контролируемого признака
        return None

    stacked = np.asarray(
        [
            [np.nan if value is None else float(value) for value in column["values"]]
            for column in columns
        ],
        dtype=float,
    )
    complete = np.all(np.isfinite(stacked), axis=0)
    stacked = stacked[:, complete]
    if stacked.shape[1] < MIN_PAIRS or np.any(np.std(stacked, axis=1) == 0):
        return None

    correlations = np.corrcoef(stacked)
    try:
        precision = np.linalg.inv(correlations)
    except np.linalg.LinAlgError:
        return None

    diagonal = np.sqrt(np.outer(np.diag(precision), np.diag(precision)))
    partial = -precision / diagonal
    np.fill_diagonal(partial, 1.0)

    labels = [column["label"] for column in columns]
    return {
        "labels": labels,
        "rows": [
            {"label": labels[index], "values": [float(value) for value in row]}
            for index, row in enumerate(partial)
        ],
        "observations": int(stacked.shape[1]),
        "controlled": size - 2,
    }


def lagged_profile(
    x_panel: dict[int, dict[str, float | None]],
    y_panel: dict[int, dict[str, float | None]],
    *,
    target_year: int,
    method: str = DEFAULT_METHOD,
    max_lag: int = MAX_LAG,
) -> list[dict[str, Any]]:
    """
    Сопоставить первый показатель за ранние годы со вторым за целевой год по всем сдвигам.

    Более сильная связь при сдвиге — довод, а не доказательство предшествования.
    """
    target = y_panel.get(target_year, {})
    if not target:
        return []

    codes = sorted(target)
    profile: list[dict[str, Any]] = []

    for lag in range(max_lag + 1):
        source = x_panel.get(target_year - lag, {})
        if not source:
            continue
        result = correlate(
            [source.get(code) for code in codes],
            [target.get(code) for code in codes],
            method,
        )
        profile.append({"lag": lag, "year": target_year - lag, "correlation": result})

    return profile


def scatter_points(
    x: list[float | None],
    y: list[float | None],
    labels: list[str],
    codes: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Собрать точки диаграммы рассеяния, отбросив неполные пары."""
    points: list[dict[str, Any]] = []
    for index, label in enumerate(labels):
        first, second = x[index], y[index]
        if first is None or second is None:
            continue
        points.append(
            {
                "name": label,
                "code": codes[index] if codes else "",
                "x": float(first),
                "y": float(second),
            }
        )
    return points


def _aligned(x: list[float | None], y: list[float | None]) -> tuple[np.ndarray, np.ndarray]:
    """Оставить только пары, в которых присутствуют оба значения."""
    first = np.asarray([np.nan if value is None else float(value) for value in x], dtype=float)
    second = np.asarray([np.nan if value is None else float(value) for value in y], dtype=float)
    if first.size != second.size:
        return np.empty(0), np.empty(0)

    usable = np.isfinite(first) & np.isfinite(second)
    return first[usable], second[usable]
