"""
Расчёты анализа против признанных библиотек: esda (PySAL) — индекс Морана и локальные индексы,
statsmodels — регрессия, pymcdm — веса и TOPSIS. Ответы заморожены в
``tests/fixtures/reference/libraries.json`` (``scripts/reference_libraries.py``), сами библиотеки
для проверки не нужны.

Расчёт без случайности должен совпасть до 10⁻⁹. Значимость по перестановкам — в пределах
случайности: у двух независимых прогонов по 9 999 перестановок разница p меньше 0,02.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from apps.analytics.core import aggregation, regression, spatial, weighting

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "reference"
DATA: dict[str, Any] = json.loads((FIXTURES / "regions.json").read_text(encoding="utf-8"))
LIBRARIES: dict[str, Any] = json.loads((FIXTURES / "libraries.json").read_text(encoding="utf-8"))
YEARS = DATA["years"]
LAST = DATA["income"][str(YEARS["last"])]
CODES = sorted(LAST)
VALUES = np.array([LAST[code] for code in CODES])
WEIGHTS = spatial.build_matrix(CODES, {code: DATA["neighbours"][code] for code in CODES})
BOTH = LIBRARIES["regression"]["codes"]
INCOME = np.array([DATA["income"][str(YEARS["both"])][code] for code in BOTH])
GRP = np.array([DATA["grp"][code] for code in BOTH])
START = np.array([DATA["income"][str(YEARS["first"])][code] for code in BOTH])
# Перестановки: разница двух независимых оценок p.
P_TOLERANCE = 0.02
# Квадранты esda: 1 — высокое среди высоких, 2 — низкое среди высоких, 3 — низкое среди низких,
# 4 — высокое среди низких.
QUADRANTS = {1: ("high", "high"), 2: ("low", "high"), 3: ("low", "low"), 4: ("high", "low")}


def same(found: list[float], expected: list[float], *, tolerance: float = 1e-9) -> None:
    assert found == pytest.approx(expected, rel=tolerance, abs=1e-12)


def test_global_moran() -> None:
    expected = LIBRARIES["spatial"]
    found = spatial.global_moran(VALUES, WEIGHTS)
    assert found is not None
    same([found.value, found.expected], [expected["moran"], expected["expected"]])


def test_local_moran() -> None:
    """Значения — с поправкой на знаменатель: esda делит на n − 1, Анселин и сайт — на n."""
    expected = LIBRARIES["spatial"]
    names = {code: code for code in CODES}
    found = {entry.code: entry for entry in spatial.local_moran(VALUES, WEIGHTS, CODES, names)}
    scale = len(CODES) / (len(CODES) - 1)
    pairs = [
        (found[code], value, quadrant, p_value)
        for code, value, quadrant, p_value in zip(
            CODES, expected["local"], expected["quadrant"], expected["local_p"], strict=True
        )
        if code in found
    ]
    assert len(pairs) == len(CODES) - len(expected["islands"])
    same([entry.local_index for entry, *_ in pairs], [value * scale for _, value, *_ in pairs])
    for entry, _, quadrant, p_value in pairs:
        own, around = QUADRANTS[quadrant]
        assert (entry.standardized >= 0) == (own == "high")
        assert (entry.spatial_lag >= 0) == (around == "high")
        assert abs(entry.p_value - p_value) < P_TOLERANCE


@pytest.mark.parametrize(
    ("model", "y", "x"),
    [
        ("simple", INCOME, GRP.reshape(-1, 1)),
        ("logs", np.log(INCOME), np.column_stack([np.log(GRP), np.log(START)])),
    ],
)
def test_regression(model: str, y: np.ndarray, x: np.ndarray) -> None:
    expected = LIBRARIES["regression"][model]
    names = [f"x{number}" for number in range(x.shape[1])]
    plain = regression.ols(y, x, names)
    robust = regression.ols(y, x, names, robust=True)
    assert plain is not None
    assert robust is not None
    same([item.value for item in plain.coefficients], expected["params"])
    same([item.std_error for item in plain.coefficients], expected["bse"])
    same([item.t_value for item in plain.coefficients], expected["tvalues"])
    same([item.p_value for item in plain.coefficients], expected["pvalues"])
    same([item.std_error for item in robust.coefficients], expected["robust_bse"])
    same([item.t_value for item in robust.coefficients], expected["robust_tvalues"])
    same([item.p_value for item in robust.coefficients], expected["robust_pvalues"])
    same(
        [plain.r_squared, plain.adjusted_r_squared, plain.f_value, plain.f_p_value],
        [expected["r_squared"], expected["adjusted"], expected["f_value"], expected["f_p_value"]],
    )
    same([plain.residual_std], [expected["residual_std"]])
    assert plain.observations == expected["nobs"]


def _scaled() -> np.ndarray:
    raw = np.column_stack([INCOME, GRP, START])
    return (raw - raw.min(axis=0)) / (raw.max(axis=0) - raw.min(axis=0))


def test_weights() -> None:
    """Энтропия у сайта сдвигает значения на малую поправку, чтобы логарифм нуля не возникал."""
    expected = LIBRARIES["choice"]
    matrix = _scaled()
    entropy = weighting.build_weights(matrix, method="entropy")
    critic = weighting.build_weights(matrix, method="critic")
    assert entropy.values == pytest.approx(expected["entropy"], abs=1e-6)
    same(critic.values, expected["critic"])


@pytest.mark.parametrize(
    ("name", "weights"), [("topsis_equal", "equal"), ("topsis_critic", "critic")]
)
def test_topsis(name: str, weights: str) -> None:
    expected = LIBRARIES["choice"]
    matrix = _scaled()
    columns = matrix.shape[1]
    values = (
        [1 / columns] * columns
        if weights == "equal"
        else weighting.build_weights(matrix, method="critic").values
    )
    territories = [{"code": code, "name": code} for code in BOTH]
    scores = aggregation.aggregate(matrix, values, territories, method="topsis")
    found = {score.code: score.value for score in scores}
    same(
        [found[code] / aggregation.SCALE_MAX for code in BOTH],
        expected[name],
    )
