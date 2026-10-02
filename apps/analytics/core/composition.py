"""
Состав субъектов в динамике: постоянный состав окна лет и субъекты вне его.

Мера по годам на меняющемся составе смешивает изменение неравенства со сменой состава.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

# Постоянный состав меньше этого числа субъектов для мер неравенства не годится.
MIN_CONSTANT = 20

# Динамика — хотя бы два года.
MIN_YEARS = 2

Panel = Mapping[int, Mapping[str, float]]


@dataclass(frozen=True, slots=True)
class Outsider:
    """Субъект, у которого значения есть не во всех годах окна."""

    code: str
    first_year: int
    last_year: int
    # Лет окна без значения между первым и последним годом субъекта.
    gaps: int


@dataclass(frozen=True, slots=True)
class Composition:
    """Состав субъектов в окне лет."""

    years: tuple[int, ...]
    constant: tuple[str, ...]
    # Появились после первого года окна.
    entered: tuple[Outsider, ...]
    # Пропали до последнего года окна.
    left: tuple[Outsider, ...]
    # Есть в первом и последнем году, но с пропусками между ними.
    gapped: tuple[Outsider, ...]
    # Годы окна, не вошедшие в состав: субъектов в них слишком мало.
    sparse: tuple[int, ...]
    # Наибольшее число субъектов за годы окна.
    fullest: int

    @property
    def outsiders(self) -> tuple[Outsider, ...]:
        """Все субъекты вне постоянного состава."""
        return self.entered + self.left + self.gapped

    @property
    def is_usable(self) -> bool:
        """Хватает ли постоянного состава для мер неравенства."""
        return len(self.constant) >= MIN_CONSTANT and len(self.years) >= MIN_YEARS

    @property
    def changes(self) -> bool:
        """Меняется ли состав в окне."""
        return bool(self.outsiders)


def compose(panel: Panel, first_year: int, last_year: int, *, share: float) -> Composition:
    """
    Определить постоянный состав окна ``first_year``–``last_year``.

    Постоянный — субъекты со значением в каждом полном году окна: субъектов в нём не меньше
    ``share`` от наибольшего их числа. Неполные годы перечислены в ``sparse`` и в расчёт
    не входят — одного такого года хватило бы, чтобы состав сжался до горстки субъектов.
    """
    window = [year for year in sorted(panel) if first_year <= year <= last_year and panel[year]]
    if not window:
        return Composition((), (), (), (), (), (), 0)

    fullest = max(len(panel[year]) for year in window)
    years = [year for year in window if len(panel[year]) >= share * fullest]
    sparse = tuple(year for year in window if year not in years)

    present: dict[str, list[int]] = {}
    for year in years:
        for code in panel[year]:
            present.setdefault(code, []).append(year)

    constant = tuple(sorted(code for code, seen in present.items() if len(seen) == len(years)))
    entered: list[Outsider] = []
    left: list[Outsider] = []
    gapped: list[Outsider] = []
    for code, seen in sorted(present.items()):
        if len(seen) == len(years):
            continue
        first, last = seen[0], seen[-1]
        span = [year for year in years if first <= year <= last]
        outsider = Outsider(code=code, first_year=first, last_year=last, gaps=len(span) - len(seen))
        if first > years[0]:
            entered.append(outsider)
        elif last < years[-1]:
            left.append(outsider)
        else:
            gapped.append(outsider)

    return Composition(
        years=tuple(years),
        constant=constant,
        entered=tuple(sorted(entered, key=lambda item: (item.first_year, item.code))),
        left=tuple(sorted(left, key=lambda item: (item.last_year, item.code))),
        gapped=tuple(gapped),
        sparse=sparse,
        fullest=fullest,
    )


def restrict(panel: Panel, composition: Composition) -> dict[int, dict[str, float]]:
    """Оставить в панели годы состава и только субъекты постоянного состава."""
    members = set(composition.constant)
    return {
        year: {code: value for code, value in panel[year].items() if code in members}
        for year in composition.years
    }
