"""
Шаг «Показатели»: название, единица и масштаб, вид величины, направленность, смена методики,
свёртка месяцев и разрезов; выбранные пересчёты можно снять, новые добавляются в исследовании
(«Посчитать»). Показатель можно скрыть (его ряды не собираются) или влить в другой — тот же
показатель под другим названием: его значения дополняют ряды того.

Описание задаётся на показатель и действует на все его ряды (разрезы и периоды года).
Подсказки — по единице и названию; решения человека хранятся в рецепте версии. Спрашивается
только неуверенное: вид величины, который не подсказали ни единица, ни название, пустая
единица и месяцы без годового ряда.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from django.db import transaction

from apps.catalog.constants import is_unnormalised_title

from . import naming
from .models import DatasetSeries, DatasetVersion

SUM = DatasetSeries.Kind.SUM.value
RELATIVE = DatasetSeries.Kind.RELATIVE.value
KINDS = (SUM, RELATIVE)
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
# Пересчёт, предложенный по умолчанию: «на 100 000 жителей» — как у карт преступности
# и заболеваемости; у денежной суммы — «на жителя» («тыс. руб. на жителя»). Численность
# населения на жителей не пересчитывается.
DEFAULT_PER = (DatasetSeries.Derived.PER_100000.value,)
MONEY_PER = (PER_ONE,)
_POPULATION = re.compile(
    r"(численност\w*\s+(постоянного\s+|всего\s+)?населени|^население\b|\bpopulation\b)",
    re.IGNORECASE,
)
# Пересчёты любого показателя: в ценах последнего года (только денежные), Россия = 100, темп
# к прошлому году, доля в сумме по субъектам (только суммы).
RECALC_REAL = DatasetSeries.Derived.REAL.value
RECALC_RUSSIA = DatasetSeries.Derived.RUSSIA.value
RECALC_GROWTH = DatasetSeries.Derived.GROWTH.value
RECALC_SHARE = DatasetSeries.Derived.SHARE.value
RECALC_CHOICES = (RECALC_REAL, RECALC_RUSSIA, RECALC_GROWTH, RECALC_SHARE)
# Свёртка месяцев в год: сумма (для сумм), среднее (для относительных), декабрь,
# «январь–декабрь» из итогов с начала года.
MONTHS_SUM = "sum"
MONTHS_MEAN = "mean"
MONTHS_DECEMBER = "december"
MONTHS_YTD = "ytd"
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

# Единица относительной величины: доля, коэффициент, на жителя, годы жизни.
_RELATIVE_UNIT = re.compile(
    r"(%|процент|промилле|‰|на\s*1\b|на\s*10\b|на\s*1\s?000|на\s*10\s?000|на\s*100|на душу|"
    r"на жител|на одного|на 1 жител|коэффициент|индекс|балл|\bлет\b|\bгод(а|ы|ов)?\b|\bраз\b|"
    r"per\s|percent|ratio|index|years)",
    re.IGNORECASE,
)
# Название относительной величины: среднее (не среднегодовая численность — она складывается;
# не «средней тяжести» и не «малого и среднего бизнеса»), доля, уровень, отношение (не «в
# отношении кого-то»), на жителя.
_RELATIVE_NAME = re.compile(
    r"(средн(?!егодов|есписочн|ей тяжест|его бизнес|его предпринимат)|в среднем|на душу|"
    r"на жител|на одного|на 1 |на 10 |на 100|"
    r"на 1 ?000|доля|удельн|уровень|коэффициент|индекс|инденкс|темп|(?<!в )отношени|"
    r"плотност|в %|в процент|% к |"
    r"продолжительност|average|mean|per capita|share|rate|ratio)",
    re.IGNORECASE,
)
# Единица суммы: люди, штуки, деньги и объёмы целиком.
_SUM_UNIT = re.compile(
    r"(человек|чел\.|единиц|штук|шт\.|тонн|\bт\b|млн|млрд|тыс|кв\.\s?м|\bга\b|гектар|"
    r"случа|people|persons|units|cases|thousand|million|billion)",
    re.IGNORECASE,
)
# Название суммы, когда единицы нет: численность, количество, число, объём, оборот.
_SUM_NAME = re.compile(r"(численност|количеств|^число |объ[её]м|оборот)", re.IGNORECASE)
# Рубли: в цены года переводит индекс потребительских цен региона.
_MONEY_UNIT = re.compile(r"(руб|₽|\brub)", re.IGNORECASE)
# Деньги в любой валюте: их естественно делить на жителя.
_CURRENCY_UNIT = re.compile(r"(руб|₽|\brub|долл|\$|\busd\b|евро|€|\beur\b)", re.IGNORECASE)
# Единица — просто рубли, без разряда: «руб.», «рублей».
_PLAIN_RUBLES = re.compile(r"^\s*(руб\.?|рубл\w*|₽)\s*$", re.IGNORECASE)
# Дата в названии («на 1 января 2024 года», «на 01.01.2024», «на 10-е марта») — не признак
# величины «на 1 жителя»: её убирают до сравнения с признаками.
_DATE = re.compile(
    r"на\s+\d{1,2}(?:-?(?:е|го))?\s+(?:январ|феврал|март|апрел|ма[яй]|июн|июл|август|"
    r"сентябр|октябр|ноябр|декабр)\w*(?:\s+\d{4}(?:\s*(?:года|г\.?))?)?"
    r"|на\s+\d{1,2}\.\d{1,2}(?:\.\d{2,4})?",
    re.IGNORECASE,
)


def _without_dates(text: str) -> str:
    """Название или единица без дат: «Численность на 1 января» → «Численность »."""
    return _DATE.sub(" ", text or "")


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
        return self.kind == SUM

    @property
    def is_money(self) -> bool:
        """Денежный показатель в рублях: только его можно перевести в цены года."""
        return bool(_MONEY_UNIT.search(f"{self.unit} {self.title}"))

    @property
    def is_currency(self) -> bool:
        """Денежный показатель в любой валюте: его делят на жителя."""
        return is_currency(self.title, self.unit)

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
    def month_methods(self) -> list[str]:
        """Способы свёртки для вида величины: месяцы складываются только у сумм."""
        return [item for item in self.month_options if item != MONTHS_SUM or self.is_sum]

    @property
    def questions(self) -> tuple[str, ...]:
        """
        О чём спросить, пока описание не сохранено: вид величины без подсказки единицы
        и названия, пустая единица, месяцы без годового ряда.
        """
        if self.answered:
            return ()
        found = []
        if not kind_is_clear(self.name, self.unit):
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
    "months",
    "polarity",
    "breaks",
    "fold",
    "recounts",
    "merge",
    "hidden",
)


def kind_is_clear(name: str, unit: str) -> bool:
    """Вид величины подсказан единицей или названием, а не выбран по умолчанию."""
    name, unit = _without_dates(name), _without_dates(unit)
    return bool(
        _PLAIN_RUBLES.match(unit)
        or _RELATIVE_UNIT.search(unit or "")
        or _RELATIVE_NAME.search(name or "")
        or _SUM_UNIT.search(unit or "")
        or _SUM_NAME.search(name or "")
    )


def is_rubles(name: str, unit: str) -> bool:
    """Рубли — по единице или названию: в цены года их переводит индекс цен."""
    return bool(_MONEY_UNIT.search(f"{unit} {name}"))


def is_currency(name: str, unit: str) -> bool:
    """Деньги в любой валюте — по единице или названию."""
    return bool(_CURRENCY_UNIT.search(f"{unit} {name}"))


def default_per(name: str, kind: str, unit: str = "") -> tuple[str, ...]:
    """
    Пересчёт по умолчанию: у денежной суммы — на жителя, у остальных — на 100 000 жителей;
    сама численность не пересчитывается.
    """
    if kind != SUM or _POPULATION.search(name or ""):
        return ()
    return MONEY_PER if is_currency(name, unit) else DEFAULT_PER


def per_allowed(indicator: Indicator, per: str) -> bool:
    """Пересчёт на жителей или площадь — у сумм; на одного жителя — только у денежных."""
    return indicator.is_sum and (per != PER_ONE or indicator.is_currency)


def guess_kind(name: str, unit: str) -> str:
    """Вид величины по единице и названию: сумма складывается по регионам, остальное — нет."""
    name, unit = _without_dates(name), _without_dates(unit)
    # Рубли без «тыс.», «млн», «млрд» — сумма на человека: зарплата, пенсия, прожиточный минимум.
    if _PLAIN_RUBLES.match(unit):
        return RELATIVE
    if _RELATIVE_UNIT.search(unit or "") or _RELATIVE_NAME.search(name or ""):
        return RELATIVE
    if _SUM_UNIT.search(unit or "") or _SUM_NAME.search(name or ""):
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
    for number, (name, items) in enumerate(grouped.items(), start=1):
        units = Counter(item["unit"] for item in items if item["unit"])
        unit = units.most_common(1)[0][0] if units else ""
        # Название без единицы в хвосте и без годов — предложение; человек его правит.
        short, unit = naming.short_title(name, unit)
        answer = answers.get(name) or {}
        kind = answer.get("kind") or guess_kind(name, unit)
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
                per=(
                    tuple(answer["per"])
                    if "per" in answer
                    else default_per(name, kind, answer.get("unit", unit))
                ),
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
    """Пересчёт возможен: в ценах года — для денежных с годами, доля — только для сумм."""
    if recalc == RECALC_REAL:
        return indicator.is_money and indicator.has_years
    if recalc == RECALC_SHARE:
        return indicator.is_sum
    return recalc in RECALC_CHOICES


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
    Без ``per`` и ``recalc`` в ответе пересчёты остаются прежними (кроме неподходящих виду).
    """
    known = {item.name: item for item in indicators_of(version)}
    cleaned: dict[str, dict[str, Any]] = {}
    for name, answer in answers.items():
        if name not in known:
            continue
        kind = str(answer.get("kind")) if answer.get("kind") in KINDS else RELATIVE
        polarity = answer.get("polarity")
        title = " ".join(str(answer.get("title") or name).split())[:TITLE_LENGTH] or name
        unit = " ".join(str(answer.get("unit") or "").split())[:UNIT_LENGTH]
        # Что разрешено, решает описание, которое человек отправил: вид величины и единица.
        described = known[name]
        described.kind, described.unit, described.title = kind, unit, title
        months = str(answer.get("months") or "")
        described.months = months if months in described.month_methods else ""
        # Пересчёты без ответа — прежние. У показателя, описанного впервые, нетронутые
        # предложенные пересчёты следуют за единицей и видом из ответа: «млрд руб.» вместо
        # пустой единицы — на жителя, а не на 100 000 жителей.
        per = answer.get("per", described.per)
        if not described.answered and list(per or []) == list(described.per):
            per = default_per(name, kind, unit)
        cleaned[name] = {
            "title": title,
            "unit": unit,
            "kind": kind,
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
                if header in (answer.get("fold_slices") or []) and kind == SUM
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
