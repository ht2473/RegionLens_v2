"""
Проверки мер связи на данных с известным ответом, частной корреляции и поправки
на множественность сравнений.
"""

from __future__ import annotations

import numpy as np
import pytest
from scipy import stats

from apps.analytics.core import correlation

pytestmark = pytest.mark.unit

# Ряд достаточной длины: коэффициент по восьми парам ничего не доказывает.
COUNT = 60


def columns(**series: list[float | None]) -> list[dict[str, object]]:
    """Собрать столбцы значений в том виде, в каком их передают представления."""
    return [{"key": name, "label": name, "values": values} for name, values in series.items()]


class TestCorrelate:
    """Парные коэффициенты связи."""

    def test_perfect_linear_relation_gives_unit(self) -> None:
        """Точная линейная связь даёт коэффициент, равный единице."""
        x = [float(value) for value in range(COUNT)]
        y = [2.0 * value + 1 for value in x]
        assert correlation.correlate(x, y, "pearson").coefficient == pytest.approx(1.0)

    def test_inverse_relation_gives_negative_unit(self) -> None:
        """Обратная связь даёт минус единицу."""
        x = [float(value) for value in range(COUNT)]
        y = [-value for value in x]
        assert correlation.correlate(x, y, "pearson").coefficient == pytest.approx(-1.0)

    def test_monotonic_relation_favours_rank_coefficient(self) -> None:
        """Монотонная нелинейная связь полностью улавливается только ранговым коэффициентом."""
        x = [float(value) for value in range(1, COUNT)]
        y = [value**3 for value in x]

        assert correlation.correlate(x, y, "spearman").coefficient == pytest.approx(1.0)
        assert correlation.correlate(x, y, "pearson").coefficient < 1.0

    def test_independent_series_are_not_significant(self) -> None:
        """Независимые ряды не дают значимой связи."""
        generator = np.random.default_rng(20260321)
        x = list(generator.normal(size=COUNT))
        y = list(generator.normal(size=COUNT))
        assert not correlation.correlate(x, y).is_significant

    def test_short_sample_is_rejected(self) -> None:
        """По четырём парам коэффициент не рассчитывается."""
        result = correlation.correlate([1.0, 2.0, 3.0, 4.0], [1.0, 2.0, 3.0, 4.0])
        assert not result.is_available
        assert result.pairs == 4

    def test_constant_series_is_rejected(self) -> None:
        """Для постоянного ряда связь не определена."""
        x = [1.0] * COUNT
        y = [float(value) for value in range(COUNT)]
        assert not correlation.correlate(x, y).is_available

    def test_incomplete_pairs_are_dropped(self) -> None:
        """Пара, в которой отсутствует значение, в расчёт не входит."""
        x: list[float | None] = [float(value) for value in range(COUNT)]
        y: list[float | None] = [float(value) for value in range(COUNT)]
        x[0] = None
        y[1] = None
        assert correlation.correlate(x, y).pairs == COUNT - 2

    def test_strength_label_follows_magnitude(self) -> None:
        """Словесная оценка тесноты зависит от величины коэффициента."""
        weak = correlation.Correlation(coefficient=0.05, p_value=0.9, pairs=COUNT)
        strong = correlation.Correlation(coefficient=0.95, p_value=0.0, pairs=COUNT)
        assert str(weak.strength) != str(strong.strength)

    def test_direction_is_reported(self) -> None:
        """Направление связи определяется знаком коэффициента."""
        assert correlation.Correlation(0.5, 0.01, COUNT).direction == "positive"
        assert correlation.Correlation(-0.5, 0.01, COUNT).direction == "negative"


