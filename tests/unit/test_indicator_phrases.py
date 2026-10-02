"""
Проверки выводов страницы показателя: кратность, пункты у процентов, молчание у величин
через ноль, одна точность на ряд вне основного набора.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from django.utils import translation

from apps.catalog.indicator import _gap, _precision
from apps.catalog.passport import times_phrase
from apps.core.showcase import change_between
from apps.warehouse.queries.common import FeaturedSeries

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def russian() -> Iterator[None]:
    """Выводы проверяются на русском — языке, на котором они написаны."""
    with translation.override("ru"):
        yield


def series(**overrides: Any) -> FeaturedSeries:
    """Показатель с разумными значениями по умолчанию."""
    values: dict[str, Any] = {
        "key": "Y000:00",
        "short_title_ru": "Средняя зарплата",
        "short_title_en": "Average wage",
        "unit_label_ru": "руб. в месяц",
        "unit_label_en": "RUB per month",
        "polarity": "positive",
        "theme": "income",
        "precision": 0,
        "order": 1,
    }
    values.update(overrides)
    return FeaturedSeries(**values)


class TestTimesPhrase:
    """Кратность словами: форма «раз» или «раза» и округление."""

    @pytest.mark.parametrize(
        ("ratio", "expected"),
        [
            (4.6, "в 4,6 раза"),
            (2.0, "в 2 раза"),
            (3.04, "в 3 раза"),
            (5.0, "в 5 раз"),
            (12.3, "в 12 раз"),
            (22.0, "в 22 раза"),
        ],
    )
    def test_forms(self, ratio: float, expected: str) -> None:
        """Дробное число — «раза»; целое — по последней цифре, 12–14 — «раз»."""
        assert times_phrase(ratio) == expected


class TestGap:
    """Разница между крайними регионами."""

    def test_ratio_for_positive_values(self) -> None:
        """У положительных величин — кратность: «в 4,6 раза»."""
        assert _gap(series(), 188_561, 40_588) == "в 4,6 раза"

    def test_points_for_percentages(self) -> None:
        """У процентов — разность в пунктах: «в 150 раз» у безработицы читалось бы как ошибка."""
        item = series(unit_label_ru="%", unit_label_en="%", precision=1)
        assert _gap(item, 30.2, 0.2) == "30,0 п. п."

    def test_nothing_when_values_cross_zero(self) -> None:
        """Естественный прирост проходит через ноль: отношение к отрицательному бессмысленно."""
        item = series(unit_label_ru="на 1000 чел.", precision=1)
        assert _gap(item, 4.1, -9.8) == ""

    def test_nothing_when_ratio_is_close_to_one(self) -> None:
        """«В 1,0 раза» ничего не добавляет к двум числам, стоящим рядом."""
        assert _gap(series(), 105, 100) == ""


class TestChangeBetween:
    """Изменение к прошлому году на плитках страны: главная, каталог, страница показателя."""

    def test_share_for_positive_values(self) -> None:
        """У положительных величин — процент к прежнему значению."""
        assert change_between(series(), 89_069, 74_854) == ("+19,0 %", 1)

    def test_points_for_percentages(self) -> None:
        """У процентов — пункты: «−21,9 %» у безработицы с 3,2 до 2,5 читается как ошибка."""
        item = series(unit_label_ru="%", unit_label_en="%", precision=1)
        assert change_between(item, 2.5, 3.2) == ("−0,7 п. п.", -1)

    def test_difference_for_negative_values(self) -> None:
        """Естественный прирост с −4,0 до −3,5 — «+0,5», а не «+12,5 %»."""
        item = series(unit_label_ru="на 1000 жителей", precision=1)
        assert change_between(item, -3.5, -4.0) == ("+0,5", 1)

    def test_difference_when_values_cross_zero(self) -> None:
        """Через ноль доля бессмысленна: с −1,2 до 0,8 — разность, а не «+166,7 %»."""
        item = series(unit_label_ru="на 1000 жителей", precision=1)
        assert change_between(item, 0.8, -1.2) == ("+2,0", 1)


class TestPrecision:
    """Одна точность на весь ряд — по порядку его середины."""

    @pytest.mark.parametrize(
        ("reference", "expected"),
        [(67_541.0, 0), (1_000.0, 0), (45.3, 1), (10.0, 1), (1.45, 2), (-3.2, 2), (None, 1)],
    )
    def test_by_magnitude(self, reference: float | None, expected: int) -> None:
        """Тысячи — целыми, десятки — с одним знаком, единицы — с двумя."""
        assert _precision(reference) == expected
