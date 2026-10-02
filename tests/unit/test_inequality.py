"""
Проверки мер неравенства на распределениях с известным ответом; неприменимая мера —
``None`` с объяснением, а не ноль.
"""

from __future__ import annotations

import numpy as np
import pytest

from apps.analytics.core import inequality

pytestmark = pytest.mark.unit

# Равномерное распределение: все территории имеют одинаковое значение.
UNIFORM = [100.0] * 20

# Ряд с выраженной правой асимметрией — типичная форма региональных показателей.
SKEWED = [1.0, 2.0, 3.0, 4.0, 5.0, 8.0, 12.0, 20.0, 40.0, 120.0]


def prepared(values: list[float], weights: list[float] | None = None) -> tuple:
    """Подготовить массивы значений и весов так же, как это делает расчёт."""
    return inequality.prepare(values, weights)


class TestGini:
    """Коэффициент Джини."""

    def test_uniform_distribution_gives_zero(self) -> None:
        """При равном распределении коэффициент равен нулю."""
        values, weights = prepared(UNIFORM)
        assert inequality.gini(values, weights) == pytest.approx(0.0, abs=1e-12)

    def test_two_values_give_known_result(self) -> None:
        """Для пары «ноль и единица» коэффициент равен половине."""
        values, weights = prepared([0.0, 1.0])
        assert inequality.gini(values, weights) == pytest.approx(0.5)

    def test_concentration_approaches_one(self) -> None:
        """Сосредоточение величины в одной территории даёт значение, близкое к единице."""
        values, weights = prepared([0.0] * 99 + [1000.0])
        result = inequality.gini(values, weights)
        assert result is not None
        assert result > 0.98

    def test_scale_invariance(self) -> None:
        """Коэффициент не меняется при умножении всех значений на постоянную."""
        values, weights = prepared(SKEWED)
        scaled_values, scaled_weights = prepared([value * 1000 for value in SKEWED])
        assert inequality.gini(values, weights) == pytest.approx(
            inequality.gini(scaled_values, scaled_weights)
        )

    def test_negative_values_are_rejected(self) -> None:
        """Для показателя с отрицательными значениями коэффициент не определён."""
        values, weights = prepared([-5.0, 1.0, 2.0, 3.0, 4.0])
        assert inequality.gini(values, weights) is None

    def test_weights_change_result(self) -> None:
        """Взвешивание меняет результат: единица наблюдения становится другой."""
        values, weights = prepared([1.0, 10.0], [1.0, 1.0])
        weighted_values, heavy = prepared([1.0, 10.0], [99.0, 1.0])
        assert inequality.gini(values, weights) != pytest.approx(
            inequality.gini(weighted_values, heavy)
        )


class TestTheil:
    """Индекс Тейла и его разложение."""

    def test_uniform_distribution_gives_zero(self) -> None:
        """При равном распределении индекс равен нулю."""
        values, weights = prepared(UNIFORM)
        assert inequality.theil(values, weights) == pytest.approx(0.0, abs=1e-12)

    def test_zero_values_are_rejected(self) -> None:
        """Нулевое значение выводит показатель из области определения индекса."""
        values, weights = prepared([0.0, 1.0, 2.0, 3.0, 4.0])
        assert inequality.theil(values, weights) is None

    def test_decomposition_sums_to_total(self) -> None:
        """Составляющие «между группами» и «внутри групп» в сумме дают целое."""
        values = [10.0, 12.0, 11.0, 50.0, 55.0, 60.0, 30.0, 28.0, 32.0]
        groups = ["A", "A", "A", "B", "B", "B", "C", "C", "C"]
        array, weight_array = prepared(values)

        result = inequality.theil_decomposition(array, weight_array, groups)
        assert result is not None
        assert result.between + result.within == pytest.approx(result.total)

    def test_identical_groups_give_no_between_component(self) -> None:
        """Если средние групп совпадают, межгрупповая составляющая обращается в ноль."""
        values = [10.0, 20.0, 10.0, 20.0]
        groups = ["A", "A", "B", "B"]
        array, weight_array = prepared(values)

        result = inequality.theil_decomposition(array, weight_array, groups)
        assert result is not None
        assert result.between == pytest.approx(0.0, abs=1e-12)
        assert result.within_share == pytest.approx(1.0)

    def test_group_shares_are_reported(self) -> None:
        """Разложение возвращает состав каждой группы."""
        values = [10.0, 12.0, 50.0, 55.0]
        groups = ["A", "A", "B", "B"]
        array, weight_array = prepared(values)

        result = inequality.theil_decomposition(array, weight_array, groups)
        assert result is not None
        assert {group["code"] for group in result.groups} == {"A", "B"}
        assert sum(group["size"] for group in result.groups) == len(values)


class TestAtkinson:
    """Индекс Аткинсона."""

    def test_uniform_distribution_gives_zero(self) -> None:
        """При равном распределении индекс равен нулю."""
        values, weights = prepared(UNIFORM)
        assert inequality.atkinson(values, weights) == pytest.approx(0.0, abs=1e-12)

    def test_grows_with_aversion_parameter(self) -> None:
        """Чем выше неприятие неравенства, тем больше значение индекса."""
        values, weights = prepared(SKEWED)
        mild = inequality.atkinson(values, weights, epsilon=0.2)
        strong = inequality.atkinson(values, weights, epsilon=1.5)
        assert strong > mild

    def test_unit_parameter_uses_geometric_mean(self) -> None:
        """При единичном параметре индекс определён через среднее геометрическое."""
        values, weights = prepared([1.0, 4.0])
        expected = 1 - (1 * 4) ** 0.5 / 2.5
        assert inequality.atkinson(values, weights, epsilon=1.0) == pytest.approx(expected)