class TestCorrelationMatrix:
    """Матрица парных связей."""

    def test_diagonal_is_unit(self) -> None:
        """На главной диагонали стоит единица."""
        values = [float(value) for value in range(COUNT)]
        matrix = correlation.correlation_matrix(columns(a=values, b=values))
        assert matrix["cells"][0][0].coefficient == pytest.approx(1.0)

    def test_matrix_is_symmetric(self) -> None:
        """Матрица симметрична: связь не зависит от порядка показателей."""
        generator = np.random.default_rng(5)
        first = list(generator.normal(size=COUNT))
        second = list(generator.normal(size=COUNT))
        matrix = correlation.correlation_matrix(columns(a=first, b=second))
        assert matrix["cells"][0][1] is matrix["cells"][1][0]

    def test_strongest_pairs_are_ordered(self) -> None:
        """Наиболее выраженные связи упорядочены по убыванию модуля коэффициента."""
        base = [float(value) for value in range(COUNT)]
        generator = np.random.default_rng(9)
        noisy = [value + float(generator.normal(0, 20)) for value in base]

        matrix = correlation.correlation_matrix(columns(a=base, b=base, c=noisy))
        pairs = correlation.strongest_pairs(matrix)
        magnitudes = [abs(pair["correlation"].coefficient) for pair in pairs]
        assert magnitudes == sorted(magnitudes, reverse=True)

    def test_insignificant_pairs_are_excluded(self) -> None:
        """В перечень наиболее выраженных связей незначимые пары не попадают."""
        generator = np.random.default_rng(13)
        matrix = correlation.correlation_matrix(
            columns(
                a=list(generator.normal(size=COUNT)),
                b=list(generator.normal(size=COUNT)),
            )
        )
        assert correlation.strongest_pairs(matrix) == []

    def test_matrix_levels_are_adjusted_for_multiplicity(self) -> None:
        """
        У пар матрицы есть уровень с поправкой Бенджамини — Хохберга по всем парам:
        он не меньше исходного и совпадает с расчётом по исходным уровням.
        """
        generator = np.random.default_rng(21)
        base = generator.normal(size=COUNT)
        matrix = correlation.correlation_matrix(
            columns(
                a=list(base),
                b=list(base + generator.normal(0, 2, size=COUNT)),
                c=list(generator.normal(size=COUNT)),
            )
        )
        cells = matrix["cells"]
        places = [(0, 1), (0, 2), (1, 2)]
        raw = [cells[row][column].p_value for row, column in places]
        expected = stats.false_discovery_control(raw, method="bh")
        for (row, column), value in zip(places, expected, strict=True):
            cell = cells[row][column]
            assert cell.p_adjusted == pytest.approx(value)
            assert cell.p_adjusted >= cell.p_value
            assert cells[column][row] is cell

    def test_significance_follows_adjusted_level(self) -> None:
        """
        Пара, значимая по исходному уровню (0,03), после поправки на три сравнения
        значимой не считается: 0,03 · 3 = 0,09.
        """
        cells = [
            [correlation.Correlation(1.0, 0.0, 0), None, None],
            [None, correlation.Correlation(1.0, 0.0, 0), None],
            [None, None, correlation.Correlation(1.0, 0.0, 0)],
        ]
        for (row, column), p_value in zip([(0, 1), (0, 2), (1, 2)], [0.03, 0.2, 0.3], strict=True):
            cells[row][column] = cells[column][row] = correlation.Correlation(0.3, p_value, COUNT)
        assert cells[0][1].is_significant

        correlation._adjust_for_multiplicity(cells)
        assert cells[0][1].p_adjusted == pytest.approx(0.09)
        assert not cells[0][1].is_significant
        assert not cells[1][0].is_significant

    def test_single_pair_keeps_raw_level(self) -> None:
        """У отдельно рассчитанной пары поправки нет: значимость — по исходному уровню."""
        values = [float(value) for value in range(COUNT)]
        pair = correlation.correlate(values, values)
        assert pair.p_adjusted is None
        assert pair.is_significant


class TestPartialCorrelation:
    """Частная корреляция."""

    def test_common_cause_relation_disappears(self) -> None:
        """Связь, целиком объяснимая третьим показателем, при его исключении исчезает."""
        generator = np.random.default_rng(20260321)
        common = generator.normal(size=200)
        first = common + generator.normal(0, 0.1, size=200)
        second = common + generator.normal(0, 0.1, size=200)

        data = columns(a=list(first), b=list(second), c=list(common))
        pair = correlation.correlate(data[0]["values"], data[1]["values"], "pearson")
        partial = correlation.partial_correlation(data)

        assert partial is not None
        assert pair.coefficient > 0.9
        assert abs(partial["rows"][0]["values"][1]) < 0.5

    def test_two_columns_are_rejected(self) -> None:
        """Частная корреляция требует хотя бы одного контролируемого показателя."""
        values = [float(value) for value in range(COUNT)]
        assert correlation.partial_correlation(columns(a=values, b=values)) is None

    def test_rows_carry_labels(self) -> None:
        """Строки матрицы возвращаются вместе с подписями показателей."""
        generator = np.random.default_rng(2)
        data = columns(
            a=list(generator.normal(size=COUNT)),
            b=list(generator.normal(size=COUNT)),
            c=list(generator.normal(size=COUNT)),
        )
        partial = correlation.partial_correlation(data)
        assert partial is not None
        assert [row["label"] for row in partial["rows"]] == ["a", "b", "c"]


class TestLaggedProfile:
    """Связь при сдвиге во времени."""

    def test_lagged_relation_is_strongest_at_true_shift(self) -> None:
        """Наибольшая связь обнаруживается при истинном сдвиге."""
        generator = np.random.default_rng(20260321)
        codes = [f"R{index:02d}" for index in range(60)]
        base = {code: float(generator.normal()) for code in codes}

        # Второй показатель повторяет первый с задержкой в два года.
        x_panel = {
            2018: {code: base[code] for code in codes},
            2019: {code: float(generator.normal()) for code in codes},
            2020: {code: float(generator.normal()) for code in codes},
        }
        y_panel = {2020: {code: base[code] for code in codes}}

        profile = correlation.lagged_profile(x_panel, y_panel, target_year=2020, max_lag=3)
        best = max(profile, key=lambda item: abs(item["correlation"].coefficient or 0))
        assert best["lag"] == 2

    def test_missing_target_year_gives_empty_profile(self) -> None:
        """Без данных за целевой год профиль не строится."""
        assert correlation.lagged_profile({}, {}, target_year=2020) == []


class TestScatterPoints:
    """Точки диаграммы рассеяния."""

    def test_incomplete_pairs_are_skipped(self) -> None:
        """Территория без одного из значений на диаграмму не попадает."""
        points = correlation.scatter_points(
            [1.0, None, 3.0], [1.0, 2.0, None], ["A", "B", "C"], ["RU-A", "RU-B", "RU-C"]
        )
        assert [point["name"] for point in points] == ["A"]

    def test_codes_are_preserved(self) -> None:
        """Код территории сохраняется для перехода к её паспорту."""
        points = correlation.scatter_points([1.0], [2.0], ["A"], ["RU-A"])
        assert points[0]["code"] == "RU-A"
