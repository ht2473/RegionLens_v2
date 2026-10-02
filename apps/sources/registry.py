"""Справочник рядов внешних источников и помесячного слоя (``source_series.json``)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from django.conf import settings
from django.utils.translation import get_language

ANNUAL_METHODS = frozenset({"sum", "december", "january_next", "weighted_mean", "ratio"})
MONTH_KINDS = frozenset({"level", "ytd", "yoy"})
COMPARE_WAYS = frozenset({"percent", "points"})
# Знаменатель «численность населения» в отношениях рядов.
POPULATION = "population"


def _localized(ru: str, en: str) -> str:
    return en if get_language() == "en" and en else ru


@dataclass(frozen=True, slots=True)
class Unit:
    """Единица ряда проекта."""

    code: str
    name_ru: str
    short_ru: str
    name_en: str
    short_en: str
    kind: str

    @property
    def short(self) -> str:
        """Краткая подпись на языке запроса."""
        return _localized(self.short_ru, self.short_en)


@dataclass(frozen=True, slots=True)
class Ratio:
    """Отношение двух величин: ряд к ряду или ряд к численности населения."""

    numerator: str
    denominator: str
    factor: float


@dataclass(frozen=True, slots=True)
class SourceSeries:
    """Ряд проекта из внешнего источника: как строятся месяцы и годовое значение."""

    key: str
    indicator: str
    section: str
    source: str
    unit: Unit
    name_ru: str
    name_en: str
    short_ru: str
    short_en: str
    annual: str
    compare: str
    precision: int
    measure: str = ""
    ratio: Ratio | None = None
    weights: str = ""
    flow: bool = False
    country_sum: bool = False

    @property
    def short_title(self) -> str:
        """Краткое название на языке запроса."""
        return _localized(self.short_ru, self.short_en)

    @property
    def kinds(self) -> tuple[str, ...]:
        """Виды месячных значений ряда."""
        return ("level", "ytd") if self.flow else ("level",)


@dataclass(frozen=True, slots=True)
class DatasetMonthly:
    """Помесячный слой ряда набора из показателя бюллетеня."""

    series: str
    source: str
    measure: str
    period_kind: str
    kind: str
    compare: str


@dataclass(frozen=True, slots=True)
class Section:
    """Раздел каталога для рядов проекта."""

    code: str
    source: str
    name_ru: str
    name_en: str


@dataclass(frozen=True, slots=True)
class Registry:
    """Справочник целиком."""

    population_total: str
    population_per_capita: str
    population_scale: float
    sections: tuple[Section, ...]
    units: dict[str, Unit]
    series: tuple[SourceSeries, ...]
    dataset_monthly: tuple[DatasetMonthly, ...]
    now: tuple[str, ...]
    by_key: dict[str, SourceSeries] = field(default_factory=dict)

    def monthly_keys(self) -> set[str]:
        """Ряды, у которых есть помесячный слой."""
        return {item.key for item in self.series} | {item.series for item in self.dataset_monthly}

    def compare_of(self, series_key: str) -> str:
        """Как сравнивать значение ряда с тем же периодом прошлого года."""
        found = self.by_key.get(series_key)
        if found is not None:
            return found.compare
        for item in self.dataset_monthly:
            if item.series == series_key:
                return item.compare
        return "percent"


@lru_cache(maxsize=1)
def registry() -> Registry:
    """Прочитать справочник; кэшируется на время жизни процесса."""
    path = settings.REFERENCE_DIR / "source_series.json"
    payload = json.loads(path.read_bytes().decode("utf-8"))
    units = {
        code: Unit(
            code=code,
            name_ru=item["name_ru"],
            short_ru=item["short_ru"],
            name_en=item["name_en"],
            short_en=item["short_en"],
            kind=item["kind"],
        )
        for code, item in payload["units"].items()
    }
    series = tuple(_series(item, units) for item in payload["series"])
    dataset_monthly = tuple(DatasetMonthly(**item) for item in payload["dataset_monthly"])
    for entry in dataset_monthly:
        if entry.kind not in MONTH_KINDS or entry.compare not in COMPARE_WAYS:
            raise ValueError(
                f"{entry.series}: неизвестный вид «{entry.kind}» или «{entry.compare}»"
            )
    population = payload["population"]
    return Registry(
        population_total=population["total"],
        population_per_capita=population["per_capita"],
        population_scale=float(population["scale"]),
        sections=tuple(Section(**item) for item in payload["sections"]),
        units=units,
        series=series,
        dataset_monthly=dataset_monthly,
        now=tuple(payload.get("now", [])),
        by_key={item.key: item for item in series},
    )


def _series(item: dict[str, Any], units: dict[str, Unit]) -> SourceSeries:
    monthly = item["monthly"]
    ratio = monthly.get("ratio")
    result = SourceSeries(
        key=item["key"],
        indicator=item["indicator"],
        section=item["section"],
        source=item["source"],
        unit=units[item["unit"]],
        name_ru=item["name_ru"],
        name_en=item["name_en"],
        short_ru=item["short_ru"],
        short_en=item["short_en"],
        annual=item["annual"],
        compare=item["compare"],
        precision=int(item.get("precision", 1)),
        measure=monthly.get("measure", ""),
        ratio=Ratio(ratio["numerator"], ratio["denominator"], float(ratio["factor"]))
        if ratio
        else None,
        weights=monthly.get("weights", ""),
        flow=bool(monthly.get("flow")),
        country_sum=monthly.get("country") == "sum_regions",
    )
    if result.annual not in ANNUAL_METHODS or result.compare not in COMPARE_WAYS:
        raise ValueError(
            f"{result.key}: неизвестный способ «{result.annual}» или «{result.compare}»"
        )
    if bool(result.measure) == bool(result.ratio):
        raise ValueError(f"{result.key}: нужен ровно один из способов — measure или ratio")
    return result
