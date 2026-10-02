"""
Проверки шкалы, весов и свёртки индекса на данных с известным ответом;
у дестимулятора наименьшее значение получает наивысшую оценку.
"""

from __future__ import annotations

import numpy as np
import pytest

from apps.analytics.core import aggregation, normalization, weighting

pytestmark = pytest.mark.unit

# Ряд из десяти значений с равным шагом: удобен для проверки шкал.
LADDER = [float(value) for value in range(10)]


class TestNormalization:
    """Приведение показателей к общей шкале."""

    def test_min_max_spans_full_scale(self) -> None:
        """Линейное растяжение помещает крайние значения в границы шкалы."""
        result = normalization.normalize(LADDER, method="minmax")
        assert result.values[0] == pytest.approx(normalization.SCALE_MIN)
        assert result.values[-1] == pytest.approx(normalization.SCALE_MAX)

    def test_negative_polarity_reverses_scale(self) -> None:
        """Для дестимулятора наименьшее значение получает наивысшую оценку."""
        result = normalization.normalize(LADDER, method="minmax", polarity="negative")
        assert result.values[0] == pytest.approx(normalization.SCALE_MAX)
        assert result.values[-1] == pytest.approx(normalization.SCALE_MIN)

    def test_rank_scale_is_evenly_spaced(self) -> None:
        """Ранговая шкала распределяет территории с равным шагом."""
        result = normalization.normalize(LADDER, method="rank")
        steps = [
            result.values[index + 1] - result.values[index] for index in range(len(LADDER) - 1)
        ]
        assert max(steps) == pytest.approx(min(steps))

    def test_rank_scale_ignores_magnitude_of_outlier(self) -> None:
        """Ранговая шкала не замечает величины выброса — только порядок."""
        moderate = normalization.normalize([1.0, 2.0, 3.0, 4.0, 5.0], method="rank")
        extreme = normalization.normalize([1.0, 2.0, 3.0, 4.0, 5000.0], method="rank")
        assert moderate.values == pytest.approx(extreme.values)

    def test_min_max_is_dominated_by_outlier(self) -> None:
        """Линейное растяжение, напротив, сжимает остальные территории вокруг выброса."""
        result = normalization.normalize([1.0, 2.0, 3.0, 4.0, 5000.0], method="minmax")
        assert result.values[3] < 1.0

    def test_standardization_reports_clipped_values(self) -> None:
        """Значения за пределами трёх стандартных отклонений усекаются и подсчитываются."""
        values = [*[10.0] * 30, 1000.0]
        result = normalization.normalize(values, method="zscore")
        assert result.clipped == 1
        assert result.note

    def test_robust_scale_resists_outlier(self) -> None:
        """Робастная шкала не смещает точку отсчёта из-за одного выброса."""
        base = [float(value) for value in range(1, 21)]
        plain = normalization.normalize(base, method="robust")
        with_outlier = normalization.normalize([*base, 10_000.0], method="robust")
        assert plain.values[0] == pytest.approx(with_outlier.values[0], abs=1.0)

    def test_logarithmic_scale_requires_positive_values(self) -> None:
        """Логарифмическая шкала неприменима к неположительным значениям."""
        result = normalization.normalize([0.0, 1.0, 2.0, 3.0], method="log")
        assert result.note

    def test_missing_values_stay_missing(self) -> None:
        """Территория без значения остаётся без оценки."""
        result = normalization.normalize([1.0, None, 3.0, 4.0])
        assert result.values[1] is None
        assert result.covered == 3

    def test_constant_series_maps_to_middle(self) -> None:
        """Ряд без разброса помещается в середину шкалы."""
        result = normalization.normalize([5.0] * 10, method="minmax")
        assert result.values[0] == pytest.approx(normalization.SCALE_MIDDLE)
        assert result.note

    def test_unknown_method_falls_back_to_min_max(self) -> None:
        """Неизвестный способ заменяется линейным растяжением, а не ошибкой."""
        result = normalization.normalize(LADDER, method="что-то ещё")
        assert result.method == "minmax"


