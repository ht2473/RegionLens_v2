"""
Сближение регионов: сигма- и бета-конвергенция рядом, даже когда они расходятся.

Границы отрезка выбирает пользователь: вывод о сближении к ним чувствителен.
Разброс по умолчанию — на постоянном составе, как динамика мер неравенства.
"""

from __future__ import annotations

from typing import Any

from django.utils.translation import gettext_lazy as _

from apps.catalog.selectors import series_options
from apps.core.charts import timeline_option
from apps.warehouse.queries import MIN_YEAR_COVERAGE, covered_years, paired_years, region_panel

from .. import charts
from ..core import composition, convergence
from ..selectors import (
    COMPOSITION_CONSTANT,
    COMPOSITIONS,
    break_marks,
    cached_result,
    comparable_from,
    describe_composition,
    district_rows,
    resolve_choice,
    resolve_one,
    resolve_span,
    series_breaks,
    too_few_regions,
)
from .base import AnalyticsView

# Постановки задачи о бета-конвергенции.
MODES: dict[str, Any] = {
    "absolute": _("абсолютная: все регионы сходятся к общему уровню"),
    "conditional": _("условная: регионы сходятся к уровню своего округа"),
}
DEFAULT_MODE = "absolute"

# Число территорий в перечнях наибольшего и наименьшего роста.
EXTREMES_LIMIT = 6


class ConvergenceView(AnalyticsView):
    """Проверка сближения регионов по разбросу значений и по темпам роста."""

    template_name = "analytics/convergence.html"
    results_template = "analytics/partials/_convergence_results.html"
    tool_code = "convergence"

    def build_context(self) -> dict[str, Any]:
        """Собрать сигма- и бета-конвергенцию за выбранный отрезок времени."""
        request = self.request
        series = resolve_one(request.GET.get("series"))
        context: dict[str, Any] = {
            "series_options": series_options,
            "series": series,
            "modes": MODES,
            "compositions": COMPOSITIONS,
        }
        if series is None:
            return context
        if too_few_regions(series):
            context["too_few"] = True
            return context

        panel = region_panel(series.key)
        years = sorted(panel)
        if len(years) < convergence.MIN_SPAN_YEARS:
            context["years"] = years
            return context

        first_year, last_year = resolve_span(
            request.GET.get("first"),
            request.GET.get("last"),
            years,
            default=_default_span(panel, years),
            min_span=convergence.MIN_SPAN_YEARS,
        )
        mode = resolve_choice(request.GET.get("mode"), MODES, DEFAULT_MODE)
        membership = resolve_choice(
            request.GET.get("composition"), COMPOSITIONS, COMPOSITION_CONSTANT
        )

        sigma = cached_result(
            "convergence-sigma",
            {
                "series": series.key,
                "first": first_year,
                "last": last_year,
                "composition": membership,
            },
            lambda: _sigma(panel, first_year, last_year, membership),
        )
        window = sigma["points"]
        breaks = series_breaks(series, first_year, last_year)
        methodological = [item for item in breaks if item["methodological"]]

        beta = _beta(series.key, first_year, last_year, mode)
        context.update(
            {
                "years": years,
                "first_year": first_year,
                "last_year": last_year,
                "mode": mode,
                "composition_mode": membership,
                "composition": describe_composition(sigma["composition"]),
                "constant_used": sigma["constant_used"],
                "breaks": breaks,
                "methodological_breaks": methodological,
                "breaks_consequence": _(
                    "Разброс и темпы роста за период сравнивают значения, "
                    "посчитанные по разным правилам."
                ),
                "comparable_from": comparable_from(
                    methodological, last_year, convergence.MIN_SPAN_YEARS
                ),
                "sigma": window,
                "sigma_trend": convergence.sigma_trend(window),
                "sigma_option": _sigma_option(window, break_marks(breaks)),
                "beta": beta,
                "beta_option": _beta_option(beta),
                "growth": _growth(beta),
                "districts": {item["code"]: item["name"] for item in district_rows()},
            }
        )
        return context


def _default_span(panel: dict[int, dict[str, float]], years: list[int]) -> tuple[int, int]:
    """Отрезок по умолчанию — от первого до последнего полного года, если он не слишком короток."""
    full = covered_years({year: len(values) for year, values in panel.items()})
    if full and full[-1] - full[0] >= convergence.MIN_SPAN_YEARS:
        return full[0], full[-1]
    return years[0], years[-1]


def _sigma(
    panel: dict[int, dict[str, float]], first_year: int, last_year: int, membership: str
) -> dict[str, Any]:
    """Разброс по годам отрезка на постоянном или меняющемся составе субъектов."""
    found = composition.compose(panel, first_year, last_year, share=MIN_YEAR_COVERAGE)
    constant_used = membership == COMPOSITION_CONSTANT and found.is_usable
    if constant_used:
        chosen = composition.restrict(panel, found)
    else:
        chosen = {year: panel[year] for year in panel if first_year <= year <= last_year}
    points = convergence.sigma_series(
        {year: list(values.values()) for year, values in chosen.items()}
    )
    return {"points": points, "composition": found, "constant_used": constant_used}


def _beta(
    series_key: str, first_year: int, last_year: int, mode: str
) -> convergence.BetaResult | None:
    """Оценить бета-конвергенцию на выбранном отрезке."""
    observations = [
        {
            "code": row["territory_code"],
            "name": row["name"],
            "district": row["district_code"] or "",
            "start": row["start_value"],
            "end": row["end_value"],
        }
        for row in paired_years(series_key, first_year, last_year)
    ]
    return convergence.beta_convergence(
        observations,
        first_year=first_year,
        last_year=last_year,
        conditional=mode == "conditional",
    )


def _sigma_option(
    points: list[convergence.SigmaPoint], marks: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Построить график стандартного отклонения логарифмов по годам с отметками разрывов."""
    if not points:
        return None

    years = [point.year for point in points]
    lines: list[dict[str, Any]] = [
        {
            "name": str(_("Стандартное отклонение логарифмов")),
            "values": [round(point.std_log, 4) for point in points],
            "width": 2.5,
        },
        {
            "name": str(_("Коэффициент вариации")),
            "values": [
                round(point.coef_variation, 4) if point.coef_variation is not None else None
                for point in points
            ],
        },
    ]
    # Разрыв в первом году графика ничего не разделяет.
    shown = [item for item in marks if item["year"] in years[1:]]
    return timeline_option(years, lines, breaks=shown)


def _beta_option(result: convergence.BetaResult | None) -> dict[str, Any] | None:
    """Построить диаграмму темпа роста от исходного уровня; линия — только при значимой связи."""
    if result is None or not result.points:
        return None

    line = None
    if result.fit.slope.is_significant:
        low = min(point["x"] for point in result.points)
        high = max(point["x"] for point in result.points)
        values = result.fit.predict([low, high])
        line = [[low, values[0]], [high, values[1]]]

    return charts.scatter_fit_option(
        result.points,
        x_name=str(_("логарифм значения в начальном году")),
        y_name=str(_("среднегодовой темп роста")),
        line=line,
        line_label=str(_("подобранная зависимость")),
    )


def _growth(result: convergence.BetaResult | None) -> dict[str, Any]:
    """Отобрать территории с наибольшим и наименьшим темпом роста."""
    if result is None:
        return {"fastest": [], "slowest": []}

    ordered = sorted(result.points, key=lambda point: point["y"], reverse=True)
    return {
        "fastest": ordered[:EXTREMES_LIMIT],
        "slowest": ordered[-EXTREMES_LIMIT:][::-1],
    }
