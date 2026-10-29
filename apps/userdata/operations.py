"""
Каталог расчётов своих данных: что можно посчитать, к чему применимо, какая у результата
единица и вид величины, как считается (текст метода) — одним перечнем. Из каталога — панель
расчёта на поле исследования, пункты «Посчитать», подсказки и пояснения методики.

Операция — либо пересчёт сборки (на жителей, на км², Россия = 100, % к прошлому году, доля
в сумме, в ценах последнего года: считается в SQL при сборке таблицы, `build.py`), либо
формула по шаблону: шаблон даёт выражение с ключами рядов, считает его песочница формул
(`formulas.py`) при сборке. Названия и единицы результата — по-русски, как у рядов таблицы.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from django.utils.functional import Promise
from django.utils.translation import gettext_lazy as _

from apps.warehouse import routing

from . import semantics
from .models import DatasetSeries
from .semantics import FLOW, INDEX, RELATIVE, STOCK

# Группы каталога по порядку.
GROUPS: tuple[tuple[str, Promise], ...] = (
    ("per", _("На жителя")),
    ("share", _("Доля и структура")),
    ("change", _("Изменение")),
    ("compare", _("Сравнение")),
    ("sum", _("Агрегирование")),
    ("regional", _("Методы регионального анализа")),
)

# Частые расчёты — плитками первым экраном окна, по порядку: первые шесть подходящих ряду.
POPULAR = (
    "per1",
    "per100000",
    "share",
    "growth",
    "baseyear",
    "real",
    "russia",
    "russiadeviation",
    "districtdeviation",
    "between",
    "annualrate",
    "structure",
)
POPULAR_SHOWN = 6
# Что получится — строкой на плитке частого расчёта.
NOTES: dict[str, Promise] = {
    "per1": _("Сумма на одного жителя"),
    "per100000": _("Как считают преступность и заболеваемость"),
    "share": _("Какая часть общей суммы у региона"),
    "growth": _("Насколько значение выросло за год"),
    "baseyear": _("Рост от выбранного года, он = 100"),
    "real": _("Рубли без инфляции"),
    "russia": _("Значение региона относительно России"),
    "russiadeviation": _("На сколько выше или ниже России"),
    "districtdeviation": _("На сколько выше или ниже своего округа"),
    "between": _("Разница между двумя годами"),
    "annualrate": _("Средний рост за год в периоде"),
    "structure": _("Доля части в итоге региона"),
}

# Виды полей операции.
SERIES = "series"
SERIES_LIST = "series_list"
YEAR = "year"
CHOICE = "choice"

# Составляющих в сумме, структуре и сдвигах — не больше: формула ссылается не больше чем
# на двенадцать рядов, у сдвига-доли каждая составляющая ещё и с итогом России.
MAX_PARTS = 8
SHIFT_SHARE_PARTS = 6

# Знаменатели профильных расчётов — официальные ряды склада. Ряда нет на складе — операция
# не предлагается. Численность — в тысячах человек.
GROUP_DENOMINATORS: dict[str, tuple[str, str]] = {
    "children": ("Y477040030:fa73d542", "детей до 15 лет"),
    "working": ("Y477170049:32882ee5", "жителей трудоспособного возраста"),
    "pensioners": ("Y477130019:79b90e3d", "пенсионеров"),
    "employed": ("Y477110372:00", "занятых"),
}
GROUP_CHOICES: tuple[tuple[str, Promise], ...] = (
    ("children", _("дети до 15 лет")),
    ("working", _("трудоспособного возраста")),
    ("pensioners", _("пенсионеры")),
    ("employed", _("занятые")),
)
# Стоимость фиксированного набора, % к среднероссийской; величина прожиточного минимума
# на душу, рублей в месяц.
PRICE_LEVEL = "Y477190020:7b64cceb"
SUBSISTENCE = "Y477110030:00"
OFFICIAL_INPUTS = (*(key for key, _name in GROUP_DENOMINATORS.values()), PRICE_LEVEL, SUBSISTENCE)

# Разряд денежной единицы: «млн руб.» — сумма, «руб.» — на человека.
_ORDER_WORD = re.compile(r"(тыс|млн|млрд|трлн)", re.IGNORECASE)
CHANGE_CHOICES: tuple[tuple[str, Promise], ...] = (
    ("percent", _("в процентах")),
    ("absolute", _("в единицах показателя")),
)


@dataclass(frozen=True, slots=True)
class Field:
    """Поле операции: ряд, перечень рядов, год или выбор."""

    name: str
    kind: str
    label: Promise
    choices: tuple[tuple[str, Promise], ...] = ()


@dataclass(frozen=True, slots=True)
class Info:
    """
    Что о ряде нужно каталогу: название, единица, тип величины и основа, деньги, годы,
    регионы. ``kind`` пусто — сумма или относительная по ``is_sum``.
    """

    key: str
    title: str
    unit: str = ""
    is_sum: bool = False
    is_currency: bool = False
    # Рубли: в цены года переводит индекс потребительских цен.
    is_rubles: bool = False
    first_year: int | None = None
    last_year: int | None = None
    regions: int = 0
    derived: str = ""
    kind: str = ""
    base: str = semantics.PEOPLE
    monthly: bool = False
    is_user: bool = True

    @property
    def years(self) -> int:
        if self.first_year is None or self.last_year is None:
            return 0
        return self.last_year - self.first_year + 1

    @property
    def type(self) -> str:
        """Тип величины для таблицы правил."""
        return self.kind or (semantics.SUM if self.is_sum else RELATIVE)

    @property
    def value(self) -> semantics.Value:
        return semantics.Value(
            type=self.type,
            base=self.base,
            is_rubles=self.is_rubles,
            is_currency=self.is_currency,
            monthly=self.monthly,
            years=self.years,
            regions=self.regions,
            is_user=self.is_user,
        )


@dataclass(frozen=True, slots=True)
class Output:
    """Новый ряд операции: название, формула с ключами рядов, единица и вид величины."""

    title: str
    expression: str
    unit: str
    kind: str = RELATIVE


@dataclass(frozen=True, slots=True)
class Operation:
    """
    Расчёт каталога. ``recount`` — код пересчёта сборки; иначе ``outputs`` строит формулы
    по значениям полей. К какому ряду применимо — правило ``calc:<код>`` в ``semantics``.
    """

    code: str
    group: str
    title: Promise
    method: Promise
    fields: tuple[Field, ...] = ()
    recount: str = ""
    official: tuple[str, ...] = ()
    outputs: Callable[[Values], list[Output]] | None = None


@dataclass(slots=True)
class Values:
    """Значения полей операции: ключи рядов, годы, окно, число, выбор и сведения о рядах."""

    series: dict[str, str] = field(default_factory=dict)
    lists: dict[str, list[str]] = field(default_factory=dict)
    numbers: dict[str, float] = field(default_factory=dict)
    choices: dict[str, str] = field(default_factory=dict)
    info: dict[str, Info] = field(default_factory=dict)

    def key(self, name: str) -> str:
        return self.series[name]

    def ref(self, name: str) -> str:
        return "{" + self.series[name] + "}"

    def title(self, name: str) -> str:
        found = self.info.get(self.series.get(name, ""))
        return found.title if found else self.series.get(name, "")

    def unit(self, name: str) -> str:
        found = self.info.get(self.series.get(name, ""))
        return found.unit if found else ""

    def year(self, name: str) -> int:
        return int(self.numbers[name])

    def is_sum(self, name: str) -> bool:
        found = self.info.get(self.series.get(name, ""))
        return bool(found and found.is_sum)

    def kind(self, name: str) -> str:
        """Тип величины ряда поля; неизвестный ряд — относительная."""
        found = self.info.get(self.series.get(name, ""))
        return found.type if found else RELATIVE


A = Field("a", SERIES, _("Показатель"))


def _ref(key: str) -> str:
    return "{" + key + "}"


def _joined(parts: Sequence[str], operator: str) -> str:
    return f" {operator} ".join(parts)


def _country(reference: str) -> str:
    """Значение России, а нет его в таблице — сумма субъектов."""
    return f"ЕСЛИПУСТО(РОССИЯ({reference}); ИТОГ({reference}))"


# --- Формулы операций --------------------------------------------------------------------


def _per_group(values: Values) -> list[Output]:
    key, name = GROUP_DENOMINATORS[values.choices["group"]]
    unit = " ".join(part for part in (values.unit("a"), f"на 1 000 {name}") if part)
    return [
        Output(f"{values.title('a')} на 1 000 {name}", f"{values.ref('a')} / {_ref(key)}", unit)
    ]


def _price_level(values: Values) -> list[Output]:
    return [
        Output(
            f"{values.title('a')} с поправкой на уровень цен",
            f"{values.ref('a')} / {_ref(PRICE_LEVEL)} * 100",
            values.unit("a"),
        )
    ]


def _subsistence(values: Values) -> list[Output]:
    return [
        Output(
            f"{values.title('a')} в прожиточных минимумах",
            f"{values.ref('a')} / {_ref(SUBSISTENCE)}",
            "прожиточных минимумов",
        )
    ]


def _structure(values: Values) -> list[Output]:
    return [
        Output(
            f"Доля «{values.title('a')}» в «{values.title('b')}»",
            f"{values.ref('a')} / {values.ref('b')} * 100",
            "%",
        )
    ]


def _concentration(values: Values) -> list[Output]:
    total = values.ref("b")
    terms = [f"({_ref(key)} / {total}) ^ 2" for key in values.lists["parts"]]
    return [
        Output(
            f"Индекс Херфиндаля — Хиршмана: {values.title('b')}",
            f"({_joined(terms, '+')}) * 10000",
            "пунктов из 10 000",
        )
    ]


def _base_year(values: Values) -> list[Output]:
    year = values.year("year")
    return [
        Output(
            f"{values.title('a')}, {year} = 100",
            f"{values.ref('a')} / ВГОДУ({values.ref('a')}; {year}) * 100",
            f"{year} = 100",
            INDEX,
        )
    ]


def _between(values: Values) -> list[Output]:
    first, last = values.year("first"), values.year("last")
    reference = values.ref("a")
    if values.choices.get("mode") == "absolute":
        return [
            Output(
                f"{values.title('a')}: изменение за {first}–{last}",
                f"ЗАГОД({reference} - ВГОДУ({reference}; {first}); {last})",
                values.unit("a"),
                values.kind("a"),
            )
        ]
    return [
        Output(
            f"{values.title('a')}: изменение за {first}–{last}, %",
            f"ЗАГОД({reference} / ВГОДУ({reference}; {first}) * 100 - 100; {last})",
            "%",
            INDEX,
        )
    ]


def _annual_rate(values: Values) -> list[Output]:
    first, last = values.year("first"), values.year("last")
    reference = values.ref("a")
    span = last - first
    return [
        Output(
            f"{values.title('a')}: среднегодовой темп за {first}–{last}",
            f"ЗАГОД(({reference} / ВГОДУ({reference}; {first})) ^ (1 / {span}) * 100 - 100; "
            f"{last})",
            "% в год",
            INDEX,
        )
    ]


def _scaled(key: str, factor: float) -> str:
    """Слагаемое в единице первого: «тыс. руб.» к «млн руб.» — делится на 1 000."""
    if factor > 1:
        return f"{_ref(key)} * {round(factor)}"
    if factor < 1:
        return f"{_ref(key)} / {round(1 / factor)}"
    return _ref(key)


def _sum(values: Values) -> list[Output]:
    keys = values.lists["parts"]
    titles = [values.info[key].title if key in values.info else key for key in keys]
    units = [values.info[key].unit for key in keys if key in values.info]
    first = semantics.unit_scale(units[0])[1] if units else 1.0
    factors = {
        key: semantics.unit_scale(values.info[key].unit)[1] / first if key in values.info else 1.0
        for key in keys
    }
    kinds = {values.info[key].type for key in keys if key in values.info}
    exact = kinds - {semantics.SUM}
    kind = exact.pop() if len(exact) == 1 else semantics.SUM
    return [
        Output(
            "Сумма: " + " + ".join(titles),
            _joined([_scaled(key, factors[key]) for key in keys], "+"),
            units[0] if units else "",
            kind,
        )
    ]


def _russia_deviation(values: Values) -> list[Output]:
    reference = values.ref("a")
    return [
        Output(
            f"{values.title('a')}: отклонение от России",
            f"{reference} - РОССИЯ({reference})",
            values.unit("a"),
        )
    ]


def _district_deviation(values: Values) -> list[Output]:
    reference = values.ref("a")
    return [
        Output(
            f"{values.title('a')}: отклонение от среднего по округу",
            f"{reference} - ОКРУГ({reference})",
            values.unit("a"),
        )
    ]


def _location_quotient(values: Values) -> list[Output]:
    part, total = values.ref("a"), values.ref("b")
    return [
        Output(
            f"Коэффициент локализации: {values.title('a')}",
            f"({part} / {total}) / ({_country(part)} / {_country(total)})",
            "",
        )
    ]


def _structural_shift(values: Values) -> list[Output]:
    first, last = values.year("first"), values.year("last")
    total = values.ref("b")
    parts = values.lists["parts"]
    terms = [
        f"({_ref(key)} / {total} - ВГОДУ({_ref(key)}; {first}) / ВГОДУ({total}; {first})) ^ 2"
        for key in parts
    ]
    return [
        Output(
            f"Индекс структурных сдвигов {first}–{last}: {values.title('b')}",
            f"ЗАГОД(КОРЕНЬ(({_joined(terms, '+')}) / {len(parts)}) * 100; {last})",
            "п. п.",
        )
    ]


def _shift_share(values: Values) -> list[Output]:
    """
    Сдвиг-доля: изменение суммы составляющих за годы — общероссийский рост, отраслевая
    структура и региональный сдвиг.
    """
    first, last = values.year("first"), values.year("last")
    parts = values.lists["parts"]

    def start(key: str) -> str:
        return f"ВГОДУ({_ref(key)}; {first})"

    def country(key: str, year: int) -> str:
        return f"ВГОДУ({_country(_ref(key))}; {year})"

    base = _joined([start(key) for key in parts], "+")
    growth = (
        f"(({_joined([country(key, last) for key in parts], '+')}) / "
        f"({_joined([country(key, first) for key in parts], '+')}) - 1)"
    )
    national = f"ЗАГОД(({base}) * {growth}; {last})"
    mix_terms = [
        f"{start(key)} * ({country(key, last)} / {country(key, first)} - 1)" for key in parts
    ]
    mix = f"ЗАГОД({_joined(mix_terms, '+')} - ({base}) * {growth}; {last})"
    shift_terms = [
        f"{start(key)} * ({_ref(key)} / {start(key)} - {country(key, last)} / "
        f"{country(key, first)})"
        for key in parts
    ]
    regional = f"ЗАГОД({_joined(shift_terms, '+')}; {last})"
    unit = values.info[parts[0]].unit if parts[0] in values.info else ""
    period = f"{first}–{last}"
    return [
        Output(f"Сдвиг-доля {period}: общероссийский рост", national, unit, FLOW),
        Output(f"Сдвиг-доля {period}: отраслевая структура", mix, unit, FLOW),
        Output(f"Сдвиг-доля {period}: региональный сдвиг", regional, unit, FLOW),
    ]


# --- Каталог ------------------------------------------------------------------------------

YEARS = (Field("first", YEAR, _("С года")), Field("last", YEAR, _("По год")))
PARTS = Field("parts", SERIES_LIST, _("Составляющие"))
TOTAL = Field("b", SERIES, _("Итог"))

OPERATIONS: tuple[Operation, ...] = (
    # На жителя: пересчёты сборки и профильные формулы.
    Operation(
        "per1",
        "per",
        _("На жителя"),
        _(
            "Сумма делится на среднегодовую численность населения года. Разряд единицы "
            "подбирается по значениям: тыс. руб. на жителя, а не дробь с нулями."
        ),
        recount=DatasetSeries.Derived.PER_1.value,
    ),
    Operation(
        "per1000",
        "per",
        _("На 1 000 жителей"),
        _("Сумма делится на среднегодовую численность населения и умножается на 1 000."),
        recount=DatasetSeries.Derived.PER_1000.value,
    ),
    Operation(
        "per10000",
        "per",
        _("На 10 000 жителей"),
        _("Сумма делится на среднегодовую численность населения и умножается на 10 000."),
        recount=DatasetSeries.Derived.PER_10000.value,
    ),
    Operation(
        "per100000",
        "per",
        _("На 100 000 жителей"),
        _(
            "Сумма делится на среднегодовую численность населения и умножается на 100 000. "
            "Так считают преступность и заболеваемость."
        ),
        recount=DatasetSeries.Derived.PER_100000.value,
    ),
    Operation(
        "perkm2",
        "per",
        _("На км²"),
        _("Сумма делится на площадь территории региона."),
        recount=DatasetSeries.Derived.PER_KM2.value,
    ),
    Operation(
        "pergroup",
        "per",
        _("На 1 000 человек группы"),
        _(
            "Сумма делится на численность группы — детей, жителей трудоспособного возраста, "
            "пенсионеров или занятых — по официальным рядам. Значения — за годы, где есть "
            "численность группы."
        ),
        fields=(A, Field("group", CHOICE, _("Группа"), GROUP_CHOICES)),
        official=tuple(key for key, _name in GROUP_DENOMINATORS.values()),
        outputs=_per_group,
    ),
    Operation(
        "pricelevel",
        "per",
        _("С поправкой на уровень цен"),
        _(
            "Значение делится на стоимость фиксированного набора товаров и услуг региона "
            "в процентах к средней по России. Получаются рубли в ценах России."
        ),
        fields=(A,),
        official=(PRICE_LEVEL,),
        outputs=_price_level,
    ),
    Operation(
        "subsistence",
        "per",
        _("В прожиточных минимумах"),
        _(
            "Значение в рублях в месяц на человека делится на прожиточный минимум региона "
            "на душу населения."
        ),
        fields=(A,),
        official=(SUBSISTENCE,),
        outputs=_subsistence,
    ),
    # Доля и структура.
    Operation(
        "share",
        "share",
        _("Доля в сумме по субъектам"),
        _("Значение региона делится на сумму значений субъектов за тот же год."),
        recount=DatasetSeries.Derived.SHARE.value,
    ),
    Operation(
        "structure",
        "share",
        _("Доля составляющей в итоге"),
        _("Составляющая делится на итог того же региона и года, в процентах."),
        fields=(Field("a", SERIES, _("Составляющая")), TOTAL),
        outputs=_structure,
    ),
    Operation(
        "hhi",
        "share",
        _("Индекс Херфиндаля — Хиршмана"),
        _(
            "Сумма квадратов долей составляющих в итоге, умноженная на 10 000. Чем ближе "
            "к 10 000, тем сильнее итог сосредоточен в одной составляющей."
        ),
        fields=(PARTS, TOTAL),
        outputs=_concentration,
    ),
    # Изменение.
    Operation(
        "growth",
        "change",
        _("% к предыдущему году"),
        _("Значение делится на значение того же периода прошлого года, только у положительных."),
        recount=DatasetSeries.Derived.GROWTH.value,
    ),
    Operation(
        "baseyear",
        "change",
        _("Базовый год = 100"),
        _("Значение каждого года делится на значение базового года того же региона."),
        fields=(A, Field("year", YEAR, _("Базовый год"))),
        outputs=_base_year,
    ),
    Operation(
        "between",
        "change",
        _("Изменение между двумя годами"),
        _(
            "Разность значений или их отношение в процентах. Результат — одно значение "
            "у региона за последний год периода."
        ),
        fields=(A, *YEARS, Field("mode", CHOICE, _("Как"), CHANGE_CHOICES)),
        outputs=_between,
    ),
    Operation(
        "annualrate",
        "change",
        _("Среднегодовой темп за период"),
        _(
            "Корень из отношения последнего года к первому, степень — число лет между ними. "
            "Только у положительных значений."
        ),
        fields=(A, *YEARS),
        outputs=_annual_rate,
    ),
    Operation(
        "real",
        "change",
        _("В ценах последнего года"),
        _(
            "Значение умножается на индексы потребительских цен региона за следующие годы. "
            "При пропуске индекса не считается."
        ),
        recount=DatasetSeries.Derived.REAL.value,
    ),
    # Сравнение.
    Operation(
        "russia",
        "compare",
        _("Россия = 100"),
        _("Значение региона делится на значение России за тот же год и умножается на 100."),
        recount=DatasetSeries.Derived.RUSSIA.value,
    ),
    Operation(
        "russiadeviation",
        "compare",
        _("Отклонение от России"),
        _("Из значения региона вычитается значение России за тот же год."),
        fields=(A,),
        outputs=_russia_deviation,
    ),
    Operation(
        "districtdeviation",
        "compare",
        _("Отклонение от среднего по округу"),
        _(
            "Из значения региона вычитается простое среднее субъектов его федерального "
            "округа за тот же год."
        ),
        fields=(A,),
        outputs=_district_deviation,
    ),
    # Агрегирование.
    Operation(
        "sum",
        "sum",
        _("Сумма показателей"),
        _("Значения складываются по региону и году. Пропуск у любого слагаемого — пропуск суммы."),
        fields=(PARTS,),
        outputs=_sum,
    ),
    # Методы регионального анализа.
    Operation(
        "lq",
        "regional",
        _("Коэффициент локализации"),
        _(
            "Доля отрасли в итоге региона, деленная на ту же долю по России. Больше 1 — "
            "отрасль в регионе представлена сильнее, чем в стране."
        ),
        fields=(Field("a", SERIES, _("Отрасль")), TOTAL),
        outputs=_location_quotient,
    ),
    Operation(
        "structuralshift",
        "regional",
        _("Индекс структурных сдвигов"),
        _(
            "Квадратичное среднее изменений долей составляющих в итоге между двумя годами, "
            "в процентных пунктах."
        ),
        fields=(PARTS, TOTAL, *YEARS),
        outputs=_structural_shift,
    ),
    Operation(
        "shiftshare",
        "regional",
        _("Разложение «сдвиг-доля»"),
        _(
            "Изменение суммы отраслей за период раскладывается на три части. Общероссийский "
            "рост, отраслевая структура региона и региональный сдвиг. Значение России — "
            "из таблицы, нет его — сумма субъектов."
        ),
        fields=(PARTS, *YEARS),
        outputs=_shift_share,
    ),
)
BY_CODE: dict[str, Operation] = {item.code: item for item in OPERATIONS}
# Доли частей считаются от итога: части должны его покрывать, иначе индекс неточен.
COVERAGE_OPERATIONS = frozenset({"hhi", "structuralshift"})


def coverage_expression(values: Values) -> str:
    """Сумма частей, деленная на итог: у частей, покрывающих итог, — около 1."""
    return f"({_joined([_ref(key) for key in values.lists['parts']], '+')}) / {values.ref('b')}"


def recount_codes() -> list[str]:
    """Пересчёты сборки в порядке каталога — для «Посчитать» в «Что сделать»."""
    return [item.recount for item in OPERATIONS if item.recount]


def reason(operation: Operation, info: Info, available: Mapping[str, bool] | None = None) -> str:
    """
    Почему операция не подходит ряду; пусто — подходит. Тип, основа, деньги и данные —
    по таблице правил; нужных рядов сайта нет — отдельно.
    """
    found = semantics.verdict(f"calc:{operation.code}", info.value).reason
    if found:
        return found
    missing = (
        operation.official
        and available is not None
        and not any(available.get(key) for key in operation.official)
    )
    return str(_("нужных показателей нет на сайте")) if missing else ""


def grouped(
    info: Info | None = None, available: Mapping[str, bool] | None = None
) -> list[dict[str, Any]]:
    """Каталог по группам; у каждой операции — причина, если ряду ``info`` не подходит."""
    found = []
    for code, title in GROUPS:
        items = [
            {"operation": item, "reason": reason(item, info, available) if info else ""}
            for item in OPERATIONS
            if item.group == code
        ]
        found.append({"code": code, "title": title, "items": items})
    return found


def arranged(catalog: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Каталог для окна: частые подходящие расчёты — плитками (не больше шести), остальные
    подходящие — группами под «Все расчёты», неподходящие — одним перечнем с причинами.
    """
    flat = [entry for group in catalog for entry in group["items"]]
    fitting = {entry["operation"].code: entry for entry in flat if not entry["reason"]}
    popular = [
        {**fitting[code], "note": NOTES.get(code, "")} for code in POPULAR if code in fitting
    ][:POPULAR_SHOWN]
    shown = {entry["operation"].code for entry in popular}
    rest = [
        {
            **group,
            "items": [
                entry
                for entry in group["items"]
                if not entry["reason"] and entry["operation"].code not in shown
            ],
        }
        for group in catalog
    ]
    return {
        "popular": popular,
        "rest": [group for group in rest if group["items"]],
        "rest_codes": {entry["operation"].code for group in rest for entry in group["items"]},
        "unavailable": [entry for entry in flat if entry["reason"]],
    }


