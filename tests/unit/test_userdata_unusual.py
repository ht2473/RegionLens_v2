"""Необычные значения: кто заметно выделяется среди регионов."""

from __future__ import annotations

import pytest

from apps.userdata import unusual

pytestmark = pytest.mark.unit

CODES = [f"R{number:02d}" for number in range(30)]


def _even(base: float = 100.0) -> dict[str, float]:
    return {code: base + number for number, code in enumerate(CODES)}


def test_far_value_stands_out() -> None:
    values = _even()
    values["R05"] = 1000.0
    values["R06"] = 400.0
    assert unusual.standing_out(values) == ["R05", "R06"]


def test_few_regions_or_flat_values_give_nothing() -> None:
    assert unusual.standing_out(dict.fromkeys(CODES, 5.0)) == []
    assert unusual.standing_out({"R01": 1.0, "R02": 900.0}) == []
