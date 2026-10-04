"""Числа и пропуски в ячейках таблиц пользователей: случаи из корпуса."""

from __future__ import annotations

import pytest

from apps.userdata import cells
from apps.userdata.cells import EMPTY, HIDDEN, MISSING, TEXT, VALUE, NumberStyle

pytestmark = pytest.mark.unit

NBSP = chr(0xA0)
NARROW = chr(0x202F)
MINUS = chr(0x2212)
COMMA = NumberStyle(decimal=",", decimals=1)
DOT = NumberStyle(decimal=".", grouping=",")


@pytest.mark.parametrize(
    ("raw", "value"),
    [
        ("12,5", 12.5),
        ("1 234,5", 1234.5),
        (f"1{NBSP}234,5", 1234.5),
        (f"1{NARROW}234,5", 1234.5),
        (f"13{NBSP}545", 13545.0),
        (f"{MINUS}6,4", -6.4),
        ("-0,1", -0.1),
        ("12,5%", 12.5),
        ("1,2E+05", 120000.0),
        (44940.0, 44940.0),
        (2014, 2014.0),
        ("1.234,5", 1234.5),
    ],
)
def test_numbers_with_decimal_comma(raw: object, value: float) -> None:
    cell = cells.parse(raw, COMMA)
    assert (cell.status, cell.value) == (VALUE, value)


@pytest.mark.parametrize(("raw", "value"), [("1,234.5", 1234.5), ("12.5", 12.5), ("1234", 1234.0)])
def test_numbers_with_decimal_point(raw: str, value: float) -> None:
    assert cells.parse(raw, DOT).value == value


@pytest.mark.parametrize(
    ("raw", "value", "notes"),
    [
        ("12,5 1)", 12.5, (1,)),
        ("12,5¹", 12.5, (1,)),
        ("12,5*", 12.5, ()),
        ("106,52)", 106.5, (2,)),
        ("12,5 2); 3)", 12.5, (2, 3)),
    ],
)
def test_footnote_after_number(raw: str, value: float, notes: tuple[int, ...]) -> None:
    cell = cells.parse(raw, COMMA)
    assert (cell.status, cell.value, cell.notes) == (VALUE, value, notes)


@pytest.mark.parametrize(
    ("raw", "notes"),
    [
        ("…", ()),
        ("...", ()),
        ("-", ()),
        ("–", ()),
        ("—", ()),
        (MINUS, ()),
        ("x", ()),
        ("х", ()),
        ("н/д", ()),
        ("нет данных", ()),
        ("…1)", (1,)),
        ("...2)", (2,)),
        ("… 4)", (4,)),
        ("-1)", (1,)),
    ],
)
def test_missing_marks(raw: str, notes: tuple[int, ...]) -> None:
    cell = cells.parse(raw, COMMA)
    assert (cell.status, cell.value, cell.notes) == (MISSING, None, notes)


def test_empty_and_text() -> None:
    assert cells.parse("", COMMA).status == EMPTY
    assert cells.parse(None, COMMA).status == EMPTY
    assert cells.parse("в 2,0 р.", COMMA).status == TEXT


def test_ebt_codes_only_when_the_column_has_them() -> None:
    style, parsed = cells.parse_column(["12,5", "-99999999", "-77777777"])
    assert style.ebt_codes
    assert [cell.status for cell in parsed] == [VALUE, MISSING, HIDDEN]
    plain = cells.guess_style(["12,5", "13,1"])
    assert cells.parse(-99999999.0, plain).status == VALUE


@pytest.mark.parametrize(
    ("column", "decimal"),
    [
        (["1,5", "2,25", "3"], ","),
        (["1.5", "2.25", "3"], "."),
        (["1,234.5", "2,000.0"], "."),
        (["1.234,5", "2.000,0"], ","),
    ],
)
def test_style_by_whole_column(column: list[str], decimal: str) -> None:
    assert cells.guess_style(column).decimal == decimal


def test_ambiguous_thousands_follow_delimiter() -> None:
    assert cells.guess_style(["1,234", "5,678"], delimiter=";").decimal == ","
    assert cells.guess_style(["1,234", "5,678"], delimiter=",").decimal == "."


def test_numeric_share() -> None:
    parsed = [cells.parse(raw, COMMA) for raw in ("1,5", "…", "", "текст")]
    assert cells.numeric_share(parsed) == pytest.approx(2 / 3)