def available_official() -> dict[str, bool]:
    """Официальные ряды профильных расчётов: есть ли каждый на складе."""
    from apps.catalog.models import Series

    present = set(
        Series.objects.filter(key__in=OFFICIAL_INPUTS, value_count__gt=0).values_list(
            "key", flat=True
        )
    )
    return {key: key in present for key in OFFICIAL_INPUTS}


def _official_sum(kind: str, unit: str) -> bool:
    """Официальный ряд — сумма: абсолютная величина или деньги с разрядом (млн руб.)."""
    from apps.catalog.constants import UnitKind

    return kind == UnitKind.ABSOLUTE or (
        kind == UnitKind.CURRENCY and bool(_ORDER_WORD.search(unit))
    )


def info_of(key: str) -> Info | None:
    """Сведения о ряде для каталога: свой — из описания таблицы, официальный — из справочника."""
    from .series import user_series_many

    if routing.is_user_key(key):
        found = user_series_many([key]).get(key)
        if found is None:
            return None
        record = found.record
        value = found.value()
        return Info(
            key=key,
            title=found.full_title_ru,
            unit=record.unit,
            is_sum=found.is_sum,
            is_currency=value.is_currency,
            is_rubles=value.is_rubles,
            first_year=record.first_year,
            last_year=record.last_year,
            regions=record.regions_count,
            derived=record.derived,
            kind=value.type,
            base=value.base,
            monthly=value.monthly,
        )
    from apps.catalog.models import Series

    series = Series.objects.select_related("indicator", "unit").filter(key=key).first()
    if series is None:
        return None
    title = series.indicator.name_ru
    if series.has_subsection and series.name_ru:
        title = f"{title} — {series.name_ru}"
    unit = series.unit.name_ru if series.unit else ""
    unit_kind = series.unit.kind if series.unit else ""
    is_sum = _official_sum(unit_kind, unit)
    return Info(
        key=key,
        title=title,
        unit=unit,
        is_sum=is_sum,
        is_currency=semantics.is_currency(title, unit),
        is_rubles=semantics.is_rubles(title, unit),
        first_year=series.first_year,
        last_year=series.last_year,
        regions=series.region_count,
        kind=semantics.official_type(is_sum=is_sum, unit_kind=unit_kind),
        base=semantics.guess_base(title),
        monthly=semantics.is_monthly(title, unit),
        is_user=False,
    )


