"""
Необычные значения ряда по регионам — правилом, без модели.

Выделяется: значение года вне полутора межквартильных размахов от квартилей (правило
Тьюки). У долей, средних и индексов это регионы, заметно отличные от остальных; у сумм —
просто крупные регионы, поэтому у сумм такое не показывается.

Резкие скачки между годами не ищутся: на корпусе таблиц правило отмечало половину таблиц,
и почти все находки были настоящими событиями, а не ошибками ввода. Смену единицы у всех
регионов сразу ловит ``consistency.scale_jumps``.
"""

from __future__ import annotations

import numpy as np

# Выделяется значение дальше стольких межквартильных размахов от квартиля.
LEVEL_IQR = 1.5
# Квартили считаются, если регионов не меньше стольких.
MIN_REGIONS = 10


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
