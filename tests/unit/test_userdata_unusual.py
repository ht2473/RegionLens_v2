"""Необычные значения: кто заметно выделяется и резкие скачки между годами."""

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


def test_typo_is_a_jump() -> None:
    before = _even()
    after = {code: value * 1.05 for code, value in before.items()}
    after["R03"] = before["R03"] * 100
    found = unusual.jumps(before, after, (2019, 2020))
    assert [jump.code for jump in found] == ["R03"]
    assert found[0].ratio == pytest.approx(100)


def test_common_shift_is_not_a_jump() -> None:
    # Все регионы выросли втрое (смена цен, единицы у всех) — не скачок одного региона.
    before = _even()
    after = {code: value * 3 for code, value in before.items()}
    assert unusual.jumps(before, after, (2019, 2020)) == []


def test_small_change_is_not_a_jump() -> None:
    before = _even()
    after = dict(before)
    after["R03"] = before["R03"] * 1.6
    assert unusual.jumps(before, after, (2019, 2020)) == []


@pytest.mark.parametrize(
    ("ratio", "text"),
    [
        (12.0, "в 12 раз"),
        (3.0, "в 3,0 раза"),
        (2.5, "в 2,5 раза"),
        (21.0, "в 21 раз"),
        (22.0, "в 22 раза"),
        (13.0, "в 13 раз"),
        (100.0, "в 100 раз"),
    ],
)
def test_times_wording(ratio: float, text: str) -> None:
    assert unusual.times(ratio) == text
