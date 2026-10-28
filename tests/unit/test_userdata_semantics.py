"""
Смысл величины своих данных: угадывание типа и основы по названию и единице, уточнение
прежнего вида и таблица правил «тип × действие» — по схеме, принятой автором.
"""

from __future__ import annotations

import pytest

from apps.userdata import semantics
from apps.userdata.semantics import (
    AREA,
    FLOW,
    INDEX,
    NONE,
    PEOPLE,
    RELATIVE,
    STOCK,
    SUM,
    Value,
)

pytestmark = pytest.mark.unit

ORDER = (FLOW, STOCK, SUM, RELATIVE, INDEX)
# Матрица схемы: «+» — можно, «→» — сумме через пересчёт на жителя, «-» — нельзя.
# Столбцы — поток, запас, сумма, относительная, индекс.
EXPECTED = {
    "view:map": "+++++",
    "view:compare": "+++++",
    "view:rankings": "+++++",
    "view:distribution": "+++++",
    "view:table": "+++++",
    "answer:multiples": "+++++",
    "view:structure": "+++--",
    "answer:notable": "+++++",
    "answer:leaders": "+++++",
    "answer:mine": "+++++",
    "answer:change": "+++++",
    "answer:growth": "+++++",
    "answer:contributions": "+++--",
    "answer:districts": "+++++",
    "answer:spread": "→→→+-",
    "answer:convergence": "→→→+-",
    "answer:neighbours": "→→→++",
    "answer:related": "→→→++",
    "answer:structure": "+++--",
    "answer:group": "+++++",
    "answer:comparison": "+++++",
    "answer:relation": "→→→++",
    "set:profile": "+++++",
    "set:pair": "+++++",
    "calc:per1": "+++--",
    "calc:per1000": "+++--",
    "calc:per10000": "+++--",
    "calc:per100000": "+++--",
    "calc:perkm2": "+++--",
    "calc:pergroup": "+++--",
    "calc:pricelevel": "---+-",
    "calc:subsistence": "---+-",
    "calc:share": "+++--",
    "calc:structure": "+++--",
    "calc:hhi": "+++--",
    "calc:growth": "++++-",
    "calc:baseyear": "++++-",
    "calc:between": "++++-",
    "calc:annualrate": "++++-",
    "calc:real": "++++-",
    "calc:russia": "---+-",
    "calc:russiadeviation": "---++",
    "calc:districtdeviation": "---++",
    "calc:sum": "+++--",
    "calc:lq": "+++--",
    "calc:structuralshift": "+++--",
    "calc:shiftshare": "+++--",
    "build:fold": "+++--",
}


def rich(kind: str, **changes: object) -> Value:
    """Ряд, которому хватает данных для любого действия: дело только в типе."""
    fields: dict[str, object] = {
        "type": kind,
        "base": PEOPLE,
        "is_rubles": True,
        "is_currency": True,
        "monthly": True,
        "years": 10,
        "regions": 85,
        "parts": 3,
    }
    fields.update(changes)
    return Value(**fields)  # type: ignore[arg-type]


def mark(verdict: semantics.Verdict) -> str:
    if verdict:
        return "+"
    return "→" if verdict.via else "-"


class TestMatrix:
    def test_every_rule_is_in_the_schema(self) -> None:
        assert set(semantics.RULES) == set(EXPECTED)

    @pytest.mark.parametrize("action", sorted(EXPECTED))
    def test_type_by_action(self, action: str) -> None:
        found = "".join(mark(semantics.verdict(action, rich(kind))) for kind in ORDER)
        assert found == EXPECTED[action]

    @pytest.mark.parametrize("action", sorted(EXPECTED))
    def test_refusal_has_reason(self, action: str) -> None:
        for kind in ORDER:
            verdict = semantics.verdict(action, rich(kind))
            assert bool(verdict) == (not verdict.reason)


class TestBase:
    def test_per_capita_needs_people(self) -> None:
        for base in (AREA, NONE):
            verdict = semantics.verdict("calc:per100000", rich(STOCK, base=base))
            assert not verdict
        assert semantics.verdict("calc:perkm2", rich(STOCK, base=NONE))

    def test_via_follows_base(self) -> None:
        assert semantics.verdict("answer:spread", rich(FLOW)).via == semantics.PER_1
        assert semantics.verdict("answer:spread", rich(FLOW, base=AREA)).via == semantics.PER_KM2
        without = semantics.verdict("answer:spread", rich(FLOW, base=NONE))
        assert not without
        assert not without.via
        assert "основы" in without.reason

    def test_official_sum_is_pointed_to_site(self) -> None:
        verdict = semantics.verdict("answer:related", rich(SUM, is_user=False))
        assert "показатель сайта" in verdict.reason

    def test_structure_only_for_own_tables(self) -> None:
        assert not semantics.verdict("view:structure", rich(STOCK, is_user=False))


