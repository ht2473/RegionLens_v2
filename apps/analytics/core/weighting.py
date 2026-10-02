"""Веса показателей интегрального индекса: равные, экспертные, энтропийные, CRITIC, компонентные."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from django.utils.translation import gettext_lazy as _

# Доступные способы назначения весов и их подписи.
METHODS: dict[str, Any] = {
    "equal": _("равные"),
    "expert": _("экспертные"),
    "entropy": _("энтропийные"),
    "critic": _("CRITIC"),
    "pca": _("по нагрузкам главных компонент"),
}
DEFAULT_METHOD = "equal"

# Наименьшее число показателей для способов, опирающихся на связи между ними.
MIN_COLUMNS = 2

# Минимальное число территорий с полными данными для расчёта весов по данным.
MIN_ROWS = 10

# Ожидаемая размерность матрицы значений «территории × показатели».
MATRIX_DIMENSIONS = 2

# Замена нулевой доли в энтропийном расчёте: логарифм нуля не определён.
EPSILON = 1e-12


@dataclass(frozen=True, slots=True)
class Weights:
    """Набор весов показателей вместе со способом их получения."""

    method: str
    values: list[float]
    note: str = ""


def build_weights(
    matrix: np.ndarray,
    *,
    method: str = DEFAULT_METHOD,
    expert: list[float] | None = None,
) -> Weights:
    """
    Рассчитать веса показателей, в сумме дающие единицу.

    Матрица — уже нормированная, иначе больший вес получал бы показатель с большими числами.
    """
    columns = matrix.shape[1] if matrix.ndim == MATRIX_DIMENSIONS else 0
    if columns == 0:
        return Weights(method=method, values=[], note="показатели не выбраны")

    if method == "expert" and expert:
        return _expert(expert, columns)

    complete = matrix[np.all(np.isfinite(matrix), axis=1)] if matrix.size else matrix
    unusable = complete.shape[0] < MIN_ROWS or columns < MIN_COLUMNS

    if method == "entropy" and not unusable:
        return _entropy(complete)
    if method == "critic" and not unusable:
        return _critic(complete)
    if method == "pca" and not unusable:
        return _principal_components(complete)

    note = ""
    if method in ("entropy", "critic", "pca") and unusable:
        note = "данных недостаточно для расчёта весов по структуре набора: веса равные"
    return Weights(method="equal", values=[1 / columns] * columns, note=note)


def _expert(expert: list[float], columns: int) -> Weights:
    """Привести к единичной сумме неотрицательные веса, заданные пользователем."""
    values = [max(0.0, float(weight)) for weight in expert[:columns]]
    values += [0.0] * (columns - len(values))

    total = sum(values)
    if total <= 0:
        return Weights(
            method="equal",
            values=[1 / columns] * columns,
            note="все заданные веса нулевые: применены равные",
        )
    return Weights(method="expert", values=[value / total for value in values])


def _entropy(matrix: np.ndarray) -> Weights:
    """Рассчитать энтропийные веса: больше весит показатель, сильнее различающий территории."""
    shifted = matrix - np.min(matrix, axis=0) + EPSILON
    shares = shifted / np.sum(shifted, axis=0)

    scale = 1 / np.log(matrix.shape[0])
    entropy = -scale * np.sum(shares * np.log(shares + EPSILON), axis=0)
    diversity = 1 - entropy

    total = float(np.sum(diversity))
    if total <= 0:
        return Weights(
            method="equal",
            values=[1 / matrix.shape[1]] * matrix.shape[1],
            note="показатели не различают территории: применены равные веса",
        )
    return Weights(method="entropy", values=[float(value / total) for value in diversity])


def _critic(matrix: np.ndarray) -> Weights:
    """Рассчитать веса CRITIC: отклонение показателя × сумма ``1 − r`` с остальными."""
    deviations = np.std(matrix, axis=0, ddof=1)
    correlations = np.corrcoef(matrix, rowvar=False)
    correlations = np.nan_to_num(correlations, nan=0.0)

    conflict = np.sum(1 - correlations, axis=1)
    information = deviations * conflict

    total = float(np.sum(information))
    if total <= 0:
        return Weights(
            method="equal",
            values=[1 / matrix.shape[1]] * matrix.shape[1],
            note="разброс показателей нулевой: применены равные веса",
        )
    return Weights(method="critic", values=[float(value / total) for value in information])


def _principal_components(matrix: np.ndarray) -> Weights:
    """Рассчитать веса по квадратам нагрузок главных компонент, взвешенным долей дисперсии."""
    standardized = (matrix - np.mean(matrix, axis=0)) / np.where(
        np.std(matrix, axis=0, ddof=1) > 0, np.std(matrix, axis=0, ddof=1), 1.0
    )
    correlations = np.corrcoef(standardized, rowvar=False)
    correlations = np.nan_to_num(correlations, nan=0.0)

    try:
        eigenvalues, eigenvectors = np.linalg.eigh(correlations)
    except np.linalg.LinAlgError:
        return Weights(
            method="equal",
            values=[1 / matrix.shape[1]] * matrix.shape[1],
            note="структуру связей определить не удалось: применены равные веса",
        )

    order = np.argsort(eigenvalues)[::-1]
    eigenvalues = np.clip(eigenvalues[order], 0, None)
    eigenvectors = eigenvectors[:, order]

    # Правило Кайзера: компоненты с собственным числом не меньше единицы.
    keep = eigenvalues >= 1.0
    if not np.any(keep):
        keep = np.zeros_like(eigenvalues, dtype=bool)
        keep[0] = True

    loadings = eigenvectors[:, keep] * np.sqrt(eigenvalues[keep])
    contribution = np.sum(loadings**2 * (eigenvalues[keep] / np.sum(eigenvalues)), axis=1)

    total = float(np.sum(contribution))
    if total <= 0:
        return Weights(
            method="equal",
            values=[1 / matrix.shape[1]] * matrix.shape[1],
            note="нагрузки компонент вырождены: применены равные веса",
        )

    kept = int(np.count_nonzero(keep))
    explained = float(np.sum(eigenvalues[keep]) / np.sum(eigenvalues))
    return Weights(
        method="pca",
        values=[float(value / total) for value in contribution],
        note=f"использовано компонент: {kept}; объяснённая доля разброса: {explained:.0%}",
    )
