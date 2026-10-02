"""
Разбиение значений на классы шкалы: квантили, равные интервалы, Дженкс,
стандартные отклонения, а также палитры и гистограмма легенды.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from django.utils.translation import gettext_lazy as _

# Допустимые способы разбиения и их подписи для интерфейса.
METHODS: dict[str, Any] = {
    "quantile": _("квантили"),
    "equal": _("равные интервалы"),
    "jenks": _("естественные границы"),
    "stddev": _("стандартные отклонения"),
}
DEFAULT_METHOD = "quantile"

# Допустимое число классов: больше семи соседние оттенки неразличимы.
MIN_CLASSES = 3
MAX_CLASSES = 7
DEFAULT_CLASSES = 5

# Число делений гистограммы распределения в легенде.
HISTOGRAM_BINS = 28

# Минимальное число различных значений: по одному значению шкала не строится.
MIN_DISTINCT_VALUES = 2

# Пороги числа классов, определяющие шаг шкалы стандартных отклонений.
STDDEV_WIDE_CLASSES = 4
STDDEV_MEDIUM_CLASSES = 6


@dataclass(frozen=True, slots=True)
class Classification:
    """
    Результат разбиения значений на классы.

    ``breaks`` — ``class_count + 1`` границ; класс — [нижняя, верхняя), последний включает максимум.
    """

    method: str
    breaks: tuple[float, ...]
    counts: tuple[int, ...]
    values_count: int

    @property
    def class_count(self) -> int:
        """Число классов."""
        return len(self.breaks) - 1

    @property
    def method_label(self) -> Any:
        """Подпись способа разбиения для легенды."""
        return METHODS.get(self.method, self.method)

    def class_of(self, value: float | None) -> int | None:
        """Определить номер класса значения, считая от нуля; пропуск — ``None``."""
        if value is None:
            return None

        for index in range(self.class_count):
            if value < self.breaks[index + 1]:
                return index
        return self.class_count - 1

    def intervals(self) -> list[dict[str, Any]]:
        """Собрать описание классов для легенды."""
        return [
            {
                "index": index,
                "lower": self.breaks[index],
                "upper": self.breaks[index + 1],
                "count": self.counts[index],
                "share": self.counts[index] / self.values_count if self.values_count else 0.0,
            }
            for index in range(self.class_count)
        ]


def classify(
    values: list[float],
    *,
    method: str = DEFAULT_METHOD,
    class_count: int = DEFAULT_CLASSES,
) -> Classification | None:
    """Разбить значения на классы выбранным способом; ``None``, если шкалы не получается."""
    clean = sorted(value for value in values if value is not None and math.isfinite(value))
    if len(clean) < MIN_CLASSES:
        return None

    class_count = max(MIN_CLASSES, min(MAX_CLASSES, class_count))

    # Классов не больше, чем различных значений.
    distinct = sorted(set(clean))
    if len(distinct) < MIN_DISTINCT_VALUES:
        return None
    class_count = min(class_count, len(distinct))

    if method == "equal":
        breaks = _equal_interval_breaks(clean, class_count)
    elif method == "jenks":
        breaks = _jenks_breaks(clean, class_count)
    elif method == "stddev":
        breaks = _stddev_breaks(clean, class_count)
    else:
        method = "quantile"
        breaks = _quantile_breaks(clean, class_count)

    breaks = _deduplicate(breaks, clean)
    counts = _count_by_class(clean, breaks)
    return Classification(
        method=method, breaks=tuple(breaks), counts=tuple(counts), values_count=len(clean)
    )


# ---------------------------------------------------------------------------------------
# Способы разбиения
# ---------------------------------------------------------------------------------------


def _equal_interval_breaks(values: list[float], class_count: int) -> list[float]:
    """Разделить диапазон значений на равные по ширине части."""
    low, high = values[0], values[-1]
    step = (high - low) / class_count
    return [low + step * index for index in range(class_count)] + [high]


def _quantile_breaks(values: list[float], class_count: int) -> list[float]:
    """Разделить упорядоченный ряд на части с равным числом значений."""
    breaks = [values[0]]
    for index in range(1, class_count):
        position = index * len(values) / class_count
        breaks.append(_interpolate(values, position))
    breaks.append(values[-1])
    return breaks


def _stddev_breaks(values: list[float], class_count: int) -> list[float]:
    """Отсчитать границы классов от среднего: шаг — половина или треть отклонения."""
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    deviation = math.sqrt(variance)

    if deviation == 0:
        return _equal_interval_breaks(values, class_count)

    half = class_count / 2
    if class_count <= STDDEV_WIDE_CLASSES:
        step = deviation
    elif class_count <= STDDEV_MEDIUM_CLASSES:
        step = deviation / 2
    else:
        step = deviation / 3

    breaks = [values[0]]
    for index in range(1, class_count):
        breaks.append(mean + (index - half) * step)
    breaks.append(values[-1])

    # Границы за пределами данных подтягиваются внутрь размаха.
    return [min(max(edge, values[0]), values[-1]) for edge in breaks]


def _jenks_breaks(values: list[float], class_count: int) -> list[float]:
    """Найти естественные границы методом Фишера — Дженкса (точное динамическое решение)."""
    count = len(values)

    # Префиксные суммы позволяют получить дисперсию любого отрезка за постоянное время.
    prefix = [0.0] * (count + 1)
    prefix_squares = [0.0] * (count + 1)
    for index, value in enumerate(values):
        prefix[index + 1] = prefix[index] + value
        prefix_squares[index + 1] = prefix_squares[index] + value * value

    def deviation(first: int, last: int) -> float:
        """Сумма квадратов отклонений на отрезке [first, last)."""
        length = last - first
        if length <= 1:
            return 0.0
        total = prefix[last] - prefix[first]
        squares = prefix_squares[last] - prefix_squares[first]
        return squares - total * total / length

    # cost[k][i] — минимальная сумма отклонений при разбиении первых i значений на k классов.
    infinity = float("inf")
    cost = [[infinity] * (count + 1) for _ in range(class_count + 1)]
    split = [[0] * (count + 1) for _ in range(class_count + 1)]
    cost[0][0] = 0.0

    for classes in range(1, class_count + 1):
        for last in range(classes, count + 1):
            for first in range(classes - 1, last):
                if cost[classes - 1][first] == infinity:
                    continue
                candidate = cost[classes - 1][first] + deviation(first, last)
                if candidate < cost[classes][last]:
                    cost[classes][last] = candidate
                    split[classes][last] = first

    # Восстановление границ по запомненным позициям разбиения.
    edges: list[float] = [values[-1]]
    position = count
    for classes in range(class_count, 0, -1):
        first = split[classes][position]
        edges.append(values[first])
        position = first

    edges.reverse()
    return edges


# ---------------------------------------------------------------------------------------
# Вспомогательные операции
# ---------------------------------------------------------------------------------------


def _interpolate(values: list[float], position: float) -> float:
    """Получить значение упорядоченного ряда в дробной позиции."""
    lower = math.floor(position)
    upper = min(lower + 1, len(values) - 1)
    weight = position - lower
    return values[lower] * (1 - weight) + values[upper] * weight


def _deduplicate(breaks: list[float], values: list[float]) -> list[float]:
    """
    Устранить совпадающие границы.

    Вырожденные классы убираются, поэтому классов может оказаться меньше запрошенного.
    """
    unique: list[float] = []
    for edge in breaks:
        if not unique or edge > unique[-1]:
            unique.append(edge)

    if len(unique) < MIN_DISTINCT_VALUES:
        return [values[0], values[-1]]
    return unique


def _count_by_class(values: list[float], breaks: list[float]) -> list[int]:
    """Подсчитать число значений в каждом классе."""
    counts = [0] * (len(breaks) - 1)
    for value in values:
        for index in range(len(counts)):
            if value < breaks[index + 1]:
                counts[index] += 1
                break
        else:
            counts[-1] += 1
    return counts


def histogram(values: list[float], bins: int = HISTOGRAM_BINS) -> list[dict[str, float]]:
    """Построить гистограмму распределения для легенды."""
    clean = [value for value in values if value is not None and math.isfinite(value)]
    if len(clean) < MIN_DISTINCT_VALUES:
        return []

    low, high = min(clean), max(clean)
    if high == low:
        return []

    width = (high - low) / bins
    counts = [0] * bins
    for value in clean:
        index = min(int((value - low) / width), bins - 1)
        counts[index] += 1

    peak = max(counts) or 1
    return [
        {
            "lower": low + width * index,
            "upper": low + width * (index + 1),
            "count": counts[index],
            "height": counts[index] / peak,
        }
        for index in range(bins)
    ]


# ---------------------------------------------------------------------------------------
# Палитры: имена переменных оформления из tokens.css
# ---------------------------------------------------------------------------------------

# Последовательная шкала: семь ступеней от светлой к тёмной.
SEQUENTIAL_STEPS = 7

# Расходящаяся шкала: три ступени в каждую сторону и нейтральная середина.
DIVERGING_VARIABLES = (
    "--scale-div-neg-3",
    "--scale-div-neg-2",
    "--scale-div-neg-1",
    "--scale-div-pos-1",
    "--scale-div-pos-2",
    "--scale-div-pos-3",
)


def sequential_palette(class_count: int) -> list[str]:
    """Подобрать равноотстоящие ступени последовательной шкалы под число классов."""
    if class_count <= 1:
        return ["--scale-seq-4"]

    indices = [
        round(index * (SEQUENTIAL_STEPS - 1) / (class_count - 1)) for index in range(class_count)
    ]
    return [f"--scale-seq-{index + 1}" for index in indices]


def diverging_palette(class_count: int) -> list[str]:
    """Подобрать переменные расходящейся шкалы: половина классов на каждую сторону."""
    half = max(1, class_count // 2)
    negative = list(DIVERGING_VARIABLES[3 - half : 3])
    positive = list(DIVERGING_VARIABLES[3 : 3 + half])
    return negative + positive


def classify_symmetric(values: list[float], class_count: int = 6) -> Classification | None:
    """
    Разбить изменения значений на классы, симметричные относительно нуля.

    Иначе граница около нуля окрасила бы рост и снижение одним цветом.
    """
    clean = [value for value in values if value is not None and math.isfinite(value)]
    if len(clean) < MIN_CLASSES:
        return None

    extreme = max(abs(min(clean)), abs(max(clean)))
    if extreme == 0:
        return None

    half = max(1, min(MAX_CLASSES, class_count) // 2)
    step = extreme / half

    breaks = [-extreme + step * index for index in range(half)]
    breaks.append(0.0)
    breaks.extend(step * (index + 1) for index in range(half))

    counts = _count_by_class(clean, breaks)
    return Classification(
        method="symmetric", breaks=tuple(breaks), counts=tuple(counts), values_count=len(clean)
    )