class TestData:
    def test_money_conditions(self) -> None:
        assert not semantics.verdict("calc:real", rich(FLOW, is_rubles=False))
        assert not semantics.verdict("calc:subsistence", rich(RELATIVE, monthly=False))
        assert semantics.verdict("calc:subsistence", rich(RELATIVE))

    def test_years_regions_region_parts(self) -> None:
        assert semantics.verdict("view:compare", rich(FLOW, years=1)).reason == (
            "у показателя один год"
        )
        assert "6" in semantics.verdict("answer:convergence", rich(RELATIVE, years=5)).reason
        assert "20" in semantics.verdict("answer:spread", rich(RELATIVE, regions=12)).reason
        assert not semantics.verdict("answer:mine", rich(RELATIVE), region_known=False)
        assert not semantics.verdict("view:structure", rich(STOCK, parts=1))

    def test_type_only(self) -> None:
        short = rich(RELATIVE, years=1, regions=5)
        assert semantics.verdict("answer:spread", short, data=False)
        assert not semantics.verdict("answer:spread", rich(INDEX, years=1), data=False)


@pytest.mark.parametrize(
    ("name", "unit", "kind", "base"),
    [
        ("Вклады физических лиц в банках на 1 января", "млн руб.", STOCK, PEOPLE),
        ("Выдано ипотечных кредитов", "млн руб.", FLOW, PEOPLE),
        ("Всего зарегистрировано преступлений", "единиц", FLOW, PEOPLE),
        ("Численность работников, выбывших из организаций", "", FLOW, PEOPLE),
        ("Просроченная задолженность по заработной плате", "тыс. рублей", STOCK, PEOPLE),
        ("Объем просроченной кредиторской задолженности", "", STOCK, PEOPLE),
        ("Индексы потребительских цен", "в % к декабрю предыдущего года", INDEX, PEOPLE),
        ("Объем платных услуг", "в % к соответствующему периоду предыдущего года", INDEX, PEOPLE),
        ("Доля убыточных предприятий", "в % к общему числу организаций", RELATIVE, PEOPLE),
        ("Вклады, Россия = 100", "Россия = 100", RELATIVE, PEOPLE),
        ("Вклады, 2020 = 100", "2020 = 100", INDEX, PEOPLE),
        ("Вклады, % к предыдущему году", "% к предыдущему году", INDEX, PEOPLE),
        ("Среднемесячная начисленная заработная плата", "рублей", RELATIVE, PEOPLE),
        ("Ожидаемая продолжительность жизни", "лет", RELATIVE, PEOPLE),
        ("Заболеваемость", "на 100 000 человек", RELATIVE, PEOPLE),
        ("Стоимость фиксированного набора товаров и услуг", "", RELATIVE, PEOPLE),
        ("Количество створов водных объектов с высоким загрязнением", "шт.", STOCK, NONE),
        ("Количество особо охраняемых природных территорий", "шт.", STOCK, NONE),
        ("Количество городов с регулярными наблюдениями", "шт.", STOCK, NONE),
        ("Численность населения", "тыс. человек", STOCK, AREA),
        ("Численность постоянного населения на 1 января", "человек", STOCK, AREA),
        ("Численность населения городов с высоким загрязнением", "тыс. человек", STOCK, PEOPLE),
        ("Протяжённость автомобильных дорог общего пользования", "км", STOCK, AREA),
        ("Валовой региональный продукт", "млн руб.", FLOW, PEOPLE),
        ("Число умерших", "человек", FLOW, PEOPLE),
        ("Накоплено отходов производства и потребления", "тысяч тонн", STOCK, PEOPLE),
        ("Поголовье крупного рогатого скота на конец года", "тыс. голов", STOCK, PEOPLE),
        ("Ввод в действие жилых домов", "тыс. кв. м", FLOW, PEOPLE),
        ("Жилищный фонд", "тыс. кв. м", STOCK, PEOPLE),
        ("Пробы почв с превышением нормативов", "единиц", SUM, PEOPLE),
        ("Транспорт; тыс.рублей", "", SUM, PEOPLE),
        ("Захоронено твердых коммунальных отходов", "тысяч тонн", FLOW, PEOPLE),
        ("Количество несанкционированных свалок", "шт.", STOCK, PEOPLE),
        ("Численность людей, обследованных на антитела к ВИЧ", "человек", FLOW, PEOPLE),
    ],
)
def test_guess(name: str, unit: str, kind: str, base: str) -> None:
    found = semantics.guess(name, unit)
    assert (found.type, found.base) == (kind, base)
    assert found.sure


