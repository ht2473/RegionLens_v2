"""
Вид «По месяцам» для таблицы: ряды одного показателя и набора разрезов по месяцам (или
кварталам, «с начала года», скользящим окнам) — линиями по годам и картой последнего периода.

Ряд таблицы — значения одного периода года по годам («июль», «январь–июль»). Для вида
по месяцам ряды показателя собираются в группу: при сборке их значения пишутся в слой
``fact_month`` файла таблицы под ключом группы, и выборки помесячного слоя склада
(``queries.monthly``) читают его без переделки.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from django.utils.translation import gettext

from apps.catalog.monthly import MONTHS, MONTHS_OF, MONTHS_SHORT, MonthlySpec
from apps.core.charts import timeline_option
from apps.core.templatetags.formatting import ru_number
from apps.sources import periods

from . import naming
from .models import DatasetSeries, DatasetVersion

# Вид периода → вид значения в fact_month и месяц, которым период заканчивается.
LEVEL = "level"
YTD = "ytd"
KINDS = {
    periods.MONTH: LEVEL,
    periods.QUARTER: LEVEL,
    periods.HALF: LEVEL,
    periods.WINDOW: LEVEL,
    periods.POINT: LEVEL,
    periods.YTD: YTD,
}
# Сколько лет график ставит рядом.
CHART_YEARS = 3
# Код издания значений таблицы в слое fact_month.
EDITION = "user"


def layout(period: str) -> tuple[str, int] | None:
    """Вид группы и месяц периода; ``None`` — годовой ряд, в помесячный вид не входит."""
    found = periods.Period.from_key(period)
    if found.kind not in KINDS:
        return None
    if found.kind == periods.QUARTER:
        return found.kind, found.number * 3
    if found.kind == periods.HALF:
        return found.kind, found.number * 6
    return found.kind, found.number


def group_code(indicator: str, slices: Any, kind: str, span: int = 0) -> str:
    """Код группы рядов: показатель, разрезы и вид периода (окно — с длиной)."""
    suffix = f"months:{kind}" + (f":{span}" if span else "")
    return naming.series_code(indicator, slices, suffix) + "-m"


def plan_fields(item: dict[str, Any]) -> dict[str, Any]:
    """Поля ряда плана сборки для слоя месяцев: ключ группы, месяц и вид значения."""
    found = layout(item["period"])
    if found is None or item["derived"]:
        return {"month_code": "", "month": 0, "month_kind": ""}
    kind, month = found
    span = periods.Period.from_key(item["period"]).span
    return {
        "month_code": group_code(item["indicator"], item["slices"], kind, span),
        "month": month,
        "month_kind": KINDS[kind],
    }


@dataclass(frozen=True, slots=True)
class Group:
    """Группа рядов таблицы для вида по месяцам."""

    key: str
    code: str
    indicator: str
    title: str
    detail: str
    unit: str
    kind: str  # вид периода
    value_kind: str  # level / ytd
    span: int
    precision: int
    polarity: str
    is_sum: bool

    @property
    def label(self) -> str:
        return f"{self.title} — {self.detail}" if self.detail else self.title


def groups(version: DatasetVersion | None) -> list[Group]:
    """Группы рядов собранной версии с периодами внутри года."""
    if version is None or version.state != DatasetVersion.State.BUILT:
        return []
    found: dict[str, Group] = {}
    kinds: dict[str, set[str]] = defaultdict(set)
    prefix = f"u:{version.dataset.code}:"
    for record in version.series.filter(derived="").order_by("order"):
        place = layout(record.period)
        if place is None:
            continue
        kind = place[0]
        span = periods.Period.from_key(record.period).span
        code = group_code(record.indicator, record.slices, kind, span)
        kinds[code].add(record.period)
        found.setdefault(
            code,
            Group(
                key=prefix + code,
                code=code,
                indicator=record.indicator,
                title=record.title,
                detail=", ".join(
                    part
                    for part in (naming.slices_text(record.slices), _kind_text(kind, span))
                    if part
                ),
                unit=record.unit,
                kind=kind,
                value_kind=KINDS[kind],
                span=span,
                precision=record.precision,
                polarity=record.polarity,
                is_sum=record.kind == DatasetSeries.Kind.SUM,
            ),
        )
    # Один период в году («январь–июль») — это обычный ряд по годам, не вид по месяцам.
    return [group for code, group in found.items() if len(kinds[code]) > 1]


def _kind_text(kind: str, span: int) -> str:
    return {
        periods.MONTH: gettext("по месяцам"),
        periods.QUARTER: gettext("по кварталам"),
        periods.HALF: gettext("по полугодиям"),
        periods.YTD: gettext("с начала года"),
        periods.WINDOW: gettext("за %(span)s мес., скользящие") % {"span": span},
        periods.POINT: gettext("на начало месяца"),
    }.get(kind, "")


def period_label(group: Group, year: int, month: int) -> str:
    """Период словами: «июль 2026», «январь — июль 2026», «II квартал 2026»."""
    if group.kind == periods.YTD and month > 1:
        return gettext("%(first)s — %(month)s %(year)s") % {
            "first": MONTHS[0],
            "month": MONTHS[month - 1],
            "year": year,
        }
    if group.kind == periods.QUARTER:
        return gettext("%(quarter)s квартал %(year)s") % {
            "quarter": naming.ROMAN[month // 3 - 1],
            "number": month // 3,
            "year": year,
        }
    if group.kind == periods.HALF:
        return (
            gettext("I полугодие %(year)s") if month == 6 else gettext("II полугодие %(year)s")  # noqa: PLR2004
        ) % {"year": year}
    if group.kind == periods.POINT:
        return gettext("на 1 %(month)s %(year)s") % {"month": MONTHS_OF[month - 1], "year": year}
    if group.kind == periods.WINDOW and group.span:
        first = (month - group.span) % 12 + 1
        return gettext("%(first)s — %(month)s %(year)s, в среднем") % {
            "first": MONTHS[first - 1],
            "month": MONTHS[month - 1],
            "year": year,
        }
    return gettext("%(month)s %(year)s") % {"month": MONTHS[month - 1], "year": year}


def chart(group: Group, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Линии по месяцам за последние годы: последний год — главный, давние — приглушены."""
    years = sorted({int(row["year"]) for row in rows if row["value"] is not None})[-CHART_YEARS:]
    if not years:
        return None
    by_year: dict[int, dict[int, float | None]] = defaultdict(dict)
    for row in rows:
        by_year[int(row["year"])][int(row["month"])] = row["value"]
    lines = [
        {
            "name": str(year),
            "values": [by_year[year].get(month) for month in range(1, 13)],
            "primary": year == years[-1],
            "muted": year < years[-1] - 1,
        }
        for year in years
    ]
    return {
        "option": timeline_option([str(label) for label in MONTHS_SHORT], lines, unit=group.unit),
        "years": years,
    }


