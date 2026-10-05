"""
Малые графики по плиточной карте: в клетке каждого субъекта — ход ряда по годам.

Шкала общая для всех клеток: высота линии сравнима между регионами, как цвет на карте.
Точка — значение выбранного года в цвете его класса шкалы. Координаты — строками:
дробь шаблон записал бы с запятой.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.urls import reverse
from django.utils.translation import gettext as _

from apps.catalog.models import Territory
from apps.core.templatetags.formatting import ru_number

# Клетка и зазор в единицах чертежа; поля линии внутри клетки и полоса подписи сверху.
CELL_WIDTH = 60
CELL_HEIGHT = 46
GAP = 4
PAD_X = 5
PAD_BOTTOM = 5
LABEL_BAND = 14
# Меньше лет — линии нет: клетка показывает одну точку.
MIN_YEARS = 2


@dataclass(frozen=True, slots=True)
class _Scale:
    """Общая шкала клеток: годы по горизонтали, значения по вертикали."""

    years: list[int]
    low: float
    spread: float

    def point(self, left: float, top: float, year: int, value: float) -> tuple[float, float]:
        """Точка года в клетке с левым верхним углом ``left``, ``top``."""
        step = (CELL_WIDTH - 2 * PAD_X) / (len(self.years) - 1)
        height = CELL_HEIGHT - LABEL_BAND - PAD_BOTTOM
        x = left + PAD_X + self.years.index(year) * step
        y = top + LABEL_BAND + (1 - (value - self.low) / self.spread) * height
        return round(x, 1), round(y, 1)


def build_multiples(
    rows: list[dict[str, Any]],
    panel: dict[int, dict[str, float]],
    year: int | None,
) -> dict[str, Any] | None:
    """
    Чертёж малых графиков: клетки по раскладке плиточной карты; ``rows`` — строки карты
    выбранного года (цвет класса и отметка выбора), ``panel`` — «год → субъект → значение».
    """
    years = sorted(panel)
    values = [value for item in years for value in panel[item].values() if value is not None]
    if len(years) < MIN_YEARS or not values:
        return None
    scale = _Scale(years=years, low=min(values), spread=(max(values) - min(values)) or 1.0)
    by_code = {row["code"]: row for row in rows}
    territories = Territory.objects.comparable().only(
        "code", "slug", "name_ru", "name_en", "abbreviation", "tile_x", "tile_y"
    )
    cells = [
        _cell(territory, scale, panel, year, by_code.get(territory.code, {}))
        for territory in territories
        if territory.tile_x is not None and territory.tile_y is not None
    ]
    if not cells:
        return None
    columns = max(int(cell["column"]) for cell in cells) + 1
    lines = max(int(cell["line"]) for cell in cells) + 1
    return {
        "width": columns * (CELL_WIDTH + GAP) - GAP,
        "height": lines * (CELL_HEIGHT + GAP) - GAP,
        "cell_width": CELL_WIDTH,
        "cell_height": CELL_HEIGHT,
        "cells": cells,
        "first_year": years[0],
        "last_year": years[-1],
        "low": scale.low,
        "high": scale.low + scale.spread,
    }


def _cell(
    territory: Territory,
    scale: _Scale,
    panel: dict[int, dict[str, float]],
    year: int | None,
    row: dict[str, Any],
) -> dict[str, Any]:
    """Клетка субъекта: линия по годам с разрывами на пропусках и точка выбранного года."""
    column, line = territory.tile_x, territory.tile_y
    assert column is not None and line is not None  # клетки без места отобраны выше
    left = column * (CELL_WIDTH + GAP)
    top = line * (CELL_HEIGHT + GAP)
    known = [
        (item, panel[item][territory.code])
        for item in scale.years
        if panel[item].get(territory.code) is not None
    ]
    segments: list[str] = []
    previous: int | None = None
    for item, value in known:
        x, y = scale.point(left, top, item, value)
        # Пропуск года разрывает линию.
        joined = previous is not None and scale.years.index(item) == scale.years.index(previous) + 1
        segments.append(f"{'L' if joined else 'M'}{x:g} {y:g}")
        previous = item
    current = panel.get(year, {}).get(territory.code) if year is not None else None
    dot = (
        scale.point(left, top, year, current) if year is not None and current is not None else None
    )
    return {
        "code": territory.code,
        "column": territory.tile_x,
        "line": territory.tile_y,
        "label": territory.short_code or territory.code,
        "href": reverse("catalog:territory-detail", kwargs={"slug": territory.slug}),
        "x": f"{left:g}",
        "y": f"{top:g}",
        "label_x": f"{left + PAD_X:g}",
        "label_y": f"{top + LABEL_BAND - 4:g}",
        "path": " ".join(segments) if len(known) >= MIN_YEARS else "",
        "dot_x": f"{dot[0]:g}" if dot else "",
        "dot_y": f"{dot[1]:g}" if dot else "",
        "colour": row.get("colour", ""),
        "selected": bool(row.get("selected")),
        "empty": not known,
        "title": _title(territory.name, known),
    }


def _title(name: str, known: list[tuple[int, float]]) -> str:
    """Подсказка клетки: первый и последний год со значениями."""
    if not known:
        return f"{name} — {_('нет данных')}"
    first, last = known[0], known[-1]
    if first[0] == last[0]:
        return f"{name}: {first[0]} — {ru_number(first[1])}"
    return f"{name}: {first[0]} — {ru_number(first[1])}, {last[0]} — {ru_number(last[1])}"
