"""
Годовые значения рядов склада из разобранного выпуска — по способу из ``source_links.json``.

Способы: взять опубликованное годовое значение (год, нарастающий итог за год, декабрь,
IV квартал) или рассчитать (среднее месяцев или кварталов, реальная зарплата, значение
на жителя). Скрытая составляющая скрывает результат, недостающая — убирает его.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import pandas as pd
from django.conf import settings

# Столбцы результата.
ANNUAL_COLUMNS = (
    "territory_code",
    "year",
    "value",
    "hidden",
    "preliminary",
    "computed",
    "frozen",
    "base_year",
)

PUBLISHED_PERIODS: dict[str, tuple[str, int]] = {
    "annual": ("annual", 12),
    "year_total": ("ytd", 12),
    "december": ("month", 12),
    "quarter_end": ("quarter", 4),
}
MEAN_PERIODS: dict[str, tuple[str, int]] = {
    "mean_months": ("month", 12),
    "mean_quarters": ("quarter", 4),
}
METHODS = frozenset({*PUBLISHED_PERIODS, *MEAN_PERIODS, "real_wage", "per_capita"})


@dataclass(frozen=True, slots=True)
class Link:
    """Связь ряда склада с показателем источника."""

    series: str
    source: str
    measure: str
    method: str
    tolerance_relative: float | None = None
    tolerance_points: float | None = None
    deflator: str = ""  # показатель цен для реальной зарплаты
    total_series: str = ""  # итоговый ряд склада для знаменателя на жителя

    @property
    def is_computed(self) -> bool:
        """Значение рассчитывается, а не берётся опубликованным."""
        return self.method not in PUBLISHED_PERIODS

    def within(self, source_value: float, dataset_value: float) -> bool:
        """Значения источника и набора совпадают в пределах допуска связи."""
        if self.tolerance_points is not None:
            return abs(source_value - dataset_value) <= self.tolerance_points
        tolerance = self.tolerance_relative if self.tolerance_relative is not None else 0.0
        if dataset_value == 0:
            return source_value == 0
        return abs(source_value - dataset_value) / abs(dataset_value) <= tolerance

    def deviation(self, source_value: float, dataset_value: float) -> float:
        """Расхождение в единицах допуска: пунктах или долях."""
        if self.tolerance_points is not None:
            return abs(source_value - dataset_value)
        if dataset_value == 0:
            return 0.0 if source_value == 0 else float("inf")
        return abs(source_value - dataset_value) / abs(dataset_value)


@lru_cache(maxsize=1)
def links() -> tuple[Link, ...]:
    """Связи из справочника; кэшируются на время жизни процесса."""
    path = settings.REFERENCE_DIR / "source_links.json"
    payload = json.loads(path.read_bytes().decode("utf-8"))
    found = []
    for item in payload["links"]:
        tolerance = item.get("tolerance", {})
        link = Link(
            series=item["series"],
            source=item["source"],
            measure=item["measure"],
            method=item["method"],
            tolerance_relative=tolerance.get("relative"),
            tolerance_points=tolerance.get("points"),
            deflator=item.get("deflator", ""),
            total_series=item.get("total_series", ""),
        )
        if link.method not in METHODS:
            raise ValueError(f"{link.series}: неизвестный способ «{link.method}»")
        found.append(link)
    return tuple(found)


def derive(
    frame: pd.DataFrame,
    link: Link,
    denominators: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    Годовые значения ряда по связи из разобранного выпуска.

    ``denominators`` — численность из набора (``territory_code``, ``year``, ``population``)
    для способа «на жителя»: за годы без неё берётся последний известный год.
    """
    measure = frame[frame["measure"] == link.measure]
    if link.method in PUBLISHED_PERIODS:
        kind, number = PUBLISHED_PERIODS[link.method]
        result = _pick(measure, kind, number)
    elif link.method in MEAN_PERIODS:
        kind, count = MEAN_PERIODS[link.method]
        result = _mean(measure, kind, count)
    elif link.method == "real_wage":
        result = _real_wage(measure, frame[frame["measure"] == link.deflator])
    else:
        result = _per_capita(_pick(measure, "ytd", 12), denominators)
    if not result.empty and link.is_computed:
        result["computed"] = True
    return result.reindex(columns=list(ANNUAL_COLUMNS))


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=list(ANNUAL_COLUMNS))


def _pick(measure: pd.DataFrame, kind: str, number: int) -> pd.DataFrame:
    """Опубликованные значения одного периода."""
    rows = measure[(measure["period_kind"] == kind) & (measure["period"] == number)]
    if rows.empty:
        return _empty()
    result = rows[["territory_code", "year", "value", "hidden", "preliminary"]].copy()
    result["computed"] = False
    result["frozen"] = False
    result["base_year"] = pd.NA
    return result.reset_index(drop=True)


