"""Проверки пересчёта денежных рядов в неизменные цены: ошибка — ложный вывод о доходах."""

from __future__ import annotations

import pytest

from apps.catalog.real_terms import real_ratio
from apps.warehouse.queries import RealBasis

pytestmark = pytest.mark.unit

# Зарплата по России за 2019–2024 годы: номинально ×1,861, индексы реальной зарплаты
# и потребительских цен Росстата за 2020–2024 годы.
REAL_WAGE = {2020: 103.8, 2021: 104.5, 2022: 100.3, 2023: 108.2, 2024: 109.7}
CPI = {2020: 104.9, 2021: 108.4, 2022: 111.9, 2023: 107.4, 2024: 109.5}


def test_real_index_is_chained() -> None:
    """Индекс реальной величины перемножается за годы после начального."""
    basis = RealBasis(method="index", index="real")
    found = real_ratio(basis, 1.861, index=REAL_WAGE, total={}, start=2019, end=2024)
    assert found == pytest.approx(1.2913, abs=1e-4)


def test_prices_divide_the_nominal_change() -> None:
    """Индекс цен делит номинальное изменение."""
    basis = RealBasis(method="prices", index="cpi")
    found = real_ratio(basis, 1.861, index=CPI, total={}, start=2019, end=2024)
    assert found == pytest.approx(1.861 / 1.4963, abs=1e-3)


def test_volume_index_is_corrected_for_population() -> None:
    """
    Душевой ряд по индексу физического объёма итога поправлен на численность.

    Итог вырос вдвое при численности −10 %: душевое значение — ×2,222; объём итога
    за год +5 % — значит, душевой реальный рост 1,05 / 0,9.
    """
    basis = RealBasis(method="volume", index="volume", total="total")
    found = real_ratio(
        basis,
        2.0 / 0.9,
        index={2024: 105.0},
        total={2023: 100.0, 2024: 200.0},
        start=2023,
        end=2024,
    )
    assert found == pytest.approx(1.05 / 0.9)


def test_total_series_itself_gets_the_volume_index() -> None:
    """У самого итога реальное изменение — произведение индексов объёма."""
    basis = RealBasis(method="volume", index="volume", total="total")
    found = real_ratio(
        basis,
        2.0,
        index={2023: 110.0, 2024: 105.0},
        total={2022: 50.0, 2024: 100.0},
        start=2022,
        end=2024,
    )
    assert found == pytest.approx(1.1 * 1.05)


def test_missing_year_gives_nothing() -> None:
    """Без индекса хотя бы за один год реального изменения нет."""
    basis = RealBasis(method="index", index="real")
    gaps = {year: value for year, value in REAL_WAGE.items() if year != 2022}
    assert real_ratio(basis, 1.861, index=gaps, total={}, start=2019, end=2024) is None
