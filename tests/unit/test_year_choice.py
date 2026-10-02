"""Проверки года по умолчанию: неполный последний год не должен открывать страницы."""

from __future__ import annotations

import pytest

from apps.catalog.selectors import default_year, resolve_year
from apps.warehouse.queries import covered_years

pytestmark = pytest.mark.unit


def test_incomplete_last_year_is_not_full() -> None:
    """Год с тремя субъектами из 85 полным не считается."""
    counts = {2022: 85, 2023: 84, 2024: 3}
    assert covered_years(counts) == [2022, 2023]
    assert default_year(counts) == 2023


def test_share_is_taken_from_the_fullest_year() -> None:
    """Порог — доля от наибольшего числа субъектов самого ряда, а не от 85."""
    counts = {2020: 40, 2021: 33, 2022: 31}
    assert covered_years(counts) == [2020, 2021]


def test_default_applies_only_without_a_request() -> None:
    """Год из адреса главнее года по умолчанию, в том числе неполный."""
    years = [2022, 2023, 2024]
    assert resolve_year(None, years, default=2023) == 2023
    assert resolve_year("2024", years, default=2023) == 2024
    assert resolve_year(None, years, default=None) == 2024


def test_empty_counts() -> None:
    """Ряд без значений — ни одного полного года."""
    assert covered_years({}) == []
    assert default_year({}) is None
