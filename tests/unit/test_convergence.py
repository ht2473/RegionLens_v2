"""
Проверки конвергенции на данных с известным поведением и согласия периода полусокращения
с коэффициентом регрессии.
"""

from __future__ import annotations

import math

import pytest

from apps.analytics.core import convergence

pytestmark = pytest.mark.unit


def converging_panel() -> dict[int, list[float | None]]:
    """
    Построить панель, в которой различия между территориями сокращаются.

    Каждая территория движется к общему уровню, ежегодно покрывая десятую часть
    оставшегося разрыва.
    """
    levels = [10.0 + index * 5 for index in range(30)]
    target = sum(levels) / len(levels)

    panel: dict[int, list[float | None]] = {}
    current = list(levels)
    for year in range(2000, 2021):
        panel[year] = list(current)
        current = [value + (target - value) * 0.1 for value in current]
    return panel


def parallel_panel() -> dict[int, list[float | None]]:
    """Построить панель, в которой все территории растут одинаковым темпом."""
    levels = [10.0 + index * 5 for index in range(30)]
    return {
        year: [value * (1.03 ** (year - 2000)) for value in levels] for year in range(2000, 2021)
    }


class TestSigmaSeries:
    """Разброс значений по годам."""

    def test_dispersion_falls_when_regions_converge(self) -> None:
        """При сближении территорий разброс логарифмов сокращается."""
        points = convergence.sigma_series(converging_panel())
        assert points[0].std_log > points[-1].std_log

    def test_dispersion_is_stable_under_equal_growth(self) -> None:
        """При равных темпах роста разброс логарифмов не меняется."""
        points = convergence.sigma_series(parallel_panel())
        assert points[0].std_log == pytest.approx(points[-1].std_log)

    def test_short_years_are_skipped(self) -> None:
        """Годы с малым числом территорий в расчёт не попадают."""
        panel: dict[int, list[float | None]] = {2000: [1.0, 2.0, 3.0], 2001: [1.0] * 25}
        years = [point.year for point in convergence.sigma_series(panel)]
        assert years == [2001]

    def test_non_positive_values_are_dropped(self) -> None:
        """Неположительные значения исключаются: логарифм от них не определён."""
        usable = [float(value) for value in range(1, 30)]
        panel: dict[int, list[float | None]] = {2000: [-1.0, 0.0, *usable]}
        points = convergence.sigma_series(panel)
        assert points[0].count == 29


class TestSigmaTrend:
    """Тенденция изменения разброса."""

    def test_converging_series_is_recognized(self) -> None:
        """Устойчивое сокращение разброса опознаётся как сближение."""
        trend = convergence.sigma_trend(convergence.sigma_series(converging_panel()))
        assert trend["available"] is True
        assert trend["converging"] is True
        assert trend["diverging"] is False

    def test_stable_series_shows_no_trend(self) -> None:
        """При неизменном разбросе ни сближение, ни расхождение не подтверждаются."""
        trend = convergence.sigma_trend(convergence.sigma_series(parallel_panel()))
        assert trend["converging"] is False
        assert trend["diverging"] is False

    def test_short_series_is_rejected(self) -> None:
        """По двум годам тенденция не оценивается."""
        points = convergence.sigma_series({2000: [1.0] * 25, 2001: [1.0] * 25})
        assert convergence.sigma_trend(points)["available"] is False


class TestBetaConvergence:
    """Связь темпа роста с исходным уровнем."""

    def observations(self, panel: dict[int, list[float | None]]) -> list[dict[str, object]]:
        """Собрать пары «начальное значение — конечное значение» из панели."""
        first, last = min(panel), max(panel)
        return [
            {
                "code": f"R{index:02d}",
                "name": f"Территория {index}",
                "district": "FD-A" if index % 2 else "FD-B",
                "start": panel[first][index],
                "end": panel[last][index],
            }
            for index in range(len(panel[first]))
        ]

    def test_converging_data_gives_negative_coefficient(self) -> None:
        """При сближении коэффициент отрицателен и статистически значим."""
        panel = converging_panel()
        result = convergence.beta_convergence(
            self.observations(panel), first_year=2000, last_year=2020
        )
        assert result is not None
        assert result.beta < 0
        assert result.is_converging

    def test_parallel_growth_gives_no_convergence(self) -> None:
        """При равных темпах роста сближение не подтверждается."""
        panel = parallel_panel()
        result = convergence.beta_convergence(
            self.observations(panel), first_year=2000, last_year=2020
        )
        assert result is not None
        assert not result.is_converging

    def test_half_life_is_reported_for_converging_data(self) -> None:
        """Для сближающихся данных рассчитывается период полусокращения разрыва."""
        panel = converging_panel()
        result = convergence.beta_convergence(
            self.observations(panel), first_year=2000, last_year=2020
        )
        assert result is not None
        assert result.speed is not None
        assert result.half_life is not None
        assert result.half_life == pytest.approx(math.log(2) / result.speed)

    def test_short_span_is_rejected(self) -> None:
        """На отрезке короче пяти лет расчёт не выполняется."""
        panel = converging_panel()
        assert (
            convergence.beta_convergence(self.observations(panel), first_year=2000, last_year=2002)
            is None
        )

    def test_few_territories_are_rejected(self) -> None:
        """По десяти территориям регрессия сближения не оценивается."""
        panel = converging_panel()
        assert (
            convergence.beta_convergence(
                self.observations(panel)[:10], first_year=2000, last_year=2020
            )
            is None
        )

    def test_conditional_model_adds_district_variables(self) -> None:
        """В условной постановке в модель входят фиктивные переменные округов."""
        panel = converging_panel()
        result = convergence.beta_convergence(
            self.observations(panel), first_year=2000, last_year=2020, conditional=True
        )
        assert result is not None
        assert result.conditional is True
        assert len(result.fit.coefficients) > 2

    def test_points_carry_both_years(self) -> None:
        """Каждая точка несёт исходное и конечное значение для диаграммы."""
        panel = converging_panel()
        result = convergence.beta_convergence(
            self.observations(panel), first_year=2000, last_year=2020
        )
        assert result is not None
        assert all({"start", "end", "x", "y"} <= set(point) for point in result.points)


class TestConvergenceSpeed:
    """Скорость сближения и период полусокращения."""

    def test_positive_coefficient_has_no_speed(self) -> None:
        """При расхождении скорость сближения не определена."""
        assert convergence.convergence_speed(0.01, 20) == (None, None)

    def test_speed_matches_definition(self) -> None:
        """Скорость согласуется с определением через коэффициент регрессии."""
        beta, span = -0.02, 20
        speed, _ = convergence.convergence_speed(beta, span)
        assert speed is not None
        assert -(1 - math.exp(-speed * span)) / span == pytest.approx(beta)

    def test_negligible_speed_hides_half_life(self) -> None:
        """Период полусокращения в сотни лет равнозначен его отсутствию."""
        _, half_life = convergence.convergence_speed(-1e-5, 20)
        assert half_life is None
