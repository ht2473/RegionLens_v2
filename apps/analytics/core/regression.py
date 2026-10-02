"""
Метод наименьших квадратов с оценкой значимости и робастными ошибками HC3.

Свой код вместо эконометрических пакетов: нужен один вид модели без долгой загрузки.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from django.utils.translation import gettext
from scipy import stats

# Уровень значимости, начиная с которого связь считается статистически подтверждённой.
SIGNIFICANCE_LEVEL = 0.05

# Минимальное число наблюдений сверх числа оцениваемых коэффициентов.
MIN_DEGREES_OF_FREEDOM = 3

# Минимальное число групп, при котором фиктивные переменные вообще имеют смысл.
MIN_GROUPS_FOR_DUMMIES = 2


@dataclass(frozen=True, slots=True)
class Coefficient:
    """Оценка одного коэффициента модели."""

    name: str
    value: float
    std_error: float
    t_value: float
    p_value: float

    @property
    def is_significant(self) -> bool:
        """Признак статистической значимости на уровне 5 %."""
        return self.p_value < SIGNIFICANCE_LEVEL

    @property
    def confidence_low(self) -> float:
        """Нижняя граница приблизительного 95-процентного интервала."""
        return self.value - 1.96 * self.std_error

    @property
    def confidence_high(self) -> float:
        """Верхняя граница приблизительного 95-процентного интервала."""
        return self.value + 1.96 * self.std_error


@dataclass(frozen=True, slots=True)
class Fit:
    """Результат оценивания линейной модели."""

    coefficients: list[Coefficient]
    r_squared: float
    adjusted_r_squared: float
    observations: int
    residual_std: float
    f_value: float
    f_p_value: float
    robust: bool
    fitted: list[float]
    residuals: list[float]

    @property
    def intercept(self) -> Coefficient:
        """Свободный член модели."""
        return self.coefficients[0]

    @property
    def slope(self) -> Coefficient:
        """Первый объясняющий коэффициент — наклон при основной переменной."""
        return self.coefficients[1]

    def predict(self, values: list[float]) -> list[float]:
        """Вычислить значения парной модели в заданных точках (для линии на графике)."""
        intercept = self.coefficients[0].value
        slope = self.coefficients[1].value
        return [intercept + slope * value for value in values]


def ols(
    y: np.ndarray,
    x: np.ndarray,
    names: list[str],
    *,
    robust: bool = False,
) -> Fit | None:
    """
    Оценить линейную модель; столбец единиц добавляется внутри.

    ``None`` — данных мало или матрица вырождена: нулевые коэффициенты утверждали бы
    отсутствие связи.
    """
    y = np.asarray(y, dtype=float)
    x = np.asarray(x, dtype=float)
    if x.ndim == 1:
        x = x.reshape(-1, 1)

    usable = np.isfinite(y) & np.all(np.isfinite(x), axis=1)
    y = y[usable]
    x = x[usable]

    observations, predictors = x.shape[0], x.shape[1] + 1
    # Постоянный столбец (фиктивная переменная с одним значением) делает матрицу вырожденной.
    if observations - predictors < MIN_DEGREES_OF_FREEDOM or np.any(np.std(x, axis=0) == 0):
        return None

    design = np.column_stack([np.ones(observations), x])
    try:
        beta, _, rank, _ = np.linalg.lstsq(design, y, rcond=None)
        gram_inverse = np.linalg.inv(design.T @ design)
    except np.linalg.LinAlgError:
        return None
    if rank < predictors:
        return None

    fitted = design @ beta
    residuals = y - fitted
    degrees = observations - predictors

    residual_sum = float(residuals @ residuals)
    total_sum = float(np.sum((y - np.mean(y)) ** 2))
    if total_sum <= 0:
        return None

    if robust:
        # HC3: остаток делится на (1 − h) — влияние наблюдения; при 85 регионах и отдельных
        # далёких точках HC1 занижает ошибку (Long, Ervin, 2000).
        leverage = np.einsum("ij,jk,ik->i", design, gram_inverse, design)
        weights = residuals**2 / np.clip(1.0 - leverage, 1e-12, None) ** 2
        meat = design.T @ (design * weights[:, None])
        covariance = gram_inverse @ meat @ gram_inverse
    else:
        covariance = gram_inverse * (residual_sum / degrees)

    std_errors = np.sqrt(np.maximum(np.diag(covariance), 0.0))
    coefficients: list[Coefficient] = []
    for index, label in enumerate([gettext("Свободный член"), *names]):
        error = float(std_errors[index])
        t_value = float(beta[index] / error) if error > 0 else 0.0
        p_value = float(2 * stats.t.sf(abs(t_value), degrees)) if error > 0 else 1.0
        coefficients.append(
            Coefficient(
                name=label,
                value=float(beta[index]),
                std_error=error,
                t_value=t_value,
                p_value=p_value,
            )
        )

    r_squared = 1 - residual_sum / total_sum
    adjusted = 1 - (1 - r_squared) * (observations - 1) / degrees
    f_value = (
        (r_squared / (predictors - 1)) / ((1 - r_squared) / degrees)
        if r_squared < 1
        else float("inf")
    )
    f_p_value = float(stats.f.sf(f_value, predictors - 1, degrees)) if np.isfinite(f_value) else 0.0

    return Fit(
        coefficients=coefficients,
        r_squared=float(r_squared),
        adjusted_r_squared=float(adjusted),
        observations=int(observations),
        residual_std=float(np.sqrt(residual_sum / degrees)),
        f_value=float(f_value),
        f_p_value=f_p_value,
        robust=robust,
        fitted=[float(value) for value in fitted],
        residuals=[float(value) for value in residuals],
    )


def dummy_matrix(groups: list[str]) -> tuple[np.ndarray, list[str]]:
    """Построить матрицу фиктивных переменных для групп территорий без базовой группы."""
    codes = sorted({code for code in groups if code})
    if len(codes) < MIN_GROUPS_FOR_DUMMIES:
        return np.empty((len(groups), 0)), []

    # Базовой становится первая по алфавиту группа: её столбец не создаётся.
    rest = codes[1:]
    matrix = np.zeros((len(groups), len(rest)))
    for column, code in enumerate(rest):
        for row, value in enumerate(groups):
            if value == code:
                matrix[row, column] = 1.0
    return matrix, [gettext("Округ: %(code)s") % {"code": code} for code in rest]
