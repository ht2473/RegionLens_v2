"""
Проверки МНК на данных с известной зависимостью; неоцениваемая модель — ``None``,
а не нулевые коэффициенты.
"""

from __future__ import annotations

import numpy as np
import pytest

from apps.analytics.core import regression

pytestmark = pytest.mark.unit


class TestOls:
    """Оценивание линейной модели."""

    def test_recovers_known_coefficients(self) -> None:
        """Коэффициенты точной зависимости восстанавливаются без ошибки."""
        x = np.arange(20, dtype=float)
        y = 3.5 + 2.0 * x

        fit = regression.ols(y, x, ["x"])
        assert fit is not None
        assert fit.intercept.value == pytest.approx(3.5)
        assert fit.slope.value == pytest.approx(2.0)
        assert fit.r_squared == pytest.approx(1.0)

    def test_noise_lowers_explained_share(self) -> None:
        """Шум в данных снижает долю объяснённого разброса."""
        generator = np.random.default_rng(20260321)
        x = np.arange(60, dtype=float)
        y = 1.0 + 0.5 * x + generator.normal(0, 5, size=60)

        fit = regression.ols(y, x, ["x"])
        assert fit is not None
        assert 0.5 < fit.r_squared < 1.0
        assert fit.slope.is_significant

    def test_absent_relation_is_not_significant(self) -> None:
        """Связь, которой в данных нет, не признаётся значимой."""
        generator = np.random.default_rng(7)
        x = generator.normal(size=80)
        y = generator.normal(size=80)

        fit = regression.ols(y, x, ["x"])
        assert fit is not None
        assert not fit.slope.is_significant

    def test_short_sample_is_rejected(self) -> None:
        """По трём наблюдениям модель не оценивается."""
        assert regression.ols(np.array([1.0, 2.0, 3.0]), np.array([1.0, 2.0, 3.0]), ["x"]) is None

    def test_constant_predictor_is_rejected(self) -> None:
        """Постоянная объясняющая переменная делает матрицу вырожденной."""
        y = np.arange(20, dtype=float)
        x = np.ones(20)
        assert regression.ols(y, x, ["x"]) is None

    def test_missing_values_are_dropped(self) -> None:
        """Наблюдения с пропуском исключаются из оценивания."""
        x = np.arange(20, dtype=float)
        y = 1.0 + x
        y[3] = np.nan

        fit = regression.ols(y, x, ["x"])
        assert fit is not None
        assert fit.observations == 19

    def test_robust_errors_differ_under_heteroscedasticity(self) -> None:
        """При неоднородной дисперсии остатков робастная ошибка отличается от обычной."""
        generator = np.random.default_rng(11)
        x = np.linspace(1, 50, 120)
        # Разброс остатков растёт вместе со значением: типичная картина для регионов.
        y = 2.0 + 0.8 * x + generator.normal(0, 1, size=120) * x

        plain = regression.ols(y, x, ["x"])
        robust = regression.ols(y, x, ["x"], robust=True)
        assert plain is not None
        assert robust is not None
        assert robust.slope.std_error != pytest.approx(plain.slope.std_error, rel=0.05)

    def test_robust_errors_are_hc3(self) -> None:
        """Робастная ошибка — HC3: квадрат остатка делится на (1 − h)², не меньше HC0."""
        generator = np.random.default_rng(5)
        x = np.concatenate([np.linspace(1, 10, 30), [40.0]])
        y = 1.0 + 0.5 * x + generator.normal(0, 1, size=31) * x

        robust = regression.ols(y, x, ["x"], robust=True)
        assert robust is not None

        design = np.column_stack([np.ones(31), x])
        inverse = np.linalg.inv(design.T @ design)
        residuals = y - design @ np.linalg.lstsq(design, y, rcond=None)[0]
        leverage = np.diag(design @ inverse @ design.T)
        hc0 = inverse @ design.T @ np.diag(residuals**2) @ design @ inverse
        hc3 = inverse @ design.T @ np.diag(residuals**2 / (1 - leverage) ** 2) @ design @ inverse
        assert robust.slope.std_error == pytest.approx(np.sqrt(hc3[1, 1]))
        assert robust.slope.std_error > np.sqrt(hc0[1, 1])

    def test_multiple_predictors_are_supported(self) -> None:
        """Модель с несколькими объясняющими переменными оценивается целиком."""
        generator = np.random.default_rng(3)
        first = generator.normal(size=100)
        second = generator.normal(size=100)
        y = 1.0 + 2.0 * first - 3.0 * second

        fit = regression.ols(y, np.column_stack([first, second]), ["a", "b"])
        assert fit is not None
        assert len(fit.coefficients) == 3
        assert fit.coefficients[2].value == pytest.approx(-3.0)

    def test_predict_returns_line_points(self) -> None:
        """Модель возвращает значения в заданных точках для линии на графике."""
        x = np.arange(20, dtype=float)
        fit = regression.ols(1.0 + 2.0 * x, x, ["x"])
        assert fit is not None
        assert fit.predict([0.0, 10.0]) == pytest.approx([1.0, 21.0])


class TestDummyMatrix:
    """Фиктивные переменные групп."""

    def test_reference_group_has_no_column(self) -> None:
        """Первая по алфавиту группа становится базовой и столбца не получает."""
        matrix, names = regression.dummy_matrix(["B", "A", "C", "A"])
        assert matrix.shape == (4, 2)
        assert len(names) == 2

    def test_single_group_produces_no_columns(self) -> None:
        """Одна группа не порождает фиктивных переменных."""
        matrix, names = regression.dummy_matrix(["A", "A", "A"])
        assert matrix.shape[1] == 0
        assert names == []

    def test_membership_is_marked(self) -> None:
        """Принадлежность к группе отмечается единицей."""
        matrix, _ = regression.dummy_matrix(["A", "B"])
        assert matrix[0, 0] == 0.0
        assert matrix[1, 0] == 1.0
