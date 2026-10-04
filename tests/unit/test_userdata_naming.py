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
