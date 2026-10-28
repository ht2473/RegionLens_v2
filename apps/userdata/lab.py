"""
Лаборатория: панель данных исследования и действия «Что сделать» у показателя.

Панель — источники исследования: свои таблицы (ряды по показателям) и ряды сайта, стоящие
на карточках или выбранные в панели. Действие ставит карточку на поле: вид, ответ числами
или пересчёт ряда своей таблицы — таблица пересобирается с новым рядом, на поле встаёт его
карта. Недоступное — серым и с причиной. Второй показатель — для связи и сравнения двух
рядов: ряд с карточки поля, похожий официальный показатель или выбранный в панели.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db.models import F
from django.http import HttpRequest
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from apps.warehouse import routing

from . import access, build, extract, indicators, jobs, operations, semantics, studies, via
from .models import Dataset, DatasetSeries, DatasetVersion, Study
from .series import PER_CAPITA, UserSeries

# Разделы окна «Что показать»: посмотреть, ответить на вопрос, сравнить, новый показатель,
# сразу несколько карточек.
LOOK, ASK, PAIR, COMPUTE, SETS = "look", "ask", "pair", "compute", "sets"
# Советов в окне «Что показать» — не больше.
ADVICE_SHOWN = 4
# Значение кнопки формы: что поставить на поле; у сравнения — с каким рядом; набор карточек.
VIEW_PREFIX, ANSWER_PREFIX, COMPUTE_PREFIX = "view:", "answer:", "compute:"
COMPARE_PREFIX = "compare:"
SET_PREFIX = "set:"
# Готовые наборы карточек исследования.
SET_PROFILE, SET_PAIR = "profile", "pair"
# Сколько рядов поля и похожих официальных показателей предлагать для сравнения.
FIELD_PARTNERS = 3
OFFICIAL_PARTNERS = 2
# Подсказок «Дальше» под карточкой — не больше.
NEXT_SHOWN = 4
# Ряды, от которых считаются пересчёты: из таблицы и свёртки.
PRIMARY = ("", DatasetSeries.Derived.SLICE_SUM.value, DatasetSeries.Derived.MONTHS.value)
SUFFIXES = {**build.PER_SUFFIXES, **build.RECALC_SUFFIXES}
# Что такое готовый набор: справка у группы действий.
SETS_HINT = gettext_lazy(
    "Набор ставит на поле сразу несколько карточек по выбранному показателю. Карточки, "
    "которые показателю не подходят, в набор не входят. Лишние убираются из меню карточки."
)
# Новый показатель: из пересчётов каталога в окне «Что показать» — эти, остальное — в окне
# «Новый показатель».
QUICK_RECOUNTS = (
    DatasetSeries.Derived.PER_1.value,
    DatasetSeries.Derived.PER_100000.value,
    DatasetSeries.Derived.SHARE.value,
    DatasetSeries.Derived.GROWTH.value,
)
# У каждого пересчёта окна — свой значок и строка «что получится».
RECOUNT_ICONS = {
    DatasetSeries.Derived.PER_1.value: "per-person",
    DatasetSeries.Derived.PER_100000.value: "per-people",
    DatasetSeries.Derived.SHARE.value: "portion",
    DatasetSeries.Derived.GROWTH.value: "percent",
}
RECOUNT_NOTES = {
    DatasetSeries.Derived.PER_1.value: gettext_lazy("Сумма, делённая на число жителей региона"),
    DatasetSeries.Derived.PER_100000.value: gettext_lazy(
        "Сколько приходится на 100 000 жителей — как у преступности и заболеваемости"
    ),
    DatasetSeries.Derived.SHARE.value: gettext_lazy("Какая часть общей суммы у каждого региона"),
    DatasetSeries.Derived.GROWTH.value: gettext_lazy("Насколько значение выросло за год"),
}
RECOUNT_TERMS = {
    DatasetSeries.Derived.PER_1.value: "per-capita",
    DatasetSeries.Derived.PER_100000.value: "per-capita",
}
# Как считаются пересчёты: справка у самого действия, а не в методике.
RECOUNT_HINT = gettext_lazy(
    "Новый показатель встаёт рядом с исходным в вашей таблице. На жителей значение делится "
    "на среднегодовую численность населения года. Доля берётся от суммы субъектов за тот же "
    "год. Процент к прошлому году считается только у положительных значений."
)


@dataclass(frozen=True, slots=True)
class Entry:
    """Ряд в панели: свой или сайта, с тем, что нужно для выбора действий."""

    key: str
    title: str
    detail: str
    unit: str
    first_year: int | None
    last_year: int | None
    regions: int
    is_user: bool
    is_sum: bool = False
    # Тип величины и основа сравнения у сумм — для таблицы правил.
    kind: str = semantics.RELATIVE
    base: str = semantics.PEOPLE
    # Рубли в месяц на человека.
    monthly: bool = False
    is_money: bool = False
    # Деньги в любой валюте: их делят на одного жителя.
    is_currency: bool = False
    # Ряд — пересчёт или формула: от него не считают.
    derived: str = ""
    # Ряд — сумма разрезов показателя: итог, а не часть структуры.
    is_total: bool = False
    # Части структуры, в которую входит ряд: разрезы его показателя или суммы таблицы.
    parts: tuple[str, ...] = ()
    # Пересчёты этого ряда, уже собранные в таблице: код пересчёта → ключ ряда.
    recounts: dict[str, str] = field(default_factory=dict)
    # Панель расчёта поля с этим рядом и выбор частей структуры.
    calc_url: str = ""
    structure_url: str = ""
    related_url: str = ""

    @property
    def years(self) -> int:
        if self.first_year is None or self.last_year is None:
            return 0
        return self.last_year - self.first_year + 1

    @property
    def value(self) -> semantics.Value:
        """Что о ряде нужно таблице правил."""
        return semantics.Value(
            type=self.kind,
            base=self.base,
            is_rubles=self.is_money,
            is_currency=self.is_currency,
            monthly=self.monthly,
            years=self.years,
            regions=self.regions,
            parts=len(self.parts),
            is_user=self.is_user,
        )


@dataclass(frozen=True, slots=True)
class Partner:
    """Второй показатель, предложенный для сравнения: ключ и короткое название."""

    key: str
    title: str
    official: bool = False


@dataclass(frozen=True, slots=True)
class Action:
    """Пункт «Что сделать»: кнопка карточки (``value``) или переход (``href``)."""

    title: str
    icon: str = ""
    value: str = ""
    href: str = ""
    reason: str = ""
    # Подсказка к доступному пункту: «уже посчитано».
    hint: str = ""
    # Ряд, для которого ставится карточка, если не ряд выбора: карта уже собранного пересчёта.
    series: str = ""
    # Окно на странице, куда фрагментом приходит ``href``: панель расчёта.
    dialog: str = ""
    # Что получится — строка под названием в окне «Что показать».
    description: str = ""
    # Термин глоссария рядом с пунктом: определение по нажатию на «?».
    term: str = ""
    # Части одного целого для карточки «Дальше»: вклады составляющих под структурой.
    parts: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Group:
    """Группа действий: «Построить», «Посчитать», «Узнать», «Вместе с …»."""

    code: str
    title: str
    actions: list[Action]
    # Группа недоступна целиком — одна причина вместо пунктов.
    reason: str = ""
    # Как устроены действия группы — пояснение по нажатию «?».
    hint: str = ""


@dataclass(slots=True)
class Indicator:
    """Показатель таблицы в панели: его ряды по разрезам, периодам и пересчётам."""

    title: str
    unit: str
    entries: list[Entry] = field(default_factory=list)


@dataclass(slots=True)
class Source:
    """Своя таблица в панели."""

    title: str
    url: str
    indicators: list[Indicator] = field(default_factory=list)
    open: bool = False


def panel(
    request: HttpRequest, study: Study, selected: str, other: str = "", picking: bool = False
) -> dict[str, Any]:
    """
    Панель данных: свои таблицы, ряды сайта, выбранный ряд и его действия; ``other`` —
    второй ряд для связи, ``picking`` — второй ряд выбирается сейчас.
    """
    blocks = studies.blocks_of(study)
    used = {key for block in blocks for key in studies.series_keys(block)}
    shown = {
        (str(block.get("target") or block.get("question")), key)
        for block in blocks
        for key in studies.series_keys(block)[:1]
    }
    tables = _tables(request, used | {selected, other}, study)
    site_keys = {key for key in used if not routing.is_user_key(key)}
    site_keys |= {key for key in (selected, other) if key and not routing.is_user_key(key)}
    site = [entry for entry in (_site_entry(key) for key in sorted(site_keys)) if entry]
    everything = {
        entry.key: entry
        for source in tables
        for indicator in source.indicators
        for entry in indicator.entries
    } | {entry.key: entry for entry in site}
    chosen = everything.get(selected)
    partner = everything.get(other) if chosen and other != selected else None
    groups: list[Group] = []
    if chosen:
        from apps.accounts.region import my_region

        partners = _partners(study, chosen, everything)
        groups = actions(
            chosen,
            partner,
            partners=partners,
            region_known=my_region(request) is not None,
            group_url=f"{reverse('userdata:study-group', args=[study.public_id])}?"
            f"{urlencode({'series': chosen.key})}",
        )
    return {
        "tables": tables,
        "site": site,
        "chosen": chosen,
        "partner": partner,
        "picking": bool(chosen) and picking and partner is None,
        "groups": groups,
        "advice": advice(chosen, groups, shown) if chosen and partner is None else [],
        "unavailable": [action for group in groups for action in group.actions if action.reason],
        "used": used,
    }


def _partners(study: Study, chosen: Entry, everything: dict[str, Entry]) -> list[Partner]:
    """
    С чем сравнить выбранный ряд: ряды карточек поля (первые по порядку) и — у своего
    ряда — официальные показатели основного набора с похожим названием.
    """
    found: dict[str, Partner] = {}
    for block in studies.blocks_of(study):
        keys = studies.series_keys(block)
        key = keys[0] if keys else ""
        entry = everything.get(key)
        if entry is None or key in found or _same_base(key, chosen.key):
            continue
        found[key] = Partner(key, entry.title, official=not entry.is_user)
        if len(found) >= FIELD_PARTNERS:
            break
    if chosen.is_user:
        for item in similar_official(chosen.title)[:OFFICIAL_PARTNERS]:
            if item.key not in found:
                found[item.key] = Partner(item.key, item.short_title, official=True)
    return list(found.values())


def _same_base(first: str, second: str) -> bool:
    """
    Один ряд таблицы или его пересчёт: сравнивать их незачем. Код пересчёта — код ряда
    с суффиксом через дефис.
    """
    if first == second:
        return True
    if not (routing.is_user_key(first) and routing.is_user_key(second)):
        return False
    one, other = (key.removeprefix(routing.USER_PREFIX).split(":", 1) for key in (first, second))
    return one[0] == other[0] and one[1].split("-")[0] == other[1].split("-")[0]


def similar_official(title: str) -> list[Any]:
    """
    Показатели основного набора с похожим названием — тем же разбором, что у поиска
    вопросом: хотя бы половина значимых слов названия и оценка не случайная.
    """
    from apps.search.index import series_index
    from apps.search.parse import MIN_COVERAGE, MIN_SCORE
    from apps.search.text import stems
    from apps.warehouse.queries import featured_set

    words = stems(title)
    if not words:
        return []
    by_key = featured_set().by_key()
    return [
        by_key[hit.key]
        for hit in series_index().search(words, limit=OFFICIAL_PARTNERS + 1)
        if hit.key in by_key
        and hit.score >= MIN_SCORE
        and len(hit.matched) / len(set(words)) >= MIN_COVERAGE
    ]


def _tables(request: HttpRequest, wanted: set[str], study: Study) -> list[Source]:
    """
    Свои собранные таблицы: показатели и их ряды со значениями; формула, открытая отсюда,
    вернётся в исследование.
    """
    found = []
    datasets = access.owned(request).select_related("current_version")
    for dataset in datasets:
        version = dataset.current_version
        if version is None or version.state != DatasetVersion.State.BUILT:
            continue
        described = indicators.indicators_of(version)
        money = {item.name for item in described if item.is_money}
        currency = {item.name for item in described if item.is_currency}
        source = Source(
            title=dataset.title,
            url=reverse("userdata:dataset", args=[dataset.public_id]),
        )
        records = [record for record in version.series.order_by("order") if record.values_count]
        recounts: dict[str, dict[str, str]] = {}
        for record in records:
            if record.base_code and record.derived in SUFFIXES:
                key = f"{routing.USER_PREFIX}{dataset.code}:{record.code}"
                recounts.setdefault(record.base_code, {})[record.derived] = key
        grouped: dict[str, Indicator] = {}
        for record in records:
            item = UserSeries(record, dataset)
            derived = (
                str(DatasetSeries.Derived(record.derived).label)
                if record.derived and record.derived != DatasetSeries.Derived.FORMULA
                else ""
            )
            value = item.value()
            entry = Entry(
                key=item.key,
                title=record.title,
                detail=", ".join(
                    part
                    for part in (item.name, derived)
                    if part and part.casefold() not in record.title.casefold()
                ),
                unit=record.unit,
                first_year=record.first_year,
                last_year=record.last_year,
                regions=record.regions_count,
                is_user=True,
                is_sum=semantics.is_additive(value.type),
                kind=value.type,
                base=value.base,
                monthly=value.monthly,
                is_money=record.indicator in money,
                is_currency=record.indicator in currency,
                derived=record.derived if record.derived not in PRIMARY else "",
                is_total=record.derived == DatasetSeries.Derived.SLICE_SUM,
                recounts=recounts.get(record.code, {}),
                calc_url=f"{reverse('userdata:calc', args=[study.public_id])}?"
                f"{urlencode({'series': item.key})}",
                structure_url=f"{reverse('userdata:structure', args=[study.public_id])}?"
                f"{urlencode({'series': item.key})}",
                related_url=f"{reverse('userdata:related', args=[dataset.public_id])}?"
                f"{urlencode({'series': item.key})}",
            )
            grouped.setdefault(record.title, Indicator(title=record.title, unit=record.unit))
            grouped[record.title].entries.append(entry)
            if item.key in wanted:
                source.open = True
        source.indicators = _with_parts(list(grouped.values()))
        found.append(source)
    if found and not any(source.open for source in found):
        found[0].open = True
    return found


def _with_parts(groups: list[Indicator]) -> list[Indicator]:
    """
    Возможные части структуры для сумм таблицы: исходные суммы той же единицы (не пересчёты
    и не сумма разрезов — она итог). Какие из них части одного целого, решает человек.
    """

    def plain(entry: Entry) -> bool:
        return entry.is_sum and not entry.derived and not entry.is_total

    by_unit: dict[str, list[str]] = {}
    for group in groups:
        for entry in group.entries:
            if plain(entry):
                by_unit.setdefault(entry.unit, []).append(entry.key)
    for group in groups:
        for index, entry in enumerate(group.entries):
            same = by_unit.get(entry.unit, [])
            if entry.is_sum and not entry.derived and len(same) > 1:
                group.entries[index] = replace(entry, parts=tuple(same))
    return groups


def structure_choice(request: HttpRequest, study: Study, key: str) -> tuple[list[Entry], list[str]]:
    """
    Части структуры для ряда своей таблицы: возможные (суммы той же единицы) и отмеченные
    заранее — разрезы его показателя, иначе показатели с тем же началом названия
    («Выбросы от стационарных…» и «Выбросы от передвижных…»).
    """
    for source in _tables(request, {key}, study):
        everything = {entry.key: entry for group in source.indicators for entry in group.entries}
        chosen = everything.get(key)
        if chosen is None:
            continue
        candidates = [everything[part] for part in chosen.parts if part in everything]
        group = next(group for group in source.indicators if chosen in group.entries)
        slices = [entry.key for entry in group.entries if entry.key in chosen.parts]
        if len(slices) > 1:
            return candidates, slices
        stem = _stem(chosen.title)
        alike = [entry.key for entry in candidates if _stem(entry.title) == stem]
        return candidates, alike if len(alike) > 1 else []
    return [], []


def _stem(title: str) -> str:
    """Первые два слова названия — общее начало частей одного целого."""
    return " ".join(title.casefold().split()[:2])


def _site_entry(key: str) -> Entry | None:
    """Ряд сайта в панели: короткое название и единица — как на холсте."""
    from apps.catalog.indicator import describe_series
    from apps.catalog.models import Series
    from apps.warehouse.duckdb_client import WarehouseNotBuiltError
    from apps.warehouse.queries import available_years

    series = Series.objects.select_related("indicator", "unit").filter(key=key).first()
    if series is None:
        return None
    item = describe_series(series)
    try:
        years = available_years(key)
    except WarehouseNotBuiltError:
        years = []
    unit_kind = series.unit.kind if series.unit else ""
    return Entry(
        key=key,
        title=item.short_title,
        detail="",
        unit=item.unit_label,
        first_year=min(years) if years else None,
        last_year=max(years) if years else None,
        regions=85,
        is_user=False,
        is_sum=bool(item.absolute),
        kind=semantics.official_type(is_sum=bool(item.absolute), unit_kind=unit_kind),
        base=semantics.guess_base(item.short_title),
    )


def actions(
    entry: Entry,
    partner: Entry | None = None,
    *,
    partners: list[Partner] | None = None,
    region_known: bool = True,
    group_url: str = "",
) -> list[Group]:
    """
    Что показать по ряду — разделы окна «Что показать»: посмотреть, ответить на вопрос,
    сравнить, новый показатель, сразу несколько карточек. У каждого пункта — что получится;
    недоступное — с причиной из таблицы правил (окно собирает такие пункты в одну строку).
    ``partners`` — что предложить для сравнения без выбора; ``group_url`` — окно выбора своей
    группы регионов.
    """
    value = entry.value

    def why(action: str) -> str:
        found = semantics.verdict(action, value, region_known=region_known)
        return "" if via.passable(found, is_user=entry.is_user) else found.reason

    groups = [
        Group(LOOK, _("Посмотреть"), _look(entry, why)),
        Group(ASK, _("Ответить на вопрос"), _ask(entry, why)),
        Group(PAIR, _("Сравнить"), _pair(entry, partner, partners, why, group_url)),
        _compute_group(entry),
        Group(SETS, _("Сразу несколько карточек"), _sets(why), hint=str(SETS_HINT)),
    ]
    return [
        replace(group, actions=[_via_note(action, entry) for action in group.actions])
        for group in groups
    ]


def _via_note(action: Action, entry: Entry) -> Action:
    """У суммы пункт, который считается по пересчёту: какой пересчёт и есть ли он уже."""
    if action.reason or action.value not in semantics.RULES:
        return action
    found = semantics.verdict(action.value, entry.value, data=False)
    if not via.passable(found, is_user=entry.is_user):
        return action
    area = found.via == semantics.PER_KM2
    if via.built(entry.key, found.via):
        note = _("считается по значению на км²") if area else _("считается по значению на жителя")
    else:
        note = (
            _("сначала посчитает значение на км² — оно встанет в таблицу")
            if area
            else _("сначала посчитает значение на жителя — оно встанет в таблицу")
        )
    description = f"{action.description}. {str(note).capitalize()}" if action.description else note
    return replace(action, description=description)


def _look(entry: Entry, why: Any) -> list[Action]:
    return [
        Action(
            _("Карта"),
            icon="map",
            value=f"{VIEW_PREFIX}map",
            reason=why("view:map"),
            description=_("Значения регионов на карте; у сумм — кругами по величине")
            if entry.is_sum
            else _("Значения регионов на карте цветом"),
            term="symbol-map" if entry.is_sum else "",
        ),
        Action(
            _("Линии по годам"),
            icon="dynamics",
            value=f"{VIEW_PREFIX}compare",
            reason=why("view:compare"),
            description=_("Как менялось значение у отмеченных регионов и по России"),
        ),
        Action(
            _("Рейтинг"),
            icon="ranking",
            value=f"{VIEW_PREFIX}rankings",
            reason=why("view:rankings"),
            description=_("Все регионы по порядку: место и значение"),
        ),
        Action(
            _("Распределение"),
            icon="distribution",
            value=f"{VIEW_PREFIX}distribution",
            reason=why("view:distribution"),
            description=_("Как разбросаны значения и где большинство регионов"),
        ),
        Action(
            _("Таблица"),
            icon="table",
            value=f"{VIEW_PREFIX}table",
            reason=why("view:table"),
            description=_("Значения регионов по годам числами"),
        ),
        Action(
            _("Малые графики"),
            icon="multiples",
            value=f"{ANSWER_PREFIX}multiples",
            reason=why("answer:multiples"),
            description=_("Ход по годам у каждого региона, по федеральным округам"),
        ),
        Action(
            _("Из чего складывается"),
            icon="structure",
            href=entry.structure_url,
            reason=why("view:structure"),
            dialog="structure-dialog",
            description=_("Доли частей в каждом регионе — столбцы по 100 %"),
            term="structure",
        ),
    ]


def _ask(entry: Entry, why: Any) -> list[Action]:  # noqa: ARG001 — как у прочих разделов
    return [
        Action(
            _("Главное в цифрах"),
            icon="list",
            value=f"{ANSWER_PREFIX}notable",
            reason=why("answer:notable"),
            description=_("Наибольшее и наименьшее значение и сколько регионов выросло"),
        ),
        Action(
            _("Кто впереди и кто позади"),
            icon="podium",
            value=f"{ANSWER_PREFIX}leaders",
            reason=why("answer:leaders"),
            description=_("Три первых и три последних региона"),
        ),
        Action(
            _("Где мой регион"),
            icon="map-pin",
            value=f"{ANSWER_PREFIX}mine",
            reason=why("answer:mine"),
            description=_("Место вашего региона среди всех и в своём округе"),
        ),
        Action(
            _("Как изменилось по России"),
            icon="trend",
            value=f"{ANSWER_PREFIX}change",
            reason=why("answer:change"),
            description=_("Значение по России и изменение за последние годы"),
        ),
        Action(
            _("У кого рост больше всего"),
            icon="growth",
            value=f"{ANSWER_PREFIX}growth",
            reason=why("answer:growth"),
            description=_("Регионы с наибольшим и наименьшим изменением"),
        ),
        Action(
            _("За счёт каких регионов изменилась сумма"),
            icon="waterfall",
            value=f"{ANSWER_PREFIX}contributions",
            reason=why("answer:contributions"),
            description=_("Сколько добавил или убавил каждый регион"),
            term="change-contribution",
        ),
        Action(
            _("Сравнить федеральные округа"),
            icon="districts",
            value=f"{ANSWER_PREFIX}districts",
            reason=why("answer:districts"),
            description=_("Значение каждого федерального округа и его доля"),
        ),
        Action(
            _("Велик ли разрыв между регионами"),
            icon="inequality",
            value=f"{ANSWER_PREFIX}spread",
            reason=why("answer:spread"),
            description=_("Во сколько раз верхние регионы опережают нижние и как менялся разрыв"),
            term="decile-ratio",
        ),
        Action(
            _("Догоняют ли отстающие"),
            icon="convergence",
            value=f"{ANSWER_PREFIX}convergence",
            reason=why("answer:convergence"),
            description=_("Сокращается ли разброс и растут ли быстрее отстающие регионы"),
            term="sigma-convergence",
        ),
        Action(
            _("Похожи ли соседние регионы"),
            icon="spatial",
            value=f"{ANSWER_PREFIX}neighbours",
            reason=why("answer:neighbours"),
            description=_("Есть ли на карте сгущения высоких и низких значений"),
            term="moran-index",
        ),
        Action(
            _("С чем это связано"),
            icon="related",
            value=f"{ANSWER_PREFIX}related",
            reason=why("answer:related"),
            description=_("Показатели сайта, сильнее всего связанные с вашим"),
            term="correlation-coefficient",
        ),
    ]


def _pair(
    entry: Entry,
    partner: Entry | None,
    partners: list[Partner] | None,
    why: Any,
    group_url: str,
) -> list[Action]:
    group = Action(
        _("Моя группа регионов против остальных"),
        icon="group",
        href=group_url,
        reason=why("answer:group") or ("" if group_url else _("группы — в исследовании")),
        dialog="group-dialog",
        description=_("Итог вашей группы и остальных регионов по годам"),
        term="weighted-mean",
    )
    if partner is not None:
        # Связь двух показателей: правило — для обоих рядов.
        relation = (
            why("answer:relation") or semantics.verdict("answer:relation", partner.value).reason
        )
        return [
            Action(
                _("Сравнение с «%(title)s»") % {"title": partner.title},
                icon="compare",
                value=f"{ANSWER_PREFIX}comparison",
                reason=why("answer:comparison"),
                description=_("Оба показателя по годам и где места регионов расходятся"),
            ),
            Action(
                _("Связь и облако точек"),
                icon="correlation",
                value=f"{ANSWER_PREFIX}relation",
                reason=relation,
                description=_("Насколько регионы с высоким одним показателем высоки и по другому"),
                term="correlation-coefficient",
            ),
            Action(
                _("Сравнить два показателя — несколько карточек"),
                icon="pair",
                value=f"{SET_PREFIX}{SET_PAIR}",
                reason=why("set:pair"),
                description=_("Сравнение, связь и карта каждого показателя"),
            ),
            group,
        ]
    offered = [
        Action(
            _("Сравнить с «%(title)s»") % {"title": item.title},
            icon="compare",
            value=f"{COMPARE_PREFIX}{item.key}",
            description=_("Официальный показатель сайта")
            if item.official and entry.is_user
            else _("Показатель с этого поля"),
        )
        for item in partners or []
    ]
    return [
        *offered,
        Action(
            _("Выбрать второй показатель"),
            icon="search",
            href=_pick_url(entry.key),
            description=_("Свой или сайта — из панели «Данные»"),
        ),
        group,
    ]


def _sets(why: Any) -> list[Action]:
    return [
        Action(
            _("Профиль показателя"),
            icon="layers",
            value=f"{SET_PREFIX}{SET_PROFILE}",
            reason=why("set:profile"),
            description=_("Карта, главное, рейтинг, линии, распределение, округа, изменение"),
        ),
    ]


def advice(entry: Entry, groups: list[Group], shown: set[tuple[str, str]]) -> list[Action]:
    """
    «Советуем» — первые пункты окна, выбранные по типу и данным: у суммы — карта, пересчёт
    на жителей, рейтинг, вклады в изменение; у относительной — карта, рейтинг, кто вырос,
    линии, связь; у индекса — карта, рейтинг, линии, главное в цифрах. Уже стоящее на поле
    для этого ряда и недоступное не советуется.
    """
    by_value = {
        action.value or action.href: action
        for group in groups
        for action in group.actions
        if not action.reason
    }
    wanted = [f"{VIEW_PREFIX}map"]
    if entry.is_user and entry.is_sum and not entry.derived:
        per = semantics.suggested_per(entry.kind, entry.base, entry.title, entry.unit)
        if per and per not in entry.recounts:
            wanted.append(f"{COMPUTE_PREFIX}{per}")
    wanted += [f"{VIEW_PREFIX}rankings"]
    if entry.years > 1:
        if entry.is_sum:
            wanted.append(f"{ANSWER_PREFIX}contributions")
        elif entry.kind == semantics.RELATIVE:
            wanted.append(f"{ANSWER_PREFIX}growth")
        wanted.append(f"{VIEW_PREFIX}compare")
    closing = ["related", "notable"] if entry.kind != semantics.INDEX else ["notable", "related"]
    wanted += [f"{ANSWER_PREFIX}{name}" for name in closing]
    found = []
    for value in wanted:
        kind = value.split(":", 1)[-1]
        if (kind, entry.key) in shown or value not in by_value:
            continue
        found.append(by_value[value])
        if len(found) == ADVICE_SHOWN:
            break
    return found


def _pick_url(key: str) -> str:
    return f"?{urlencode({'series': key, 'pick': '1'})}#study-sources"


def _compute_group(entry: Entry) -> Group:
    """
    Новый показатель из ряда своей таблицы: самые частые пересчёты одним нажатием
    (собранные — сразу на поле), остальные расчёты и формула — в окне «Новый показатель».
    """
    title = _("Новый показатель")
    calc = Action(
        _("Другие расчёты и формула"),
        icon="calculator",
        href=entry.calc_url,
        dialog="calc-dialog",
        description=_("Доля, темп роста, базовый год, Россия = 100 и своя формула"),
    )
    if not entry.is_user:
        return Group(COMPUTE, title, [], reason=_("новый показатель считается из своих таблиц"))
    if entry.derived:
        return Group(COMPUTE, title, [calc])
    info = operations.Info(
        key=entry.key,
        title=entry.title,
        unit=entry.unit,
        is_sum=entry.is_sum,
        is_currency=entry.is_currency,
        is_rubles=entry.is_money,
        first_year=entry.first_year,
        last_year=entry.last_year,
        regions=entry.regions,
        kind=entry.kind,
        base=entry.base,
        monthly=entry.monthly,
    )
    found = [
        Action(
            str(operation.title),
            icon=RECOUNT_ICONS[operation.recount],
            value=f"{COMPUTE_PREFIX}{operation.recount}",
            reason=operations.reason(operation, info),
            description=_("Уже посчитано — карта встанет на поле")
            if operation.recount in entry.recounts
            else str(RECOUNT_NOTES[operation.recount]),
            term=RECOUNT_TERMS.get(operation.recount, ""),
        )
        for operation in operations.OPERATIONS
        if operation.recount in QUICK_RECOUNTS
    ]
    return Group(COMPUTE, title, [*found, calc], hint=str(RECOUNT_HINT))


def add(
    request: HttpRequest,
    study: Study,
    value: str,
    series_key: str,
    other: str = "",
    *,
    parts: list[str] | None = None,
    group: str = "",
) -> dict[str, Any]:
    """
    Поставить на поле карточку по кнопке «Что сделать»; общий год и регионы — исследования.
    ``parts`` — части структуры, отмеченные человеком (только из возможных для ряда);
    ``group`` — своя группа регионов (только своя). У набора — первая из его карточек.
    """
    if value.startswith(VIEW_PREFIX):
        target = value.removeprefix(VIEW_PREFIX)
        return studies.add_view(study, target, urlencode({"series": series_key}))
    if value in semantics.RULES and value.startswith((ANSWER_PREFIX, SET_PREFIX)):
        # Сумма — через пересчёт на жителя: карточка ставится на него.
        series_key = via.target(request, value, series_key)
        if other and value == f"{ANSWER_PREFIX}relation":
            other = via.target(request, value, other)
    if value.startswith(ANSWER_PREFIX):
        question = value.removeprefix(ANSWER_PREFIX)
        return _add_answer(request, study, question, series_key, other, parts=parts, group=group)
    if value.startswith(COMPARE_PREFIX):
        partner = value.removeprefix(COMPARE_PREFIX)
        return studies.add_answer(study, "comparison", series_key, partner)
    if value.startswith(SET_PREFIX):
        blocks = set_blocks(value.removeprefix(SET_PREFIX), series_key, other)
        return studies.append_all(study, blocks)[0]
    if value.startswith(COMPUTE_PREFIX):
        key = compute(request, series_key, value.removeprefix(COMPUTE_PREFIX))
        return studies.add_view(study, "map", urlencode({"series": key}))
    raise studies.StudyError(_("Такой карточки нет."))


def _add_answer(
    request: HttpRequest,
    study: Study,
    question: str,
    series_key: str,
    other: str,
    *,
    parts: list[str] | None,
    group: str,
) -> dict[str, Any]:
    """Карточка-ответ: у группы — своя группа, у структуры и вкладов частей — части ряда."""
    if question in studies.GROUP_QUESTIONS:
        from . import groups

        try:
            chosen = groups.owned(request).filter(public_id=group).first() if group else None
        except ValueError, ValidationError:
            chosen = None
        if chosen is None:
            raise studies.StudyError(_("Выберите группу регионов."))
        return studies.add_answer(study, question, series_key, group=str(chosen.public_id))
    if question in studies.PARTS_QUESTIONS or (question in studies.SPLIT_QUESTIONS and parts):
        candidates, _chosen = structure_choice(request, study, series_key)
        allowed = {entry.key for entry in candidates}
        wanted = [key for key in dict.fromkeys(parts or []) if key in allowed]
        if question in studies.SPLIT_QUESTIONS and len(wanted) < 2:  # noqa: PLR2004
            raise studies.StudyError(_("Для вкладов составляющих нужны хотя бы две части."))
        return studies.add_answer(study, question, series_key, parts=wanted)
    return studies.add_answer(study, question, series_key, other)


def set_blocks(code: str, key: str, other: str = "") -> list[dict[str, Any]]:
    """
    Готовый набор карточек по ряду: профиль показателя или два показателя. Карточки, которым
    ряд не подходит (один год, мало регионов), в набор не входят.
    """
    info = operations.info_of(key)
    if info is None:
        raise studies.StudyError(_("Такого показателя нет."))
    if code == SET_PROFILE:
        return _profile_blocks(key, info)
    if code == SET_PAIR:
        return _pair_blocks(key, other, info)
    raise studies.StudyError(_("Такого набора нет."))


def _allowed(action: str, info: operations.Info) -> bool:
    return bool(semantics.verdict(action, info.value))


def _profile_blocks(key: str, info: operations.Info) -> list[dict[str, Any]]:
    """Профиль показателя: карта, что заметно, рейтинг, линии, распределение, округа, изменение."""
    query = urlencode({"series": key})
    blocks = [
        studies.view_block("map", query),
        studies.answer_block("notable", key),
        studies.view_block("rankings", query),
    ]
    if _allowed("view:compare", info):
        blocks.append(studies.view_block("compare", query))
    blocks += [studies.view_block("distribution", query), studies.answer_block("districts", key)]
    change = next(
        (
            question
            for question in ("contributions", "growth")
            if _allowed(f"answer:{question}", info)
        ),
        "",
    )
    if change:
        blocks.append(studies.answer_block(change, key))
    return blocks


def _pair_blocks(key: str, other: str, info: operations.Info) -> list[dict[str, Any]]:
    """Два показателя: сравнение, связь (если регионов хватает), карта каждого."""
    blocks = [studies.answer_block("comparison", key, other)]
    partner = operations.info_of(other)
    if _allowed("answer:relation", info) and (
        partner is None or _allowed("answer:relation", partner)
    ):
        blocks.append(studies.answer_block("relation", key, other))
    return [
        *blocks,
        studies.view_block("map", urlencode({"series": key})),
        studies.view_block("map", urlencode({"series": other})),
    ]


def compute(request: HttpRequest, series_key: str, recount: str) -> str:
    """
    Пересчитать ряд своей таблицы: отметить пересчёт в описании показателя и пересобрать
    таблицу; вернуть ключ нового ряда. Собранный уже пересчёт не пересобирается.
    """
    if recount not in SUFFIXES or not routing.is_user_key(series_key):
        raise studies.StudyError(_("Такого пересчёта нет."))
    dataset_code, code = series_key.removeprefix(routing.USER_PREFIX).split(":", 1)
    dataset = access.owned(request).filter(code=dataset_code).first()
    version = dataset.current_version if dataset is not None else None
    if version is None or version.state != DatasetVersion.State.BUILT:
        raise studies.StudyError(_("Таблица ещё не собрана."))
    record = version.series.filter(code=code).first()
    if record is None or record.derived not in PRIMARY:
        raise studies.StudyError(_("Пересчитывается исходный показатель таблицы."))
    target = f"{routing.USER_PREFIX}{dataset_code}:{record.code}{SUFFIXES[recount]}"
    if version.series.filter(code=f"{record.code}{SUFFIXES[recount]}").exists():
        return target
    if not extract.is_current(version):
        raise studies.StudyError(_("Таблицу нужно разобрать заново: откройте её шаг «Показатели»."))
    _mark(version, record.indicator, recount)
    if jobs.start(version, jobs.BUILD) == jobs.FAILED:
        raise studies.StudyError(_("Таблица не пересобрана. Причина — на её шаге «Показатели»."))
    return target


def _mark(version: DatasetVersion, name: str, recount: str) -> None:
    """
    Отметить пересчёт в описании показателя; описание остальных — как было. Пересчёт,
    который показателю не подходит (на жителей у доли), отклоняется, а не теряется молча.
    """
    described = {item.name: item for item in indicators.indicators_of(version)}
    item = described.get(name)
    allowed = (
        indicators.per_allowed(item, recount)
        if item is not None and recount in build.PER_SUFFIXES
        else item is not None and indicators.recalc_allowed(item, recount)
    )
    if not allowed:
        raise studies.StudyError(_("Этот пересчёт показателю не подходит."))
    answers: dict[str, dict[str, Any]] = {}
    for item in described.values():
        answers[item.name] = {
            "title": item.title,
            "unit": item.unit,
            "kind": item.kind,
            "base": item.base,
            "polarity": item.polarity,
            "per": list(item.per),
            "breaks": ", ".join(str(year) for year in item.breaks),
            "break_note": item.break_note,
            "recalc": list(item.recalc),
            "fold_slices": list(item.fold_slices),
            "months": item.months,
        }
    answer = answers.get(name)
    if answer is None:
        raise studies.StudyError(_("Показателя нет в описании таблицы."))
    field_name = "per" if recount in build.PER_SUFFIXES else "recalc"
    if recount not in answer[field_name]:
        answer[field_name].append(recount)
    indicators.save(version, answers)


# --- Собранная таблица — в исследование -------------------------------------------------

# Набор → исследование, из панели которого начата его загрузка. По набору, а не одно на
# сеанс: запомненное раз исследование забирало и таблицы, загруженные потом не из него.
STUDY_SESSION_KEY = "userdata_study_for"
# Сколько незаконченных загрузок помнить.
STUDY_MEMORY = 20


def next_steps(study: Study, block: dict[str, Any]) -> list[Action]:
    """
    «Дальше» под карточкой — короткие пути к пунктам «Что сделать» для ряда своей таблицы:
    на жителя у исходной суммы (карта пересчёта или сам пересчёт), рейтинг, сравнение
    с официальным показателем, линии по годам и кто вырос сильнее — чего на поле ещё нет.
    Только под первой карточкой ряда: одни и те же подсказки под каждой — шум.
    """
    keys = studies.series_keys(block)
    key = keys[0] if keys else ""
    if not routing.is_user_key(key):
        return []
    blocks = studies.blocks_of(study)
    if block.get("question") in studies.PARTS_QUESTIONS:
        return _parts_steps(blocks, block)
    first = next((item for item in blocks if studies.series_keys(item)[:1] == [key]), None)
    if first is None or first["id"] != block["id"]:
        return []
    dataset_code, code = key.removeprefix(routing.USER_PREFIX).split(":", 1)
    dataset = Dataset.objects.select_related("current_version").filter(code=dataset_code).first()
    version = dataset.current_version if dataset is not None else None
    if version is None:
        return []
    record = version.series.filter(code=code).first()
    if record is None:
        return []
    shown = {
        (item.get("target") or item.get("question"), found)
        for item in blocks
        for found in studies.series_keys(item)[:1]
    }
    years = (record.last_year or 0) - (record.first_year or 0) + 1 if record.first_year else 0
    steps: list[Action] = []
    per_capita = _per_capita_step(version, record, shown)
    if per_capita is not None:
        steps.append(per_capita)
    if ("rankings", key) not in shown:
        steps.append(Action(_("Рейтинг"), value=f"{VIEW_PREFIX}rankings"))
    steps.append(Action(_("Сравнить с официальным"), href=_pick_url(key)))
    if years > 1 and ("compare", key) not in shown:
        steps.append(Action(_("Линии по годам"), value=f"{VIEW_PREFIX}compare"))
    changed = _change_step(record, key, shown) if years > 1 else None
    if changed is not None:
        steps.append(changed)
    return [replace(step, series=step.series or key) for step in steps[:NEXT_SHOWN]]


def _per_capita_step(
    version: DatasetVersion, record: DatasetSeries, shown: set[tuple[Any, str]]
) -> Action | None:
    """
    «На жителя» — у исходной суммы: карта уже собранного пересчёта или сам пересчёт; у
    пересчёта, формулы и численности населения его нет.
    """
    item = UserSeries(record, version.dataset)
    if not item.is_sum or record.derived not in PRIMARY:
        return None
    dataset_code = version.dataset.code
    recounts = {
        item.derived: item
        for item in version.series.filter(
            base_code=record.code, derived__in=PER_CAPITA, values_count__gt=0
        )
    }
    built = next((recounts[item] for item in PER_CAPITA if item in recounts), None)
    if built is not None:
        per_key = f"{routing.USER_PREFIX}{dataset_code}:{built.code}"
        if ("map", per_key) in shown:
            return None
        return Action(_("На жителя"), value=f"{VIEW_PREFIX}map", series=per_key)
    suggested = semantics.suggested_per(item.value_type, item.base, record.indicator, record.unit)
    if not suggested:
        return None
    return Action(_("На жителя"), value=f"{COMPUTE_PREFIX}{suggested}")


def _parts_steps(blocks: list[dict[str, Any]], block: dict[str, Any]) -> list[Action]:
    """
    «Дальше» под структурой: как изменилась сумма частей и какая часть дала изменение —
    вклады составляющих, если таких карточек с теми же частями на поле ещё нет и лет больше
    одного.
    """
    parts = list(block.get("parts") or [])
    if any(
        item.get("question") in studies.SPLIT_QUESTIONS and item.get("parts") == parts
        for item in blocks
    ):
        return []
    dataset_code, code = parts[0].removeprefix(routing.USER_PREFIX).split(":", 1)
    record = DatasetSeries.objects.filter(
        version__dataset__code=dataset_code,
        version__dataset__current_version=F("version"),
        code=code,
    ).first()
    if record is None or not record.first_year or record.first_year == record.last_year:
        return []
    return [
        Action(
            _("Почему изменилась сумма"),
            value=f"{ANSWER_PREFIX}contributions",
            series=parts[0],
            parts=tuple(parts),
        )
    ]


def _change_step(record: DatasetSeries, key: str, shown: set[tuple[Any, str]]) -> Action | None:
    """Изменение суммы раскладывается на вклады регионов; у относительной — кто вырос."""
    if semantics.is_additive(record.kind):
        if ("contributions", key) in shown:
            return None
        return Action(_("Почему изменилось"), value=f"{ANSWER_PREFIX}contributions")
    if ("growth", key) in shown:
        return None
    return Action(_("Кто вырос сильнее"), value=f"{ANSWER_PREFIX}growth")


def chosen_study(request: HttpRequest, public_id: str) -> Study | None:
    """Своё исследование по адресу ``?study=``; чужое и неизвестное — ``None``."""
    try:
        identifier = UUID(public_id)
    except ValueError:
        return None
    return studies.owned(request).filter(public_id=identifier).first()


def remember_study(request: HttpRequest, dataset: Dataset, study: Study | None) -> None:
    """Запомнить, что собранная таблица вернётся в исследование, откуда начата загрузка."""
    if study is None:
        return
    memory = dict(request.session.get(STUDY_SESSION_KEY) or {})
    memory[str(dataset.public_id)] = str(study.public_id)
    request.session[STUDY_SESSION_KEY] = dict(list(memory.items())[-STUDY_MEMORY:])


def destination(request: HttpRequest, dataset: Dataset) -> Study | None:
    """Исследование, куда откроется собранная таблица; ``None`` — в новое."""
    remembered = (request.session.get(STUDY_SESSION_KEY) or {}).get(str(dataset.public_id), "")
    return chosen_study(request, remembered) if remembered else None


def starter_blocks(version: DatasetVersion, key: str) -> list[dict[str, Any]]:
    """
    Первые карточки нового исследования по ряду: карта, что заметно, рейтинг, линии
    по годам (если лет больше одного), связь с официальными показателями (если регионов
    хватает для связи).
    """
    code = key.rsplit(":", 1)[-1]
    record = version.series.filter(code=code).first()
    years = (
        record.last_year - record.first_year + 1
        if record is not None and record.first_year and record.last_year
        else 0
    )
    query = urlencode({"series": key})
    blocks = [
        studies.view_block("map", query),
        studies.answer_block("notable", key),
        studies.view_block("rankings", query),
    ]
    if years > 1:
        blocks.append(studies.view_block("compare", query))
    if record is not None and semantics.verdict(
        "answer:related", UserSeries(record, version.dataset).value()
    ):
        blocks.append(studies.answer_block("related", key))
    return blocks


def open_in_study(request: HttpRequest, version: DatasetVersion) -> str | None:
    """
    Адрес исследования с картой первого ряда собранной таблицы: того, откуда начата
    загрузка, иначе того, где ряды таблицы уже есть (без новой карточки), иначе нового.
    Куда попала таблица, говорит сообщение на странице исследования. Исследования
    не завести (предел) — ``None``.
    """
    from django.contrib import messages

    from .views import first_key

    key = first_key(version)
    if key is None:
        return None
    dataset = version.dataset
    study = destination(request, dataset)
    memory = dict(request.session.get(STUDY_SESSION_KEY) or {})
    if memory.pop(str(dataset.public_id), None) is not None:
        request.session[STUDY_SESSION_KEY] = memory
    if study is None:
        existing = next(
            (
                item
                for item in studies.owned(request).order_by("-updated_at")
                if dataset.code in studies.dataset_codes(item)
            ),
            None,
        )
        if existing is not None:
            url = reverse("userdata:study", args=[existing.public_id])
            return f"{url}?{urlencode({'series': key})}"
    created = study is None
    try:
        if study is None:
            study = studies.create(request, dataset.title)
            block = studies.append_all(study, starter_blocks(version, key))[0]
        else:
            block = studies.add_view(study, "map", urlencode({"series": key}))
    except studies.StudyError:
        return None
    text = (
        _("Таблица «%(table)s» собрана: карта, рейтинг и первые выводы — в новом исследовании.")
        if created
        else _("Таблица «%(table)s» собрана, её карта добавлена в исследование «%(study)s».")
    )
    messages.success(request, text % {"table": dataset.title, "study": study.title})
    url = reverse("userdata:study", args=[study.public_id])
    return f"{url}?{urlencode({'series': key, 'added': block['id']})}#block-{block['id']}"
