"""
Межрегиональное неравенство: меры за год и их динамика, кривая Лоренца, разложение по округам.

Взвешивание по населению переводит вопрос с различия территорий на различие условий жизни.
Динамика по умолчанию — на постоянном составе: вход новых субъектов менял бы меры сам по себе.
"""

from __future__ import annotations

from typing import Any

from django.utils.translation import gettext_lazy as _

from apps.catalog.selectors import default_year, resolve_year, series_options
from apps.userdata.series import UserSeries
from apps.warehouse.queries import (
    MIN_YEAR_COVERAGE,
    covered_years,
    hidden_counts,
    region_panel,
    series_values_by_territory,
)

from .. import charts
from ..core import composition, inequality
from ..selectors import (
    COMPOSITION_CONSTANT,
    COMPOSITIONS,
    break_marks,
    cached_result,
    comparable_from,
    describe_composition,
    district_rows,
    population_series_key,
    region_rows,
    resolve_choice,
    resolve_float,
    resolve_one,
    resolve_span,
    series_breaks,
    too_few_regions,
)
from .base import AnalyticsView

# Способы взвешивания территорий.
WEIGHTING: dict[str, Any] = {
    "none": _("территории равнозначны"),
    "population": _("по численности населения"),
}
DEFAULT_WEIGHTING = "none"

# Меры на графике динамики, без отношения максимума к минимуму: оно скачет.
TIMELINE_MEASURES = ("gini", "theil", "cv", "decile")

# Границы параметра неприятия неравенства в индексе Аткинсона.
MIN_EPSILON = 0.1
MAX_EPSILON = 2.0

# Число территорий в перечнях крайних значений.
EXTREMES_LIMIT = 5

# Наименьшее число лет, при котором график динамики мер имеет смысл.
MIN_TIMELINE_YEARS = 2

# Меры, изменение которых за период называется числом.
CHANGE_MEASURES = ("gini", "theil", "cv")

# Подписи мер неравенства и краткое пояснение к каждой.
MEASURE_LABELS: dict[str, dict[str, Any]] = {
    "cv": {
        "title": _("Коэффициент вариации"),
        "hint": _("Отношение стандартного отклонения к среднему; простейшая мера разброса"),
        "digits": 3,
    },
    "gini": {
        "title": _("Коэффициент Джини"),
        "hint": _("От нуля при полном равенстве до единицы при полной концентрации"),
        "digits": 3,
    },
    "theil": {
        "title": _("Индекс Тейла"),
        "hint": _("Раскладывается на различия между округами и внутри округов"),
        "digits": 4,
    },
    "atkinson": {
        "title": _("Индекс Аткинсона"),
        "hint": _("Учитывает отставание нижней части тем сильнее, чем выше параметр"),
        "digits": 4,
    },
    "decile": {
        "title": _("Децильный коэффициент"),
        "hint": _("Во сколько раз девятый дециль превышает первый"),
        "digits": 2,
    },
    "range": {
        "title": _("Отношение максимума к минимуму"),
        "hint": _("Простейшая мера: определяется двумя крайними территориями"),
        "digits": 1,
    },
}


