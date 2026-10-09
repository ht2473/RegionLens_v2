"""
Миниатюры сохранённого: плиточная картограмма, линии, столбики или точки — по виду.

Миниатюра узнаётся глазом, а не читается: несколько путей SVG без подписей, по одному
на класс. Значения — из тех же выборок склада, что у страниц (кэш до пересборки);
без склада, без ряда и у инструментов анализа — значок вида.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import duckdb
from django.core.cache import cache

from apps.warehouse.duckdb_client import WarehouseNotBuiltError
from apps.warehouse.queries import (
    COUNTRY_CODE,
    available_years,
    series_values,
    series_values_by_territory,
)

# Плитка картограммы: шаг сетки и сторона квадрата в единицах чертежа.
TILE_STEP = 10
TILE_SIZE = 9
# Классы картограммы — квинтили склада.
CLASSES = 5
# Поле линий, столбиков и точек: пропорции окна миниатюры в плитке.
WIDTH = 200
HEIGHT = 64
PAD = 4
# Точки распределения: столбцов и сторона точки.
DOT_BINS = 40
DOT = 3
# Сколько регионов линиями: как у сравнения на странице динамики.
LINES = 3
# Раскладка плиток хранится час: справочник меняется редко.
TILES_CACHE_KEY = "workspace:thumb-tiles"
TILES_CACHE_SECONDS = 3600

# Чего нет в складе или в файле набора — миниатюра становится значком.
_UNAVAILABLE = (WarehouseNotBuiltError, duckdb.Error, LookupError, ValueError)


@dataclass(frozen=True, slots=True)
class Thumb:
    """Миниатюра: значок или чертёж из путей (класс оформления, путь)."""

    kind: str
    icon: str = ""
    width: int = WIDTH
    height: int = HEIGHT
    paths: tuple[tuple[str, str], ...] = ()


def icon_thumb(icon: str) -> Thumb:
    """Миниатюра-значок."""
    return Thumb(kind="icon", icon=icon)


def _tiles() -> dict[str, tuple[int, int]]:
    """Клетки плиточной картограммы субъектов: код → (столбец, строка)."""
    found = cache.get(TILES_CACHE_KEY)
    if found is None:
        from apps.catalog.models import Territory

        found = {
            code: (x, y)
            for code, x, y in Territory.objects.comparable()
            .filter(tile_x__isnull=False, tile_y__isnull=False)
            .values_list("code", "tile_x", "tile_y")
        }
        cache.set(TILES_CACHE_KEY, found, TILES_CACHE_SECONDS)
    return found


def _squares(cells: Iterable[tuple[int, int]]) -> str:
    """Путь квадратов клеток."""
    return "".join(
        f"M{x * TILE_STEP} {y * TILE_STEP}h{TILE_SIZE}v{TILE_SIZE}h-{TILE_SIZE}z" for x, y in cells
    )


def _grid_size(tiles: dict[str, tuple[int, int]]) -> tuple[int, int]:
    """Ширина и высота сетки в единицах чертежа."""
    columns = max(x for x, _y in tiles.values()) + 1
    rows = max(y for _x, y in tiles.values()) + 1
    return columns * TILE_STEP, rows * TILE_STEP


def _latest_year(key: str, year: Any) -> int | None:
    """Год вида или последний год ряда со значениями по субъектам."""
    try:
        return int(year)
    except TypeError, ValueError:
        years = available_years(key)
        return years[-1] if years else None


def map_thumb(key: str, year: Any = None, chosen: Iterable[str] = ()) -> Thumb | None:
    """Плиточная картограмма ряда за год: пять классов, без данных и выбранные регионы."""
    tiles = _tiles()
    found_year = _latest_year(key, year)
    if not tiles or found_year is None:
        return None
    rows = series_values_by_territory(key, found_year)
    steps: dict[str, int] = {}
    for row in rows:
        if row["value"] is not None and row.get("quintile"):
            steps[row["territory_code"]] = min(int(row["quintile"]), CLASSES)
    if not steps:
        return None
    paths = [
        (
            f"step-{step}",
            _squares(cell for code, cell in tiles.items() if steps.get(code) == step),
        )
        for step in range(1, CLASSES + 1)
    ]
    paths.append(("none", _squares(cell for code, cell in tiles.items() if code not in steps)))
    marked = [tiles[code] for code in chosen if code in tiles]
    if marked:
        paths.append(("chosen", _squares(marked)))
    width, height = _grid_size(tiles)
    return Thumb(kind="map", width=width, height=height, paths=_present(paths))


def region_thumb(code: str) -> Thumb | None:
    """Плиточная картограмма с отмеченным регионом."""
    tiles = _tiles()
    if code not in tiles:
        return None
    width, height = _grid_size(tiles)
    paths = [
        ("land", _squares(cell for other, cell in tiles.items() if other != code)),
        ("region", _squares([tiles[code]])),
    ]
    return Thumb(kind="map", width=width, height=height, paths=_present(paths))


def lines_thumb(key: str, chosen: Iterable[str] = ()) -> Thumb | None:
    """Ход ряда у выбранных регионов (до трёх) на фоне России; без выбора — Россия."""
    codes = [code for code in chosen if code != COUNTRY_CODE][:LINES]
    values = series_values([key], [*codes, COUNTRY_CODE]).get(key, {})
    lines = {code: points for code, points in values.items() if len(points) > 1}
    if not lines:
        return None
    years = sorted({year for points in lines.values() for year in points})
    low = min(value for points in lines.values() for value in points.values())
    high = max(value for points in lines.values() for value in points.values())
    span_x = (years[-1] - years[0]) or 1
    span_y = (high - low) or 1

    def path(points: dict[int, float]) -> str:
        parts = []
        for index, year in enumerate(sorted(points)):
            x = PAD + (year - years[0]) / span_x * (WIDTH - 2 * PAD)
            y = HEIGHT - PAD - (points[year] - low) / span_y * (HEIGHT - 2 * PAD)
            parts.append(f"{'M' if index == 0 else 'L'}{x:.1f} {y:.1f}")
        return "".join(parts)

    paths = []
    if COUNTRY_CODE in lines:
        paths.append(("country", path(lines[COUNTRY_CODE])))
    paths.extend(
        (f"line-{index}", path(lines[code])) for index, code in enumerate(codes, 1) if code in lines
    )
    return Thumb(kind="lines", paths=_present(paths))


def bars_thumb(key: str, year: Any = None, chosen: Iterable[str] = ()) -> Thumb | None:
    """Регионы по убыванию столбиками; выбранные — цветом."""
    found_year = _latest_year(key, year)
    if found_year is None:
        return None
    rows = sorted(
        (row for row in series_values_by_territory(key, found_year) if row["value"] is not None),
        key=lambda row: -row["value"],
    )
    if not rows:
        return None
    marked = set(chosen)
    low = min(0.0, *(row["value"] for row in rows))
    high = max(row["value"] for row in rows)
    span = (high - low) or 1
    step = (WIDTH - 2 * PAD) / len(rows)
    others: list[str] = []
    picked: list[str] = []
    for index, row in enumerate(rows):
        x = PAD + index * step + step / 2
        top = HEIGHT - PAD - (row["value"] - low) / span * (HEIGHT - 2 * PAD)
        bar = f"M{x:.1f} {HEIGHT - PAD}V{top:.1f}"
        (picked if row["territory_code"] in marked else others).append(bar)
    return Thumb(
        kind="bars", paths=_present([("bar", "".join(others)), ("chosen", "".join(picked))])
    )


def dots_thumb(key: str, year: Any = None, chosen: Iterable[str] = ()) -> Thumb | None:
    """Распределение точками: регион — точка в столбце своего значения."""
    found_year = _latest_year(key, year)
    if found_year is None:
        return None
    rows = [row for row in series_values_by_territory(key, found_year) if row["value"] is not None]
    if not rows:
        return None
    low = min(row["value"] for row in rows)
    high = max(row["value"] for row in rows)
    span = (high - low) or 1
    marked = set(chosen)
    heights = [0] * DOT_BINS
    others: list[str] = []
    picked: list[str] = []
    for row in sorted(rows, key=lambda row: row["value"]):
        column = min(int((row["value"] - low) / span * DOT_BINS), DOT_BINS - 1)
        x = PAD + column * (WIDTH - 2 * PAD) / DOT_BINS
        y = HEIGHT - PAD - (heights[column] + 1) * (DOT + 1)
        heights[column] += 1
        dot = f"M{x:.1f} {max(y, 0):.1f}h{DOT}v{DOT}h-{DOT}z"
        (picked if row["territory_code"] in marked else others).append(dot)
    return Thumb(
        kind="dots", paths=_present([("dot", "".join(others)), ("chosen", "".join(picked))])
    )


def _present(paths: list[tuple[str, str]]) -> tuple[tuple[str, str], ...]:
    """Только непустые пути."""
    return tuple((name, d) for name, d in paths if d)


# Чертёж по коду страницы вида; остальные страницы — значком. Страницы поверхности без ряда
# в адресе открываются рядом по умолчанию — им и рисуются.
_SURFACE = ("map", "rankings", "distribution", "compare")
_BY_TARGET = {
    "map": map_thumb,
    "rankings": bars_thumb,
    "distribution": dots_thumb,
}


def view_thumb(target: str, parameters: dict[str, Any] | None, icon: str) -> Thumb:
    """Миниатюра сохранённого вида: по странице и её ряду, иначе значок."""
    from apps.catalog.selectors import default_series_key

    values = parameters or {}
    listed = values.get("series")
    key = (listed[0] if isinstance(listed, list) and listed else listed) or ""
    if not key and target in _SURFACE:
        key = default_series_key() or ""
    chosen = values.get("territory") or []
    chosen = chosen if isinstance(chosen, list) else [chosen]
    try:
        if key and target == "compare":
            found = lines_thumb(str(key), chosen)
        elif key and target in _BY_TARGET:
            found = _BY_TARGET[target](str(key), values.get("year"), chosen)
        else:
            found = None
    except _UNAVAILABLE:
        found = None
    return found or icon_thumb(icon)


def series_thumb(key: str, icon: str = "map") -> Thumb:
    """Миниатюра ряда — картограмма последнего года (показатель, ряд своей таблицы)."""
    try:
        found = map_thumb(key) if key else None
    except _UNAVAILABLE:
        found = None
    return found or icon_thumb(icon)


def territory_thumb(code: str) -> Thumb:
    """Миниатюра региона — его место на плиточной картограмме."""
    return region_thumb(code) or icon_thumb("map-pin")
