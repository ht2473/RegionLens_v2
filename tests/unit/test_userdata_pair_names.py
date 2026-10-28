"""Подписи двух показателей на графиках сравнения и связи."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from apps.userdata.answers import _pair_names

pytestmark = pytest.mark.unit


def _state(key: str) -> SimpleNamespace:
    return SimpleNamespace(series=SimpleNamespace(key=key))


def _subject(title: str) -> SimpleNamespace:
    return SimpleNamespace(title=title)


def test_different_titles_stay_as_is() -> None:
    names = _pair_names(_state("u:abc:1"), _state("Y1:00"), _subject("Вклады"), _subject("Доходы"))
    assert names == ["Вклады", "Доходы"]


def test_same_titles_name_their_source() -> None:
    """Своя численность и численность Росстата — одной строкой в легенде не различить."""
    title = "Среднегодовая численность населения"
    names = _pair_names(_state("u:abc:1"), _state("Y1:00"), _subject(title), _subject(title))
    assert names == [f"{title} — своя таблица", f"{title} — данные сайта"]


def test_same_titles_of_one_kind_are_numbered() -> None:
    title = "Численность"
    names = _pair_names(_state("u:a:1"), _state("u:b:2"), _subject(title), _subject(title))
    assert names == [f"{title} (1)", f"{title} (2)"]
