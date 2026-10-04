"""
Шаг «Показатели»: название, единица, вид величины, направленность и пересчёт на жителя.

Описание задаётся на показатель и действует на все его ряды (разрезы и периоды года).
Подсказки — по единице и названию; решения человека хранятся в рецепте версии.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from django.db import transaction

from apps.catalog.constants import is_unnormalised_title

from .models import DatasetSeries, DatasetVersion

SUM = DatasetSeries.Kind.SUM.value
RELATIVE = DatasetSeries.Kind.RELATIVE.value
KINDS = (SUM, RELATIVE)
POLARITIES = tuple(choice.value for choice in DatasetSeries.Polarity)
# Пересчёты суммы: на жителей и на площадь.
PER_CHOICES = (
    DatasetSeries.Derived.PER_1000.value,
    DatasetSeries.Derived.PER_100000.value,
    DatasetSeries.Derived.PER_KM2.value,
)
# Пересчёт, предложенный по умолчанию: «на 100 000 жителей» — как у карт преступности
# и заболеваемости.
DEFAULT_PER = (DatasetSeries.Derived.PER_100000.value,)
TITLE_LENGTH = 300
UNIT_LENGTH = 120

# Единица относительной величины: доля, коэффициент, на жителя, годы жизни.
_RELATIVE_UNIT = re.compile(
    r"(%|процент|промилле|‰|на\s*1\b|на\s*10\b|на\s*1\s?000|на\s*10\s?000|на\s*100|на душу|"
    r"на жител|на одного|на 1 жител|коэффициент|индекс|балл|\bлет\b|\bгод|\bраз\b|"
    r"per\s|percent|ratio|index|years)",
    re.IGNORECASE,
)
# Название относительной величины: среднее (не среднегодовая численность — она складывается),
# доля, уровень, на жителя.
_RELATIVE_NAME = re.compile(
    r"(средн(?!егодов|есписочн)|в среднем|на душу|на жител|на одного|на 1 |на 10 |на 100|"
    r"на 1 ?000|доля|удельн|уровень|коэффициент|индекс|темп|отношени|плотност|"
    r"продолжительност|average|mean|per capita|share|rate|ratio)",
    re.IGNORECASE,
)
# Единица суммы: люди, штуки, деньги и объёмы целиком.
_SUM_UNIT = re.compile(
    r"(человек|чел\.|единиц|штук|шт\.|тонн|\bт\b|млн|млрд|тыс|кв\.\s?м|\bга\b|гектар|"
    r"случа|people|persons|units|cases|thousand|million|billion)",
    re.IGNORECASE,
)


@dataclass(slots=True)
class Indicator:
    """Показатель набора с описанием: подсказки и решения человека."""

    name: str
    title: str
    unit: str
    kind: str
    polarity: str
    per: tuple[str, ...]
    series: int = 0
    note: str = ""
    units: list[str] = field(default_factory=list)
    regions: int = 0
    first_year: int | None = None
    last_year: int | None = None

    @property
    def is_sum(self) -> bool:
        return self.kind == SUM


def guess_kind(name: str, unit: str) -> str:
    """Вид величины по единице и названию: сумма складывается по регионам, остальное — нет."""
    if _RELATIVE_UNIT.search(unit or "") or _RELATIVE_NAME.search(name or ""):
        return RELATIVE
    if _SUM_UNIT.search(unit or ""):
        return SUM
    return SUM if is_unnormalised_title(f"{name} {unit}", "unknown") else RELATIVE


def indicators_of(version: DatasetVersion) -> list[Indicator]:
    """Показатели извлечённой таблицы с описанием из рецепта или подсказками."""
    report = version.report.get("extract") or {}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in report.get("series", []):
        grouped[item["indicator"]].append(item)
    answers: Mapping[str, Any] = version.recipe.get("indicators") or {}
    notes: Mapping[str, str] = report.get("notes") or {}
    result = []
    for name, items in grouped.items():
        units = Counter(item["unit"] for item in items if item["unit"])
        unit = units.most_common(1)[0][0] if units else ""
        answer = answers.get(name) or {}
        kind = answer.get("kind") or guess_kind(name, unit)
        years = [item[key] for item in items for key in ("first_year", "last_year") if item[key]]
        result.append(
            Indicator(
                name=name,
                title=answer.get("title") or name,
                unit=answer.get("unit", unit),
                kind=kind,
                polarity=answer.get("polarity") or DatasetSeries.Polarity.NEUTRAL.value,
                per=tuple(answer["per"])
                if "per" in answer
                else (DEFAULT_PER if kind == SUM else ()),
                series=len(items),
                note=notes.get(name, ""),
                units=[value for value, _count in units.most_common()],
                regions=max((item["regions"] for item in items), default=0),
                first_year=min(years) if years else None,
                last_year=max(years) if years else None,
            )
        )
    return result


def save(version: DatasetVersion, answers: Mapping[str, Mapping[str, Any]]) -> None:
    """Записать описание показателей в рецепт; неизвестные показатели и значения отбрасываются."""
    known = {item.name for item in indicators_of(version)}
    cleaned: dict[str, dict[str, Any]] = {}
    for name, answer in answers.items():
        if name not in known:
            continue
        kind = answer.get("kind") if answer.get("kind") in KINDS else RELATIVE
        polarity = answer.get("polarity")
        cleaned[name] = {
            "title": " ".join(str(answer.get("title") or name).split())[:TITLE_LENGTH] or name,
            "unit": " ".join(str(answer.get("unit") or "").split())[:UNIT_LENGTH],
            "kind": kind,
            "polarity": polarity
            if polarity in POLARITIES
            else DatasetSeries.Polarity.NEUTRAL.value,
            "per": [item for item in answer.get("per") or [] if item in PER_CHOICES]
            if kind == SUM
            else [],
        }
    with transaction.atomic():
        version.refresh_from_db(fields=["recipe"])
        version.recipe = {**version.recipe, "indicators": cleaned}
        version.save(update_fields=["recipe", "updated_at"])
