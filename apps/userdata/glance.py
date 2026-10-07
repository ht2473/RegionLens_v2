"""
«Первый взгляд» на таблицу — правилами, без модели: по ведущему ряду каждого показателя
охват, крайние значения, Джини, глобальный индекс Морана, связь с численностью населения,
сумма субъектов против значения страны, смена состава субъектов и пропуски последнего года;
и какие виды открыть первыми.

Вид величины по данным: если сумма субъектов совпадает со значением страны, величина
складывается; если значение страны лежит между наименьшим и наибольшим значением субъектов,
а сумма с ним не сходится, — это доля или среднее. Иначе вывода нет. Расхождение с описанием
таблицы называется словами; само описание не меняется.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from functools import partial
from typing import Any

import numpy as np
from django.utils.translation import gettext as _

from apps.analytics.core import composition, correlation, inequality, spatial
from apps.analytics.selectors import cached_result, neighbour_map, population_series_key
from apps.catalog.selectors import default_year
from apps.warehouse.queries import COUNTRY_CODE, MIN_YEAR_COVERAGE, region_panel, series_values

from . import matching, recognize
from .models import Dataset, DatasetSeries, DatasetVersion
from .series import FEW_REGIONS, UserSeries

# Сколько показателей разбирается: остальные — по ссылкам на виды.
LEAD_LIMIT = 30
# Сумма субъектов «совпадает» со значением страны при расхождении не больше этого.
SUM_TOLERANCE = 0.02
# Сумма субъектов не меньше этой доли итога страны — часть итога не распределена
# по регионам (преступления на транспорте, расходы вне регионов).
UNALLOCATED_SHARE = 0.85
# Строка России в единицах крупнее, чем у регионов (млрд и млн руб., млн и тыс. м²):
# сумма регионов больше значения России ровно во столько раз.
UNIT_SCALES = (1_000, 1_000_000)
SCALE_TOLERANCE = 0.005
# Связь с численностью, при которой относительная величина похожа на сумму.
POPULATION_LINK = 0.9
# Сколько названий субъектов называть в перечнях.
NAMES_SHOWN = 5
# Редакция содержимого: кэш расчётов не знает о правке кода.
RESULT_REVISION = 1

KIND_SUM = "sum"
KIND_RELATIVE = "relative"


@dataclass(slots=True)
class Look:
    """Первый взгляд на один ряд."""

    series: UserSeries
    first_year: int | None = None
    last_year: int | None = None
    full_year: int | None = None
    regions: int = 0
    fullest: int = 0
    highest: tuple[str, float] | None = None
    lowest: tuple[str, float] | None = None
    gini: float | None = None
    moran: float | None = None
    moran_direction: str = ""
    population: float | None = None
    population_significant: bool = False
    country: float | None = None
    subjects_sum: float | None = None
    data_kind: str = ""
    kind_note: str = ""
    # Во сколько раз единица строки России крупнее единицы регионов; 1 — та же.
    country_scale: int = 1
    entered: list[str] = field(default_factory=list)
    left: list[str] = field(default_factory=list)
    gapped: int = 0
    missing_last: list[str] = field(default_factory=list)
    missing_count: int = 0

    @property
    def composition_text(self) -> str:
        """Смена состава субъектов словами; пусто — состав постоянный."""
        parts = []
        if self.entered:
            parts.append(_("появились — %(names)s") % {"names": _names(self.entered)})
        if self.left:
            parts.append(_("пропали — %(names)s") % {"names": _names(self.left)})
        if self.gapped:
            parts.append(_("с пропусками лет — %(count)s") % {"count": self.gapped})
        if not parts:
            return ""
        return _(
            "Состав субъектов меняется: %(parts)s. Неравенство и сближение по умолчанию "
            "считаются на постоянном составе."
        ) % {"parts": "; ".join(parts)}


def _names(names: list[str]) -> str:
    """Первые названия перечня и сколько ещё."""
    shown = ", ".join(names[:NAMES_SHOWN])
    if len(names) > NAMES_SHOWN:
        shown += " " + _("и ещё %(count)s") % {"count": len(names) - NAMES_SHOWN}
    return shown


def lead_series(version: DatasetVersion, dataset: Dataset) -> list[UserSeries]:
    """
    Ведущий ряд каждого показателя таблицы: итог по разрезам («Всего»), годовой период,
    иначе первый по порядку.
    """
    grouped: dict[str, list[DatasetSeries]] = defaultdict(list)
    for record in version.series.filter(derived="").order_by("order"):
        grouped[record.indicator].append(record)
    leads = []
    for records in grouped.values():
        values: dict[str, set[str]] = defaultdict(set)
        for record in records:
            for header, value in record.slices:
                values[header].add(value)
        best = min(records, key=partial(_lead_score, values=values))
        leads.append(UserSeries(best, dataset))
    return leads[:LEAD_LIMIT]


def _lead_score(record: DatasetSeries, values: dict[str, set[str]]) -> tuple[int, int]:
    """Чем меньше, тем лучше: больше итогов по разрезам, годовой период."""
    totals = sum(1 for header, value in record.slices if recognize.is_total(value, values[header]))
    return (-totals, 0 if record.period == "year:12" else 1)


def glance(dataset: Dataset, version: DatasetVersion) -> dict[str, Any]:
    """Первый взгляд на таблицу: разбор ведущих рядов показателей."""
    leads = lead_series(version, dataset)
    looks = cached_result(
        "userdata-glance",
        {"series": ",".join(item.key for item in leads), "revision": RESULT_REVISION},
        lambda: [_look(item) for item in leads],
    )
    total = version.series.filter(derived="").values("indicator").distinct().count()
    return {
        "looks": looks,
        "more": max(total - len(leads), 0),
    }


def checks(dataset: Dataset, version: DatasetVersion) -> list[Look]:
    """
    Ведущие ряды с замечаниями — для страницы таблицы: вид величины по данным расходится
    с описанием, состав субъектов меняется, у последнего года есть пропуски.
    """
    return [
        look
        for look in glance(dataset, version)["looks"]
        if look.kind_note or look.composition_text or look.missing_count
    ]


def _look(series: UserSeries) -> Look:
    """Разобрать один ряд."""
    from apps.analytics.selectors import region_rows

    look = Look(series=series)
    panel = region_panel(series.key)
    counts = {year: len(values) for year, values in panel.items()}
    if not counts:
        return look
    years = sorted(panel)
    look.first_year, look.last_year = years[0], years[-1]
    look.fullest = max(counts.values())
    look.full_year = default_year(counts)
    if look.full_year is None:
        return look
    values = panel[look.full_year]
    look.regions = len(values)
    names = {row["code"]: row["name"] for row in region_rows()}
    ordered = sorted(values.items(), key=lambda item: item[1])
    look.lowest = (names.get(ordered[0][0], ordered[0][0]), ordered[0][1])
    look.highest = (names.get(ordered[-1][0], ordered[-1][0]), ordered[-1][1])
    if look.regions >= FEW_REGIONS:
        array, weights = inequality.prepare(list(values.values()), None)
        look.gini = inequality.gini(array, weights) if array.size else None
        _spatial(look, values)
    _population(look, values)
    _country(look, values)
    _composition(look, panel, years, names)
    return look


def _spatial(look: Look, values: dict[str, float]) -> None:
    """Глобальный индекс Морана по соседству с мостами и морем."""
    codes = sorted(values)
    neighbours = neighbour_map("all")
    weights = spatial.build_matrix(codes, {code: neighbours.get(code, []) for code in codes})
    found = spatial.global_moran(np.asarray([values[code] for code in codes], dtype=float), weights)
    if found is not None:
        look.moran = found.value
        look.moran_direction = found.direction


def _population(look: Look, values: dict[str, float]) -> None:
    """Связь значений с численностью населения того же года (Спирмен)."""
    key = population_series_key()
    if key is None or look.full_year is None:
        return
    people = region_panel(key)
    year = look.full_year if look.full_year in people else max(people, default=None)
    if year is None:
        return
    codes = sorted(values)
    found = correlation.correlate(
        [values[code] for code in codes], [people[year].get(code) for code in codes]
    )
    look.population = found.coefficient
    look.population_significant = found.is_significant


def _country(look: Look, values: dict[str, float]) -> None:
    """Значение страны против суммы и разброса субъектов: вид величины по данным."""
    year = look.full_year
    if year is None:
        return
    wanted = [COUNTRY_CODE, *matching.NESTED_PARENTS]
    found = series_values([look.series.key], wanted).get(look.series.key, {})
    value = found.get(COUNTRY_CODE, {}).get(year)
    described = KIND_SUM if look.series.is_sum else KIND_RELATIVE
    if value is not None and look.regions == look.fullest and value != 0:
        look.country = value
        look.subjects_sum = sum(values.values())
        # Область без округов не вычислена (у долей и средних её нет): в сумму идёт итог
        # области за вычетом её округов.
        for total, alone in matching.NESTED_PARENTS.items():
            whole = found.get(total, {}).get(year)
            if alone not in values and whole is not None:
                members = matching.NESTED_MEMBERS[total]
                look.subjects_sum += whole - sum(values.get(code, 0) for code in members)
        look.country_scale = country_scale(look.subjects_sum, value, list(values.values()))
        if look.country_scale > 1:
            look.data_kind = KIND_SUM
        else:
            look.data_kind = data_kind(look.subjects_sum, value, list(values.values()))
    elif (
        described == KIND_RELATIVE
        and look.population is not None
        and look.population >= POPULATION_LINK
        and look.population_significant
    ):
        look.kind_note = _(
            "Значения растут вместе с численностью населения: похоже на сумму. Если это так, "
            "отметьте «сумма» в описании — появится пересчёт на жителей."
        )
        return
    if look.country_scale > 1:
        look.kind_note = _(
            "Значение России в таблице — в единицах в %(scale)s раз крупнее, чем у регионов: "
            "сумма регионов больше его ровно во столько раз. Пересчёт «Россия = 100» по такой "
            "строке неверен."
        ) % {"scale": f"{look.country_scale:,}".replace(",", chr(0xA0))}
        if described == KIND_RELATIVE:
            look.kind_note += " " + _("Величина складывается по регионам, как сумма.")
        return
    if look.data_kind == KIND_SUM and described == KIND_RELATIVE:
        look.kind_note = _(
            "Значения регионов в сумме дают значение России (или большую его часть): "
            "величина складывается по регионам, как сумма."
        )
    elif look.data_kind == KIND_RELATIVE and described == KIND_SUM:
        look.kind_note = _(
            "Значение России лежит между значениями регионов и не равно их сумме: похоже "
            "на долю или среднее, а не на сумму."
        )


def country_scale(subjects_sum: float, country: float, values: list[float]) -> int:
    """
    Во сколько раз единица строки России крупнее единицы регионов: сумма неотрицательных
    значений не меньше двадцати субъектов больше значения России ровно в 1 000 или в 1 000 000
    раз. Иначе — 1.
    """
    if len(values) < FEW_REGIONS or min(values) < 0 or country <= 0:
        return 1
    for scale in UNIT_SCALES:
        if abs(subjects_sum / country / scale - 1) <= SCALE_TOLERANCE:
            return scale
    return 1


def data_kind(subjects_sum: float, country: float, values: list[float]) -> str:
    """
    Вид величины по данным: «sum» — сумма субъектов совпадает с итогом страны или
    составляет его большую часть, а итог больше любого субъекта (часть итога не распределена
    по регионам); «relative» — значение страны лежит между значениями субъектов, а их сумма
    больше него примерно в n раз (n — число субъектов: от n/4 до 4n); иначе пусто. Субъектов
    должно быть не меньше двадцати, значения — неотрицательные.
    """
    if len(values) < FEW_REGIONS or min(values) < 0 or country <= 0:
        return ""
    ratio = subjects_sum / country
    if abs(ratio - 1) <= SUM_TOLERANCE:
        return KIND_SUM
    if UNALLOCATED_SHARE <= ratio < 1 and country > max(values):
        return KIND_SUM
    count = len(values)
    if min(values) <= country <= max(values) and count / 4 <= ratio <= count * 4:
        return KIND_RELATIVE
    return ""


def _composition(
    look: Look, panel: dict[int, dict[str, float]], years: list[int], names: dict[str, str]
) -> None:
    """Смена состава субъектов за все годы и пропуски последнего года."""
    if len(years) > 1:
        found = composition.compose(panel, years[0], years[-1], share=MIN_YEAR_COVERAGE)
        look.entered = [names.get(item.code, item.code) for item in found.entered]
        look.left = [names.get(item.code, item.code) for item in found.left]
        look.gapped = len(found.gapped)
    last = panel[years[-1]]
    if len(last) < look.fullest:
        everyone = {code for values in panel.values() for code in values}
        missing = sorted(names.get(code, code) for code in everyone - set(last))
        look.missing_count = len(missing)
        look.missing_last = missing[:NAMES_SHOWN]
