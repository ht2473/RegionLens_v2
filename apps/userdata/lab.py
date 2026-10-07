"""
Лаборатория: панель данных исследования и действия «Что сделать» у показателя.

Панель — источники исследования: свои таблицы (ряды по показателям) и ряды сайта, стоящие
на карточках или выбранные в панели. Действие ставит карточку на поле: вид, ответ числами
или пересчёт ряда своей таблицы — таблица пересобирается с новым рядом, на поле встаёт его
карта. Недоступное — серым и с причиной. Второй показатель выбирается для связи двух рядов.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

from django.http import HttpRequest
from django.urls import reverse
from django.utils.translation import gettext as _

from apps.warehouse import routing

from . import access, build, extract, indicators, jobs, studies
from .models import DatasetSeries, DatasetVersion, Study
from .series import FEW_REGIONS, UserSeries

BUILD, COMPUTE, LEARN, PAIR = "build", "compute", "learn", "pair"
# Значение кнопки формы: что поставить на поле.
VIEW_PREFIX, ANSWER_PREFIX, COMPUTE_PREFIX = "view:", "answer:", "compute:"
# Ряды, от которых считаются пересчёты: из таблицы и свёртки.
PRIMARY = ("", DatasetSeries.Derived.SLICE_SUM.value, DatasetSeries.Derived.MONTHS.value)
# Пересчёты в меню по порядку: код, подпись, только для сумм, только для денег.
RECOUNTS = (
    (DatasetSeries.Derived.PER_100000.value, True, False),
    (DatasetSeries.Derived.PER_1000.value, True, False),
    (DatasetSeries.Derived.PER_KM2.value, True, False),
    (DatasetSeries.Derived.RUSSIA.value, False, False),
    (DatasetSeries.Derived.GROWTH.value, False, False),
    (DatasetSeries.Derived.SHARE.value, True, False),
    (DatasetSeries.Derived.REAL.value, False, True),
)
SUFFIXES = {**build.PER_SUFFIXES, **build.RECALC_SUFFIXES}


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
    is_money: bool = False
    # Ряд — пересчёт или формула: от него не считают.
    derived: str = ""
    # Пересчёты этого ряда, уже собранные в таблице: код пересчёта → ключ ряда.
    recounts: dict[str, str] = field(default_factory=dict)
    formula_url: str = ""
    related_url: str = ""

    @property
    def years(self) -> int:
        if self.first_year is None or self.last_year is None:
            return 0
        return self.last_year - self.first_year + 1


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


@dataclass(frozen=True, slots=True)
class Group:
    """Группа действий: «Построить», «Посчитать», «Узнать», «Вместе с …»."""

    code: str
    title: str
    actions: list[Action]
    # Группа недоступна целиком — одна причина вместо пунктов.
    reason: str = ""


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
    series_count: int
    indicators: list[Indicator] = field(default_factory=list)
    open: bool = False


def panel(
    request: HttpRequest, study: Study, selected: str, other: str = "", picking: bool = False
) -> dict[str, Any]:
    """
    Панель данных: свои таблицы, ряды сайта, выбранный ряд и его действия; ``other`` —
    второй ряд для связи, ``picking`` — второй ряд выбирается сейчас.
    """
    used = {key for block in studies.blocks_of(study) for key in studies.series_keys(block)}
    tables = _tables(request, used | {selected, other})
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
    return {
        "tables": tables,
        "site": site,
        "chosen": chosen,
        "partner": partner,
        "picking": bool(chosen) and picking and partner is None,
        "groups": actions(chosen, partner) if chosen else [],
        "used": used,
    }


def _tables(request: HttpRequest, wanted: set[str]) -> list[Source]:
    """Свои собранные таблицы: показатели и их ряды со значениями."""
    found = []
    datasets = access.owned(request).select_related("current_version")
    for dataset in datasets:
        version = dataset.current_version
        if version is None or version.state != DatasetVersion.State.BUILT:
            continue
        money = {item.name for item in indicators.indicators_of(version) if item.is_money}
        source = Source(
            title=dataset.title,
            url=reverse("userdata:dataset", args=[dataset.public_id]),
            series_count=version.series_count,
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
                is_sum=item.is_sum,
                is_money=record.indicator in money,
                derived=record.derived if record.derived not in PRIMARY else "",
                recounts=recounts.get(record.code, {}),
                formula_url=reverse("userdata:formula-new", args=[dataset.public_id]),
                related_url=f"{reverse('userdata:related', args=[dataset.public_id])}?"
                f"{urlencode({'series': item.key})}",
            )
            grouped.setdefault(record.title, Indicator(title=record.title, unit=record.unit))
            grouped[record.title].entries.append(entry)
            if item.key in wanted:
                source.open = True
        source.indicators = list(grouped.values())
        found.append(source)
    if found and not any(source.open for source in found):
        found[0].open = True
    return found


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
    return Entry(
        key=key,
        title=item.short_title,
        detail="",
        unit=item.unit_label,
        first_year=min(years) if years else None,
        last_year=max(years) if years else None,
        regions=85,
        is_user=False,
    )


def actions(entry: Entry, partner: Entry | None = None) -> list[Group]:
    """Что можно сделать с рядом: построить, посчитать, узнать; недоступное — с причиной."""
    one_year = _("у показателя один год") if entry.years < 2 else ""  # noqa: PLR2004
    few = (
        _("нужны значения хотя бы по %(count)s регионам") % {"count": FEW_REGIONS}
        if entry.regions < FEW_REGIONS
        else ""
    )
    build_group = [
        Action(_("Карта"), "map", value=f"{VIEW_PREFIX}map"),
        Action(_("Линии по годам"), "dynamics", value=f"{VIEW_PREFIX}compare", reason=one_year),
        Action(_("Рейтинг"), "ranking", value=f"{VIEW_PREFIX}rankings"),
        Action(_("Распределение"), "distribution", value=f"{VIEW_PREFIX}distribution"),
        Action(_("Таблица"), "table", value=f"{VIEW_PREFIX}table"),
    ]
    learn = [
        Action(_("Лидеры и отстающие"), "ranking", value=f"{ANSWER_PREFIX}leaders"),
        Action(_("Как изменился"), "dynamics", value=f"{ANSWER_PREFIX}change", reason=one_year),
        Action(
            _("Насколько различаются регионы"),
            "inequality",
            value=f"{ANSWER_PREFIX}spread",
            reason=few,
        ),
        Action(_("Похожи ли соседи"), "spatial", value=f"{ANSWER_PREFIX}neighbours", reason=few),
        Action(_("С чем связан"), "correlation", value=f"{ANSWER_PREFIX}related", reason=few),
    ]
    groups = [
        Group(BUILD, _("Построить"), build_group),
        _compute_group(entry, one_year),
        Group(LEARN, _("Узнать"), learn),
    ]
    if partner is not None:
        groups.append(
            Group(
                PAIR,
                _("Вместе с «%(title)s»") % {"title": partner.title},
                [
                    Action(
                        _("Связь и облако точек"),
                        "correlation",
                        value=f"{ANSWER_PREFIX}relation",
                        reason=few,
                    )
                ],
            )
        )
    else:
        groups.append(
            Group(
                PAIR,
                _("С другим показателем"),
                [Action(_("Выбрать второй показатель"), "compare", href=_pick_url(entry.key))],
            )
        )
    return groups


def _pick_url(key: str) -> str:
    return f"?{urlencode({'series': key, 'pick': '1'})}#study-sources"


def _compute_group(entry: Entry, one_year: str) -> Group:
    """Пересчёты ряда своей таблицы; уже собранные — сразу на поле."""
    title = _("Посчитать")
    if not entry.is_user:
        return Group(COMPUTE, title, [], reason=_("пересчёты — для своих таблиц"))
    if entry.derived:
        return Group(COMPUTE, title, [], reason=_("ряд уже пересчитан — считайте от исходного"))
    found = []
    for code, sums_only, money_only in RECOUNTS:
        reason = ""
        if sums_only and not entry.is_sum:
            reason = _("только для сумм по регионам")
        elif money_only and not entry.is_money:
            reason = _("только для денежных показателей")
        elif code == DatasetSeries.Derived.GROWTH.value:
            reason = one_year
        found.append(
            Action(
                str(DatasetSeries.Derived(code).label).capitalize(),
                value=f"{COMPUTE_PREFIX}{code}",
                reason=reason,
                hint=_("уже посчитано") if code in entry.recounts else "",
            )
        )
    found.append(Action(_("Формула"), href=entry.formula_url))
    return Group(COMPUTE, title, found)


def add(
    request: HttpRequest, study: Study, value: str, series_key: str, other: str = ""
) -> dict[str, Any]:
    """Поставить на поле карточку по кнопке «Что сделать»; общий год и регионы — исследования."""
    if value.startswith(VIEW_PREFIX):
        target = value.removeprefix(VIEW_PREFIX)
        return studies.add_view(study, target, urlencode({"series": series_key}))
    if value.startswith(ANSWER_PREFIX):
        return studies.add_answer(study, value.removeprefix(ANSWER_PREFIX), series_key, other)
    if value.startswith(COMPUTE_PREFIX):
        key = compute(request, series_key, value.removeprefix(COMPUTE_PREFIX))
        return studies.add_view(study, "map", urlencode({"series": key}))
    raise studies.StudyError(_("Такой карточки нет."))


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
        raise studies.StudyError(_("Пересчитывается исходный ряд таблицы."))
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
        item.is_sum
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

# Исследование, из панели которого начата загрузка таблицы.
STUDY_SESSION_KEY = "userdata_study"


def remember_study(request: HttpRequest, public_id: str) -> None:
    """Запомнить исследование, в которое вернётся собранная таблица; чужое — нет."""
    try:
        identifier = UUID(public_id)
    except ValueError:
        return
    if studies.owned(request).filter(public_id=identifier).exists():
        request.session[STUDY_SESSION_KEY] = str(identifier)


def open_in_study(request: HttpRequest, version: DatasetVersion) -> str | None:
    """
    Адрес исследования с картой первого ряда собранной таблицы: того, откуда начата
    загрузка, иначе того, где ряды таблицы уже есть (без новой карточки), иначе нового.
    Исследования не завести (предел) — ``None``.
    """
    from .views import first_key

    key = first_key(version)
    if key is None:
        return None
    owned = studies.owned(request)
    remembered = request.session.pop(STUDY_SESSION_KEY, "")
    study = owned.filter(public_id=remembered).first() if remembered else None
    if study is None:
        code = version.dataset.code
        existing = next(
            (item for item in owned.order_by("-updated_at") if code in studies.dataset_codes(item)),
            None,
        )
        if existing is not None:
            url = reverse("userdata:study", args=[existing.public_id])
            return f"{url}?{urlencode({'series': key})}"
    try:
        if study is None:
            study = studies.create(request, version.dataset.title)
        block = studies.add_view(study, "map", urlencode({"series": key}))
    except studies.StudyError:
        return None
    url = reverse("userdata:study", args=[study.public_id])
    return f"{url}?{urlencode({'series': key, 'added': block['id']})}#block-{block['id']}"