# --- Предупреждения в момент расчёта ----------------------------------------------------------

# Итог среди слагаемых: «Всего», «Итого» в названии или разрезе.
_TOTAL_WORD = re.compile(r"\b(всего|итого|в целом)\b", re.IGNORECASE)
# Валовая добавленная стоимость по видам деятельности — не ВРП.
_VALUE_ADDED = re.compile(r"добавленн", re.IGNORECASE)


def warnings(expression: str, info: Mapping[str, Info]) -> list[str]:
    """
    О чём предупредить до сохранения формулы — правилами по её дереву и описаниям рядов:
    сумма в разных единицах, итог вместе со своими частями, сумма относительных величин,
    сумма добавленной стоимости вместо ВРП, деление величин за разные годы.
    """
    from . import formulas

    tree = formulas.parse(expression)
    found: list[str] = []
    for group in _additive_groups(tree):
        found += _sum_warnings([info[key] for key in group if key in info])
    for left, right in _divisions(tree):
        message = _years_warning(
            [info[key] for key in left if key in info], [info[key] for key in right if key in info]
        )
        if message:
            found.append(message)
    return list(dict.fromkeys(found))


def _keys(node: Any) -> list[str]:
    from . import formulas

    return [reference.key or reference.text for reference in formulas.references(node)]