class InequalityView(AnalyticsView):
    """Меры межрегионального неравенства с разложением по федеральным округам."""

    template_name = "analytics/inequality.html"
    results_template = "analytics/partials/_inequality_results.html"
    tool_code = "inequality"

    def build_context(self) -> dict[str, Any]:
        """Собрать меры неравенства, кривую Лоренца и разложение по округам."""
        request = self.request
        series = resolve_one(request.GET.get("series"))
        context: dict[str, Any] = {
            "series_options": series_options,
            "series": series,
            "weighting_options": WEIGHTING,
            "compositions": COMPOSITIONS,
        }
        if series is None:
            return context
        if too_few_regions(series):
            context["too_few"] = True
            return context
        # Сумма из своей таблицы: неравенство сумм — различие в размере регионов.
        context["per_capita"] = (
            series.per_capita() if isinstance(series, UserSeries) and series.is_sum else None
        )

        weighting = resolve_choice(request.GET.get("weighting"), WEIGHTING, DEFAULT_WEIGHTING)
        epsilon = resolve_float(
            request.GET.get("epsilon"),
            default=inequality.DEFAULT_EPSILON,
            low=MIN_EPSILON,
            high=MAX_EPSILON,
        )
        mode = resolve_choice(request.GET.get("composition"), COMPOSITIONS, COMPOSITION_CONSTANT)

        panel = region_panel(series.key)
        years = sorted(panel)
        counts = {item: len(values) for item, values in panel.items()}
        year = resolve_year(request.GET.get("year"), years, default=default_year(counts))
        context.update(
            {
                "years": years,
                "year": year,
                "weighting": weighting,
                "epsilon": epsilon,
                "composition_mode": mode,
            }
        )
        if year is None:
            return context

        full = covered_years(counts) or years
        first_year, last_year = resolve_span(
            request.GET.get("first"),
            request.GET.get("last"),
            years,
            default=(full[0], full[-1]),
            min_span=MIN_TIMELINE_YEARS - 1,
        )

        weights_panel = _population_panel() if weighting == "population" else {}
        codes = [row["code"] for row in region_rows()]

        snapshot = _snapshot(panel.get(year, {}), weights_panel.get(year, {}), codes, epsilon)
        dynamics = cached_result(
            "inequality-timeline",
            {
                "series": series.key,
                "weighting": weighting,
                "epsilon": round(epsilon, 3),
                "first": first_year,
                "last": last_year,
                "composition": mode,
            },
            lambda: _dynamics(
                panel,
                weights_panel,
                codes,
                epsilon=epsilon,
                span=(first_year, last_year),
                mode=mode,
            ),
        )
        timeline = dynamics["timeline"]
        breaks = series_breaks(series, first_year, last_year)
        hidden = hidden_counts(series.key)
        methodological = [item for item in breaks if item["methodological"]]

        context.update(
            {
                "first_year": first_year,
                "last_year": last_year,
                "composition": describe_composition(dynamics["composition"]),
                "constant_used": dynamics["constant_used"],
                "period_change": _period_change(timeline),
                "breaks": breaks,
                "methodological_breaks": methodological,
                "breaks_consequence": _(
                    "Изменение мер за период сравнивает значения, посчитанные по разным правилам."
                ),
                "comparable_from": comparable_from(
                    methodological, last_year, MIN_TIMELINE_YEARS - 1
                ),
                "hidden": hidden,
                "hidden_in_window": any(hidden.get(row["year"]) for row in timeline),
                "hidden_year": hidden.get(year, 0),
                "bounds": _bounds(panel.get(year, {}), hidden.get(year, 0))
                if weighting == DEFAULT_WEIGHTING
                else {},
                "snapshot": snapshot,
                "lorenz_option": charts.lorenz_option(
                    snapshot["lorenz"], label=str(series.indicator.name)
                )
                if snapshot.get("lorenz")
                else None,
                "timeline": timeline,
                "timeline_option": _timeline_option(timeline, break_marks(breaks)),
                "measure_labels": MEASURE_LABELS,
                "extremes": _extremes(series.key, year),
            }
        )
        context.update(_decomposition(panel.get(year, {}), weights_panel.get(year, {})))
        return context


def _population_panel() -> dict[int, dict[str, float]]:
    """Получить значения численности населения для взвешивания."""
    key = population_series_key()
    return region_panel(key) if key else {}


def _snapshot(
    values: dict[str, float],
    weights: dict[str, float],
    codes: list[str],
    epsilon: float,
) -> dict[str, Any]:
    """Рассчитать меры неравенства за выбранный год."""
    ordered = [values.get(code) for code in codes]
    ordered_weights = [weights.get(code) for code in codes] if weights else None
    return inequality.measure_set(ordered, ordered_weights, epsilon=epsilon)


def _dynamics(
    panel: dict[int, dict[str, float]],
    weights_panel: dict[int, dict[str, float]],
    codes: list[str],
    *,
    epsilon: float,
    span: tuple[int, int],
    mode: str,
) -> dict[str, Any]:
    """
    Рассчитать меры по годам отрезка на постоянном или меняющемся составе субъектов.

    При взвешивании субъект без численности в каком-то году выпадает так же, как без значения.
    Слишком малый постоянный состав заменяется всеми доступными субъектами.
    """
    first_year, last_year = span
    usable = _with_weights(panel, weights_panel) if weights_panel else panel
    found = composition.compose(usable, first_year, last_year, share=MIN_YEAR_COVERAGE)
    constant_used = mode == COMPOSITION_CONSTANT and found.is_usable
    if constant_used:
        chosen = composition.restrict(usable, found)
    else:
        chosen = {year: usable[year] for year in usable if first_year <= year <= last_year}
    return {
        "timeline": _timeline(chosen, weights_panel, codes, epsilon),
        "composition": found,
        "constant_used": constant_used,
    }


def _with_weights(
    panel: dict[int, dict[str, float]], weights_panel: dict[int, dict[str, float]]
) -> dict[int, dict[str, float]]:
    """Оставить субъекты, у которых в том же году есть и значение, и численность."""
    return {
        year: {
            code: value for code, value in values.items() if weights_panel.get(year, {}).get(code)
        }
        for year, values in panel.items()
    }


