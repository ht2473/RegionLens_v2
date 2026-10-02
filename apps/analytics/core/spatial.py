"""
Пространственная автокорреляция: глобальный и локальные индексы Морана.

Соседство — из матрицы справочника; значимость — перестановками, без допущения нормальности.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from django.utils.translation import gettext_lazy as _

# Способы определения соседства.
SCHEMES: dict[str, Any] = {
    "land": _("общая сухопутная граница"),
    "all": _("граница, мост или морская связь"),
    "knn": _("пять ближайших по расстоянию"),
}
DEFAULT_SCHEME = "all"

# Число ближайших территорий в схеме соседства по расстоянию.
NEAREST_COUNT = 5

# Число перестановок: наименьший достижимый уровень значимости — ровно 0,001.
PERMUTATIONS = 999

# Постоянное зерно перестановок: уровень значимости не меняется от открытия к открытию.
RANDOM_SEED = 20260321

# Уровень значимости для отнесения территории к пространственному кластеру.
SIGNIFICANCE_LEVEL = 0.05

# Минимальное число территорий со связями, при котором расчёт имеет смысл.
MIN_TERRITORIES = 20

# Обозначения типов пространственного соседства.
QUADRANT_HIGH_HIGH = "HH"
QUADRANT_LOW_LOW = "LL"
QUADRANT_HIGH_LOW = "HL"
QUADRANT_LOW_HIGH = "LH"

QUADRANT_LABELS: dict[str, Any] = {
    QUADRANT_HIGH_HIGH: _("высокие среди высоких"),
    QUADRANT_LOW_LOW: _("низкие среди низких"),
    QUADRANT_HIGH_LOW: _("высокий среди низких"),
    QUADRANT_LOW_HIGH: _("низкий среди высоких"),
}


@dataclass(frozen=True, slots=True)
class GlobalMoran:
    """Глобальный индекс пространственной автокорреляции."""

    value: float
    expected: float
    z_score: float
    p_value: float
    permutations: int
    observations: int
    scheme: str

    @property
    def is_significant(self) -> bool:
        """Признак статистически значимой пространственной структуры."""
        return self.p_value < SIGNIFICANCE_LEVEL

    @property
    def direction(self) -> str:
        """Вид структуры: группировка похожих значений или их чередование."""
        if not self.is_significant:
            return "random"
        return "clustered" if self.value > self.expected else "dispersed"


@dataclass(frozen=True, slots=True)
class LocalMoran:
    """Локальный индекс для одной территории."""

    code: str
    name: str
    value: float
    standardized: float
    spatial_lag: float
    local_index: float
    p_value: float
    quadrant: str
    neighbours: int

    @property
    def is_significant(self) -> bool:
        """Признак того, что положение территории отличается от случайного."""
        return self.p_value < SIGNIFICANCE_LEVEL

    @property
    def label(self) -> Any:
        """Подпись типа окружения; для незначимых территорий пустая."""
        return QUADRANT_LABELS[self.quadrant] if self.is_significant else ""


def build_matrix(
    codes: list[str],
    neighbours: dict[str, list[str]],
) -> np.ndarray:
    """
    Построить матрицу пространственных весов и привести строки к единичной сумме.

    Иначе регион с восемью соседями весил бы вчетверо больше региона с двумя.
    """
    index = {code: position for position, code in enumerate(codes)}
    matrix = np.zeros((len(codes), len(codes)), dtype=float)

    for code, related in neighbours.items():
        row = index.get(code)
        if row is None:
            continue
        for neighbour in related:
            column = index.get(neighbour)
            if column is not None and column != row:
                matrix[row, column] = 1.0

    totals = matrix.sum(axis=1, keepdims=True)
    return np.divide(matrix, totals, out=np.zeros_like(matrix), where=totals > 0)


def nearest_neighbours(
    codes: list[str],
    coordinates: dict[str, tuple[float, float]],
    count: int = NEAREST_COUNT,
) -> dict[str, list[str]]:
    """Определить соседство по расстоянию между центрами на сфере — для проверки устойчивости."""
    usable = [code for code in codes if code in coordinates]
    if len(usable) <= count:
        return {code: [other for other in usable if other != code] for code in usable}

    radians = np.asarray(
        [[np.radians(coordinates[code][0]), np.radians(coordinates[code][1])] for code in usable]
    )
    latitudes = radians[:, 0][:, None]
    longitudes = radians[:, 1][:, None]

    # Сферический косинус — одним матричным выражением.
    cosines = np.sin(latitudes) * np.sin(latitudes).T + np.cos(latitudes) * np.cos(
        latitudes
    ).T * np.cos(longitudes - longitudes.T)
    distances = np.arccos(np.clip(cosines, -1.0, 1.0))
    np.fill_diagonal(distances, np.inf)

    result: dict[str, list[str]] = {}
    for position, code in enumerate(usable):
        order = np.argsort(distances[position])[:count]
        result[code] = [usable[int(other)] for other in order]
    return result


def global_moran(
    values: np.ndarray,
    weights: np.ndarray,
    *,
    scheme: str = DEFAULT_SCHEME,
    permutations: int = PERMUTATIONS,
) -> GlobalMoran | None:
    """Рассчитать глобальный индекс Морана; ожидание при случайности — ``−1/(n−1)``."""
    if values.size < MIN_TERRITORIES or weights.shape[0] != values.size:
        return None

    deviations = values - float(np.mean(values))
    denominator = float(deviations @ deviations)
    if denominator <= 0:
        return None

    observed = _moran_statistic(deviations, weights, denominator)
    expected = -1 / (values.size - 1)

    rng = np.random.default_rng(RANDOM_SEED)
    sample = np.empty(permutations, dtype=float)
    for index in range(permutations):
        shuffled = rng.permutation(deviations)
        sample[index] = _moran_statistic(shuffled, weights, denominator)

    # Двусторонний уровень значимости по перестановкам.
    extreme = int(np.count_nonzero(np.abs(sample - expected) >= abs(observed - expected)))
    p_value = (extreme + 1) / (permutations + 1)

    spread = float(np.std(sample, ddof=1))
    z_score = (observed - expected) / spread if spread > 0 else 0.0

    return GlobalMoran(
        value=observed,
        expected=expected,
        z_score=z_score,
        p_value=p_value,
        permutations=permutations,
        observations=int(values.size),
        scheme=scheme,
    )


def local_moran(
    values: np.ndarray,
    weights: np.ndarray,
    codes: list[str],
    names: dict[str, str],
    *,
    permutations: int = PERMUTATIONS,
) -> list[LocalMoran]:
    """
    Рассчитать локальные индексы условной перестановкой: значение территории на месте.

    Территории без соседей исключаются: локальный индекс для них не определён.
    """
    if values.size != len(codes) or values.size < MIN_TERRITORIES:
        return []

    deviations = values - float(np.mean(values))
    variance = float(deviations @ deviations) / values.size
    if variance <= 0:
        return []

    standardized = deviations / np.sqrt(variance)
    lag = weights @ standardized
    local = standardized * lag

    neighbour_counts = np.count_nonzero(weights, axis=1)
    rng = np.random.default_rng(RANDOM_SEED)
    results: list[LocalMoran] = []

    for position, code in enumerate(codes):
        count = int(neighbour_counts[position])
        if count == 0:
            continue

        others = np.delete(standardized, position)
        row_weights = np.delete(weights[position], position)
        active = row_weights[row_weights > 0]

        sample = np.empty(permutations, dtype=float)
        for index in range(permutations):
            drawn = rng.choice(others, size=active.size, replace=False)
            sample[index] = standardized[position] * float(active @ drawn)

        extreme = int(np.count_nonzero(np.abs(sample) >= abs(local[position])))
        p_value = (extreme + 1) / (permutations + 1)

        results.append(
            LocalMoran(
                code=code,
                name=names.get(code, code),
                value=float(values[position]),
                standardized=float(standardized[position]),
                spatial_lag=float(lag[position]),
                local_index=float(local[position]),
                p_value=p_value,
                quadrant=_quadrant(standardized[position], lag[position]),
                neighbours=count,
            )
        )

    return results


def summarize_quadrants(entries: list[LocalMoran]) -> list[dict[str, Any]]:
    """Сгруппировать территории по типу пространственного окружения."""
    groups: dict[str, list[LocalMoran]] = {code: [] for code in QUADRANT_LABELS}
    for entry in entries:
        if entry.is_significant:
            groups[entry.quadrant].append(entry)

    return [
        {
            "code": code,
            "label": QUADRANT_LABELS[code],
            "count": len(items),
            "items": sorted(items, key=lambda item: abs(item.local_index), reverse=True),
        }
        for code, items in groups.items()
    ]


def _moran_statistic(deviations: np.ndarray, weights: np.ndarray, denominator: float) -> float:
    """Вычислить значение индекса при заданном векторе отклонений."""
    size = deviations.size
    total_weight = float(np.sum(weights))
    if total_weight <= 0:
        return 0.0
    cross = float(deviations @ (weights @ deviations))
    return (size / total_weight) * (cross / denominator)


def _quadrant(value: float, lag: float) -> str:
    """Определить тип окружения территории по знакам её значения и значений соседей."""
    if value >= 0:
        return QUADRANT_HIGH_HIGH if lag >= 0 else QUADRANT_HIGH_LOW
    return QUADRANT_LOW_HIGH if lag >= 0 else QUADRANT_LOW_LOW
