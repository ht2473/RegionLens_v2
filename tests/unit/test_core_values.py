"""
Формулы анализа и карты на числах, посчитанных вручную: нормировка, свёртка индекса, веса,
границы классов, доверительный интервал. Проверяются сами значения, а не только то, что расчёт
не падает: порча формулы должна быть заметна здесь, без страниц и склада.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pytest

from apps.analytics.core import (
    aggregation,
    convergence,
    correlation,
    inequality,
    normalization,
    regression,
    spatial,
    weighting,
)
from apps.maps import classification

TERRITORIES = [{"code": f"T{number}", "name": f"T{number}"} for number in range(3)]


def scores(matrix: list[list[float]], weights: list[float], method: str) -> dict[str, float]:
    rows = TERRITORIES[: len(matrix)]
    found = aggregation.aggregate(np.array(matrix), weights, rows, method=method)
    return {score.code: score.value for score in found}


class TestNormalization:
    def test_rank_spreads_places_over_the_scale(self) -> None:
        found = normalization.normalize([30.0, 10.0, 20.0], method="rank")
        assert found.values == pytest.approx([100.0, 0.0, 50.0])

    def test_rank_gives_ties_the_mean_place(self) -> None:
        found = normalization.normalize([1.0, 1.0, 2.0], method="rank")
        assert found.values == pytest.approx([25.0, 25.0, 100.0])

    def test_zscore_maps_three_deviations_to_the_edges(self) -> None:
        found = normalization.normalize([1.0, 2.0, 3.0], method="zscore")
        assert found.values == pytest.approx([50 - 50 / 3, 50.0, 50 + 50 / 3])

    def test_robust_uses_median_and_quartiles(self) -> None:
        found = normalization.normalize([1.0, 2.0, 3.0, 4.0, 5.0], method="robust")
        spread = 2 / 1.349
        assert found.values[2] == pytest.approx(50.0)
        assert found.values[4] == pytest.approx(50 + 2 / spread / 3 * 50)

    def test_log_stretches_orders_of_magnitude_evenly(self) -> None:
        found = normalization.normalize([1.0, 10.0, 100.0], method="log")
        assert found.values == pytest.approx([0.0, 50.0, 100.0])

    def test_negative_polarity_turns_the_scale_over(self) -> None:
        found = normalization.normalize([0.0, 5.0, 10.0], method="minmax", polarity="negative")
        assert found.values == pytest.approx([100.0, 50.0, 0.0])


class TestAggregation:
    def test_additive_is_a_weighted_mean(self) -> None:
        found = scores([[40.0, 80.0]], [0.25, 0.75], "additive")
        assert found["T0"] == pytest.approx(70.0)

    def test_gap_hands_its_weight_to_the_rest(self) -> None:
        found = scores([[math.nan, 30.0, 60.0]], [0.2, 0.3, 0.5], "additive")
        assert found["T0"] == pytest.approx(30 * 0.375 + 60 * 0.625)

    def test_too_many_gaps_drop_the_region(self) -> None:
        assert scores([[math.nan, math.nan, 60.0]], [0.2, 0.3, 0.5], "additive") == {}

    def test_geometric_mean_penalises_imbalance(self) -> None:
        found = scores([[25.0, 100.0]], [0.5, 0.5], "geometric")
        assert found["T0"] == pytest.approx(50.0)

    def test_distance_measures_from_the_best_values(self) -> None:
        found = scores([[100.0, 100.0], [0.0, 100.0], [0.0, 0.0]], [0.5, 0.5], "distance")
        assert found["T0"] == pytest.approx(100.0)
        assert found["T1"] == pytest.approx((1 - math.sqrt(0.5)) * 100)
        assert found["T2"] == pytest.approx(0.0)

    def test_rank_shift_counts_places(self) -> None:
        def table(*codes: str) -> list[aggregation.Score]:
            return [
                aggregation.Score(code=c, name=c, value=0.0, covered=1, total=1, contributions=[])
                for c in codes
            ]

        found = aggregation.rank_shift(table("a", "b", "c"), table("c", "b", "a"))
        assert found["max_shift"] == 2
        assert found["mean_shift"] == pytest.approx(4 / 3)
        assert found["unchanged"] == 1


class TestWeights:
    def test_expert_weights_add_up_to_one(self) -> None:
        found = weighting.build_weights(np.ones((3, 2)), method="expert", expert=[1.0, 3.0])
        assert found.values == pytest.approx([0.25, 0.75])

    def test_short_expert_list_is_padded_and_negative_clipped(self) -> None:
        found = weighting.build_weights(np.ones((3, 3)), method="expert", expert=[-1.0, 2.0])
        assert found.values == pytest.approx([0.0, 1.0, 0.0])

    def test_too_few_rows_fall_back_to_equal(self) -> None:
        matrix = np.random.default_rng(1).random((weighting.MIN_ROWS - 1, 3))
        found = weighting.build_weights(matrix, method="critic")
        assert (found.method, found.values) == ("equal", pytest.approx([1 / 3] * 3))

    def test_enough_rows_are_used(self) -> None:
        matrix = np.random.default_rng(1).random((weighting.MIN_ROWS, 2))
        for method in ("entropy", "critic", "pca"):
            assert weighting.build_weights(matrix, method=method).method == method

    def test_principal_components_follow_the_formula(self) -> None:
        """Веса — квадраты нагрузок компонент Кайзера, взвешенные долей дисперсии."""
        matrix = np.random.default_rng(7).normal(size=(40, 4))
        matrix[:, 1] = matrix[:, 0] + 0.1 * matrix[:, 1]
        found = weighting.build_weights(matrix, method="pca")

        eigenvalues, vectors = np.linalg.eigh(np.corrcoef(matrix, rowvar=False))
        keep = eigenvalues >= 1
        loadings = vectors[:, keep] * np.sqrt(eigenvalues[keep])
        expected = np.sum(loadings**2 * (eigenvalues[keep] / eigenvalues.sum()), axis=1)
        assert found.values == pytest.approx(list(expected / expected.sum()))


class TestClasses:
    def test_quantiles_interpolate_between_values(self) -> None:
        values = [0.0, 10.0, 20.0, 30.0, 40.0]
        assert classification._quantile_breaks(values, 2) == pytest.approx([0.0, 25.0, 40.0])

    def test_stddev_step_depends_on_class_count(self) -> None:
        values = [float(number) for number in range(-50, 51)]
        deviation = math.sqrt(sum(v * v for v in values) / len(values))
        wide = classification._stddev_breaks(values, 4)
        medium = classification._stddev_breaks(values, 6)
        narrow = classification._stddev_breaks(values, 7)
        assert wide[1:-1] == pytest.approx([-deviation, 0.0, deviation])
        assert medium[2] - medium[1] == pytest.approx(deviation / 2)
        assert narrow[2] - narrow[1] == pytest.approx(deviation / 3)

    def test_stddev_of_equal_values_falls_back_to_equal_intervals(self) -> None:
        assert classification._stddev_breaks([5.0, 5.0, 5.0], 3) == pytest.approx([5.0] * 4)

    def test_stddev_edges_stay_inside_the_data(self) -> None:
        breaks = classification._stddev_breaks([0.0, 0.0, 0.0, 100.0], 7)
        assert min(breaks) == 0.0
        assert max(breaks) == 100.0

    def test_histogram_bins_cover_the_range(self) -> None:
        bins = classification.histogram([0.0, 1.0, 2.0, 4.0], bins=4)
        assert [item["count"] for item in bins] == [1, 1, 1, 1]
        assert (bins[0]["lower"], bins[0]["upper"]) == pytest.approx((0.0, 1.0))
        assert bins[-1]["upper"] == pytest.approx(4.0)
        assert [item["height"] for item in bins] == pytest.approx([1.0] * 4)


class TestRegression:
    def test_confidence_interval_is_two_sided(self) -> None:
        coefficient = regression.Coefficient(
            name="x", value=10.0, std_error=2.0, t_value=5.0, p_value=0.01
        )
        assert coefficient.confidence_low == pytest.approx(10 - 1.96 * 2)
        assert coefficient.confidence_high == pytest.approx(10 + 1.96 * 2)

    def test_prediction_follows_the_line(self) -> None:
        x = np.arange(10.0)
        fit = regression.ols(3 + 2 * x, x, ["x"])
        assert fit is not None
        assert fit.predict([0.0, 10.0]) == pytest.approx([3.0, 23.0])


class TestCorrelation:
    def test_strongest_pairs_go_by_absolute_value(self) -> None:
        def cell(value: float) -> correlation.Correlation:
            return correlation.Correlation(coefficient=value, p_value=0.001, pairs=20)

        weak, strong = cell(0.5), cell(-0.9)
        matrix: dict[str, Any] = {
            "size": 3,
            "labels": ["a", "b", "c"],
            "keys": ["a", "b", "c"],
            "cells": [
                [cell(1.0), weak, strong],
                [weak, cell(1.0), weak],
                [strong, weak, cell(1.0)],
            ],
        }
        found = correlation.strongest_pairs(matrix)
        assert (found[0]["first_key"], found[0]["second_key"]) == ("a", "c")
        assert len(found) == 3

    def test_partial_correlation_removes_the_third_variable(self) -> None:
        """Сверка с определением: связь остатков после регрессии на третий показатель."""
        rng = np.random.default_rng(3)
        third = rng.normal(size=60)
        first = third + rng.normal(size=60)
        second = third + rng.normal(size=60)
        columns = [
            {"label": label, "values": list(values)}
            for label, values in (("x", first), ("y", second), ("z", third))
        ]
        found = correlation.partial_correlation(columns)
        assert found is not None

        def residual(values: np.ndarray) -> np.ndarray:
            slope, intercept = np.polyfit(third, values, 1)
            return values - (slope * third + intercept)

        expected = np.corrcoef(residual(first), residual(second))[0, 1]
        assert found["rows"][0]["values"][1] == pytest.approx(expected)
        assert found["controlled"] == 1


class TestSpatial:
    def test_nearest_neighbours_use_great_circle(self) -> None:
        """На высоких широтах градус долготы короче: ближний сосед — по долготе."""
        coordinates = {
            "A": (70.0, 0.0),
            "B": (70.0, 2.0),
            "C": (68.0, 0.0),
            "D": (0.0, 100.0),
        }
        found = spatial.nearest_neighbours(list(coordinates), coordinates, count=1)
        assert found["A"] == ["B"]

    def test_significance_cannot_reach_zero(self) -> None:
        values = np.arange(30.0)
        codes = [f"T{number}" for number in range(30)]
        chain = {
            code: codes[max(n - 1, 0) : n] + codes[n + 1 : n + 2] for n, code in enumerate(codes)
        }
        found = spatial.global_moran(values, spatial.build_matrix(codes, chain), permutations=19)
        assert found is not None
        assert found.p_value >= 1 / 20

    def test_quadrant_of_zero_counts_as_high(self) -> None:
        assert spatial._quadrant(0.0, 0.0) == spatial.QUADRANT_HIGH_HIGH
        assert spatial._quadrant(-1.0, 1.0) == spatial.QUADRANT_LOW_HIGH
        assert spatial._quadrant(-1.0, -1.0) == spatial.QUADRANT_LOW_LOW
        assert spatial._quadrant(1.0, -1.0) == spatial.QUADRANT_HIGH_LOW


class TestInequality:
    def test_decile_ratio_uses_ninth_over_first(self) -> None:
        values = np.arange(1.0, 12.0)
        assert inequality.decile_ratio(values) == pytest.approx(10 / 2)

    def test_range_ratio_needs_positive_minimum(self) -> None:
        assert inequality.range_ratio(np.array([2.0, 8.0])) == pytest.approx(4.0)
        assert inequality.range_ratio(np.array([0.0, 8.0])) is None

    def test_lorenz_curve_accumulates_shares(self) -> None:
        points = inequality.lorenz_curve(np.array([1.0, 3.0]), np.ones(2))
        assert points[0] == {"population": 0.0, "value": 0.0}
        assert points[1] == pytest.approx({"population": 0.5, "value": 0.25})
        assert points[-1] == {"population": 1.0, "value": 1.0}


class TestConvergence:
    def test_speed_and_half_life(self) -> None:
        speed, half_life = convergence.convergence_speed(-0.05, 10)
        assert speed == pytest.approx(-math.log(0.5) / 10)
        assert half_life == pytest.approx(math.log(2) / speed)

    def test_divergence_has_no_speed(self) -> None:
        assert convergence.convergence_speed(0.1, 10) == (None, None)

    def test_too_fast_factor_has_no_speed(self) -> None:
        assert convergence.convergence_speed(-0.2, 10) == (None, None)

    def test_sigma_trend_reads_first_and_last(self) -> None:
        base = [float(number) for number in range(1, 26)]
        points = convergence.sigma_series(
            {
                2010: base,
                2011: [value**0.9 for value in base],
                2012: [value**0.8 for value in base],
            }
        )
        found = convergence.sigma_trend(points)
        assert len(points) == 3
        assert found["change"] == pytest.approx(points[-1].std_log - points[0].std_log)
        assert found["change"] < 0