def _mean(measure: pd.DataFrame, kind: str, count: int) -> pd.DataFrame:
    """Среднее всех периодов года; год без полного набора периодов не рассчитывается."""
    rows = measure[measure["period_kind"] == kind]
    if rows.empty:
        return _empty()
    grouped = rows.groupby(["territory_code", "year"], as_index=False).agg(
        periods=("period", "nunique"),
        known=("value", "count"),
        value=("value", "mean"),
        hidden=("hidden", "any"),
        preliminary=("preliminary", "any"),
    )
    grouped = grouped[grouped["periods"] == count]
    # Скрытый месяц скрывает год, пропущенный — убирает его.
    complete = (grouped["known"] == count) | grouped["hidden"]
    grouped = grouped[complete].copy()
    grouped.loc[grouped["hidden"], "value"] = None
    grouped["computed"] = True
    grouped["frozen"] = False
    grouped["base_year"] = pd.NA
    return grouped.drop(columns=["periods", "known"]).reset_index(drop=True)


def _real_wage(wages: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    """
    Индекс реальной зарплаты к прошлому году: рост средней номинальной, делённый на рост
    среднегодовых цен, как считает Росстат.

    Среднегодовой рост цен выводится из индексов «к декабрю прошлого года» по месяцам.
    """
    nominal = _mean(wages, "month", 12).set_index(["territory_code", "year"])
    cpi = prices[prices["period_kind"] == "month"]
    if nominal.empty or cpi.empty:
        return _empty()
    by_year = cpi.groupby(["territory_code", "year"]).agg(
        months=("period", "nunique"),
        mean=("value", "mean"),
        preliminary=("preliminary", "any"),
    )
    december = cpi[cpi["period"] == 12].set_index(["territory_code", "year"])["value"]  # noqa: PLR2004
    rows = []
    for (territory, year), current in nominal.iterrows():
        previous_key = (territory, year - 1)
        if previous_key not in nominal.index or (territory, year) not in by_year.index:
            continue
        if previous_key not in by_year.index or previous_key not in december.index:
            continue
        prices_now, prices_before = by_year.loc[(territory, year)], by_year.loc[previous_key]
        if prices_now["months"] != 12 or prices_before["months"] != 12:  # noqa: PLR2004
            continue
        previous = nominal.loc[previous_key]
        hidden = bool(current["hidden"] or previous["hidden"])
        value = None
        if not hidden:
            # Отношение среднегодовых уровней цен: декабрь прошлого года связывает базы.
            price_growth = (
                december.loc[previous_key] * prices_now["mean"] / (100 * prices_before["mean"])
            )
            value = 100 * (current["value"] / previous["value"]) / price_growth
        rows.append(
            {
                "territory_code": territory,
                "year": year,
                "value": value,
                "hidden": hidden,
                "preliminary": bool(current["preliminary"] or prices_now["preliminary"]),
                "computed": True,
                "frozen": False,
                "base_year": pd.NA,
            }
        )
    return pd.DataFrame(rows, columns=list(ANNUAL_COLUMNS)) if rows else _empty()


def _per_capita(totals: pd.DataFrame, denominators: pd.DataFrame | None) -> pd.DataFrame:
    """
    Значение на жителя: итог источника, делённый на численность набора.

    Росстат не публикует численность по субъектам после 2024 года: за более поздние
    годы знаменатель — численность последнего известного года (отметка ``frozen``).
    """
    if totals.empty or denominators is None or denominators.empty:
        return _empty()
    population = denominators.dropna(subset=["population"])
    by_territory: dict[str, pd.DataFrame] = {
        code: rows.sort_values("year") for code, rows in population.groupby("territory_code")
    }
    rows: list[dict[str, Any]] = []
    for item in totals.itertuples(index=False):
        known = by_territory.get(item.territory_code)
        if known is None:
            continue
        earlier = known[known["year"] <= item.year]
        if earlier.empty:
            continue
        base = earlier.iloc[-1]
        base_year = int(base["year"])
        value = None if item.hidden or item.value is None else item.value / base["population"]
        rows.append(
            {
                "territory_code": item.territory_code,
                "year": int(item.year),
                "value": value,
                "hidden": bool(item.hidden),
                "preliminary": bool(item.preliminary),
                "computed": True,
                "frozen": base_year < int(item.year),
                "base_year": base_year if base_year < int(item.year) else pd.NA,
            }
        )
    return pd.DataFrame(rows, columns=list(ANNUAL_COLUMNS)) if rows else _empty()
