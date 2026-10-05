"""
Коды-маски вместо чисел: 9999, 8888, 3333, 2222 и подобные.

Наборы «Если быть точным» заменяют пропуски и значения без точного числа кодами из одинаковых
цифр («9999 — нет данных», «8888 — в регионе не ведутся наблюдения», «2222 — меньше 6 %») и
описывают их в файле описания, у каждого набора — свои. На карте такой код выглядит самым
большим значением. Код узнаётся по самим значениям: число из четырёх и более одинаковых
цифр, которое в десять и более раз больше почти всех остальных значений своего показателя
(95-й процентиль). Человек может вернуть код в числа на шаге «Показатели».
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

# Во сколько раз код больше 95-го процентиля остальных значений показателя.
RATIO = 10
# Доля значений показателя, ниже которой лежат «почти все» остальные.
QUANTILE = 0.95
# Показатель только из кодов: код — маска, если встретился хотя бы столько раз.
ALONE_REPEATS = 2
_REPDIGIT = re.compile(r"(\d)\1{3,}")


@dataclass(slots=True)
class Mask:
    """Код в столбце значений: сколько раз встретился и сочтён ли пропуском."""

    value: float
    count: int = 0
    flagged: int = 0

    @property
    def text(self) -> str:
        """Код как в таблице: без дробной части."""
        return str(int(self.value))

    @property
    def detected(self) -> bool:
        """Похож на маску в большинстве показателей, где встретился."""
        return self.flagged * 2 > self.count


def is_candidate(value: float | None) -> bool:
    """Целое из четырёх и более одинаковых цифр (со знаком или без)."""
    if value is None or not math.isfinite(value) or value != int(value):
        return False
    return bool(_REPDIGIT.fullmatch(str(abs(int(value)))))


def detect(groups: Mapping[str, Iterable[float]]) -> dict[float, Mask]:
    """Коды по показателям: ``groups`` — значения каждого показателя (без пропусков)."""
    found: dict[float, Mask] = {}
    for values in groups.values():
        numbers = list(values)
        candidates = {value for value in numbers if is_candidate(value)}
        if not candidates:
            continue
        others = sorted(abs(value) for value in numbers if value not in candidates)
        bound = others[int(QUANTILE * (len(others) - 1))] if others else None
        for value in candidates:
            repeats = numbers.count(value)
            mask = found.setdefault(value, Mask(value))
            mask.count += repeats
            if (bound is None and repeats >= ALONE_REPEATS) or (
                bound is not None and abs(value) >= RATIO * bound
            ):
                mask.flagged += repeats
    return found


def chosen(found: Mapping[float, Mask], answer: list[str] | None) -> set[float]:
    """Коды, которые станут пропусками: по ответу человека или найденные по значениям."""
    if answer is None:
        return {value for value, mask in found.items() if mask.detected}
    wanted = {text.strip() for text in answer}
    return {value for value, mask in found.items() if mask.text in wanted}
