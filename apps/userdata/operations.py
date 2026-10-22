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

from .indicators import RELATIVE, SUM
from .models import DatasetSeries

# Группы каталога по порядку.
GROUPS: tuple[tuple[str, Promise], ...] = (
    ("per", _("На жителя")),
    ("share", _("Доля и структура")),
    ("change", _("Изменение")),
    ("compare", _("Сравнение")),
    ("sum", _("Сложение")),
    ("regional", _("Региональные методы")),
)

# Виды полей операции.
SERIES = "series"
SERIES_LIST = "series_list"
YEAR = "year"
WINDOW = "window"
NUMBER = "number"
CHOICE = "choice"

# Составляющих в сумме, структуре и сдвигах — не больше: формула ссылается не больше чем
# на двенадцать рядов, у сдвига-доли каждая составляющая ещё и с итогом России.
MAX_PARTS = 8
SHIFT_SHARE_PARTS = 6
# Окно скользящего среднего, лет.
WINDOWS = (2, 3, 4, 5, 7, 10)

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
TARGET_CHOICES: tuple[tuple[str, Promise], ...] = (
    ("percent", _("процент цели")),
    ("deviation", _("отклонение от цели")),
)


@dataclass(frozen=True, slots=True)
class Field:
    """Поле операции: ряд, перечень рядов, год, окно, число или выбор."""

    name: str
    kind: str
    label: Promise
    choices: tuple[tuple[str, Promise], ...] = ()