def test_guess_without_signs_is_not_sure() -> None:
    found = semantics.guess("В том числе: по собственному желанию", "")
    assert not found.sure


@pytest.mark.parametrize(
    ("kind", "name", "unit", "refined"),
    [
        (SUM, "Вклады на 1 января", "млн руб.", STOCK),
        (SUM, "Выдано кредитов", "млн руб.", FLOW),
        (SUM, "Пробы почв с превышением нормативов", "единиц", SUM),
        # Решение человека «сумма» сильнее признаков относительной величины.
        (SUM, "Средняя заработная плата", "руб.", SUM),
        (RELATIVE, "Индекс производства", "в % к предыдущему году", INDEX),
        (RELATIVE, "Доля", "%", RELATIVE),
        (RELATIVE, "Выдано кредитов", "млн руб.", RELATIVE),
        (FLOW, "Вклады на 1 января", "млн руб.", FLOW),
    ],
)
def test_refine(kind: str, name: str, unit: str, refined: str) -> None:
    assert semantics.refine(kind, name, unit) == refined


def test_series_type() -> None:
    assert semantics.series_type(SUM, derived="per100000") == RELATIVE
    assert semantics.series_type(RELATIVE, derived="growth") == INDEX
    assert semantics.series_type(SUM, name="Вклады на 1 января", unit="млн руб.") == STOCK
    assert semantics.series_type(SUM, name="Вклады на 1 января", chosen=SUM) == SUM
    # Выбор человека — у исходного показателя; пересчёт на жителя от суммы — относительная.
    assert semantics.series_type(SUM, derived="per1", chosen=STOCK) == RELATIVE


def test_months_by_type() -> None:
    every = ("sum", "mean", "december", "ytd")
    assert semantics.months_methods(FLOW, every) == ["sum", "ytd"]
    assert semantics.months_methods(STOCK, every) == ["mean", "december"]
    assert semantics.months_methods(SUM, every) == list(every)
    assert semantics.months_methods(RELATIVE, every) == ["mean", "december", "ytd"]
    assert semantics.months_methods(INDEX, every) == ["ytd"]


def test_suggested_per() -> None:
    assert semantics.suggested_per(STOCK, PEOPLE, "Вклады", "млн руб.") == semantics.PER_1
    assert semantics.suggested_per(STOCK, PEOPLE, "Жилищный фонд", "тыс. кв. м") == "per1"
    assert semantics.suggested_per(FLOW, PEOPLE, "Преступления", "единиц") == "per100000"
    assert semantics.suggested_per(STOCK, NONE, "Посты наблюдения", "шт.") == ""
    assert semantics.suggested_per(STOCK, AREA, "Численность населения", "человек") == ""
    assert semantics.suggested_per(RELATIVE, PEOPLE, "Доля", "%") == ""


def test_parts_reason() -> None:
    assert semantics.parts_reason([FLOW, FLOW], ["млн руб.", "млн  руб."]) == ""
    assert semantics.parts_reason([FLOW, SUM], ["т", "т"]) == ""
    assert "индексы" in semantics.parts_reason([FLOW, RELATIVE], ["т", "%"])
    assert "на дату" in semantics.parts_reason([FLOW, STOCK], ["т", "т"])
    assert "единице" in semantics.parts_reason([STOCK, STOCK], ["т", "кг"])
    # Разряд не мешает: сумма приведёт слагаемые к единице первого.
    assert semantics.parts_reason([FLOW, FLOW], ["млн руб.", "тыс. руб."]) == ""
    assert "единице" in semantics.parts_reason([FLOW, FLOW], ["млн руб.", "тыс. человек"])


@pytest.mark.parametrize(
    ("unit", "expected"),
    [
        ("млн руб.", ("руб.", 1e6)),
        ("тыс.  человек", ("человек", 1e3)),
        ("Млрд руб", ("руб", 1e9)),
        ("руб.", ("руб.", 1.0)),
        ("тысяча", ("тысяча", 1.0)),
    ],
)
def test_unit_scale(unit: str, expected: tuple[str, float]) -> None:
    assert semantics.unit_scale(unit) == expected


def test_country_only_indicator() -> None:
    """Показатель только по России: всё недоступно с одной причиной, кроме изменения по России."""
    country = Value(type=STOCK, base=NONE, years=9, regions=0)
    assert country.country_only
    for action in semantics.RULES:
        found = semantics.verdict(action, country)
        if action == "answer:change":
            assert found
        else:
            assert "только значение по России" in found.reason, action
    assert not Value(type=STOCK, years=9, regions=0, is_user=False).country_only
    assert not Value(type=STOCK, years=0, regions=0).country_only