def _additive_groups(tree: Any) -> list[list[str]]:
    """Слагаемые-ряды каждой суммы или разности: «[A] + [B] - [C]» — [A, B, C]."""
    from .formulas import Binary, Call, Compare, Reference, Unary

    groups: list[list[str]] = []

    def terms(node: Any) -> list[Any]:
        if isinstance(node, Binary) and node.operator in "+-":
            return terms(node.left) + terms(node.right)
        return [node]

    def walk(node: Any) -> None:
        if isinstance(node, Binary) and node.operator in "+-":
            parts = terms(node)
            keys = [item.key for item in parts if isinstance(item, Reference) and item.key]
            if len(keys) > 1:
                groups.append(keys)
            for item in parts:
                if not isinstance(item, Reference):
                    walk(item)
            return
        if isinstance(node, Binary | Compare):
            walk(node.left)
            walk(node.right)
        elif isinstance(node, Unary):
            walk(node.operand)
        elif isinstance(node, Call):
            for argument in node.arguments:
                walk(argument)

    walk(tree)
    return groups


def _divisions(tree: Any) -> list[tuple[list[str], list[str]]]:
    """Ряды числителя и знаменателя каждого деления, где оба — ряды без сдвига по годам."""
    from .formulas import Binary, Call, Compare, Unary

    found: list[tuple[list[str], list[str]]] = []
    shifting = {"AGO", "AT", "INYEAR", "MOVING", "CUMSUM"}

    def shifted(node: Any) -> bool:
        if isinstance(node, Call):
            return node.name in shifting or any(shifted(item) for item in node.arguments)
        if isinstance(node, Binary | Compare):
            return shifted(node.left) or shifted(node.right)
        if isinstance(node, Unary):
            return shifted(node.operand)
        return False

    def walk(node: Any) -> None:
        if isinstance(node, Binary):
            if node.operator == "/" and not shifted(node):
                left, right = _keys(node.left), _keys(node.right)
                if left and right:
                    found.append((left, right))
            walk(node.left)
            walk(node.right)
        elif isinstance(node, Compare):
            walk(node.left)
            walk(node.right)
        elif isinstance(node, Unary):
            walk(node.operand)
        elif isinstance(node, Call):
            for argument in node.arguments:
                walk(argument)

    walk(tree)
    return found


