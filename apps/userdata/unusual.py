"""
Необычные значения ряда по регионам — правилами, без модели.

Выделяется: значение года вне полутора межквартильных размахов от квартилей (правило
Тьюки). У долей, средних и индексов это регионы, заметно отличные от остальных; у сумм —
просто крупные регионы, поэтому у сумм такое не показывается.

Скачок: изменение региона между соседними годами ряда не меньше чем вдвое (в обе стороны)
и вне трёх межквартильных размахов изменений всех регионов того же года — изменения
в логарифмах. Так выглядит опечатка или смена единицы; у настоящих данных скачок редок
(на рядах основного набора сайта — 0,2 региона на ряд).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from django.utils.translation import gettext as _

from apps.core.templatetags.formatting import ru_number

# Выделяется значение дальше стольких межквартильных размахов от квартиля.
LEVEL_IQR = 1.5
# Скачок — изменение дальше стольких размахов изменений года и не меньше чем вдвое.
JUMP_IQR = 3.0
JUMP_RATIO = 2.0
# Квартили считаются, если регионов не меньше стольких.
MIN_REGIONS = 10


@dataclass(frozen=True, slots=True)
class Jump:
    """Резкий скачок значения региона между соседними годами ряда."""

    code: str
    year_before: int
    year: int
    before: float
    after: float

    @property
    def ratio(self) -> float:
        """Во сколько раз изменилось: больше единицы — и рост, и падение."""
        return max(self.after / self.before, self.before / self.after)


def times(ratio: float) -> str:
    """«в 12 раз», «в 3 раза», «в 2,5 раза»: дробное и 2–4 (кроме 12–14) — «раза»."""
    digits = 1 if ratio < 10 else 0  # noqa: PLR2004 — десятые только у малых
    text = ru_number(ratio, digits)
    whole = round(ratio, digits)
    number = int(whole)
    few = whole != number or (number % 10 in {2, 3, 4} and number % 100 not in {12, 13, 14})
    return (_("в %(ratio)s раза") if few else _("в %(ratio)s раз")) % {"ratio": text}


def standing_out(values: dict[str, float]) -> list[str]:
    """Регионы со значением вне полутора межквартильных размахов — дальние первыми."""
    if len(values) < MIN_REGIONS:
        return []
    low, high = np.percentile(list(values.values()), [25, 75])
    spread = high - low
    if spread <= 0:
        return []
    bottom, top = low - LEVEL_IQR * spread, high + LEVEL_IQR * spread
    found = {
        code: max(bottom - value, value - top)
        for code, value in values.items()
        if value < bottom or value > top
    }
    return sorted(found, key=lambda code: -found[code])


def jumps(before: dict[str, float], after: dict[str, float], years: tuple[int, int]) -> list[Jump]:
    """Скачки между двумя соседними годами ряда — сильные первыми."""
    logs = {
        code: math.log(after[code] / before[code])
        for code in after
        if code in before and before[code] > 0 and after[code] > 0
    }
    if len(logs) < MIN_REGIONS:
        return []
    low, high = np.percentile(list(logs.values()), [25, 75])
    spread = high - low
    bottom, top = low - JUMP_IQR * spread, high + JUMP_IQR * spread
    found = [
        Jump(code, years[0], years[1], before[code], after[code])
        for code, change in logs.items()
        if abs(change) >= math.log(JUMP_RATIO) and (change < bottom or change > top)
    ]
    return sorted(found, key=lambda jump: -jump.ratio)