class TestWeighting:
    """Назначение весов показателям."""

    def matrix(self) -> np.ndarray:
        """Матрица «территории × показатели» с разной различающей способностью."""
        generator = np.random.default_rng(20260321)
        varied = generator.uniform(0, 100, size=40)
        flat = np.full(40, 50.0) + generator.normal(0, 0.5, size=40)
        return np.column_stack([varied, flat])

    def test_equal_weights_sum_to_one(self) -> None:
        """Равные веса в сумме дают единицу."""
        result = weighting.build_weights(self.matrix(), method="equal")
        assert sum(result.values) == pytest.approx(1.0)

    def test_expert_weights_are_normalized(self) -> None:
        """Экспертные веса приводятся к единичной сумме, сохраняя соотношение."""
        result = weighting.build_weights(self.matrix(), method="expert", expert=[3.0, 1.0])
        assert sum(result.values) == pytest.approx(1.0)
        assert result.values[0] == pytest.approx(0.75)

    def test_zero_expert_weights_fall_back_to_equal(self) -> None:
        """Нулевые экспертные веса заменяются равными с объяснением."""
        result = weighting.build_weights(self.matrix(), method="expert", expert=[0.0, 0.0])
        assert result.method == "equal"
        assert result.note

    def test_entropy_favours_discriminating_indicator(self) -> None:
        """Энтропийный способ даёт больший вес показателю, сильнее различающему территории."""
        result = weighting.build_weights(self.matrix(), method="entropy")
        assert result.values[0] > result.values[1]

    def test_critic_weights_sum_to_one(self) -> None:
        """Веса CRITIC в сумме дают единицу."""
        result = weighting.build_weights(self.matrix(), method="critic")
        assert sum(result.values) == pytest.approx(1.0)

    def test_critic_penalizes_duplicated_indicator(self) -> None:
        """Показатель, дублирующий другой, получает меньший вес, чем самостоятельный."""
        generator = np.random.default_rng(7)
        base = generator.uniform(0, 100, size=60)
        duplicate = base + generator.normal(0, 0.5, size=60)
        separate = generator.uniform(0, 100, size=60)

        matrix = np.column_stack([base, duplicate, separate])
        result = weighting.build_weights(matrix, method="critic")
        assert result.values[2] > result.values[0]

    def test_principal_components_report_explained_share(self) -> None:
        """Способ по главным компонентам сообщает долю объяснённого разброса."""
        result = weighting.build_weights(self.matrix(), method="pca")
        assert result.method == "pca"
        assert result.note

    def test_short_sample_falls_back_to_equal(self) -> None:
        """При недостатке территорий веса по структуре набора не рассчитываются."""
        result = weighting.build_weights(np.ones((4, 2)), method="entropy")
        assert result.method == "equal"
        assert result.note

    def test_empty_matrix_gives_no_weights(self) -> None:
        """Без выбранных показателей весов нет."""
        result = weighting.build_weights(np.empty((0, 0)), method="equal")
        assert result.values == []