def _sum_warnings(items: list[Info]) -> list[str]:
    """Предупреждения о сложении рядов."""
    found: list[str] = []
    if len(items) < 2:  # noqa: PLR2004 — сумма — это хотя бы два слагаемых
        return found
    units = list(dict.fromkeys(" ".join(item.unit.casefold().split()) for item in items))
    if len(units) > 1:
        found.append(
            str(_("Складываются показатели в разных единицах: %(units)s. Приведите их к одной."))
            % {"units": ", ".join(item for item in units if item) or "—"}
        )
    kinds = {item.type for item in items}
    if {FLOW, STOCK} <= kinds:
        found.append(
            str(
                _(
                    "Складываются значения за период и на дату: выдано за год и остаток "
                    "на 1 января не складываются."
                )
            )
        )
    if INDEX in kinds:
        found.append(str(_("Среди слагаемых — индексы: темпы роста не складываются.")))
    if any(not item.is_sum and item.type != INDEX for item in items):
        found.append(
            str(
                _(
                    "Среди слагаемых — относительные величины: доли и средние не складываются. "
                    "Сумма имеет смысл для численности, денег и объёмов целиком."
                )
            )
        )
    totals = [item for item in items if _TOTAL_WORD.search(item.title)]
    if totals and any(
        item not in totals and _stem(item.title) == _stem(total.title)
        for total in totals
        for item in items
    ):
        found.append(
            str(_("Среди слагаемых — итог «%(total)s» и его части: значения посчитаются дважды."))
            % {"total": totals[0].title}
        )
    if sum(1 for item in items if _VALUE_ADDED.search(item.title)) > 1:
        found.append(
            str(
                _(
                    "Сумма добавленной стоимости по видам деятельности — не ВРП: в ВРП входят "
                    "ещё чистые налоги на продукты."
                )
            )
        )
    return found


def _stem(title: str) -> str:
    """Показатель без разреза: часть названия до тире."""
    return title.split(" — ", 1)[0].strip().casefold()


def _years_warning(left: list[Info], right: list[Info]) -> str:
    """Числитель и знаменатель — за разные годы: значения будут только за общие."""
    spans = [
        (item.first_year, item.last_year)
        for item in (*left, *right)
        if item.first_year is not None and item.last_year is not None
    ]
    if len(spans) < 2 or len(set(spans)) == 1:  # noqa: PLR2004 — сравниваются хотя бы два ряда
        return ""
    first = max(span[0] for span in spans)
    last = min(span[1] for span in spans)
    if first > last:
        return str(_("У числителя и знаменателя нет общих лет: значений не будет."))
    return str(
        _(
            "Числитель и знаменатель — за разные годы. Значения будут только за общие годы: "
            "%(first)s–%(last)s."
        )
    ) % {"first": first, "last": last}
