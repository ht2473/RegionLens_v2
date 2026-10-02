"""
Пространственный анализ: глобальный индекс Морана, диаграмма Морана и карта локальных индексов.

При сухопутной схеме Калининградская и Сахалинская области остаются без соседей.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from django.utils.translation import gettext_lazy as _

from apps.catalog.constants import territory_short_code
from apps.catalog.selectors import default_year, resolve_year, series_options
from apps.maps.tiles import tile_grid, tile_position
from apps.warehouse.queries import region_panel

from .. import charts
from ..core import spatial
from ..selectors import (
    cached_result,
    coordinates,
    neighbour_map,
    region_rows,
    resolve_choice,
    resolve_one,
)
from .base import AnalyticsView

# Цвета типов окружения: сгущения — насыщенные, исключения — промежуточные.
QUADRANT_COLOURS: dict[str, str] = {
    spatial.QUADRANT_HIGH_HIGH: "--scale-div-pos-3",
    spatial.QUADRANT_LOW_LOW: "--scale-div-neg-3",
    spatial.QUADRANT_HIGH_LOW: "--scale-div-pos-1",
    spatial.QUADRANT_LOW_HIGH: "--scale-div-neg-1",
}

# Типы окружения, у которых заливка тёмная и подпись плитки должна быть светлой.
LIGHT_LABEL_QUADRANTS = (spatial.QUADRANT_HIGH_HIGH, spatial.QUADRANT_LOW_LOW)


class SpatialView(AnalyticsView):
    """Глобальный индекс Морана и карта локальных пространственных кластеров."""

    template_name = "analytics/spatial.html"
    results_template = "analytics/partials/_spatial_results.html"
    tool_code = "spatial"

    def build_context(self) -> dict[str, Any]:
        """Собрать индекс Морана, диаграмму и карту локальных индексов."""
        request = self.request
        series = resolve_one(request.GET.get("series"))
        scheme = resolve_choice(request.GET.get("scheme"), spatial.SCHEMES, spatial.DEFAULT_SCHEME)

        context: dict[str, Any] = {
            "series_options": series_options,
            "series": series,
            "schemes": spatial.SCHEMES,
            "scheme": scheme,
        }
        if series is None:
            return context

        panel = region_panel(series.key)
        years = sorted(panel)
        counts = {item: len(values) for item, values in panel.items()}
        year = resolve_year(request.GET.get("year"), years, default=default_year(counts))
        context.update({"years": years, "year": year})
        if year is None:
            return context

        rows = region_rows()
        values = panel.get(year, {})
        present = [row for row in rows if values.get(row["code"]) is not None]
        neighbours = _neighbours(scheme, [row["code"] for row in present])

        codes = [row["code"] for row in present]
        weights = spatial.build_matrix(codes, neighbours)
        vector = np.asarray([values[code] for code in codes], dtype=float)

        analysis = cached_result(
            "spatial",
            {"series": series.key, "year": year, "scheme": scheme},
            lambda: _analyse(vector, weights, codes, present, scheme),
        )

        isolated = [
            row["name"] for index, row in enumerate(present) if not neighbours.get(codes[index])
        ]
        context.update(analysis)
        context.update(
            {
                "isolated": isolated,
                "quadrant_labels": spatial.QUADRANT_LABELS,
                "tile_rows": _tile_rows(rows, analysis.get("local", [])),
                "tile_grid": tile_grid(rows),
                "tile_caption": _("Карта локальных пространственных кластеров"),
                "quadrant_colours": QUADRANT_COLOURS,
            }
        )
        return context


def _neighbours(scheme: str, codes: list[str]) -> dict[str, list[str]]:
    """Определить соседей субъектов по выбранной схеме."""
    if scheme == "knn":
        return spatial.nearest_neighbours(codes, coordinates())
    return {code: neighbour_map(scheme).get(code, []) for code in codes}


def _analyse(
    vector: np.ndarray,
    weights: np.ndarray,
    codes: list[str],
    rows: list[dict[str, Any]],
    scheme: str,
) -> dict[str, Any]:
    """Выполнить пространственный анализ; результат с перестановками кэшируется целиком."""
    names = {row["code"]: row["name"] for row in rows}
    global_index = spatial.global_moran(vector, weights, scheme=scheme)
    local = spatial.local_moran(vector, weights, codes, names)

    return {
        "global_moran": global_index,
        "local": local,
        "quadrants": spatial.summarize_quadrants(local),
        "moran_option": _moran_option(global_index, local),
        "significant_count": sum(1 for entry in local if entry.is_significant),
    }


def _moran_option(
    global_index: spatial.GlobalMoran | None,
    local: list[spatial.LocalMoran],
) -> dict[str, Any] | None:
    """Построить диаграмму Морана с раскраской точек по типу окружения."""
    if global_index is None or not local:
        return None

    grouped: dict[str, list[dict[str, Any]]] = {code: [] for code in spatial.QUADRANT_LABELS}
    for entry in local:
        grouped[entry.quadrant].append(
            {
                "name": entry.name,
                "x": round(entry.standardized, 4),
                "y": round(entry.spatial_lag, 4),
            }
        )

    quadrants = [
        {"name": str(spatial.QUADRANT_LABELS[code]), "points": points}
        for code, points in grouped.items()
        if points
    ]
    limit = max(abs(entry.standardized) for entry in local)
    return charts.moran_option(quadrants, slope=global_index.value, limit=limit)


def _tile_rows(
    rows: list[dict[str, Any]],
    local: list[spatial.LocalMoran],
) -> list[dict[str, Any]]:
    """Подготовить плитки карты локальных индексов; незначимые — штриховкой."""
    entries = {entry.code: entry for entry in local}
    tiles: list[dict[str, Any]] = []

    for row in rows:
        entry = entries.get(row["code"])
        significant_entry = entry if entry is not None and entry.is_significant else None
        if significant_entry is not None:
            title = f"{row['name']} — {spatial.QUADRANT_LABELS[significant_entry.quadrant]}"
        elif entry is not None:
            title = f"{row['name']} — {_('окружение неотличимо от случайного')}"
        else:
            title = f"{row['name']} — {_('не участвует в расчёте')}"

        tiles.append(
            {
                "code": row["code"],
                "slug": row["slug"],
                "abbreviation": territory_short_code(row["code"], row["abbreviation"]),
                "tile_px": tile_position(row["tile_x"]),
                "tile_py": tile_position(row["tile_y"]),
                "colour": (
                    QUADRANT_COLOURS[significant_entry.quadrant]
                    if significant_entry is not None
                    else ""
                ),
                "light_label": (
                    significant_entry is not None
                    and significant_entry.quadrant in LIGHT_LABEL_QUADRANTS
                ),
                "title": title,
            }
        )
    return tiles
