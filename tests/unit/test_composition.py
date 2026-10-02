"""Проверки постоянного состава: вход новых субъектов не должен менять меры сам по себе."""

from __future__ import annotations

import pytest

from apps.analytics.core import composition

pytestmark = pytest.mark.unit

SHARE = 0.8


def panel(rows: dict[int, str]) -> dict[int, dict[str, float]]:
    """Панель из строк вида «AB C»: буква — субъект со значением в этом году."""
    return {year: dict.fromkeys(codes.split(), 1.0) for year, codes in rows.items()}


class TestCompose:
    """Состав окна лет."""

    def test_constant_are_present_every_year(self) -> None:
        """Постоянный состав — субъекты со значением в каждом году."""
        data = panel({2001: "A B C D", 2002: "A B C D E", 2003: "A B C D E"})
        found = composition.compose(data, 2001, 2003, share=SHARE)
        assert found.constant == ("A", "B", "C", "D")
        assert [item.code for item in found.entered] == ["E"]
        assert found.entered[0].first_year == 2002

    def test_left_and_gapped_are_told_apart(self) -> None:
        """Пропавший до конца окна и пропустивший год посередине — разные случаи."""
        data = panel(
            {
                2001: "A B C D E F",
                2002: "A B C D F",
                2003: "A B C D E",
            }
        )
        found = composition.compose(data, 2001, 2003, share=SHARE)
        assert found.constant == ("A", "B", "C", "D")
        assert [(item.code, item.last_year) for item in found.left] == [("F", 2002)]
        assert [(item.code, item.gaps) for item in found.gapped] == [("E", 1)]

    def test_sparse_year_does_not_shrink_the_set(self) -> None:
        """Год, где субъектов меньше порога, в состав не входит и перечисляется."""
        data = panel({2001: "A B C D E", 2002: "A", 2003: "A B C D E"})
        found = composition.compose(data, 2001, 2003, share=SHARE)
        assert found.sparse == (2002,)
        assert found.years == (2001, 2003)
        assert found.constant == ("A", "B", "C", "D", "E")

    def test_window_limits_the_years(self) -> None:
        """Годы вне окна не влияют на состав."""
        data = panel({2001: "A B", 2002: "A B C", 2003: "A B C"})
        found = composition.compose(data, 2002, 2003, share=SHARE)
        assert found.constant == ("A", "B", "C")
        assert not found.changes

    def test_small_constant_set_is_not_usable(self) -> None:
        """Постоянный состав меньше порога для мер не годится."""
        data = panel({2001: "A B C", 2002: "A B C"})
        assert not composition.compose(data, 2001, 2002, share=SHARE).is_usable

    def test_empty_window(self) -> None:
        """Пустое окно — пустой состав, без ошибки."""
        found = composition.compose({}, 2001, 2003, share=SHARE)
        assert found.constant == ()
        assert not found.is_usable


def test_restrict_keeps_constant_members_and_years() -> None:
    """Сужение панели оставляет годы состава и только постоянных субъектов."""
    data = panel({2001: "A B C D E", 2002: "A", 2003: "A B C D E F"})
    found = composition.compose(data, 2001, 2003, share=SHARE)
    restricted = composition.restrict(data, found)
    assert sorted(restricted) == [2001, 2003]
    assert set(restricted[2003]) == {"A", "B", "C", "D", "E"}