class TestAggregation:
    """Свёртка нормированных значений."""

    def territories(self, count: int) -> list[dict[str, str]]:
        """Перечень территорий для расчёта оценок."""
        return [{"code": f"R{index:02d}", "name": f"Территория {index}"} for index in range(count)]

    def test_additive_matches_weighted_mean(self) -> None:
        """Аддитивная свёртка равна взвешенному среднему нормированных значений."""
        matrix = np.array([[100.0, 0.0], [50.0, 50.0]])
        scores = aggregation.aggregate(matrix, [0.25, 0.75], self.territories(2), method="additive")
        by_code = {score.code: score.value for score in scores}
        assert by_code["R00"] == pytest.approx(25.0)
        assert by_code["R01"] == pytest.approx(50.0)

    def test_geometric_penalizes_weak_component(self) -> None:
        """Геометрическая свёртка наказывает провал по одному показателю сильнее аддитивной."""
        matrix = np.array([[100.0, 1.0], [50.0, 50.0]])
        additive = aggregation.aggregate(matrix, [0.5, 0.5], self.territories(2), method="additive")
        geometric = aggregation.aggregate(
            matrix, [0.5, 0.5], self.territories(2), method="geometric"
        )
        assert additive[0].code == "R00"
        assert geometric[0].code == "R01"

    def test_topsis_returns_scale_bounded_values(self) -> None:
        """Оценки TOPSIS остаются в границах общей шкалы."""
        generator = np.random.default_rng(3)
        matrix = generator.uniform(0, 100, size=(20, 3))
        scores = aggregation.aggregate(matrix, [1 / 3] * 3, self.territories(20), method="topsis")
        assert all(0 <= score.value <= 100 for score in scores)

    def test_distance_gives_leader_full_score(self) -> None:
        """При расстоянии до эталона наибольшую оценку получает ближайшая к нему территория."""
        matrix = np.array([[100.0, 100.0], [0.0, 0.0], [50.0, 50.0]])
        scores = aggregation.aggregate(matrix, [0.5, 0.5], self.territories(3), method="distance")
        assert scores[0].code == "R00"
        assert scores[0].value == pytest.approx(100.0)

    def test_scores_are_sorted(self) -> None:
        """Результат упорядочен по убыванию оценки."""
        generator = np.random.default_rng(11)
        matrix = generator.uniform(0, 100, size=(20, 2))
        scores = aggregation.aggregate(matrix, [0.5, 0.5], self.territories(20))
        assert [score.value for score in scores] == sorted(
            (score.value for score in scores), reverse=True
        )

    def test_sparse_territory_is_excluded(self) -> None:
        """Территория с недостаточным покрытием в рейтинг не попадает."""
        matrix = np.array([[100.0, 100.0, 100.0], [50.0, np.nan, np.nan]])
        scores = aggregation.aggregate(matrix, [1 / 3] * 3, self.territories(2))
        assert [score.code for score in scores] == ["R00"]

    def test_missing_weight_is_redistributed(self) -> None:
        """
        Вес недостающего показателя перераспределяется между имеющимися.

        Иначе отсутствие данных штрафовало бы территорию так же, как плохое значение.
        """
        matrix = np.array([[90.0, 60.0, 30.0], [90.0, 60.0, np.nan]])
        scores = aggregation.aggregate(matrix, [1 / 3] * 3, self.territories(2))
        by_code = {score.code: score.value for score in scores}
        assert by_code["R00"] == pytest.approx(60.0)
        assert by_code["R01"] == pytest.approx(75.0)

    def test_partial_coverage_is_reported(self) -> None:
        """Неполнота данных отмечается в результате."""
        matrix = np.array([[80.0, 40.0, np.nan], [80.0, 40.0, 20.0]])
        scores = aggregation.aggregate(matrix, [1 / 3] * 3, self.territories(2))
        partial = {score.code: score.is_partial for score in scores}
        assert partial["R00"] is True
        assert partial["R01"] is False


class TestRankShift:
    """Проверка устойчивости рейтинга."""

    def scores(self, order: list[str]) -> list[aggregation.Score]:
        """Собрать рейтинг из заданного порядка территорий."""
        return [
            aggregation.Score(
                code=code,
                name=code,
                value=float(100 - index),
                covered=1,
                total=1,
                contributions=[1.0],
            )
            for index, code in enumerate(order)
        ]

    def test_identical_rankings_show_no_shift(self) -> None:
        """Совпадающие рейтинги дают нулевое смещение."""
        result = aggregation.rank_shift(self.scores(["A", "B", "C"]), self.scores(["A", "B", "C"]))
        assert result["mean_shift"] == pytest.approx(0.0)
        assert result["unchanged"] == 3

    def test_reversal_gives_maximum_shift(self) -> None:
        """Обратный порядок даёт наибольшее смещение позиций."""
        result = aggregation.rank_shift(self.scores(["A", "B", "C"]), self.scores(["C", "B", "A"]))
        assert result["max_shift"] == 2

    def test_disjoint_rankings_are_rejected(self) -> None:
        """Рейтинги без общих территорий не сравниваются."""
        result = aggregation.rank_shift(self.scores(["A"]), self.scores(["B"]))
        assert result["available"] is False
