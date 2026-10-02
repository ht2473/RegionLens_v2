"""
Свёртка нормированных показателей в оценку 0–100: аддитивная, геометрическая, TOPSIS,
расстояние до эталона; проверка устойчивости рейтинга.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from django.utils.translation import gettext_lazy as _

from .normalization import SCALE_MAX

# Доступные способы свёртки и их подписи.
METHODS: dict[str, Any] = {
    "additive": _("аддитивная (среднее арифметическое)"),
    "geometric": _("геометрическая (среднее геометрическое)"),
    "topsis": _("TOPSIS (близость к эталону)"),
    "distance": _("расстояние до эталона"),
}
DEFAULT_METHOD = "additive"

# Замена нуля при геометрической свёртке: иначе один показатель обнулит индекс.
GEOMETRIC_FLOOR = 1.0

# Наименьшая доля показателей с данными, при которой территория остаётся в рейтинге.
MIN_COVERAGE = 0.6


@dataclass(frozen=True, slots=True)
class Score:
    """Итоговая оценка одной территории."""

    code: str
    name: str
    value: float
    covered: int
    total: int
    contributions: list[float | None]

    @property
    def coverage(self) -> float:
        """Доля показателей, по которым есть данные."""
        return self.covered / self.total if self.total else 0.0

    @property
    def is_partial(self) -> bool:
        """Признак того, что оценка получена не по всем показателям."""
        return self.covered < self.total


def aggregate(
    matrix: np.ndarray,
    weights: list[float],
    territories: list[dict[str, str]],
    *,
    method: str = DEFAULT_METHOD,
) -> list[Score]:
    """
    Свернуть нормированные значения (пропуск — ``nan``) в итоговые оценки территорий.

    Веса недостающих показателей перераспределяются, чтобы пропуск не штрафовал как плохое значение.
    """
    if matrix.size == 0 or not weights:
        return []

    weight_array = np.asarray(weights, dtype=float)
    scores: list[Score] = []

    for index, territory in enumerate(territories):
        row = matrix[index]
        present = np.isfinite(row)
        covered = int(np.count_nonzero(present))
        if covered == 0 or covered / row.size < MIN_COVERAGE:
            continue

        row_weights = np.where(present, weight_array, 0.0)
        total_weight = float(np.sum(row_weights))
        if total_weight <= 0:
            continue
        row_weights = row_weights / total_weight
        filled = np.where(present, row, 0.0)

        if method == "geometric":
            value = _geometric(filled, row_weights, present)
        elif method == "topsis":
            value = float("nan")  # рассчитывается ниже по всей матрице сразу
        elif method == "distance":
            value = float("nan")
        else:
            value = float(np.sum(filled * row_weights))

        scores.append(
            Score(
                code=territory["code"],
                name=territory["name"],
                value=value,
                covered=covered,
                total=int(row.size),
                contributions=[
                    float(row[column] * row_weights[column]) if present[column] else None
                    for column in range(row.size)
                ],
            )
        )

    if method == "topsis":
        _fill_topsis(scores, matrix, weight_array, territories)
    elif method == "distance":
        _fill_distance(scores, matrix, weight_array, territories)

    scores.sort(key=lambda score: score.value, reverse=True)
    return scores


def _geometric(values: np.ndarray, weights: np.ndarray, present: np.ndarray) -> float:
    """Рассчитать взвешенное среднее геометрическое; нули заменяются ``GEOMETRIC_FLOOR``."""
    safe = np.where(present, np.maximum(values, GEOMETRIC_FLOOR), 1.0)
    logarithms = np.log(safe) * weights
    return float(np.exp(np.sum(logarithms)))


def _fill_topsis(
    scores: list[Score],
    matrix: np.ndarray,
    weights: np.ndarray,
    territories: list[dict[str, str]],
) -> None:
    """
    Рассчитать оценки TOPSIS: близость к наилучшему сочетанию относительно наихудшего.

    Пропуски заменяются средним по показателю: расстояние определено для полного вектора.
    """
    prepared = _filled_matrix(matrix)
    weighted = prepared * weights

    best = np.max(weighted, axis=0)
    worst = np.min(weighted, axis=0)

    to_best = np.sqrt(np.sum((weighted - best) ** 2, axis=1))
    to_worst = np.sqrt(np.sum((weighted - worst) ** 2, axis=1))
    total = to_best + to_worst

    closeness = np.divide(to_worst, total, out=np.full_like(total, 0.5), where=total > 0)
    _apply(scores, territories, closeness * SCALE_MAX)


def _fill_distance(
    scores: list[Score],
    matrix: np.ndarray,
    weights: np.ndarray,
    territories: list[dict[str, str]],
) -> None:
    """Рассчитать оценки как близость к эталону из наилучших достигнутых значений."""
    prepared = _filled_matrix(matrix)
    reference = np.max(prepared, axis=0)

    distance = np.sqrt(np.sum(weights * (prepared - reference) ** 2, axis=1))
    longest = float(np.max(distance))
    if longest <= 0:
        _apply(scores, territories, np.full(prepared.shape[0], SCALE_MAX))
        return
    _apply(scores, territories, (1 - distance / longest) * SCALE_MAX)


def _filled_matrix(matrix: np.ndarray) -> np.ndarray:
    """Заменить пропуски средним по показателю."""
    prepared = matrix.copy()
    for column in range(prepared.shape[1]):
        values = prepared[:, column]
        present = np.isfinite(values)
        if np.any(present):
            values[~present] = float(np.mean(values[present]))
        else:
            values[:] = 0.0
    return prepared


def _apply(
    scores: list[Score],
    territories: list[dict[str, str]],
    values: np.ndarray,
) -> None:
    """Проставить рассчитанные оценки территориям, оставшимся в рейтинге."""
    by_code = {territory["code"]: values[index] for index, territory in enumerate(territories)}
    for position, score in enumerate(scores):
        scores[position] = Score(
            code=score.code,
            name=score.name,
            value=float(by_code.get(score.code, 0.0)),
            covered=score.covered,
            total=score.total,
            contributions=score.contributions,
        )


def rank_shift(
    baseline: list[Score],
    variant: list[Score],
) -> dict[str, Any]:
    """Сравнить два рейтинга: среднее и наибольшее смещение мест, самые подвижные территории."""
    first = {score.code: position + 1 for position, score in enumerate(baseline)}
    second = {score.code: position + 1 for position, score in enumerate(variant)}
    shared = set(first) & set(second)
    if not shared:
        return {"available": False}

    names = {score.code: score.name for score in baseline}
    shifts: list[dict[str, Any]] = [
        {"code": code, "name": names.get(code, code), "shift": first[code] - second[code]}
        for code in shared
    ]
    magnitudes: list[int] = [abs(int(item["shift"])) for item in shifts]
    shifts.sort(key=lambda item: abs(int(item["shift"])), reverse=True)

    return {
        "available": True,
        "mean_shift": sum(magnitudes) / len(magnitudes),
        "max_shift": max(magnitudes),
        "unchanged": sum(1 for value in magnitudes if value == 0),
        "movers": shifts[:5],
        "count": len(shared),
    }
