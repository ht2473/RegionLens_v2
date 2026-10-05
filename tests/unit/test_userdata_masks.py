"""Коды-маски вместо чисел: что считается кодом и когда он становится пропуском."""

from __future__ import annotations

import pytest

from apps.userdata import masks

pytestmark = pytest.mark.unit


class TestCandidate:
    @pytest.mark.parametrize("value", [9999.0, 8888.0, 2222.0, -99999999.0, 1111.0])
    def test_repeated_digits(self, value: float) -> None:
        assert masks.is_candidate(value)

    @pytest.mark.parametrize("value", [999.0, 9998.0, 8888.5, 12.0, None, float("nan")])
    def test_other_numbers(self, value: float | None) -> None:
        assert not masks.is_candidate(value)


class TestDetect:
    def test_code_among_percents(self) -> None:
        # «Население в городах с высоким уровнем загрязнения, %» и маска 8888.
        found = masks.detect({"air": [0.0, 12.0, 48.0, 100.0, 8888.0, 8888.0, 75.0]})
        assert found[8888.0].detected
        assert found[8888.0].count == 2
        assert masks.chosen(found, None) == {8888.0}

    def test_plausible_value_is_kept(self) -> None:
        # Расходы в млн рублей: 8888 — обычное число среди тысяч и десятков тысяч.
        found = masks.detect({"spending": [1200.0, 29100.0, 8888.0, 969965.0, 15000.0]})
        assert not found[8888.0].detected
        assert masks.chosen(found, None) == set()

    def test_single_count_among_counts(self) -> None:
        found = masks.detect({"crimes": [1111.0, 17861.0, 5400.0, 674317.0, 980.0]})
        assert not found[1111.0].detected

    def test_indicator_of_codes_only(self) -> None:
        found = masks.detect({"index": [9999.0, 9999.0, 9999.0]})
        assert found[9999.0].detected

    def test_per_indicator(self) -> None:
        # 9999 — маска у процентов и обычное число у численности: решает большинство вхождений.
        found = masks.detect(
            {
                "share": [9999.0] * 5 + [12.0, 30.0, 55.0],
                "people": [9999.0, 25000.0, 40000.0, 120000.0],
            }
        )
        assert found[9999.0].count == 6
        assert found[9999.0].flagged == 5
        assert found[9999.0].detected

    def test_answer_overrides(self) -> None:
        found = masks.detect({"air": [1.0, 2.0, 8888.0, 3333.0]})
        assert masks.chosen(found, []) == set()
        assert masks.chosen(found, ["3333"]) == {3333.0}
