"""
Числа карточек лаборатории без базы: мера изменения «кто вырос сильнее» (одна на всех
регионов), подпись со знаком, места регионов с равными значениями; шаблон сохранённой
формулы и вопросы шага «Показатели».
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from apps.userdata import answers, build, indicators
from apps.userdata.formula_views import FREE, template_of

NBSP = chr(0xA0)
MINUS = chr(0x2212)


def _item(percentage: bool) -> SimpleNamespace:
    return SimpleNamespace(is_percentage=percentage)


@pytest.mark.parametrize(
    ("percentage", "pairs", "expected"),
    [
        (True, [(10.0, 12.0)], answers.POINTS),
        (False, [(10.0, 12.0), (5.0, 4.0)], answers.PERCENT),
        # Хоть одно значение не больше нуля — проценты теряют смысл у всех: разность.
        (False, [(10.0, 12.0), (-3.0, 2.0)], answers.DIFFERENCE),
        (False, [(0.0, 2.0)], answers.DIFFERENCE),
    ],
)
def test_change_mode(percentage: bool, pairs: list[tuple[float, float]], expected: str) -> None:
    assert answers.change_mode(_item(percentage), pairs) == expected


def test_shift_and_signed() -> None:
    assert answers.shift_of(answers.PERCENT, 12.0, 10.0) == pytest.approx(20.0)
    assert answers.shift_of(answers.POINTS, 12.5, 10.0) == pytest.approx(2.5)
    assert answers.signed(answers.PERCENT, 20.0, 0) == f"+20,0{NBSP}%"
    assert answers.signed(answers.DIFFERENCE, -150.0, 0) == f"{MINUS}150"
    assert answers.signed(answers.POINTS, 0.04, 1) == "0,0 п. п."


def test_places_share_ties() -> None:
    places = answers._places({"A": 5.0, "B": 7.0, "C": 5.0, "D": 1.0})
    assert places == {"B": 1, "A": 2, "C": 2, "D": 4}


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("{u:abc:def} / {Y477110461:00} * 1000", ("per1000", "u:abc:def", "Y477110461:00")),
        ("{u:abc:def}/{u:abc:xyz}*100", ("share", "u:abc:def", "u:abc:xyz")),
        ("{u:abc:def} - {u:abc:xyz}", ("diff", "u:abc:def", "u:abc:xyz")),
        ("{u:abc:def} / {u:abc:xyz}", ("ratio", "u:abc:def", "u:abc:xyz")),
        ("{u:abc:def} / {u:abc:xyz} * 10", (FREE, "", "")),
        ("RANK({u:abc:def})", (FREE, "", "")),
    ],
)
def test_template_of(expression: str, expected: tuple[str, str, str]) -> None:
    assert template_of(expression) == expected


@pytest.mark.parametrize(
    ("name", "unit", "clear"),
    [
        ("Численность населения", "", True),
        ("Средняя зарплата", "рублей", True),
        ("Выбросы", "тонн", True),
        ("Показатель 7", "", False),
    ],
)
def test_kind_is_clear(name: str, unit: str, clear: bool) -> None:
    assert indicators.kind_is_clear(name, unit) is clear


def test_questions_only_until_answered() -> None:
    item = indicators.Indicator(
        name="Показатель 7", title="Показатель 7", unit="", kind="relative", polarity="", per=()
    )
    item.periods = {f"month:{number}" for number in range(1, 13)}
    assert item.questions == ("kind", "unit", "months")
    assert "kind" not in item.fields and "title" in item.fields
    item.answered = True
    assert item.questions == ()


@pytest.mark.parametrize(
    ("name", "kind", "expected"),
    [
        ("Всего зарегистрировано преступлений", "sum", indicators.DEFAULT_PER),
        ("Численность населения на 1 января", "sum", ()),
        ("Численность постоянного населения", "sum", ()),
        ("regions population", "sum", ()),
        ("Средняя зарплата", "relative", ()),
    ],
)
def test_default_per_skips_population(name: str, kind: str, expected: tuple[str, ...]) -> None:
    assert indicators.default_per(name, kind) == expected


@pytest.mark.parametrize(
    ("name", "unit", "expected"),
    [
        ("Валовой региональный продукт", "млрд руб.", indicators.MONEY_PER),
        ("Экспорт", "млн долл. США", indicators.MONEY_PER),
        ("Расходы бюджета, млн рублей", "", indicators.MONEY_PER),
        ("Выбросы загрязняющих веществ", "тыс. тонн", indicators.DEFAULT_PER),
    ],
)
def test_money_sum_is_divided_per_person(name: str, unit: str, expected: tuple[str, ...]) -> None:
    """Денежную сумму естественно делить на жителя, остальные — на 100 000 жителей."""
    assert indicators.default_per(name, indicators.SUM, unit) == expected


@pytest.mark.parametrize(
    ("unit", "middle", "factor", "expected"),
    [
        ("млрд руб.", 0.000_333, 1e6, "тыс. руб. на жителя"),
        ("млн рублей", 0.5, 1e3, "тыс. рублей на жителя"),
        ("тыс. руб.", 0.2, 1e3, "руб. на жителя"),
        ("млн долл. США", 0.002, 1e3, "тыс. долл. США на жителя"),
        ("рублей", 50_000, 1e-3, "тыс. рублей на жителя"),
        ("трлн руб.", 0.000_008, 1e6, "млн руб. на жителя"),
        ("", None, 1, "на жителя"),
    ],
)
def test_per_person_order(unit: str, middle: float | None, factor: float, expected: str) -> None:
    """Разряд единицы на жителя — тот, при котором середина значений не меньше единицы."""
    found_factor, found_unit = build.per_person(unit, middle)
    assert found_factor == pytest.approx(factor)
    assert found_unit == expected