def _timeline(
    panel: dict[int, dict[str, float]],
    weights_panel: dict[int, dict[str, float]],
    codes: list[str],
    epsilon: float,
) -> list[dict[str, Any]]:
    """Рассчитать меры неравенства по годам панели вместе с числом территорий по годам."""
    rows: list[dict[str, Any]] = []
    for year in sorted(panel):
        result = _snapshot(panel[year], weights_panel.get(year, {}), codes, epsilon)
        if not result.get("available"):
            continue
        rows.append(
            {
                "year": year,
                "count": result["count"],
                "mean": result["mean"],
                "measures": {code: measure.value for code, measure in result["by_code"].items()},
            }
        )
    return rows


def _period_change(timeline: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Изменение мер между первым и последним годом динамики, в долях от первого года."""
    if len(timeline) < MIN_TIMELINE_YEARS:
        return None
    first, last = timeline[0], timeline[-1]
    measures = []
    for code in CHANGE_MEASURES:
        start, end = first["measures"].get(code), last["measures"].get(code)
        if start is None or end is None or start == 0:
            continue
        measures.append(
            {
                "code": code,
                "title": MEASURE_LABELS[code]["title"],
                "digits": MEASURE_LABELS[code]["digits"],
                "start": start,
                "end": end,
                "change": end / start - 1,
            }
        )
    if not measures:
        return None
    return {
        "first_year": first["year"],
        "last_year": last["year"],
        "count": last["count"],
        "measures": measures,
    }


def _bounds(values: dict[str, float], hidden: int) -> dict[str, Any]:
    """Границы мер за год, если часть значений скрыта источником; территории равнозначны."""
    found = inequality.hidden_bounds(list(values.values()), hidden)
    return {
        code: {
            "title": MEASURE_LABELS[code]["title"],
            "digits": MEASURE_LABELS[code]["digits"],
            "bounds": bounds,
        }
        for code, bounds in found.items()
    }


def _timeline_option(
    timeline: list[dict[str, Any]], marks: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Построить график динамики мер, приведённых к первому году: шкалы мер несопоставимы."""
    if len(timeline) < MIN_TIMELINE_YEARS:
        return None

    years = [row["year"] for row in timeline]
    lines: list[dict[str, Any]] = []

    for code in TIMELINE_MEASURES:
        values = [row["measures"].get(code) for row in timeline]
        base = next((value for value in values if value not in (None, 0)), None)
        if base is None:
            continue
        lines.append(
            {
                "name": MEASURE_LABELS[code]["title"],
                "values": [
                    round(value / base * 100, 2) if value is not None else None for value in values
                ],
            }
        )

    # Разрыв в первом году графика ничего не разделяет.
    shown = [item for item in marks if item["year"] in years[1:]]
    return charts.measures_timeline_option(years, lines, breaks=shown) if lines else None


def _decomposition(
    values: dict[str, float],
    weights: dict[str, float],
) -> dict[str, Any]:
    """Разложить индекс Тейла по федеральным округам."""
    rows = region_rows()
    codes = [row["code"] for row in rows]
    districts = [row["district_code"] for row in rows]
    labels = {item["code"]: item["name"] for item in district_rows()}

    array, weight_array = inequality.prepare(
        [values.get(code) for code in codes],
        [weights.get(code) for code in codes] if weights else None,
    )
    # Тот же отбор, что в расчёте, иначе округа разойдутся со значениями по длине.
    usable_districts = [
        districts[index]
        for index, code in enumerate(codes)
        if values.get(code) is not None and (not weights or weights.get(code))
    ]

    decomposition = inequality.theil_decomposition(
        array, weight_array, usable_districts, labels=labels
    )
    return {
        "decomposition": decomposition,
        "decomposition_option": charts.decomposition_option(decomposition.groups)
        if decomposition and decomposition.groups
        else None,
    }


def _extremes(series_key: str, year: int) -> dict[str, Any]:
    """Получить территории с наибольшими и наименьшими значениями за год."""
    slugs = {row["code"]: row["slug"] for row in region_rows()}
    rows = [
        {**row, "slug": slugs.get(row["territory_code"], "")}
        for row in series_values_by_territory(series_key, year)
        if row["value"] is not None
    ]
    rows.sort(key=lambda row: row["value"], reverse=True)
    return {"top": rows[:EXTREMES_LIMIT], "bottom": rows[-EXTREMES_LIMIT:][::-1]}
