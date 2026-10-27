"""
Шаг «Показатели»: название, единица и масштаб, тип величины и основа сравнения,
направленность, смена методики, свёртка месяцев и разрезов; выбранные пересчёты можно снять,
новые добавляются в исследовании («Посчитать»). Показатель можно скрыть (его ряды
не собираются) или влить в другой — тот же показатель под другим названием: его значения
дополняют ряды того.

Описание задаётся на показатель и действует на все его ряды (разрезы и периоды года).
Подсказки — по единице и названию (``semantics.guess``); решения человека хранятся в рецепте
версии. Спрашивается только неуверенное: тип, который не подсказали ни единица, ни название,
пустая единица и месяцы без годового ряда. Что с каким типом можно — ``semantics``.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from django.db import transaction

from . import naming, semantics
from .models import DatasetSeries, DatasetVersion

FLOW = semantics.FLOW
STOCK = semantics.STOCK
SUM = semantics.SUM
RELATIVE = semantics.RELATIVE
INDEX = semantics.INDEX
KINDS = semantics.TYPES
POLARITIES = tuple(choice.value for choice in DatasetSeries.Polarity)
# Пересчёты суммы: на жителей и на площадь.
PER_ONE = DatasetSeries.Derived.PER_1.value
PER_CHOICES = (
    PER_ONE,
    DatasetSeries.Derived.PER_1000.value,
    DatasetSeries.Derived.PER_10000.value,
    DatasetSeries.Derived.PER_100000.value,
    DatasetSeries.Derived.PER_KM2.value,
)
# Пересчёты любого показателя: в ценах последнего года (только денежные), Россия = 100, темп
# к прошлому году, доля в сумме по субъектам (только суммы).
RECALC_REAL = DatasetSeries.Derived.REAL.value
RECALC_RUSSIA = DatasetSeries.Derived.RUSSIA.value
RECALC_GROWTH = DatasetSeries.Derived.GROWTH.value
RECALC_SHARE = DatasetSeries.Derived.SHARE.value
RECALC_CHOICES = (RECALC_REAL, RECALC_RUSSIA, RECALC_GROWTH, RECALC_SHARE)
# Свёртка месяцев в год: сумма месяцев, среднее за месяц, декабрь, «январь–декабрь» из итогов
# с начала года; какие подходят типу — ``semantics.MONTHS_BY_TYPE``.
MONTHS_SUM = semantics.MONTHS_SUM
MONTHS_MEAN = semantics.MONTHS_MEAN
MONTHS_DECEMBER = semantics.MONTHS_DECEMBER
MONTHS_YTD = semantics.MONTHS_YTD
MONTHS_CHOICES = (MONTHS_SUM, MONTHS_MEAN, MONTHS_DECEMBER, MONTHS_YTD)
YEAR_PERIOD = "year:12"
TITLE_LENGTH = 300
UNIT_LENGTH = 120
NOTE_LENGTH = 300
# Сколько лет смены методики можно отметить у показателя.
MAX_BREAKS = 10
# Допустимые годы смены методики.
FIRST_BREAK_YEAR = 1900
LAST_BREAK_YEAR = 2100
# Масштаб: значения умножаются на 1000 в этой степени, разряд единицы сдвигается.
SCALES = (-2, -1, 0, 1, 2)


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
    # Смена методики: годы, с которых значения считаются по-другому, и пояснение.
    breaks: tuple[int, ...] = ()
    break_note: str = ""
    # Пересчёты (в ценах года, Россия = 100, темп, доля), сложение по разрезам
    # и свёртка месяцев в год.
    recalc: tuple[str, ...] = ()
    fold_slices: tuple[str, ...] = ()
    months: str = ""
    units: list[str] = field(default_factory=list)
    regions: int = 0
    first_year: int | None = None
    last_year: int | None = None
    # Что есть в таблице: разрезы с несколькими значениями и периоды года.
    slice_headers: list[str] = field(default_factory=list)
    periods: set[str] = field(default_factory=set)
    # Описание уже сохранено человеком: вопросов больше нет.
    answered: bool = False
    # Номер в форме шага.
    number: int = 0
    # Скрыт: ряды не собираются. Масштаб — степень тысячи. Влит в показатель ``merge``.
    hidden: bool = False
    scale: int = 0
    merge: str = ""
    # Основа сравнения регионов у сумм: жители, площадь, не делить.
    base: str = semantics.PEOPLE

    @property
    def scaled_unit(self) -> str:
        """Единица рядов после масштаба: «тыс. руб.» при ÷ 1 000 — «млн руб.»."""
        return naming.scaled_unit(self.unit, self.scale)

    @property
    def scale_options(self) -> list[tuple[int, str]]:
        """Масштабы с единицей, которая получится: (степень тысячи, единица)."""
        return [(exponent, naming.scaled_unit(self.unit, exponent)) for exponent in SCALES]

    @property
    def is_sum(self) -> bool:
        """Складывается по регионам: поток, запас или сумма."""
        return semantics.is_additive(self.kind)

    @property
    def is_money(self) -> bool:
        """Денежный показатель в рублях: только его можно перевести в цены года."""
        return semantics.is_rubles(self.title, self.unit)

    @property
    def is_currency(self) -> bool:
        """Денежный показатель в любой валюте: его делят на жителя."""
        return semantics.is_currency(self.title, self.unit)

    @property
    def value(self) -> semantics.Value:
        """Что о показателе нужно таблице правил."""
        years = (
            self.last_year - self.first_year + 1
            if self.first_year is not None and self.last_year is not None
            else 0
        )
        return semantics.Value(
            type=self.kind,
            base=self.base,
            is_rubles=self.is_money,
            is_currency=self.is_currency,
            monthly=semantics.is_monthly(f"{self.name} {self.title}", self.unit),
            years=years,
            regions=self.regions,
        )

    @property
    def has_years(self) -> bool:
        """Есть годовые значения (или месяцы сворачиваются в год)."""
        return YEAR_PERIOD in self.periods or bool(self.months)

    @property
    def month_options(self) -> list[str]:
        """Способы свёртки месяцев в год, возможные по периодам таблицы."""
        methods: list[str] = []
        if all(f"month:{number}" in self.periods for number in range(1, 13)):
            methods += [MONTHS_SUM, MONTHS_MEAN]
        if "month:12" in self.periods:
            methods.append(MONTHS_DECEMBER)
        if "ytd:12" in self.periods:
            methods.append(MONTHS_YTD)
        return methods

    @property
    def kind_label(self) -> str:
        """Тип величины словами: «сумма на дату»."""
        return str(DatasetSeries.Kind(self.kind).label) if self.kind in KINDS else ""

    @property
    def month_types(self) -> dict[str, str]:
        """Каким типам подходит каждый способ свёртки — для поля «Месяцы — в год»."""
        return {
            method: " ".join(
                kind for kind, allowed in semantics.MONTHS_BY_TYPE.items() if method in allowed
            )
            for method in self.month_options
        }

    @property
    def month_methods(self) -> list[str]:
        """Способы свёртки, подходящие типу: месяцы складываются у потока, у запаса — декабрь."""
        return semantics.months_methods(self.kind, self.month_options)

    @property
    def questions(self) -> tuple[str, ...]:
        """
        О чём спросить, пока описание не сохранено: вид величины без подсказки единицы
        и названия, пустая единица, месяцы без годового ряда.
        """
        if self.answered:
            return ()
        found = []
        if not semantics.guess(self.name, self.unit).sure:
            found.append("kind")
        if not self.unit:
            found.append("unit")
        if self.month_options and YEAR_PERIOD not in self.periods:
            found.append("months")
        return tuple(found)

    @property
    def fields(self) -> tuple[str, ...]:
        """Поля описания, кроме вопросов: они — в «Изменить»."""
        return tuple(name for name in FIELDS if name not in self.questions)

    @property
    def recounts(self) -> tuple[str, ...]:
        """Выбранные пересчёты: на жителей и площадь, затем прочие."""
        return (*self.per, *self.recalc)


# Поля описания показателя в шаге «Показатели» по порядку.
FIELDS = (
    "title",
    "unit",
    "scale",
    "kind",
    "base",
    "months",
    "polarity",
    "breaks",
    "fold",
    "recounts",
    "merge",
    "hidden",
)


def kind_is_clear(name: str, unit: str) -> bool:
    """Тип подсказан единицей или названием, а не выбран по умолчанию."""
    return semantics.guess(name, unit).sure


def is_rubles(name: str, unit: str) -> bool:
    """Рубли — по единице или названию: в цены года их переводит индекс цен."""
    return semantics.is_rubles(name, unit)


def is_currency(name: str, unit: str) -> bool:
    """Деньги в любой валюте — по единице или названию."""
    return semantics.is_currency(name, unit)


def suggested_per(name: str, kind: str, unit: str = "", base: str = "") -> tuple[str, ...]:
    """
    Какой пересчёт предложить сумме: деньгам и объёмам — на жителя, людям и событиям —
    на 100 000 жителей; при основе «площадь» и «не делить» — никакой. Сам по себе пересчёт
    не строится: таблица собирается в числах человека, пересчёт — по нажатию.
    """
    found = semantics.suggested_per(kind, base or semantics.guess_base(name), name, unit)
    return (found,) if found else ()


def per_allowed(indicator: Indicator, per: str) -> bool:
    """Пересчёт на жителей или площадь — по таблице правил: у сумм с подходящей основой."""
    return bool(semantics.verdict(f"calc:{per}", indicator.value, data=False))


def guess_kind(name: str, unit: str) -> str:
    """Тип величины по единице и названию."""
    return semantics.guess(name, unit).type


def indicators_of(version: DatasetVersion) -> list[Indicator]:
    """Показатели извлечённой таблицы с описанием из рецепта или подсказками."""
    report = version.report.get("extract") or {}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in report.get("series", []):
        grouped[item["indicator"]].append(item)
    answers: Mapping[str, Any] = version.recipe.get("indicators") or {}
    notes: Mapping[str, str] = report.get("notes") or {}
    result = []
    for number, (name, items) in enumerate(grouped.items(), start=1):
        units = Counter(item["unit"] for item in items if item["unit"])
        unit = units.most_common(1)[0][0] if units else ""
        # Название без единицы в хвосте и без годов — предложение; человек его правит.
        short, unit = naming.short_title(name, unit)
        answer = answers.get(name) or {}
        guessed = semantics.guess(name, unit)
        # Решение человека — тип; прежний вид «сумма» и «относительная» уточняется по названию.
        kind = (
            answer.get("type")
            or semantics.refine(str(answer.get("kind") or ""), name, unit)
            or guessed.type
        )
        years = [item[key] for item in items for key in ("first_year", "last_year") if item[key]]
        values: dict[str, set[str]] = defaultdict(set)
        for item in items:
            for header, value in item["slices"]:
                values[header].add(value)
        result.append(
            Indicator(
                name=name,
                title=answer.get("title") or short,
                unit=answer.get("unit", unit),
                kind=kind,
                polarity=answer.get("polarity") or DatasetSeries.Polarity.NEUTRAL.value,
                per=tuple(answer.get("per") or ()),
                series=len(items),
                note=notes.get(name, ""),
                breaks=tuple(item["year"] for item in answer.get("breaks") or []),
                break_note=next((item["note"] for item in answer.get("breaks") or []), ""),
                recalc=tuple(answer.get("recalc") or ()),
                fold_slices=tuple(answer.get("fold_slices") or ()),
                months=str(answer.get("months") or ""),
                units=[value for value, _count in units.most_common()],
                regions=max((item["regions"] for item in items), default=0),
                first_year=min(years) if years else None,
                last_year=max(years) if years else None,
                slice_headers=[header for header, found in values.items() if len(found) > 1],
                periods={item["period"] for item in items},
                answered=bool(answer),
                number=number,
                hidden=bool(answer.get("hidden")),
                scale=int(answer.get("scale") or 0) if answer.get("scale") in SCALES else 0,
                merge=str(answer.get("merge") or ""),
                base=str(answer["base"]) if answer.get("base") in semantics.BASES else guessed.base,
            )
        )
    return result


def merge_target(indicator: Indicator, known: Mapping[str, Indicator]) -> Indicator | None:
    """
    Показатель, в который влит этот: не он сам, не скрытый и сам никуда не влитый — без
    цепочек; скрытый показатель никуда не вливается.
    """
    target = known.get(indicator.merge)
    if (
        indicator.hidden
        or target is None
        or target.name == indicator.name
        or target.hidden
        or target.merge
    ):
        return None
    return target


def parse_years(text: Any) -> list[int]:
    """Годы из строки «2019, 2022»: только четырёхзначные в разумных границах, по порядку."""
    found = {
        int(item)
        for item in re.findall(r"\d{4}", str(text or ""))
        if FIRST_BREAK_YEAR <= int(item) <= LAST_BREAK_YEAR
    }
    return sorted(found)[:MAX_BREAKS]


def _breaks(years: Any, note: Any) -> list[dict[str, Any]]:
    """Смена методики для рецепта: год и общее пояснение."""
    text = " ".join(str(note or "").split())[:NOTE_LENGTH]
    return [{"year": year, "note": text} for year in parse_years(years)]


def recalc_allowed(indicator: Indicator, recalc: str) -> bool:
    """
    Пересчёт возможен по таблице правил: в ценах года — рубли (и есть годы), доля — суммы,
    Россия = 100 — относительные, темп — всё, кроме индекса.
    """
    if recalc not in RECALC_CHOICES:
        return False
    if recalc == RECALC_REAL and not indicator.has_years:
        return False
    return bool(semantics.verdict(f"calc:{recalc}", indicator.value, data=False))


def breaks_of(recipe: Mapping[str, Any], name: str) -> list[dict[str, Any]]:
    """Смена методики показателя ``name`` из рецепта версии."""
    answer = (recipe.get("indicators") or {}).get(name) or {}
    return [
        {"year": int(item["year"]), "note": str(item.get("note") or "")}
        for item in answer.get("breaks") or []
    ]


def save(version: DatasetVersion, answers: Mapping[str, Mapping[str, Any]]) -> None:
    """
    Записать описание показателей в рецепт; неизвестные показатели и значения отбрасываются.
    Без ``per`` и ``recalc`` в ответе пересчёты остаются прежними (кроме неподходящих типу).
    Тип — ``type`` или ``kind`` ответа; у суммы без уточнения его уточняет способ свёртки
    месяцев: месяцы складываются — поток, декабрь или среднее — запас.
    """
    known = {item.name: item for item in indicators_of(version)}
    cleaned: dict[str, dict[str, Any]] = {}
    for name, answer in answers.items():
        if name not in known:
            continue
        described = known[name]
        chosen = answer.get("type") or answer.get("kind")
        kind = str(chosen) if chosen in KINDS else described.kind
        months = str(answer.get("months") or "")
        if kind == SUM and months in semantics.MONTHS_TYPE:
            kind = semantics.MONTHS_TYPE[months]
        base = answer.get("base")
        polarity = answer.get("polarity")
        title = " ".join(str(answer.get("title") or name).split())[:TITLE_LENGTH] or name
        unit = " ".join(str(answer.get("unit") or "").split())[:UNIT_LENGTH]
        # Что разрешено, решает описание, которое человек отправил: тип, основа и единица.
        described.kind, described.unit, described.title = kind, unit, title
        described.base = str(base) if base in semantics.BASES else described.base
        described.months = months if months in described.month_methods else ""
        # Пересчёты без ответа — прежние; сами по себе они не строятся.
        per = answer.get("per", described.per)
        cleaned[name] = {
            "title": title,
            "unit": unit,
            "type": kind,
            "base": described.base,
            "polarity": polarity
            if polarity in POLARITIES
            else DatasetSeries.Polarity.NEUTRAL.value,
            "per": [
                item for item in per or [] if item in PER_CHOICES and per_allowed(described, item)
            ],
            "breaks": _breaks(answer.get("breaks"), answer.get("break_note")),
            "recalc": [
                item
                for item in RECALC_CHOICES
                if item in (answer.get("recalc", described.recalc) or [])
                and recalc_allowed(described, item)
            ],
            "fold_slices": [
                header
                for header in described.slice_headers
                if header in (answer.get("fold_slices") or [])
                and semantics.verdict("build:fold", described.value, data=False)
            ],
            "months": described.months,
            "hidden": bool(answer.get("hidden")),
            "scale": int(answer["scale"]) if str(answer.get("scale")) in map(str, SCALES) else 0,
            "merge": str(answer.get("merge") or "") if answer.get("merge") in known else "",
        }
    # Влить можно только в показатель, который сам виден и никуда не влит: без цепочек.
    for name, item in cleaned.items():
        target = cleaned.get(item["merge"])
        if (
            item["hidden"]
            or target is None
            or item["merge"] == name
            or target["hidden"]
            or target["merge"]
        ):
            item["merge"] = ""
    with transaction.atomic():
        version.refresh_from_db(fields=["recipe"])
        version.recipe = {**version.recipe, "indicators": cleaned}
        version.save(update_fields=["recipe", "updated_at"])
