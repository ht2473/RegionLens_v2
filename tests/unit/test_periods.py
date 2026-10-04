"""Периоды в подписях таблиц: общие правила сбора и своих данных."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from apps.sources.periods import (
    ANNUAL,
    HALF,
    MONTH,
    POINT,
    QUARTER,
    WINDOW,
    YTD,
    Period,
    Stamp,
    period_of,
    stamp,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (2014, Stamp(2014, ANNUAL)),
        (2014.0, Stamp(2014, ANNUAL)),
        ("2014", Stamp(2014, ANNUAL)),
        ("2014 г.", Stamp(2014, ANNUAL)),
        ("2014 год", Stamp(2014, ANNUAL)),
        ("year2014", Stamp(2014, ANNUAL)),
        ("2014*", Stamp(2014, ANNUAL)),
        ("20163)", Stamp(2016, ANNUAL, (3,))),
        ("2025 год1", Stamp(2025, ANNUAL, (1,))),
        ("январь 2024", Stamp(2024, Period(MONTH, 1))),
        ("Март 2024 г.", Stamp(2024, Period(MONTH, 3))),
        ("янв.24", Stamp(2024, Period(MONTH, 1))),
        ("2024-03", Stamp(2024, Period(MONTH, 3))),
        ("03.2024", Stamp(2024, Period(MONTH, 3))),
        ("I квартал 2024", Stamp(2024, Period(QUARTER, 1))),
        ("1 кв. 2024", Stamp(2024, Period(QUARTER, 1))),
        ("2024 Q1", Stamp(2024, Period(QUARTER, 1))),
        ("Q3 2024", Stamp(2024, Period(QUARTER, 3))),
        ("I полугодие 2024", Stamp(2024, Period(YTD, 6))),
        ("II полугодие", Stamp(None, Period(HALF, 2))),
        ("9 месяцев 2024", Stamp(2024, Period(YTD, 9))),
        ("январь–июль 2026", Stamp(2026, Period(YTD, 7))),
        ("январь-декабрь 2025", Stamp(2025, ANNUAL)),
        ("май 2026 г. – июль 2026 г.", Stamp(2026, Period(WINDOW, 7, 3))),
        ("ноябрь 2025 г. - январь 2026 г.", Stamp(2026, Period(WINDOW, 1, 3))),
        ("апрель-июнь 2025", Stamp(2025, Period(QUARTER, 2))),
        ("на 1 января 2024 г.", Stamp(2024, Period(POINT, 1))),
        ("на конец 2023 года", Stamp(2024, Period(POINT, 1))),
        ("01.01.2024", Stamp(2024, Period(POINT, 1))),
        ("2024-07-01", Stamp(2024, Period(POINT, 7))),
        (date(2024, 1, 1), Stamp(2024, Period(POINT, 1))),
        (datetime(2023, 12, 31), Stamp(2024, Period(POINT, 1))),
    ],
)
def test_stamps(value: object, expected: Stamp) -> None:
    assert stamp(value) == expected


@pytest.mark.parametrize(
    ("label", "period"),
    [
        ("январь", Period(MONTH, 1)),
        ("май", Period(MONTH, 5)),
        ("Март", Period(MONTH, 3)),
        ("I квартал", Period(QUARTER, 1)),
        ("IV квартал3", None),
        ("январь-\nфевраль ", Period(YTD, 2)),
        ("январь-июль", Period(YTD, 7)),
        ("год", ANNUAL),
        ("январь-декабрь", ANNUAL),
        ("I полугодие", Period(YTD, 6)),
    ],
)
def test_periods_without_year(label: str, period: Period | None) -> None:
    assert period_of(label) == period


@pytest.mark.parametrize(
    "value",
    ["Всего", "", None, True, 12.5, 3000, "в % к соответствующему периоду", "2,5", "Москва"],
)
def test_not_periods(value: object) -> None:
    assert stamp(value) is None


def test_period_key_round_trip() -> None:
    for period in (ANNUAL, Period(MONTH, 3), Period(WINDOW, 7, 3)):
        assert Period.from_key(period.key) == period
