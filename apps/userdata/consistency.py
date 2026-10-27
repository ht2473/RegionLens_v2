"""
Проверки данных таблицы до сборки: нули, похожие на пропуски, и согласованность.

Правила — постоянными ниже. Сумма регионов против строки «Российская Федерация» и части
против итога — правила, которые в верной таблице выполняются; скачок разряда между годами
и резкий скачок одного региона (``unusual``) — подозрения. Все дают замечание «проверьте»
с местом и сборку не останавливают; какие из них показывать, решает шаг «Проверка» по типу
величины (суммы складываются, доли — нет; скачок региона — у любой величины).

Нули, похожие на пропуски, — вопрос на шаге «Проверка»: у отмеченного показателя нули при
сборке становятся пропусками (``build``). Учитываются только нули, записанные числом: ноль
из прочерка и «0,0» — признаки источника, а не заглушки.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from apps.catalog.constants import ValueQuality

from . import matching, unusual

if TYPE_CHECKING:
    from .extract import Collector

# Нулей больше этой доли значений показателя — вопрос.
ZERO_SHARE = 0.3
# Год, в котором у всех регионов ноль, — если регионов в нём не меньше стольких.
ALL_ZERO_REGIONS = 5
# Сумма регионов расходится со строкой страны больше чем на эту долю — замечание.
SUM_GAP = 0.01
# Сумма считается по годам, где субъектов почти все — не меньше стольких (и столько же,
# сколько в самом полном году ряда): часть таблицы, продолженной на следующей странице,
# со строкой страны не сравнивается.
SUM_REGIONS = 80
# Скачок разряда: середина значений регионов выросла или упала примерно во столько раз.
SCALE_STEPS = (1_000, 1_000_000)
# Допуск скачка: отношение середин — от половины шага до двух шагов.
SCALE_SLACK = 2
# Скачок разряда ищется у лет, где регионов не меньше стольких.
SCALE_REGIONS = 10
# Части больше итога больше чем на эту долю — замечание.
PART_GAP = 0.01
# Значение разреза — итог: «Всего», «Итого», «Оба пола».
_TOTAL = re.compile(r"^\s*(всего|итого|все\b|оба пола|total\b|all\b)", re.IGNORECASE)
# Часть «из них» входит в соседнюю часть, а не в итог рядом с ней.
_SUBSET = re.compile(r"^\s*(из\s+н|в\s+т\.?\s*ч|в\s+том\s+числе\s+из)", re.IGNORECASE)
# Сколько замечаний одного вида хранится в отчёте.
CHECKS_KEPT = 30

COUNTRY = "country"
SCALE = "scale"
PARTS = "parts"
JUMP = "jump"


def review(collector: Collector) -> dict[str, Any]:
    """Нули, похожие на пропуски, и замечания о согласованности — для отчёта извлечения."""
    frame = _frame(collector)
    if frame.empty:
        return {"zeros": [], "checks": []}
    names = [info.indicator for info in collector.infos]
    frame["indicator"] = frame["series"].map(dict(enumerate(names)))
    regions = frame[frame["code"].isin(_subjects())]
    return {
        "zeros": zeros(regions),
        "checks": [
            *country_sums(frame, names),
            *scale_jumps(regions, names),
            *parts_over_total(regions, collector),
            *region_jumps(regions, names),
        ],
    }


def _frame(collector: Collector) -> pd.DataFrame:
    """Значения по территориям справочника: ряд, код, год, значение, качество, признаки."""
    frame = pd.DataFrame(
        {
            "series": collector.series_index,
            "code": collector.codes,
            "year": collector.years,
            "value": collector.values,
            "quality": collector.qualities,
            "flags": collector.flags,
        }
    )
    return frame[frame["code"].notna() & frame["value"].notna()].reset_index(drop=True)


def _subjects() -> set[str]:
    from apps.sources.territories import region_codes

    return set(region_codes())


# --- Нули -----------------------------------------------------------------------------------


def zeros(regions: pd.DataFrame) -> list[dict[str, Any]]:
    """
    Показатели, у которых нули похожи на пропуски: нулей больше трети, ноль между двумя
    обычными годами региона (оба соседа не ниже медианы ненулевых значений) или ноль
    у всех регионов года. Отмечен заранее — только показатель с нулевым годом.
    """
    frame = regions.sort_values(["series", "code", "year"]).reset_index(drop=True)
    frame["zero"] = (
        (frame["value"] == 0) & (frame["flags"] == 0) & (frame["quality"] == ValueQuality.OBSERVED)
    )
    if not frame["zero"].any():
        return []
    nonzero = frame.loc[frame["value"] != 0]
    middle = nonzero.groupby("indicator")["value"].apply(lambda values: values.abs().median())
    frame["middle"] = frame["indicator"].map(middle)
    grouped = frame.groupby(["series", "code"], sort=False)
    for side, step in (("before", 1), ("after", -1)):
        frame[side] = grouped["value"].shift(step)
        frame[f"{side}_year"] = grouped["year"].shift(step)
    frame["between"] = (
        frame["zero"]
        & (frame["before"].abs() >= frame["middle"])
        & (frame["after"].abs() >= frame["middle"])
        & (frame["before"] != 0)
        & (frame["after"] != 0)
    )
    years = frame.groupby(["series", "year"]).agg(count=("zero", "size"), zeros=("zero", "sum"))
    empty = years[(years["count"] >= ALL_ZERO_REGIONS) & (years["zeros"] == years["count"])]
    series_names = frame.drop_duplicates("series").set_index("series")["indicator"]
    empty_years: defaultdict[str, set[int]] = defaultdict(set)
    for series, year in empty.index:
        empty_years[series_names[series]].add(int(year))

    found = []
    for indicator, rows in frame.groupby("indicator", sort=False):
        count, zero = len(rows), int(rows["zero"].sum())
        between = rows[rows["between"]]
        chosen = sorted(empty_years.get(str(indicator), set()))
        if not zero or not (zero > count * ZERO_SHARE or len(between) or chosen):
            continue
        example = None
        if len(between):
            first = between.iloc[0]
            example = {
                "code": first["code"],
                "before": float(first["before"]),
                "after": float(first["after"]),
                "year_before": int(first["before_year"]),
                "year": int(first["year"]),
                "year_after": int(first["after_year"]),
            }
        found.append(
            {
                "indicator": str(indicator),
                "values": count,
                "zeros": zero,
                "between": len(between),
                "years": chosen,
                "example": example,
                "suggested": bool(chosen),
            }
        )
    return found


# --- Согласованность ------------------------------------------------------------------------


def country_sums(frame: pd.DataFrame, names: list[str]) -> list[dict[str, Any]]:
    """
    Сумма регионов против строки страны — у рядов со строкой страны, по годам с полным
    составом регионов; область без автономных округов берётся как итог за вычетом округов.
    Строка страны в единицах крупнее (тыс. против млн) — не расхождение: о ней говорит
    «первый взгляд» на странице таблицы.
    """
    subjects = _subjects()
    country = frame[frame["code"] == matching.COUNTRY_CODE]
    if country.empty:
        return []
    found: dict[int, dict[str, Any]] = {}
    for series, rows in frame[frame["series"].isin(country["series"])].groupby("series"):
        by_year = {
            int(year): dict(zip(group["code"], group["value"], strict=True))
            for year, group in rows.groupby("year")
        }
        sums = {year: _subjects_sum(values, subjects) for year, values in by_year.items()}
        fullest = max((count for _total, count in sums.values()), default=0)
        if fullest < SUM_REGIONS:
            continue
        for year, (total, count) in sums.items():
            value = by_year[year].get(matching.COUNTRY_CODE)
            if count != fullest or not value or total is None:
                continue
            subjects_values = [found for code, found in by_year[year].items() if code in subjects]
            if min(subjects_values) <= value <= max(subjects_values):
                # Значение страны среди значений регионов — так выглядит доля или средняя,
                # а не сумма: об этом спрашивает «Что это за величина».
                continue
            gap = total / value - 1
            if abs(gap) <= SUM_GAP or any(
                abs(total / value / step - 1) <= SUM_GAP for step in SCALE_STEPS
            ):
                continue
            worst = found.get(int(series))
            if worst is None:
                found[int(series)] = worst = {
                    "kind": COUNTRY,
                    "indicator": names[int(series)],
                    "series": int(series),
                    "years": 0,
                    "gap": 0.0,
                }
            worst["years"] += 1
            if abs(gap) > abs(worst["gap"]):
                worst.update(year=year, gap=round(gap, 4))
    return _by_indicator(found.values())


def _subjects_sum(values: dict[str, float], subjects: set[str]) -> tuple[float | None, int]:
    """Сумма и число субъектов; у области без округов — итог с округами за их вычетом."""
    total = sum(value for code, value in values.items() if code in subjects)
    count = sum(1 for code in values if code in subjects)
    for whole, alone in matching.NESTED_PARENTS.items():
        if alone in values or whole not in values:
            continue
        members = matching.NESTED_MEMBERS[whole]
        if any(member not in values for member in members):
            return None, count
        total += values[whole] - sum(values[member] for member in members)
        count += 1
    if any(value < 0 for code, value in values.items() if code in subjects):
        return None, count
    return total, count


def scale_jumps(regions: pd.DataFrame, names: list[str]) -> list[dict[str, Any]]:
    """Середина значений регионов за год выросла или упала в тысячу (миллион) раз."""
    present = regions[regions["value"] != 0]
    middles = present.groupby(["series", "year"])["value"].agg(
        middle=lambda values: values.abs().median(), count="size"
    )
    middles = middles[middles["count"] >= SCALE_REGIONS].reset_index()
    found: dict[int, dict[str, Any]] = {}
    for series, rows in middles.groupby("series"):
        ordered = rows.sort_values("year")
        pairs = zip(ordered.itertuples(), ordered.iloc[1:].itertuples(), strict=False)
        for before, after in pairs:
            ratio = after.middle / before.middle
            step = next(
                (
                    step
                    for step in SCALE_STEPS
                    if step / SCALE_SLACK <= max(ratio, 1 / ratio) <= step * SCALE_SLACK
                ),
                None,
            )
            if step is None or int(series) in found:
                continue
            found[int(series)] = {
                "kind": SCALE,
                "indicator": names[int(series)],
                "series": int(series),
                "year_before": int(before.year),
                "year": int(after.year),
                "step": step,
                "up": ratio > 1,
            }
    return _by_indicator(found.values())


def region_jumps(regions: pd.DataFrame, names: list[str]) -> list[dict[str, Any]]:
    """
    Резкий скачок региона между соседними годами ряда — правило ``unusual.jumps`` на всю
    таблицу разом: у показателя — самый сильный скачок и число таких мест.
    """
    frame = regions.sort_values(["series", "code", "year"]).reset_index(drop=True)
    grouped = frame.groupby(["series", "code"], sort=False)
    frame["before"] = grouped["value"].shift(1)
    frame["year_before"] = grouped["year"].shift(1)
    pairs = frame[(frame["before"] > 0) & (frame["value"] > 0)].copy()
    if pairs.empty:
        return []
    pairs["change"] = np.log(pairs["value"] / pairs["before"])
    by_year = pairs.groupby(["series", "year"])["change"]
    quartiles = by_year.quantile([0.25, 0.75]).unstack()
    quartiles.columns = ["low", "high"]
    quartiles["count"] = by_year.size()
    pairs = pairs.join(quartiles, on=["series", "year"])
    spread = pairs["high"] - pairs["low"]
    flagged = pairs[
        (pairs["count"] >= unusual.MIN_REGIONS)
        & (pairs["change"].abs() >= np.log(unusual.JUMP_RATIO))
        & (
            (pairs["change"] < pairs["low"] - unusual.JUMP_IQR * spread)
            | (pairs["change"] > pairs["high"] + unusual.JUMP_IQR * spread)
        )
    ]
    found: dict[str, dict[str, Any]] = {}
    for row in flagged.assign(size=flagged["change"].abs()).sort_values("size").itertuples():
        indicator = names[int(row.series)]
        entry = found.setdefault(indicator, {"places": 0})
        entry.update(
            kind=JUMP,
            indicator=indicator,
            series=int(row.series),
            code=row.code,
            year_before=int(row.year_before),
            year=int(row.year),
            before=float(row.before),
            after=float(row.value),
            places=entry["places"] + 1,
        )
    return _by_indicator(found.values())


def parts_over_total(regions: pd.DataFrame, collector: Collector) -> list[dict[str, Any]]:
    """
    Части больше итога: у разреза со значением «Всего» сумма остальных значений того же
    разреза (без «из них») больше итога региона за год.
    """
    groups: defaultdict[tuple[Any, ...], list[tuple[str, int]]] = defaultdict(list)
    for index, info in enumerate(collector.infos):
        for position, (header, value) in enumerate(info.slices):
            rest = info.slices[:position] + info.slices[position + 1 :]
            groups[(info.indicator, info.period, header, rest)].append((value, index))
    values = {
        (int(series), code, int(year)): value
        for series, code, year, value in regions[["series", "code", "year", "value"]].itertuples(
            index=False
        )
    }
    by_series: defaultdict[int, list[tuple[str, int, float]]] = defaultdict(list)
    for (series, code, year), value in values.items():
        by_series[series].append((code, year, value))
    found = []
    for (indicator, _period, header, _rest), members in groups.items():
        totals = [index for value, index in members if _TOTAL.match(value)]
        parts = [
            index
            for value, index in members
            if not _TOTAL.match(value) and not _SUBSET.match(value)
        ]
        if len(totals) != 1 or len(parts) < 2:  # noqa: PLR2004 — итог и хотя бы две части
            continue
        over = 0
        example = None
        for code, year, total in by_series.get(totals[0], []):
            shares = [values.get((part, code, year)) for part in parts]
            known = [share for share in shares if share is not None]
            if total <= 0 or len(known) < 2 or min(known) < 0:  # noqa: PLR2004
                continue
            if sum(known) > total * (1 + PART_GAP):
                over += 1
                example = example or {"code": code, "year": year, "total": total, "sum": sum(known)}
        if over:
            found.append(
                {
                    "kind": PARTS,
                    "indicator": indicator,
                    "series": totals[0],
                    "header": header,
                    "places": over,
                    "example": example,
                }
            )
    return found[:CHECKS_KEPT]


def _by_indicator(items: Any) -> list[dict[str, Any]]:
    """Одно замечание на показатель — первое по порядку рядов."""
    seen: set[str] = set()
    kept = []
    for item in sorted(items, key=lambda entry: entry["series"]):
        if item["indicator"] in seen:
            continue
        seen.add(item["indicator"])
        kept.append(item)
    return kept[:CHECKS_KEPT]