@dataclass(frozen=True, slots=True)
class Info:
    """Что о ряде нужно каталогу: название, единица, вид величины, деньги, годы, регионы."""

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

    @property
    def years(self) -> int:
        if self.first_year is None or self.last_year is None:
            return 0
        return self.last_year - self.first_year + 1


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
    по значениям полей. ``sums_only`` и ``currency_only`` — к какому первому ряду применимо.
    """

    code: str
    group: str
    title: Promise
    method: Promise
    fields: tuple[Field, ...] = ()
    recount: str = ""
    sums_only: bool = False
    currency_only: bool = False
    rubles_only: bool = False
    relative_only: bool = False
    min_years: int = 1
    min_regions: int = 0
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


def _russia_share(values: Values) -> list[Output]:
    return [
        Output(
            f"{values.title('a')}: доля в значении по России",
            f"{values.ref('a')} / РОССИЯ({values.ref('a')}) * 100",
            "% от значения по России",
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
                SUM if values.is_sum("a") else RELATIVE,
            )
        ]
    return [
        Output(
            f"{values.title('a')}: изменение за {first}–{last}, %",
            f"ЗАГОД({reference} / ВГОДУ({reference}; {first}) * 100 - 100; {last})",
            "%",
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
        )
    ]


def _moving(values: Values) -> list[Output]:
    window = int(values.numbers["window"])
    is_sum = values.is_sum("a")
    return [
        Output(
            f"{values.title('a')}: скользящее среднее за {window} года"
            if window < 5  # noqa: PLR2004 — «года» до пяти, дальше «лет»
            else f"{values.title('a')}: скользящее среднее за {window} лет",
            f"СКОЛЬЗЯЩЕЕ({values.ref('a')}; {window})",
            values.unit("a"),
            SUM if is_sum else RELATIVE,
        )
    ]


def _cumulative(values: Values) -> list[Output]:
    return [
        Output(
            f"{values.title('a')}: накопленный итог",
            f"НАКОПЛЕННОЕ({values.ref('a')})",
            values.unit("a"),
            SUM,
        )
    ]


def _sum(values: Values) -> list[Output]:
    keys = values.lists["parts"]
    titles = [values.info[key].title if key in values.info else key for key in keys]
    units = [values.info[key].unit for key in keys if key in values.info]
    all_sums = all(values.info[key].is_sum for key in keys if key in values.info)
    return [
        Output(
            "Сумма: " + " + ".join(titles),
            _joined([_ref(key) for key in keys], "+"),
            units[0] if units else "",
            SUM if all_sums else RELATIVE,
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


def _z_score(values: Values) -> list[Output]:
    reference = values.ref("a")
    return [
        Output(
            f"{values.title('a')}: z-оценка",
            f"({reference} - СРЕДНЕЕ({reference})) / СТАНДОТКЛОН({reference})",
            "стандартных отклонений",
        )
    ]


def _weighted_district(values: Values) -> list[Output]:
    return [
        Output(
            f"{values.title('a')}: среднее по округу, взвешенное по «{values.title('b')}»",
            f"ВЗВОКРУГ({values.ref('a')}; {values.ref('b')})",
            values.unit("a"),
        )
    ]


def _target(values: Values) -> list[Output]:
    target = values.numbers["target"]
    text = f"{target:g}"
    if values.choices.get("mode") == "deviation":
        return [
            Output(
                f"{values.title('a')}: отклонение от цели {text}",
                f"{values.ref('a')} - {text}",
                values.unit("a"),
            )
        ]
    return [
        Output(
            f"{values.title('a')}: % цели {text}",
            f"{values.ref('a')} / {text} * 100",
            "% цели",
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
        Output(f"Сдвиг-доля {period}: общероссийский рост", national, unit, SUM),
        Output(f"Сдвиг-доля {period}: отраслевая структура", mix, unit, SUM),
        Output(f"Сдвиг-доля {period}: региональный сдвиг", regional, unit, SUM),
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
        sums_only=True,
        currency_only=True,
    ),
    Operation(
        "per1000",
        "per",
        _("На 1 000 жителей"),
        _("Сумма делится на среднегодовую численность населения и умножается на 1 000."),
        recount=DatasetSeries.Derived.PER_1000.value,
        sums_only=True,
    ),
    Operation(
        "per10000",
        "per",
        _("На 10 000 жителей"),
        _("Сумма делится на среднегодовую численность населения и умножается на 10 000."),
        recount=DatasetSeries.Derived.PER_10000.value,
        sums_only=True,
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
        sums_only=True,
    ),
    Operation(
        "perkm2",
        "per",
        _("На км²"),
        _("Сумма делится на площадь территории региона."),
        recount=DatasetSeries.Derived.PER_KM2.value,
        sums_only=True,
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
        sums_only=True,
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
        currency_only=True,
        relative_only=True,
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
        currency_only=True,
        relative_only=True,
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
        sums_only=True,
    ),
    Operation(
        "russiashare",
        "share",
        _("Доля в значении по России"),
        _(
            "Значение региона делится на значение России из таблицы. Отличается от доли "
            "в сумме субъектов, когда часть итога не распределена по регионам."
        ),
        fields=(A,),
        sums_only=True,
        outputs=_russia_share,
    ),
    Operation(
        "structure",
        "share",
        _("Доля составляющей в итоге"),
        _("Составляющая делится на итог того же региона и года, в процентах."),
        fields=(Field("a", SERIES, _("Составляющая")), TOTAL),
        sums_only=True,
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
        sums_only=True,
        outputs=_concentration,
    ),
    # Изменение.
    Operation(
        "growth",
        "change",
        _("% к предыдущему году"),
        _("Значение делится на значение того же периода прошлого года, только у положительных."),
        recount=DatasetSeries.Derived.GROWTH.value,
        min_years=2,
    ),
    Operation(
        "baseyear",
        "change",
        _("Базовый год = 100"),
        _("Значение каждого года делится на значение базового года того же региона."),
        fields=(A, Field("year", YEAR, _("Базовый год"))),
        min_years=2,
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
        min_years=2,
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
        min_years=3,
        outputs=_annual_rate,
    ),
    Operation(
        "moving",
        "change",
        _("Скользящее среднее"),
        _("Среднее за окно лет, считая текущий. Неполное окно или пропуск в нём — без значения."),
        fields=(A, Field("window", WINDOW, _("Окно, лет"))),
        min_years=3,
        outputs=_moving,
    ),
    Operation(
        "cumulative",
        "change",
        _("Накопленный итог"),
        _("Сумма с первого года со значением. После пропуска года итог не продолжается."),
        fields=(A,),
        sums_only=True,
        min_years=2,
        outputs=_cumulative,
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
        rubles_only=True,
        min_years=1,
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
        relative_only=True,
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
        relative_only=True,
        outputs=_district_deviation,
    ),
    Operation(
        "zscore",
        "compare",
        _("z-оценка"),
        _(
            "Отклонение от среднего по субъектам за год, деленное на стандартное отклонение. "
            "Показывает, насколько регион необычен в своём году."
        ),
        fields=(A,),
        min_regions=20,
        outputs=_z_score,
    ),
    Operation(
        "target",
        "compare",
        _("Достижение цели"),
        _("Значение делится на целевое значение, в процентах, или из него вычитается цель."),
        fields=(
            A,
            Field("target", NUMBER, _("Целевое значение")),
            Field("mode", CHOICE, _("Как"), TARGET_CHOICES),
        ),
        outputs=_target,
    ),
    # Сложение.
    Operation(
        "sum",
        "sum",
        _("Сумма показателей"),
        _("Значения складываются по региону и году. Пропуск у любого слагаемого — пропуск суммы."),
        fields=(PARTS,),
        outputs=_sum,
    ),
    Operation(
        "weighteddistrict",
        "sum",
        _("Взвешенное среднее по округу"),
        _(
            "Среднее субъектов федерального округа, взвешенное по второму показателю, — "
            "например, средняя зарплата по численности работников."
        ),
        fields=(A, Field("b", SERIES, _("Вес"))),
        relative_only=True,
        outputs=_weighted_district,
    ),
    # Региональные методы.
    Operation(
        "lq",
        "regional",
        _("Коэффициент локализации"),
        _(
            "Доля отрасли в итоге региона, деленная на ту же долю по России. Больше 1 — "
            "отрасль в регионе представлена сильнее, чем в стране."
        ),
        fields=(Field("a", SERIES, _("Отрасль")), TOTAL),
        sums_only=True,
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
        sums_only=True,
        min_years=2,
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
        sums_only=True,
        min_years=2,
        outputs=_shift_share,
    ),
)
BY_CODE: dict[str, Operation] = {item.code: item for item in OPERATIONS}


def recount_codes() -> list[str]:
    """Пересчёты сборки в порядке каталога — для «Посчитать» в «Что сделать»."""
    return [item.recount for item in OPERATIONS if item.recount]


def reason(operation: Operation, info: Info, available: Mapping[str, bool] | None = None) -> str:
    """Почему операция не подходит ряду; пусто — подходит."""
    missing = (
        operation.official
        and available is not None
        and not any(available.get(key) for key in operation.official)
    )
    checks = (
        (operation.sums_only and not info.is_sum, _("только для сумм по регионам")),
        (operation.relative_only and info.is_sum, _("только для относительных величин")),
        (operation.currency_only and not info.is_currency, _("только для денежных показателей")),
        (operation.rubles_only and not info.is_rubles, _("только для показателей в рублях")),
        (
            operation.min_years > 1 and info.years < operation.min_years,
            _("нужно лет не меньше: %(count)s") % {"count": operation.min_years},
        ),
        (
            bool(operation.min_regions) and info.regions < operation.min_regions,
            _("нужны значения хотя бы по %(count)s регионам") % {"count": operation.min_regions},
        ),
        (missing, _("нужных официальных рядов нет на сайте")),
    )
    return next((str(message) for failed, message in checks if failed), "")


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
    from . import indicators
    from .series import user_series_many

    if routing.is_user_key(key):
        found = user_series_many([key]).get(key)
        if found is None:
            return None
        record = found.record
        return Info(
            key=key,
            title=found.full_title_ru,
            unit=record.unit,
            is_sum=found.is_sum,
            is_currency=indicators.is_currency(record.title, record.unit),
            is_rubles=indicators.is_rubles(record.title, record.unit),
            first_year=record.first_year,
            last_year=record.last_year,
            regions=record.regions_count,
            derived=record.derived,
        )
    from apps.catalog.models import Series

    series = Series.objects.select_related("indicator", "unit").filter(key=key).first()
    if series is None:
        return None
    title = series.indicator.name_ru
    if series.has_subsection and series.name_ru:
        title = f"{title} — {series.name_ru}"
    unit = series.unit.name_ru if series.unit else ""
    return Info(
        key=key,
        title=title,
        unit=unit,
        is_sum=_official_sum(series.unit.kind if series.unit else "", unit),
        is_currency=indicators.is_currency(title, unit),
        is_rubles=indicators.is_rubles(title, unit),
        first_year=series.first_year,
        last_year=series.last_year,
        regions=series.region_count,
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
            str(_("Складываются ряды в разных единицах: %(units)s. Приведите их к одной."))
            % {"units": ", ".join(item for item in units if item) or "—"}
        )
    if any(not item.is_sum for item in items):
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
