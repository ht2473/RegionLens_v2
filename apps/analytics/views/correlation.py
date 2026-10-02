"""
Корреляционный анализ: матрица связей, разбор пары с диаграммой рассеяния и запаздывание.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from django.utils.translation import gettext_lazy as _

from apps.catalog.models import Series
from apps.catalog.selectors import resolve_year, series_options
from apps.warehouse.queries import region_panel, series_years

from .. import charts
from ..core import correlation, regression
from ..selectors import (
    MAX_SERIES,
    MIN_SERIES,
    cached_result,
    latest_full_year,
    region_rows,
    resolve_choice,
    resolve_many,
    series_columns,
    year_choices,
)
from .base import AnalyticsView

# Наибольшее число показателей в матрице.
MATRIX_LIMIT = MAX_SERIES

# Число связей в перечне наиболее выраженных.
STRONGEST_LIMIT = 8


class CorrelationView(AnalyticsView):
    """Матрица связей показателей, разбор пары и проверка запаздывания."""

    template_name = "analytics/correlation.html"
    results_template = "analytics/partials/_correlation_results.html"
    tool_code = "correlation"

    def build_context(self) -> dict[str, Any]:
        """Собрать матрицу связей, диаграмму рассеяния и профиль запаздывания."""
        request = self.request
        selected = resolve_many(request.GET.getlist("series"), limit=MATRIX_LIMIT)
        method = resolve_choice(
            request.GET.get("method"), correlation.METHODS, correlation.DEFAULT_METHOD
        )

        context: dict[str, Any] = {
            "series_options": series_options,
            "selected": selected,
            "selected_keys": [series.key for series in selected],
            "methods": correlation.METHODS,
            "method": method,
            "max_series": MATRIX_LIMIT,
            "min_series": MIN_SERIES,
        }
        if len(selected) < MIN_SERIES:
            return context

        keys = [series.key for series in selected]
        bounds = series_years(keys)
        years = year_choices(bounds)
        year = resolve_year(request.GET.get("year"), years, default=latest_full_year(keys))
        context.update({"years": years, "year": year})
        if year is None:
            return context

        columns = series_columns(selected, year)
        context["columns"] = columns

        matrix = correlation.correlation_matrix(columns, method)
        context.update(
            {
                "matrix": matrix,
                "matrix_option": _matrix_option(matrix),
                "strongest": correlation.strongest_pairs(matrix, limit=STRONGEST_LIMIT),
                "partial": correlation.partial_correlation(columns),
            }
        )
        context.update(_pair_context(request, selected, columns, matrix, year))
        return context


def _matrix_option(matrix: dict[str, Any]) -> dict[str, Any]:
    """
    Подготовить матрицу связей к отрисовке.

    Незначимые после поправки на множественность коэффициенты не показываются.
    """
    cells: list[dict[str, Any]] = []
    for row in range(matrix["size"]):
        for column in range(matrix["size"]):
            cell: correlation.Correlation = matrix["cells"][row][column]
            visible = cell.is_available and (row == column or cell.is_significant)
            cells.append(
                {
                    "x": column,
                    "y": row,
                    "value": (
                        round(cell.coefficient, 2)
                        if visible and cell.coefficient is not None
                        else None
                    ),
                }
            )
    return charts.matrix_option([str(label) for label in matrix["labels"]], cells)


def _pair_context(
    request: Any,
    selected: list[Series],
    columns: list[dict[str, Any]],
    matrix: dict[str, Any],
    year: int,
) -> dict[str, Any]:
    """Разобрать выбранную пару показателей: диаграмма, регрессия, запаздывание."""
    method = matrix["method"]
    keys = [series.key for series in selected]
    first_key = request.GET.get("x") if request.GET.get("x") in keys else keys[0]
    second_key = request.GET.get("y") if request.GET.get("y") in keys else keys[1]
    if first_key == second_key:
        second_key = next(key for key in keys if key != first_key)

    by_key = {column["key"]: column for column in columns}
    first, second = by_key[first_key], by_key[second_key]

    rows = region_rows()
    points = correlation.scatter_points(
        first["values"],
        second["values"],
        [row["name"] for row in rows],
        [row["code"] for row in rows],
    )
    # Коэффициент пары — из матрицы, с поправкой на множественность.
    pair = matrix["cells"][matrix["keys"].index(first_key)][matrix["keys"].index(second_key)]

    fit = None
    line = None
    if points:
        fit = regression.ols(
            np.asarray([point["y"] for point in points]),
            np.asarray([point["x"] for point in points]),
            [str(first["short"])],
            robust=True,
        )
        if fit is not None and fit.slope.is_significant:
            low = min(point["x"] for point in points)
            high = max(point["x"] for point in points)
            predicted = fit.predict([low, high])
            line = [[low, predicted[0]], [high, predicted[1]]]

    # Сдвиг — от года, за который взят второй показатель.
    target_year = second["year"] or year
    lag_profile = cached_result(
        "correlation-lag",
        {"x": first_key, "y": second_key, "year": target_year, "method": method},
        lambda: correlation.lagged_profile(
            _optional_panel(region_panel(first_key)),
            _optional_panel(region_panel(second_key)),
            target_year=target_year,
            method=method,
        ),
    )

    return {
        "pair_first": first,
        "pair_second": second,
        "pair": pair,
        "pair_points": points,
        "pair_fit": fit,
        "scatter_option": charts.scatter_fit_option(
            points,
            x_name=_axis_name(first),
            y_name=_axis_name(second),
            line=line,
            line_label=str(_("линия связи")),
        )
        if points
        else None,
        "lag_profile": lag_profile,
        "lag_target_year": target_year,
        "lag_option": charts.lag_option(lag_profile) if lag_profile else None,
    }


def _optional_panel(panel: dict[int, dict[str, float]]) -> dict[int, dict[str, float | None]]:
    """Привести панель значений к виду с явными пропусками."""
    return {year: dict(values) for year, values in panel.items()}


def _axis_name(column: dict[str, Any]) -> str:
    """Составить подпись оси: название показателя, единица измерения и год."""
    parts = [str(column["short"])]
    if column["unit"]:
        parts.append(f"({column['unit']})")
    if column["year"]:
        parts.append(str(column["year"]))
    return " ".join(parts)
