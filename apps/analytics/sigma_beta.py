"""
Сигма- и бета-конвергенция по ряду склада: отрезок по умолчанию, разброс на постоянном
составе, значения пар лет, выводы одним словом и проверка устойчивости к составу.

Общие для инструмента «Конвергенция» и карточки исследования «Сближаются ли регионы»:
вывод карточки не должен расходиться с инструментом, куда она ведёт.
"""

from __future__ import annotations

from typing import Any

from apps.warehouse.queries import MIN_YEAR_COVERAGE, covered_years, paired_years

from .core import composition, convergence
from .selectors import COMPOSITION_CONSTANT, robustness_excluded, without

# Постановка бета-конвергенции по умолчанию: все регионы сходятся к общему уровню.
DEFAULT_MODE = "absolute"

# Вывод о сближении одним словом.
VERDICT_CONVERGING = "converging"
VERDICT_DIVERGING = "diverging"
VERDICT_NONE = "none"
VERDICT_UNAVAILABLE = "unavailable"


def default_span(panel: dict[int, dict[str, float]], years: list[int]) -> tuple[int, int]:
    """Отрезок по умолчанию — от первого до последнего полного года, если он не слишком короток."""
    full = covered_years({year: len(values) for year, values in panel.items()})
    if full and full[-1] - full[0] >= convergence.MIN_SPAN_YEARS:
        return full[0], full[-1]
    return years[0], years[-1]


def sigma(
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


def observations(series_key: str, first_year: int, last_year: int) -> list[dict[str, Any]]:
    """Значения субъектов в начальном и конечном году отрезка."""
    return [
        {
            "code": row["territory_code"],
            "name": row["name"],
            "district": row["district_code"] or "",
            "start": row["start_value"],
            "end": row["end_value"],
        }
        for row in paired_years(series_key, first_year, last_year)
    ]


def beta(
    items: list[dict[str, Any]], first_year: int, last_year: int, mode: str = DEFAULT_MODE
) -> convergence.BetaResult | None:
    """Оценить бета-конвергенцию по значениям пар лет."""
    return convergence.beta_convergence(
        items,
        first_year=first_year,
        last_year=last_year,
        conditional=mode == "conditional",
    )


def sigma_verdict(trend: dict[str, Any]) -> str:
    """Вывод о разбросе: сокращается, растёт, без устойчивого изменения."""
    if not trend.get("available"):
        return VERDICT_UNAVAILABLE
    if trend["converging"]:
        return VERDICT_CONVERGING
    return VERDICT_DIVERGING if trend["diverging"] else VERDICT_NONE


def beta_verdict(result: convergence.BetaResult | None) -> str:
    """Вывод о догоняющем росте: сближение, расхождение, связь не подтверждена."""
    if result is None:
        return VERDICT_UNAVAILABLE
    if result.is_converging:
        return VERDICT_CONVERGING
    if result.beta > 0 and result.fit.slope.is_significant:
        return VERDICT_DIVERGING
    return VERDICT_NONE


def robustness(
    panel: dict[int, dict[str, float]],
    series_key: str,
    span: tuple[int, int],
    membership: str,
    mode: str,
) -> dict[str, Any]:
    """
    Выводы о сигма- и бета-конвергенции без Москвы с областью и без Северного Кавказа.

    Вывод устойчив, если в каждом составе он тот же (составы без оценки не учитываются).
    """
    first_year, last_year = span
    pairs = observations(series_key, first_year, last_year)
    rows: list[dict[str, Any]] = []
    for variant, excluded in robustness_excluded().items():
        points = sigma(without(panel, excluded), first_year, last_year, membership)["points"]
        result = beta(
            [item for item in pairs if item["code"] not in excluded], first_year, last_year, mode
        )
        rows.append(
            {
                "variant": variant,
                "count": result.fit.observations if result else (points[-1].count if points else 0),
                "sigma": sigma_verdict(convergence.sigma_trend(points)),
                "beta": beta_verdict(result),
                "beta_value": result.beta if result else None,
                "beta_p": result.fit.slope.p_value if result else None,
                "half_life": result.half_life if result else None,
            }
        )
    stable = all(
        len({row[name] for row in rows if row[name] != VERDICT_UNAVAILABLE}) <= 1
        for name in ("sigma", "beta")
    )
    return {"rows": rows, "stable": stable}
