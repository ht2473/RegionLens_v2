"""
Примеры к разделам методики на нынешних данных.

Пример — две-три строки с числами по основному набору и региону читателя (без выбранного
региона — Республика Татарстан). Слова — из правил, числа — из склада, поэтому пример
не расходится с сайтом после новой сборки. Числа хранятся в кэше расчётов до новой сборки;
данных нет — раздел показывается без примера.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Any
from urllib.parse import urlencode

import numpy as np
from django.http import HttpRequest
from django.urls import reverse
from django.utils.translation import get_language, gettext_lazy
from django.utils.translation import gettext as _

from apps.analytics.core import convergence, correlation, inequality, normalization, spatial
from apps.analytics.core.weighting import build_weights
from apps.analytics.selectors import (
    cached_result,
    comparable_from,
    latest_full_year,
    neighbour_map,
    series_breaks,
)
from apps.catalog.constants import BreakKind
from apps.catalog.models import Series, Territory
from apps.catalog.passport import ordinal
from apps.core.templatetags.formatting import p_level, ru_number
from apps.warehouse.duckdb_client import WarehouseNotBuiltError

# Регион примеров, если читатель своего не выбрал.
DEFAULT_REGION = "RU-TA"
# Ряды основного набора, на которых строятся примеры.
WAGE = "Y477110378:00"
INCOME = "Y477110374:00"
UNEMPLOYMENT = "Y477110418:00"
POVERTY = "Y477110463:00"
LIFE = "Y477110256:00"
GRP_PER_CAPITA = "Y477110006:00"
EMPLOYMENT = "Y477110421:00"
# Лет в примере конвергенции; классов в примере шкалы карты.
CONVERGENCE_YEARS = 10
MAP_CLASSES = 5
# Редакция примеров: кэш расчётов не знает о правке кода.
RESULT_REVISION = 1

DIRECTIONS = {
    "clustered": gettext_lazy("соседние регионы похожи"),
    "dispersed": gettext_lazy("соседние регионы различаются"),
    "random": gettext_lazy("размещение неотличимо от случайного"),
}


@dataclass(frozen=True, slots=True)
class Example:
    """Пример раздела: о чём он, строки с числами и адрес того же расчёта на сайте."""

    subject: str
    lines: tuple[str, ...]
    url: str = ""


def examples_for(request: HttpRequest) -> dict[str, Example]:
    """Примеры по кодам разделов для региона читателя; склада нет — примеров нет."""
    from apps.accounts.region import my_region

    region = my_region(request) or Territory.objects.filter(code=DEFAULT_REGION).first()
    if region is None:
        return {}
    found = {}
    try:
        for code, builder in BUILDERS.items():
            example = cached_result(
                "methodology-example",
                {"section": code, "region": region.code, "revision": RESULT_REVISION},
                partial(_build, builder, region),
            )
            if example:
                found[code] = example
    except WarehouseNotBuiltError:
        return {}
    return found


# --- Общие части --------------------------------------------------------------------------


def _build(builder: Callable[[Territory], Example | None], region: Territory) -> Example | bool:
    """Пример или False: пустой результат тоже хранится и не считается заново."""
    return builder(region) or False


def _url(name: str, **params: Any) -> str:
    return f"{reverse(name)}?{urlencode(params, doseq=True)}"


def _featured(key: str) -> Any:
    from apps.warehouse.queries import featured_series

    return next((item for item in featured_series() if item.key == key), None)


def _values(key: str, year: int) -> dict[str, float]:
    from apps.warehouse.queries import region_panel

    return region_panel(key).get(year) or {}


def _subject(*parts: Any) -> str:
    return ", ".join(str(part) for part in parts if part not in (None, ""))


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


def _signed(value: float, digits: int) -> str:
    """Число со знаком минус, а не дефисом."""
    return ru_number(value, digits).replace("-", "−")


def _quoted(item: Any) -> str:
    return f"«{item.short_title}»"


# --- Подготовка данных --------------------------------------------------------------------


def _breaks(region: Territory) -> Example | None:  # noqa: ARG001 - разрыв общий для всех
    """Последний разрыв по смене правил у первого ряда основного набора, где он есть."""
    from apps.warehouse.queries import featured_series, series_covered_years

    for item in featured_series():
        series = Series.objects.filter(key=item.key).first()
        bounds = series_covered_years([item.key]).get(item.key)
        if series is None or bounds is None:
            continue
        found = [
            row for row in series_breaks(series, *bounds) if row["kind"] != BreakKind.TERRITORY
        ]
        if not found:
            continue
        last = found[-1]
        return Example(
            subject=item.short_title,
            lines=(
                _("Разрыв в %(year)s году: %(kind)s.")
                % {"year": last["year"], "kind": str(last["label"]).lower()},
                _("Изменение между %(before)s и %(year)s годами не считается.")
                % {"before": last["year"] - 1, "year": last["year"]},
            ),
            url=_url("compare:index", series=item.key),
        )
    return None


def _revision(region: Territory) -> Example | None:
    """Самый поздний пересмотр региона по первым рядам основного набора."""
    from apps.warehouse.queries import featured_series, revision_trace, series_revisions

    english = get_language() == "en"
    for item in featured_series()[:12]:
        rows = [
            row for row in series_revisions(item.key, 500) if row["territory_code"] == region.code
        ]
        if not rows:
            continue
        year = max(int(row["year"]) for row in rows)
        trace = [
            row for row in revision_trace(item.key, region.code, year) if row["value"] is not None
        ]
        if len(trace) < 2:  # noqa: PLR2004 - пересмотр — это два выпуска
            continue
        first, last = trace[0], trace[-1]

        def edition(row: dict[str, Any]) -> str:
            publication = row.get("publication_en") if english else row.get("publication_ru")
            return _subject(publication, row.get("edition_year"))

        return Example(
            subject=_subject(item.short_title, region.name, year),
            lines=(
                f"{edition(first)}: {ru_number(first['value'], item.precision)} {item.unit_label}.",
                _("%(edition)s: %(value)s %(unit)s — действует.")
                % {
                    "edition": edition(last),
                    "value": ru_number(last["value"], item.precision),
                    "unit": item.unit_label,
                },
            ),
            url=_url("analytics:revisions", series=item.key, territory=region.code, year=year),
        )
    return None


# --- Карта, места и оценки -----------------------------------------------------------------


def _map_classes(region: Territory) -> Example | None:  # noqa: ARG001 - шкала общая
    """Границы пяти классов квантилями у средней зарплаты."""
    from apps.maps.classification import classify

    item = _featured(WAGE)
    year = latest_full_year([WAGE])
    if item is None or year is None:
        return None
    scale = classify(list(_values(WAGE, year).values()), method="quantile", class_count=MAP_CLASSES)
    if scale is None:
        return None
    inner = ", ".join(ru_number(value, 0) for value in scale.breaks[1:-1])
    counts = ", ".join(str(count) for count in scale.counts)
    return Example(
        subject=_subject(item.short_title, year),
        lines=(
            _("Границы классов: %(breaks)s %(unit)s.") % {"breaks": inner, "unit": item.unit_label},
            _("Регионов в классах: %(counts)s.") % {"counts": counts},
        ),
        url=_url("maps:choropleth", series=WAGE, year=year),
    )


def _positions(region: Territory) -> dict[str, Any]:
    """Положения региона из паспорта: с разрывами и пересчётом в неизменные цены."""
    from apps.catalog.passport import build_passport

    return build_passport(region.code).by_key()


def _standing(region: Territory) -> Example | None:
    """Доля регионов с худшим значением и ступень оценки."""
    positions = _positions(region)
    for key in (LIFE, WAGE, UNEMPLOYMENT):
        position = positions.get(key)
        if position is None or position.favourable is None:
            continue
        item = position.series
        return Example(
            subject=_subject(item.short_title, region.name, position.year),
            lines=(
                f"{ru_number(position.value, item.precision)} {item.unit_label}, "
                f"{position.place_text}.",
                _("p = %(share)s: %(standing)s.")
                % {"share": ru_number(position.favourable, 2), "standing": position.standing},
            ),
            url=reverse("catalog:territory-detail", kwargs={"slug": region.slug}),
        )
    return None


def _trend_example(position: Any, region: Territory) -> Example:
    """Значения пятилетней давности и последнего года и изменение словами, как в паспорте."""
    item = position.series
    return Example(
        subject=_subject(item.short_title, region.name),
        lines=(
            _("%(past)s в %(past_year)s году, %(value)s %(unit)s в %(year)s году.")
            % {
                "past_year": position.past_year,
                "past": ru_number(position.past_value, item.precision),
                "year": position.year,
                "value": ru_number(position.value, item.precision),
                "unit": item.unit_label,
            },
            _capital(position.trend) + ".",
        ),
        url=_url("compare:index", series=item.key, territory=region.code),
    )


def _five_year(region: Territory) -> Example | None:
    """Изменение за пять лет: сначала показатели в процентах, где оно в пунктах."""
    positions = _positions(region)
    for key in (UNEMPLOYMENT, EMPLOYMENT, POVERTY, LIFE):
        position = positions.get(key)
        if position is not None and position.change is not None and position.trend:
            return _trend_example(position, region)
    return None


def _real_terms(region: Territory) -> Example | None:
    """Реальное изменение первого денежного ряда, у которого есть индексы за все годы."""
    for position in _positions(region).values():
        if position.is_money and position.real_ratio is not None and position.change is not None:
            return _trend_example(position, region)
    return None


def _similar(region: Territory) -> Example | None:
    """Шесть ближайших регионов и признак наибольшего различия с первым."""
    from apps.analytics.similar import similar_territories

    found = similar_territories(region.code)
    if found is None or not found.is_available:
        return None
    first = found.items[0]
    return Example(
        subject=_subject(region.name, found.year),
        lines=(
            _("Похожие: %(names)s.") % {"names": ", ".join(item["name"] for item in found.items)},
            _("С регионом «%(name)s» сильнее всего различается «%(feature)s».")
            % {"name": first["name"], "feature": first["apart_label"]},
        ),
        url=reverse("catalog:territory-detail", kwargs={"slug": region.slug}),
    )


# --- Качество данных ------------------------------------------------------------------------


def _coverage(region: Territory) -> Example | None:  # noqa: ARG001 - число рядов общее
    """Сколько рядов сайта пригодны к сравнениям."""
    total = Series.objects.count()
    ready = Series.objects.filter(is_analysis_ready=True).count()
    if not total:
        return None
    return Example(
        subject=_("ряды сайта"),
        lines=(
            _("Рядов, пригодных к сравнениям: %(ready)s из %(total)s.")
            % {"ready": ru_number(ready, 0), "total": ru_number(total, 0)},
        ),
        url=reverse("catalog:dataset"),
    )


# --- Нормализация, веса, свёртка ----------------------------------------------------------------


def _normalization(region: Territory) -> Example | None:
    """Оценка региона по средней зарплате четырьмя способами и середина шкалы min-max."""
    item = _featured(WAGE)
    year = latest_full_year([WAGE])
    if item is None or year is None:
        return None
    values = _values(WAGE, year)
    if region.code not in values:
        return None
    codes = sorted(values)
    raw: list[float | None] = [values[code] for code in codes]
    position = codes.index(region.code)
    scores = {
        method: normalization.normalize(raw, method=method).values
        for method in ("minmax", "zscore", "robust", "rank")
    }
    if any(score[position] is None for score in scores.values()):
        return None
    middle = float(np.median([value for value in scores["minmax"] if value is not None]))
    return Example(
        subject=_subject(item.short_title, region.name, year),
        lines=(
            _("min-max %(minmax)s, z-оценка %(zscore)s, робастная %(robust)s, ранговая %(rank)s.")
            % {method: ru_number(score[position], 0) for method, score in scores.items()},
            _("Половина регионов при min-max получает меньше %(middle)s из 100.")
            % {"middle": ru_number(middle, 0)},
        ),
    )


def _matrix(keys: tuple[str, ...]) -> tuple[int, np.ndarray, list[Any]] | None:
    """Нормированная матрица регионов по рядам за общий год; направленность учтена."""
    items = [_featured(key) for key in keys]
    year = min((latest_full_year([key]) or 0) for key in keys)
    if not year or any(item is None for item in items):
        return None
    panels = [_values(key, year) for key in keys]
    codes = sorted(set.intersection(*(set(panel) for panel in panels)))
    columns = [
        normalization.normalize(
            [panel[code] for code in codes],
            polarity="negative" if item.polarity == "negative" else "positive",
        ).values
        for item, panel in zip(items, panels, strict=True)
    ]
    matrix = np.asarray(columns, dtype=float).T
    return year, matrix, items


def _weights(region: Territory) -> Example | None:  # noqa: ARG001 - веса общие
    """Энтропийные веса и веса CRITIC трёх показателей."""
    built = _matrix((WAGE, UNEMPLOYMENT, LIFE))
    if built is None:
        return None
    year, matrix, items = built
    lines = []
    for method, label in (("entropy", _("Энтропия")), ("critic", "CRITIC")):
        weights = build_weights(matrix, method=method)
        if weights.method != method:
            return None
        pairs = ", ".join(
            f"{item.short_title.lower()} {ru_number(value, 2)}"
            for item, value in zip(items, weights.values, strict=True)
        )
        lines.append(f"{label}: {pairs}.")
    return Example(subject=_subject(_("три показателя"), year), lines=tuple(lines))


def _aggregation(region: Territory) -> Example | None:
    """Индекс региона из трёх показателей аддитивной свёрткой и TOPSIS при равных весах."""
    from apps.analytics.core.aggregation import aggregate

    built = _matrix((WAGE, UNEMPLOYMENT, LIFE))
    if built is None:
        return None
    year, matrix, items = built
    codes = sorted(set.intersection(*(set(_values(item.key, year)) for item in items)))
    territories = [{"code": code, "name": code} for code in codes]
    weights = [1 / len(items)] * len(items)
    found = {}
    for method in ("additive", "topsis"):
        scores = aggregate(matrix, weights, territories, method=method)
        place = next(
            (index for index, score in enumerate(scores, start=1) if score.code == region.code),
            None,
        )
        value = next((score.value for score in scores if score.code == region.code), None)
        if place is None or value is None or not math.isfinite(value):
            return None
        found[method] = (value, place, len(scores))
    titles = ", ".join(item.short_title.lower() for item in items)
    return Example(
        subject=_subject(region.name, year),
        lines=(
            _("Равные веса: %(titles)s.") % {"titles": titles},
            _(
                "Аддитивная %(additive)s (%(additive_place)s место), TOPSIS %(topsis)s "
                "(%(topsis_place)s место)."
            )
            % {
                "additive": ru_number(found["additive"][0], 0),
                "additive_place": ordinal(found["additive"][1]),
                "topsis": ru_number(found["topsis"][0], 0),
                "topsis_place": ordinal(found["topsis"][1]),
            },
        ),
    )


# --- Статистика, неравенство, пространство -----------------------------------------------------


def _correlation(region: Territory) -> Example | None:  # noqa: ARG001 - связь по стране
    """Коэффициент Спирмена средней зарплаты и уровня бедности."""
    first, second = _featured(WAGE), _featured(POVERTY)
    year = min(latest_full_year([WAGE]) or 0, latest_full_year([POVERTY]) or 0)
    if first is None or second is None or not year:
        return None
    x, y = _values(WAGE, year), _values(POVERTY, year)
    codes = sorted(set(x) & set(y))
    found = correlation.correlate([x[code] for code in codes], [y[code] for code in codes])
    if found.coefficient is None:
        return None
    return Example(
        subject=_subject(f"{_quoted(first)} и {_quoted(second)}", year),
        lines=(
            _("Спирмен: ρ = %(rho)s, %(p)s, регионов: %(pairs)s.")
            % {
                "rho": _signed(found.coefficient, 2),
                "p": p_level(found.p_value, "p"),
                "pairs": found.pairs,
            },
            (
                _("Связь %(strength)s, обратная.")
                if found.direction == "negative"
                else _("Связь %(strength)s, прямая.")
            )
            % {"strength": found.strength},
        ),
        url=_url("analytics:correlation", series=[WAGE, POVERTY], year=year),
    )


def _inequality(region: Territory) -> Example | None:  # noqa: ARG001 - мера по стране
    """Меры неравенства среднего дохода за последний полный год."""
    item = _featured(INCOME)
    year = latest_full_year([INCOME])
    if item is None or year is None:
        return None
    values, weights = inequality.prepare(list(_values(INCOME, year).values()), None)
    gini = inequality.gini(values, weights)
    theil = inequality.theil(values, weights)
    decile = inequality.decile_ratio(values)
    if gini is None or theil is None or decile is None:
        return None
    return Example(
        subject=_subject(item.short_title, year),
        lines=(
            _("Джини %(gini)s, Тейл %(theil)s, децильный коэффициент %(decile)s.")
            % {
                "gini": ru_number(gini, 3),
                "theil": ru_number(theil, 3),
                "decile": ru_number(decile, 2),
            },
        ),
        url=_url("analytics:inequality", series=INCOME, year=year),
    )


def _convergence(region: Territory) -> Example | None:  # noqa: ARG001 - мера по стране
    """Бета-конвергенция за десять лет у первого ряда, где отрезок не пересекает разрыв."""
    from apps.warehouse.queries import region_panel

    for key in (INCOME, WAGE, GRP_PER_CAPITA, LIFE):
        item = _featured(key)
        last = latest_full_year([key])
        series = Series.objects.filter(key=key).first()
        if item is None or last is None or series is None:
            continue
        first = last - CONVERGENCE_YEARS
        breaks = [row for row in series_breaks(series, first, last) if row["methodological"]]
        if breaks:
            # Отрезок по одним правилам — от последнего разрыва, если он не короче пяти лет.
            start = comparable_from(breaks, last, convergence.MIN_SPAN_YEARS)
            if start is None:
                continue
            first = start
        panel = region_panel(key)
        before, after = panel.get(first) or {}, panel.get(last) or {}
        observations = [
            {"code": code, "start": before[code], "end": after[code]}
            for code in sorted(set(before) & set(after))
        ]
        found = convergence.beta_convergence(observations, first_year=first, last_year=last)
        if found is None:
            continue
        lines = [
            _("β = %(beta)s, %(p)s.")
            % {"beta": _signed(found.beta, 4), "p": p_level(found.fit.slope.p_value, "p")}
        ]
        if found.is_converging and found.half_life is not None:
            lines.append(
                _("Полупериод — %(years)s лет.") % {"years": ru_number(found.half_life, 0)}
            )
        elif found.is_converging:
            lines.append(_("Сближение есть, но полупериод дольше 200 лет."))
        else:
            lines.append(_("Сближения нет: регионы с низким стартом не росли быстрее."))
        return Example(
            subject=_subject(item.short_title, f"{first}–{last}"),
            lines=tuple(lines),
            url=_url("analytics:convergence", series=key, first=first, last=last),
        )
    return None


def _spatial(region: Territory) -> Example | None:  # noqa: ARG001 - мера по стране
    """Глобальный индекс Морана средней зарплаты по соседству с мостами и морем."""
    item = _featured(WAGE)
    year = latest_full_year([WAGE])
    if item is None or year is None:
        return None
    values = _values(WAGE, year)
    codes = sorted(values)
    adjacency = neighbour_map("all")
    weights = spatial.build_matrix(codes, {code: adjacency.get(code, []) for code in codes})
    found = spatial.global_moran(np.asarray([values[code] for code in codes], dtype=float), weights)
    if found is None:
        return None
    return Example(
        subject=_subject(item.short_title, year),
        lines=(
            _("I = %(value)s, %(p)s: %(direction)s.")
            % {
                "value": ru_number(found.value, 2),
                "p": p_level(found.p_value, "p"),
                "direction": DIRECTIONS[found.direction],
            },
        ),
        url=_url("analytics:spatial", series=WAGE, year=year),
    )


BUILDERS: dict[str, Callable[[Territory], Example | None]] = {
    "deduplication": _revision,
    "comparability-breaks": _breaks,
    "map-classes": _map_classes,
    "standing": _standing,
    "five-year-change": _five_year,
    "real-terms": _real_terms,
    "similar-regions": _similar,
    "coverage": _coverage,
    "normalization": _normalization,
    "weights": _weights,
    "aggregation": _aggregation,
    "correlation": _correlation,
    "inequality": _inequality,
    "convergence": _convergence,
    "spatial": _spatial,
}
