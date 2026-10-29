"""Подписи рядов своих данных: период года словами и устойчивый код ряда."""

from __future__ import annotations

import pytest
from django.utils import translation

from apps.exports.renderers.safety import safe_cell, safe_text
from apps.userdata import naming


@pytest.mark.parametrize(
    ("key", "russian", "english"),
    [
        ("year:12", "", ""),
        ("month:7", "июль", "July"),
        ("quarter:2", "II квартал", "Q2"),
        ("ytd:7", "январь–июль", "January–July"),
        ("window:7:3", "май–июль", "May–July"),
        ("point:1", "на 1 января", "as of 1 January"),
        ("half:2", "II полугодие", "H2"),
    ],
)
def test_period_text(key: str, russian: str, english: str) -> None:
    with translation.override("ru"):
        assert naming.period_text(key) == russian
    with translation.override("en"):
        assert naming.period_text(key) == english


def test_full_title() -> None:
    with translation.override("ru"):
        slices = [["Пол", "Мужчины"], ["Возраст", "15–19 лет"]]
        assert naming.full_title("Число умерших", slices, "ytd:6") == (
            "Число умерших — Мужчины, 15–19 лет, январь–июнь"
        )
        assert naming.full_title("Число умерших", [], "year:12") == "Число умерших"


def test_series_code_is_stable() -> None:
    code = naming.series_code("Число умерших", [("Пол", "Мужчины")], "year:12")
    assert code == naming.series_code("  число  умерших ", [("пол", "мужчины")], "year:12")
    assert code != naming.series_code("Число умерших", [("Пол", "Женщины")], "year:12")
    assert code != naming.series_code("Число умерших", [("Пол", "Мужчины")], "ytd:6")
    assert len(code) == 12


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ('=HYPERLINK("http://x")', '\'=HYPERLINK("http://x")'),
        ("+7", "'+7"),
        ("-cmd", "'-cmd"),
        ("@SUM(A1)", "'@SUM(A1)"),
        ("\tx", "'\tx"),
        ("Москва", "Москва"),
    ],
)
def test_formula_cells_are_neutralised(value: str, expected: str) -> None:
    assert safe_text(value) == expected


def test_numbers_stay_numbers() -> None:
    assert safe_cell(-5.0) == -5.0
    assert safe_cell(None) is None


@pytest.mark.parametrize(
    ("name", "unit", "title", "found_unit"),
    [
        (
            "Сальдированный финансовый результат деятельности организаций, млрд руб.",
            "",
            "Сальдированный финансовый результат деятельности организаций",
            "млрд руб.",
        ),
        (
            "Население в городах с высоким уровнем загрязнения воздуха, % городского населения",
            "процент",
            "Население в городах с высоким уровнем загрязнения воздуха",
            "% городского населения",
        ),
        (
            "Ввод в действие жилых домов, тыс. м2 общей площади жилых помещений",
            "",
            "Ввод в действие жилых домов",
            "тыс. м2 общей площади жилых помещений",
        ),
        (
            "Величина прожиточного минимума пенсионера в субъекте Российской Федерации",
            "руб.",
            "Величина прожиточного минимума пенсионера",
            "руб.",
        ),
        (
            "Итоги внешней торговли субъектов Российской Федерации за 2020-2021 годы",
            "",
            "Итоги внешней торговли",
            "",
        ),
        ("Регион: решения на 1 января 2007 года", "", "Регион: решения", ""),
        # Единица в столбце другая — хвост названия не снимается.
        ("Выпуск продукции, млрд руб.", "тыс. руб.", "Выпуск продукции, млрд руб.", "тыс. руб."),
        # Скобки различают показатели — остаются.
        (
            "Количество особо охраняемых природных территорий (региональных)",
            "шт.",
            "Количество особо охраняемых природных территорий (региональных)",
            "шт.",
        ),
        ("Выбросы в 1000 случаях", "", "Выбросы в 1000 случаях", ""),
    ],
)
def test_short_title(name: str, unit: str, title: str, found_unit: str) -> None:
    """Короткое название — без единицы в хвосте, годов и «субъектов Российской Федерации»."""
    assert naming.short_title(name, unit) == (title, found_unit)


class TestScaledUnit:
    """Масштаб показателя сдвигает разряд единицы; за разрядами — множитель."""

    @pytest.mark.parametrize(
        ("unit", "exponent", "expected"),
        [
            ("тысяч тонн", -1, "млн тонн"),
            ("тыс. руб.", -1, "млн руб."),
            ("млн рублей", 1, "тыс. рублей"),
            ("человек", -1, "тыс. человек"),
            ("тыс. руб.", 0, "тыс. руб."),
            ("руб.", 1, "руб. × 0,001"),
            ("трлн руб.", -1, "трлн руб. × 1 000"),
        ],
    )
    def test_order_moves(self, unit: str, exponent: int, expected: str) -> None:
        assert naming.scaled_unit(unit, exponent) == expected


class TestGroupTotal:
    """Итог части регионов: сумма у сумм, среднее, взвешенное численностью, у долей."""

    def test_sum_and_weighted_mean(self) -> None:
        from apps.userdata.groups import _total

        values = {"A": 10.0, "B": 30.0}
        people = {"A": {2020: 100.0}, "B": {2019: 300.0}}
        assert _total(values, people, 2020, is_sum=True) == 40.0
        # У B численность 2020 года нет — берётся последняя известная.
        assert _total(values, people, 2020, is_sum=False) == pytest.approx(25.0)
        assert _total({}, people, 2020, is_sum=True) is None
        assert _total({"C": 5.0}, people, 2020, is_sum=False) is None
