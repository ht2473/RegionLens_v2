"""
Конструктор интегрального индекса: шкала, веса, свёртка и направленность — в руках пользователя.

Рядом с рейтингом — проверка устойчивости к смене способа расчёта. Ряд без направленности
в расчёт не входит.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from apps.catalog.models import Series
from apps.catalog.selectors import resolve_year, series_options
from apps.warehouse.queries import series_years
from apps.warehouse.queries.common import featured_set

from .. import charts
from ..core import aggregation, normalization, weighting
from ..selectors import (
    MAX_SERIES,
    MIN_SERIES,
    latest_full_year,
    region_rows,
    resolve_choice,
    resolve_many,
    series_columns,
    series_label,
    year_choices,
)
from .base import AnalyticsView

# Число показателей, чей вклад показывается на диаграмме состава оценки.
CONTRIBUTION_LIMIT = 15

# Направленности, допустимые в индексе.
DIRECTIONS = ("positive", "negative")

# Сочетания шкалы и свёртки для проверки устойчивости.
SENSITIVITY_VARIANTS: tuple[dict[str, str], ...] = (
    {"normalization": "rank", "aggregation": "additive"},
    {"normalization": "zscore", "aggregation": "additive"},
    {"normalization": "minmax", "aggregation": "geometric"},
    {"normalization": "minmax", "aggregation": "topsis"},
)


class IndexBuilderView(AnalyticsView):
    """Построение составного показателя из выбранных рядов."""

    template_name = "analytics/index_builder.html"
    results_template = "analytics/partials/_index_results.html"
    tool_code = "index-builder"

    def build_context(self) -> dict[str, Any]:
        """Собрать рейтинг по составному показателю и проверку его устойчивости."""
        request = self.request
        chosen = resolve_many(request.GET.getlist("series"))
        if not request.GET.getlist("series"):
            # В набор по умолчанию не берутся ряды без направленности.
            chosen = [series for series in chosen if series.polarity in DIRECTIONS]
        directions = _directions(request, chosen)
        selected = [series for series in chosen if directions[series.key]["value"]]

        options = {
            "normalization": resolve_choice(
                request.GET.get("normalization"),
                normalization.METHODS,
                normalization.DEFAULT_METHOD,
            ),
            "weighting": resolve_choice(
                request.GET.get("weighting"), weighting.METHODS, weighting.DEFAULT_METHOD
            ),
            "aggregation": resolve_choice(
                request.GET.get("aggregation"), aggregation.METHODS, aggregation.DEFAULT_METHOD
            ),
        }

        context: dict[str, Any] = {
            "series_options": series_options,
            "selected": selected,
            # В форме остаются и ряды без направленности, чтобы её можно было задать.
            "selected_keys": [series.key for series in chosen],
            "undirected": [
                directions[series.key] for series in chosen if not directions[series.key]["value"]
            ],
            "normalization_methods": normalization.METHODS,
            "weighting_methods": weighting.METHODS,
            "aggregation_methods": aggregation.METHODS,
            "max_series": MAX_SERIES,
            "min_series": MIN_SERIES,
            "min_coverage": aggregation.MIN_COVERAGE,
            **options,
        }
        if len(selected) < MIN_SERIES:
            return context

        keys = [series.key for series in selected]
        years = year_choices(series_years(keys))
        year = resolve_year(request.GET.get("year"), years, default=latest_full_year(keys))
        context.update({"years": years, "year": year})
        if year is None:
            return context

        expert = _expert_weights(request, selected)
        columns = series_columns(selected, year)
        for column in columns:
            column["direction"] = directions[column["key"]]
            column["polarity"] = column["direction"]["value"]
        rows = region_rows()
        territories = [{"code": row["code"], "name": row["name"]} for row in rows]

        result = _build(columns, territories, options, expert)
        context.update(
            {
                "columns": columns,
                "expert_weights": expert,
                "scores": result["scores"],
                "weights": result["weights"],
                "weight_rows": _weight_rows(columns, result["weights"], expert),
                "normalized": result["normalized"],
                "contribution_option": _contribution_option(result["scores"], columns),
                "slugs": {row["code"]: row["slug"] for row in rows},
            }
        )

        sensitivity = _sensitivity(columns, territories, options, expert, result["scores"])
        requested = request.GET.get("compare", "")
        comparison = next((row for row in sensitivity if row["code"] == requested), None)
        context.update(
            {
                "sensitivity": sensitivity,
                "compare": comparison["code"] if comparison else "",
                "comparison": comparison,
                "ranking": _ranking(result["scores"], context["slugs"], comparison),
            }
        )
        return context


def _directions(request: Any, chosen: list[Series]) -> dict[str, dict[str, Any]]:
    """
    Направленность каждого отмеченного ряда: заданная пользователем или предложенная.

    Параметр ``direction=<ключ ряда>:<positive|negative>`` — по ключу, а не по порядку.
    """
    requested: dict[str, str] = {}
    for raw in request.GET.getlist("direction"):
        key, _, value = raw.rpartition(":")
        if value in DIRECTIONS:
            requested[key] = value

    featured = featured_set().by_key()
    directions: dict[str, dict[str, Any]] = {}
    for series in chosen:
        suggested = series.polarity if series.polarity in DIRECTIONS else ""
        value = requested.get(series.key) or suggested
        if value and value != suggested:
            origin = "user"
        elif series.key in featured:
            origin = "featured"
        elif getattr(series, "is_user", False):
            origin = "described"
        else:
            origin = "guess"
        directions[series.key] = {
            "key": series.key,
            "label": series_label(series),
            "value": value,
            "suggested": suggested,
            "origin": origin if value else "",
        }
    return directions


def _expert_weights(request: Any, selected: list[Series]) -> list[float]:
    """
    Прочитать веса ``weight:<ключ ряда>``, заданные пользователем.

    Пустой или неразобранный вес — единичный.
    """
    weights: list[float] = []
    for series in selected:
        raw = request.GET.get(f"weight:{series.key}")
        try:
            value = float(raw.replace(",", ".")) if raw else 1.0
        except ValueError:
            value = 1.0
        weights.append(max(0.0, value))
    return weights


def _build(
    columns: list[dict[str, Any]],
    territories: list[dict[str, str]],
    options: dict[str, str],
    expert: list[float],
) -> dict[str, Any]:
    """Выполнить полный расчёт индекса: шкала, веса по нормированным данным, свёртка."""
    normalized = [
        normalization.normalize(
            column["values"],
            method=options["normalization"],
            polarity=column["polarity"],
        )
        for column in columns
    ]

    matrix = np.asarray(
        [[np.nan if value is None else value for value in item.values] for item in normalized],
        dtype=float,
    ).T

    weights = weighting.build_weights(matrix, method=options["weighting"], expert=expert)
    scores = aggregation.aggregate(
        matrix,
        weights.values,
        territories,
        method=options["aggregation"],
    )
    return {"normalized": normalized, "weights": weights, "scores": scores}


def _ranking(
    scores: list[aggregation.Score],
    slugs: dict[str, str],
    comparison: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """
    Собрать строки рейтинга, при сравнении — с местом во втором расчёте.

    Смещение — «место в основном минус место в сравниваемом»: плюс — при другом способе выше.
    """
    rows: list[dict[str, Any]] = []
    positions = comparison["positions"] if comparison else {}

    for position, score in enumerate(scores, start=1):
        other = positions.get(score.code)
        rows.append(
            {
                "position": position,
                "score": score,
                "slug": slugs.get(score.code, ""),
                "other_position": other,
                # Выпавший из сравниваемого расчёта — без смещения, а не ноль.
                "shift": None if other is None else position - other,
            }
        )
    return rows


def _weight_rows(
    columns: list[dict[str, Any]],
    weights: weighting.Weights,
    expert: list[float],
) -> list[dict[str, Any]]:
    """Сопоставить показателям применённый вес и вес, введённый пользователем."""
    return [
        {
            "column": column,
            "weight": weights.values[index] if index < len(weights.values) else 0.0,
            "expert": expert[index] if index < len(expert) else 1.0,
        }
        for index, column in enumerate(columns)
    ]


def _rounded_contribution(contributions: list[float | None], index: int) -> float | None:
    """Округлить вклад показателя в сводную оценку; без значения — ``None``, а не ноль."""
    if index >= len(contributions):
        return None
    value = contributions[index]
    return None if value is None else round(value, 2)


def _contribution_option(
    scores: list[aggregation.Score],
    columns: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Построить состав оценки для верхней части рейтинга."""
    if not scores:
        return None

    top = scores[:CONTRIBUTION_LIMIT]
    names = [score.name for score in top]
    components = [
        {
            "label": str(column["short"]),
            "values": [_rounded_contribution(score.contributions, index) for score in top],
        }
        for index, column in enumerate(columns)
    ]
    return charts.contribution_option(names, components)


def _sensitivity(
    columns: list[dict[str, Any]],
    territories: list[dict[str, str]],
    options: dict[str, str],
    expert: list[float],
    baseline: list[aggregation.Score],
) -> list[dict[str, Any]]:
    """Проверить устойчивость рейтинга, пересчитав его в каждом сочетании шкалы и свёртки."""
    results: list[dict[str, Any]] = []
    for variant in SENSITIVITY_VARIANTS:
        merged = {**options, **variant}
        if merged == options:
            continue

        scores = _build(columns, territories, merged, expert)["scores"]
        shift = aggregation.rank_shift(baseline, scores)
        if not shift.get("available"):
            continue

        results.append(
            {
                # Сочетания различаются шкалой и свёрткой; веса — выбранные пользователем.
                "code": f"{merged['normalization']}:{merged['aggregation']}",
                "normalization": normalization.METHODS[merged["normalization"]],
                "aggregation": aggregation.METHODS[merged["aggregation"]],
                "shift": shift,
                "leader": scores[0].name if scores else "",
                # Места в этом сочетании — из уже выполненного расчёта.
                "positions": {score.code: position + 1 for position, score in enumerate(scores)},
                "values": {score.code: score.value for score in scores},
            }
        )
    return results
