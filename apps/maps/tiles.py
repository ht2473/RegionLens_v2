"""Геометрия плиточной картограммы — общая для карты и пространственного анализа."""

from __future__ import annotations

from typing import Any

# Шаг сетки и размер плитки в единицах чертежа; разница — зазор между соседями.
TILE_STEP = 10
TILE_SIZE = 9


def tile_position(cell: int | None) -> int | None:
    """Перевести номер клетки в координату чертежа."""
    return cell * TILE_STEP if cell is not None else None


def tile_grid(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Определить размеры сетки по размещённым территориям справочника."""
    placed = [
        row for row in rows if row.get("tile_x") is not None and row.get("tile_y") is not None
    ]
    if not placed:
        return {"available": False, "columns": 0, "rows": 0, "placed": 0}

    columns = max(row["tile_x"] for row in placed) + 1
    lines = max(row["tile_y"] for row in placed) + 1
    return {
        "available": True,
        "columns": columns,
        "rows": lines,
        "width": columns * TILE_STEP,
        "height": lines * TILE_STEP,
        "size": TILE_SIZE,
        "placed": len(placed),
    }