def spec(group: Group) -> MonthlySpec:
    """Описание группы для карты последнего периода — как у рядов сайта."""
    return MonthlySpec(
        key=group.key,
        title=group.label,
        unit=group.unit,
        precision=group.precision,
        compare="percent" if group.is_sum else "points",
        kind=group.value_kind,
        chart_kind=group.value_kind,
        style=YTD if group.kind == periods.YTD else "month",
        polarity=group.polarity,
        chart_unit=group.unit,
    )


def change(group: Group, value: float | None, previous: float | None) -> str:
    """Изменение к тому же периоду прошлого года: у сумм — в процентах, иначе — в пунктах."""
    if value is None or previous is None:
        return ""
    if group.is_sum:
        if previous == 0:
            return ""
        share = value / previous - 1
        sign = "+" if share > 0 else "−" if share < 0 else ""
        return gettext("%(sign)s%(value)s %% к тому же периоду прошлого года") % {
            "sign": sign,
            "value": ru_number(abs(share) * 100, 1),
        }
    delta = value - previous
    sign = "+" if delta > 0 else "−" if delta < 0 else ""
    return gettext("%(sign)s%(value)s к тому же периоду прошлого года") % {
        "sign": sign,
        "value": ru_number(abs(delta), max(group.precision, 1)),
    }
