"""
Помесячный слой для читателя: блок «Что сейчас» в паспорте и помесячный блок страницы
показателя.

Значение сравнивается с тем же периодом прошлого года: суммы с начала года и уровни —
изменением в процентах, доли, ставки и индексы — разницей в пунктах.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from django.urls import reverse
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from apps.catalog.constants import ValueFlag
from apps.catalog.models import Series, Territory
from apps.catalog.provenance import release_phrase, source_short, source_title
from apps.core.charts import timeline_option
from apps.core.templatetags.formatting import ru_number
from apps.maps.cartogram import build_map
from apps.maps.classification import classify, sequential_palette
from apps.sources.registry import SourceSeries, registry
from apps.warehouse.queries import COUNTRY_CODE, featured_set, series_timeline
from apps.warehouse.queries.monthly import month_latest, month_points, month_series, month_timeline
from apps.warehouse.queries.sources import population_last_year

MONTHS = (
    _("январь"),
    _("февраль"),
    _("март"),
    _("апрель"),
    _("май"),
    _("июнь"),
    _("июль"),
    _("август"),
    _("сентябрь"),
    _("октябрь"),
    _("ноябрь"),
    _("декабрь"),
)
# Родительный падеж — в датах «на 1 августа 2026».
MONTHS_OF = (
    _("января"),
    _("февраля"),
    _("марта"),
    _("апреля"),
    _("мая"),
    _("июня"),
    _("июля"),
    _("августа"),
    _("сентября"),
    _("октября"),
    _("ноября"),
    _("декабря"),
)
MONTHS_SHORT = (
    _("янв"),
    _("фев"),
    _("мар"),
    _("апр"),
    _("май"),
    _("июн"),
    _("июл"),
    _("авг"),
    _("сен"),
    _("окт"),
    _("ноя"),
    _("дек"),
)

# Сколько лет помесячный график ставит рядом.
CHART_YEARS = 3
# Классы карты последнего месяца — как у живой карты.
MAP_METHOD = "quantile"
MAP_CLASSES = 5
# Приставка опознавателей контуров второй карты страницы показателя.
MAP_PREFIX = "rl-month"
# Изменение меньше этого — без оценки: доля от прошлого значения или пункты.
QUIET_SHARE = 0.005
QUIET_POINTS = 0.05


@dataclass(frozen=True, slots=True)
class MonthlySpec:
    """Как показывать месячные значения ряда."""

    key: str
    title: str
    unit: str
    precision: int
    compare: str  # percent | points
    kind: str  # вид значения для «Что сейчас» и карты
    chart_kind: str  # вид значения для графика по месяцам
    style: str  # month | ytd | stock | register | moving3 | ytd_index | yoy
    polarity: str
    chart_unit: str = ""

    @property
    def is_index(self) -> bool:
        """Значение — индекс, 100 — без изменения: показывается приростом."""
        return self.style in {"ytd_index", "yoy"}

    @property
    def is_money(self) -> bool:
        """Денежная величина в текущих ценах: рост без оценки."""
        return "руб" in self.unit.lower() or "rub" in self.unit.lower()


def monthly_spec(series_key: str) -> MonthlySpec | None:
    """Описание месячных значений ряда; ``None`` — помесячного слоя у ряда нет."""
    book = registry()
    featured = featured_set().by_key().get(series_key)
    item = book.by_key.get(series_key)
    if item is not None:
        kind = "ytd" if item.flow else "level"
        return MonthlySpec(
            key=series_key,
            title=featured.short_title if featured else item.short_title,
            unit=item.unit.short,
            precision=featured.precision if featured else item.precision,
            compare=item.compare,
            kind=kind,
            chart_kind="level",
            style=_style(item),
            polarity=featured.polarity if featured else "neutral",
            chart_unit=item.unit.short,
        )
    entry = next((entry for entry in book.dataset_monthly if entry.series == series_key), None)
    if entry is None or featured is None:
        return None
    style = {"ytd": "ytd_index", "yoy": "yoy"}.get(entry.kind, "month")
    if entry.measure.endswith("_3m"):
        style = "moving3"
    return MonthlySpec(
        key=series_key,
        title=featured.short_title,
        unit="%" if style in {"ytd_index", "yoy"} else featured.unit_label,
        precision=featured.precision,
        compare=entry.compare,
        kind=entry.kind,
        chart_kind=entry.kind,
        style=style,
        polarity=featured.polarity,
        chart_unit=featured.unit_label,
    )


def _style(item: SourceSeries) -> str:
    """Как называть период ряда проекта: по способу годового значения его основы."""
    book = registry()
    base = item
    while base.ratio is not None and base.ratio.numerator in book.by_key:
        base = book.by_key[base.ratio.numerator]
    if base.source == "fns_sme":
        return "register"
    if base.flow:
        return "ytd"
    if base.annual == "december":
        return "stock"
    return "month"


def annual_method_text(item: SourceSeries) -> str:
    """Как получено годовое значение ряда проекта."""
    if item.annual == "ratio" and item.ratio is not None:
        if item.ratio.denominator == "population":
            return gettext("на жителя: годовое значение, делённое на численность населения")
        return gettext("отношение годовых значений")
    return {
        "sum": gettext("сумма двенадцати месяцев"),
        "december": gettext("на конец года"),
        "january_next": gettext("на конец года — реестр на 10 января"),
        "weighted_mean": gettext("среднее двенадцати месяцев, взвешенное по объёму выдачи"),
    }.get(item.annual, "")


def latest_period(series_key: str) -> str:
    """Последний период ряда по России словами; пусто — месяцев нет."""
    spec = monthly_spec(series_key)
    if spec is None:
        return ""
    rows = [
        row
        for row in month_timeline(series_key, COUNTRY_CODE, spec.kind)
        if row["value"] is not None
    ]
    if not rows:
        return ""
    return period_text(spec.style, int(rows[-1]["year"]), int(rows[-1]["month"]))


def period_text(style: str, year: int, month: int) -> str:  # noqa: PLR0911 - по ветви на вид
    """Подпись периода: «июль 2026», «январь — июль 2026», «на 1 августа 2026»."""
    name = MONTHS[month - 1]
    if style in {"ytd", "ytd_index", "yoy"}:
        if month == 1:
            span = gettext("%(month)s %(year)s") % {"month": name, "year": year}
        else:
            span = gettext("%(first)s — %(month)s %(year)s") % {
                "first": MONTHS[0],
                "month": name,
                "year": year,
            }
        if style == "ytd_index":
            return gettext("%(span)s к декабрю %(previous)s") % {"span": span, "previous": year - 1}
        if style == "yoy":
            return gettext("%(span)s к тому же периоду %(previous)s года") % {
                "span": span,
                "previous": year - 1,
            }
        return span
    if style == "stock":
        following_year, following = (year + 1, 1) if month == 12 else (year, month + 1)  # noqa: PLR2004
        return gettext("на 1 %(month)s %(year)s") % {
            "month": MONTHS_OF[following - 1],
            "year": following_year,
        }
    if style == "register":
        return gettext("на 10 %(month)s %(year)s") % {"month": MONTHS_OF[month - 1], "year": year}
    if style == "moving3":
        first_month = (month - 3) % 12 + 1
        first_year = year if month > 2 else year - 1  # noqa: PLR2004
        if first_year == year:
            return gettext("%(first)s — %(month)s %(year)s, в среднем") % {
                "first": MONTHS[first_month - 1],
                "month": name,
                "year": year,
            }
        return gettext("%(first)s %(first_year)s — %(month)s %(year)s, в среднем") % {
            "first": MONTHS[first_month - 1],
            "first_year": first_year,
            "month": name,
            "year": year,
        }
    return gettext("%(month)s %(year)s") % {"month": name, "year": year}


# ---------------------------------------------------------------------------------------
# «Что сейчас»
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NowRow:
    """Строка «Что сейчас»: последнее значение, изменение за год, источник."""

    key: str
    title: str
    value: str
    unit: str
    period: str
    change: str
    tone: str
    country: str
    source: str
    note: str
    href: str


def now_rows(territory_code: str) -> list[NowRow]:
    """Последние месячные значения рядов «Что сейчас» по территории."""
    specs = [spec for key in registry().now if (spec := monthly_spec(key)) is not None]
    available = month_series()
    specs = [spec for spec in specs if spec.key in available]
    if not specs:
        return []
    keys = [spec.key for spec in specs]
    own = _by_period(month_points(territory_code, keys))
    country = (
        own if territory_code == COUNTRY_CODE else _by_period(month_points(COUNTRY_CODE, keys))
    )
    slugs = dict(Series.objects.filter(key__in=keys).values_list("key", "indicator__slug"))

    rows = []
    for spec in specs:
        points = own.get((spec.key, spec.kind), {})
        known = [period for period, row in points.items() if row["value"] is not None]
        if not known:
            continue
        year, month = max(known)
        current = points[(year, month)]
        previous = points.get((year - 1, month))
        change, tone = compare(spec, current["value"], previous["value"] if previous else None)
        if not change and spec.style == "moving3":
            change = _annual_reference(spec, territory_code, year - 1)
        country_row = country.get((spec.key, spec.kind), {}).get((year, month))
        slug = slugs.get(spec.key)
        rows.append(
            NowRow(
                key=spec.key,
                title=spec.title,
                value=value_text(spec, current["value"]),
                unit=spec.unit,
                period=period_text(spec.style, year, month),
                change=change,
                tone=tone,
                country=(
                    gettext("по России — %(value)s %(unit)s")
                    % {"value": value_text(spec, country_row["value"]), "unit": spec.unit}
                    if country_row is not None
                    and country_row["value"] is not None
                    and territory_code != COUNTRY_CODE
                    else ""
                ),
                source=_short_source_text(current),
                note=_note(current),
                href=(
                    reverse("catalog:series-detail", kwargs={"slug": slug}) + f"?series={spec.key}"
                    if slug
                    else ""
                ),
            )
        )
    return rows


def value_text(spec: MonthlySpec, value: float | None) -> str:
    """Число для показа; у индекса — прирост со знаком: 104,8 → «+4,8»."""
    if value is None:
        return "—"
    if spec.is_index:
        growth = value - 100
        text = ru_number(abs(growth), spec.precision)
        return ("+" if growth > 0 else "−" if growth < 0 else "") + text
    return ru_number(value, spec.precision)


def compare(spec: MonthlySpec, value: float | None, previous: float | None) -> tuple[str, str]:
    """Изменение к тому же периоду прошлого года словами и его оценка."""
    if value is None or previous is None:
        return "", "neutral"
    if spec.is_index:
        text = gettext("год назад за тот же период — %(value)s %%") % {
            "value": value_text(spec, previous)
        }
        return text, _tone(spec, value - previous, QUIET_POINTS)
    if spec.compare == "points":
        delta = value - previous
        sign = "+" if delta > 0 else "−" if delta < 0 else ""
        text = gettext("%(sign)s%(value)s п. за год") % {
            "sign": sign,
            "value": ru_number(abs(delta), max(spec.precision, 1)),
        }
        return text, _tone(spec, delta, QUIET_POINTS)
    if previous == 0:
        return "", "neutral"
    share = value / previous - 1
    sign = "+" if share > 0 else "−" if share < 0 else ""
    if spec.style == "ytd":
        template = gettext("%(sign)s%(value)s %% к тому же периоду прошлого года")
    else:
        template = gettext("%(sign)s%(value)s %% за год")
    text = template % {"sign": sign, "value": ru_number(abs(share) * 100, 1)}
    return text, _tone(spec, share, QUIET_SHARE)


def _tone(spec: MonthlySpec, delta: float, quiet: float) -> str:
    if spec.is_money or spec.polarity not in {"positive", "negative"} or abs(delta) < quiet:
        return "neutral"
    better = delta > 0 if spec.polarity == "positive" else delta < 0
    return "positive" if better else "negative"


def _annual_reference(spec: MonthlySpec, territory_code: str, year: int) -> str:
    """Среднегодовое значение прошлого года, если того же периода год назад нет."""
    value = next(
        (
            row["value"]
            for row in series_timeline(spec.key, territory_code)
            if row["year"] == year and row["value"] is not None
        ),
        None,
    )
    if value is None:
        return ""
    return gettext("в %(year)s году в среднем — %(value)s %(unit)s") % {
        "year": year,
        "value": ru_number(value, spec.precision),
        "unit": spec.unit,
    }


def _by_period(rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[tuple[int, int], Any]]:
    grouped: dict[tuple[str, str], dict[tuple[int, int], Any]] = defaultdict(dict)
    for row in rows:
        grouped[(row["series_key"], row["kind"])][(int(row["year"]), int(row["month"]))] = row
    return grouped


def _source_text(row: dict[str, Any]) -> str:
    released = row.get("released_on")
    return f"{source_title(row['source_code'])}, {release_phrase(row['edition_label'], released)}"


def _short_source_text(row: dict[str, Any]) -> str:
    released = row.get("released_on")
    return f"{source_short(row['source_code'])}, {release_phrase(row['edition_label'], released)}"


def _note(row: dict[str, Any]) -> str:
    flags = ValueFlag(int(row.get("flags") or 0))
    if ValueFlag.PRELIMINARY in flags:
        return gettext("предварительные данные")
    if ValueFlag.FROZEN_DENOMINATOR in flags:
        year = population_last_year()
        if year is not None:
            return gettext("численность — за %(year)s год") % {"year": year}
    return ""


# ---------------------------------------------------------------------------------------
# Страница показателя
# ---------------------------------------------------------------------------------------


def month_block(series_key: str) -> dict[str, Any] | None:
    """График по месяцам для России (год к году) и карта последнего периода по регионам."""
    spec = monthly_spec(series_key)
    if spec is None or series_key not in month_series():
        return None
    context: dict[str, Any] = {"spec": spec}

    timeline = month_timeline(series_key, COUNTRY_CODE, spec.chart_kind)
    years = sorted({int(row["year"]) for row in timeline})[-CHART_YEARS:]
    if years:
        by_year: dict[int, dict[int, float | None]] = defaultdict(dict)
        for row in timeline:
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
        context["chart"] = timeline_option(
            [str(label) for label in MONTHS_SHORT], lines, unit=spec.chart_unit
        )
        context["chart_years"] = years
        context["chart_note"] = _chart_note(spec)

    latest = month_latest(series_key, spec.kind)
    if latest is not None:
        context.update(_month_map(spec, latest))
        country = month_timeline(series_key, COUNTRY_CODE, spec.kind)
        points = {(int(row["year"]), int(row["month"])): row for row in country}
        now = points.get((latest["year"], latest["month"]))
        before = points.get((latest["year"] - 1, latest["month"]))
        if now is not None and now["value"] is not None:
            change, tone = compare(
                spec, now["value"], before["value"] if before is not None else None
            )
            if not change and spec.style == "moving3":
                change = _annual_reference(spec, COUNTRY_CODE, latest["year"] - 1)
            context["country"] = {
                "value": value_text(spec, now["value"]),
                "change": change,
                "tone": tone,
                "source": _source_text(now),
            }
    return context


def _chart_note(spec: MonthlySpec) -> str:
    if spec.style == "ytd" and spec.chart_kind == "level":
        return gettext("Значения за каждый месяц, а не с начала года.")
    if spec.style == "moving3":
        return gettext("Каждая точка — среднее за три месяца, которыми она заканчивается.")
    if spec.style == "register":
        return gettext("Сведения реестра на 10-е число месяца.")
    if spec.style == "stock":
        return gettext("Значения на конец месяца.")
    return ""


def _month_map(spec: MonthlySpec, latest: dict[str, Any]) -> dict[str, Any]:
    values = {row["territory_code"]: row["value"] for row in latest["rows"]}
    classification = classify(
        [value for value in values.values() if value is not None],
        method=MAP_METHOD,
        class_count=MAP_CLASSES,
    )
    palette = sequential_palette(classification.class_count) if classification else []
    rows = []
    missing = 0
    for territory in Territory.objects.comparable().only(
        "code", "slug", "name_ru", "name_en", "abbreviation", "display_order"
    ):
        value = values.get(territory.code)
        class_index = (
            classification.class_of(value) if classification and value is not None else None
        )
        missing += value is None
        rows.append(
            {
                "code": territory.code,
                "name": territory.name,
                "abbreviation": territory.short_code,
                "href": reverse("catalog:territory-detail", kwargs={"slug": territory.slug}),
                "title": (
                    f"{territory.name}: {value_text(spec, value)} {spec.unit}"
                    if value is not None
                    else territory.name
                ),
                "colour": palette[class_index] if class_index is not None else "",
                "class_index": class_index,
                "mapped": None,
            }
        )
    from apps.core.showcase import scale_legend

    ranked = sorted(
        (row for row in latest["rows"] if row["value"] is not None), key=lambda row: row["value"]
    )
    return {
        "period": period_text(spec.style, latest["year"], latest["month"]),
        "geo_map": build_map(rows, prefix=MAP_PREFIX),
        "legend": scale_legend(classification, palette, spec.precision),
        "missing": missing,
        "highest": _extreme(spec, ranked[-1]) if ranked else None,
        "lowest": _extreme(spec, ranked[0]) if ranked else None,
    }


def _extreme(spec: MonthlySpec, row: dict[str, Any]) -> dict[str, str]:
    from django.utils.translation import get_language

    name = row["name_en"] if get_language() == "en" and row.get("name_en") else row["name_ru"]
    return {"name": name, "value": value_text(spec, row["value"])}