class TestLorenzCurve:
    """Кривая Лоренца."""

    def test_starts_at_origin_and_ends_at_unit(self) -> None:
        """Кривая начинается в начале координат и заканчивается в единице."""
        values, weights = prepared(SKEWED)
        points = inequality.lorenz_curve(values, weights)
        assert points[0] == {"population": 0.0, "value": 0.0}
        assert points[-1] == {"population": 1.0, "value": 1.0}

    def test_is_monotonic(self) -> None:
        """Накопленные доли не убывают."""
        values, weights = prepared(SKEWED)
        points = inequality.lorenz_curve(values, weights)
        shares = [point["value"] for point in points]
        assert shares == sorted(shares)

    def test_uniform_distribution_lies_on_diagonal(self) -> None:
        """При равном распределении кривая совпадает с линией равенства."""
        values, weights = prepared(UNIFORM)
        for point in inequality.lorenz_curve(values, weights):
            assert point["value"] == pytest.approx(point["population"])


class TestMeasureSet:
    """Сводный расчёт набора мер."""

    def test_short_series_is_rejected(self) -> None:
        """По трём территориям меры неравенства не рассчитываются."""
        result = inequality.measure_set([1.0, 2.0, 3.0])
        assert result["available"] is False

    def test_all_measures_are_returned(self) -> None:
        """Возвращаются все меры набора, включая нерассчитанные."""
        result = inequality.measure_set(SKEWED)
        assert {measure.code for measure in result["measures"]} == {
            "cv",
            "gini",
            "theil",
            "atkinson",
            "decile",
            "range",
        }

    def test_unavailable_measure_carries_explanation(self) -> None:
        """Неприменимая мера возвращает пустое значение и причину, а не ноль."""
        result = inequality.measure_set([-1.0, 0.0, 5.0, 10.0, 20.0, 30.0])
        theil = result["by_code"]["theil"]
        assert theil.value is None
        assert theil.note

    def test_missing_values_are_dropped(self) -> None:
        """Территории без значения исключаются из расчёта."""
        result = inequality.measure_set([*SKEWED, None, None])
        assert result["count"] == len(SKEWED)


class TestPrepare:
    """Отбор пригодных пар «значение — вес»."""

    def test_missing_weight_removes_observation(self) -> None:
        """Территория без веса выпадает из расчёта целиком."""
        values, weights = inequality.prepare([1.0, 2.0, 3.0], [1.0, None, 1.0])
        assert list(values) == [1.0, 3.0]
        assert list(weights) == [1.0, 1.0]

    def test_zero_weight_removes_observation(self) -> None:
        """Нулевой вес равносилен отсутствию наблюдения."""
        values, _ = inequality.prepare([1.0, 2.0], [0.0, 1.0])
        assert list(values) == [2.0]

    def test_infinite_value_is_dropped(self) -> None:
        """Бесконечное значение в расчёт не попадает."""
        values, _ = inequality.prepare([float("inf"), 2.0])
        assert list(values) == [2.0]


class TestWeightedMean:
    """Взвешенное среднее."""

    def test_matches_numpy_average(self) -> None:
        """Результат совпадает с эталонным расчётом."""
        values = np.array([1.0, 2.0, 3.0])
        weights = np.array([1.0, 2.0, 7.0])
        assert inequality.weighted_mean(values, weights) == pytest.approx(
            float(np.average(values, weights=weights))
        )


class TestHiddenBounds:
    """Границы мер при скрытых значениях в пределах наблюдаемого размаха."""

    def test_no_hidden_values_no_bounds(self) -> None:
        """Без скрытых значений границ нет."""
        assert inequality.hidden_bounds(SKEWED, 0) == {}

    def test_bounds_cover_every_admissible_filling(self) -> None:
        """Любая подстановка скрытых значений в наблюдаемый размах даёт меру внутри границ."""
        found = inequality.hidden_bounds(SKEWED, 3)
        low, high = min(SKEWED), max(SKEWED)
        rng = np.random.default_rng(7)
        for _ in range(500):
            filled = np.concatenate((SKEWED, rng.uniform(low, high, 3)))
            weights = np.ones_like(filled)
            assert found["gini"].low - 1e-9 <= inequality.gini(filled, weights)
            assert inequality.gini(filled, weights) <= found["gini"].high + 1e-9
            theil = inequality.theil(filled, weights)
            assert found["theil"].low - 1e-9 <= theil <= found["theil"].high + 1e-9

    def test_bounds_are_attained(self) -> None:
        """Верхняя граница достигается в вершине: скрытые — в минимуме или максимуме."""
        found = inequality.hidden_bounds(SKEWED, 2)
        low, high = min(SKEWED), max(SKEWED)
        corners = [
            inequality.gini(np.array([*SKEWED, *values]), np.ones(len(SKEWED) + 2))
            for values in ((low, low), (low, high), (high, high))
        ]
        assert found["gini"].high == pytest.approx(max(corners))

    def test_equal_values_leave_no_room(self) -> None:
        """Если все наблюдаемые равны, скрытым некуда отклониться: границы совпадают."""
        found = inequality.hidden_bounds(UNIFORM, 4)
        assert found["gini"].low == pytest.approx(0.0, abs=1e-12)
        assert found["gini"].high == pytest.approx(0.0, abs=1e-12)

    def test_non_positive_values_skip_theil(self) -> None:
        """Тейл для нулевых значений не определён — и границ у него нет."""
        found = inequality.hidden_bounds([0.0, *SKEWED], 2)
        assert "theil" not in found
        assert "gini" in found
