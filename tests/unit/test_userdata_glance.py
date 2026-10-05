"""Вид величины по данным в «первом взгляде»: сумма, доля или среднее, сдвиг единиц."""

from __future__ import annotations

import pytest

from apps.userdata.glance import KIND_RELATIVE, KIND_SUM, country_scale, data_kind

REGIONS = [float(value) for value in range(10, 95)]
TOTAL = sum(REGIONS)


@pytest.mark.parametrize(
    ("subjects", "country", "values", "expected"),
    [
        (TOTAL, TOTAL, REGIONS, KIND_SUM),
        (TOTAL, TOTAL * 1.015, REGIONS, KIND_SUM),
        # Часть итога страны не распределена по регионам (преступления на транспорте).
        (TOTAL, TOTAL / 0.96, REGIONS, KIND_SUM),
        (TOTAL, 50.0, REGIONS, KIND_RELATIVE),
        # Итог много больше суммы и больше любого субъекта — вывода нет.
        (TOTAL, TOTAL * 2, REGIONS, ""),
        # Россия в млрд руб., регионы в млн, часть оборота не распределена: не доля.
        (TOTAL, TOTAL / 730, REGIONS, ""),
        # Отрицательные значения и мало субъектов — вывода нет.
        (TOTAL, TOTAL, [-1.0, *REGIONS], ""),
        (30.0, 30.0, [10.0, 20.0], ""),
    ],
)
def test_data_kind(subjects: float, country: float, values: list[float], expected: str) -> None:
    assert data_kind(subjects, country, values) == expected


def test_country_in_larger_units() -> None:
    # Регионы — в млн руб., Россия — в млрд руб.
    assert country_scale(TOTAL, TOTAL / 1000, REGIONS) == 1000
    assert country_scale(TOTAL, TOTAL / 1_000_000, REGIONS) == 1_000_000
    assert country_scale(TOTAL, TOTAL, REGIONS) == 1
    # Индексы около 100: сумма 85 индексов в сто раз больше значения России — совпадение.
    indices = [100.0 + index / 10 for index in range(85)]
    assert country_scale(sum(indices), sum(indices) / 100, indices) == 1
