"""
Эталон расчётов по признанным библиотекам: ответы esda (PySAL), statsmodels и pymcdm на тех же
данных, что и ``tests/fixtures/reference/regions.json``.

Библиотеки нужны только для пересборки эталона и в зависимости проекта не входят: запуск — в
отдельном окружении, без Django и без функций сайта:

    python -m venv refenv
    refenv/Scripts/python.exe -m pip install esda libpysal statsmodels pymcdm
    refenv/Scripts/python.exe -X utf8 scripts/reference_libraries.py

Проверка ``tests/unit/test_reference_libraries.py`` сверяет с этими ответами функции сайта.
Пересобирать — только если сменились данные эталона.
"""

from __future__ import annotations

import json
import math
from importlib.metadata import version
from pathlib import Path

import esda
import libpysal
import numpy as np
import pymcdm
import statsmodels.api as sm

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "reference"
DATA = json.loads((FIXTURES / "regions.json").read_text(encoding="utf-8"))
OUT = FIXTURES / "libraries.json"
PERMUTATIONS = 9999
SEED = 20261030


def rounded(values: object) -> list[float]:
    return [float(value) for value in np.asarray(values, dtype=float).ravel()]


def spatial_answers(codes: list[str], values: np.ndarray) -> dict[str, object]:
    """Индекс Морана и локальные индексы на тех же соседях со строчной нормировкой весов."""
    neighbours = {code: [n for n in DATA["neighbours"][code] if n in codes] for code in codes}
    weights = libpysal.weights.W(neighbours, id_order=codes, silence_warnings=True)
    weights.transform = "R"
    np.random.seed(SEED)
    overall = esda.Moran(values, weights, permutations=PERMUTATIONS, two_tailed=True)
    local = esda.Moran_Local(
        values, weights, permutations=PERMUTATIONS, keep_simulations=True, seed=SEED, n_jobs=1
    )
    extreme = np.sum(np.abs(local.sim) >= np.abs(local.Is), axis=0)
    return {
        "moran": float(overall.I),
        "expected": float(overall.EI),
        "p_two_sided": float(overall.p_sim),
        "islands": list(weights.islands),
        # esda делит на n − 1, Анселин (1995) и сайт — на n; здесь — как у esda.
        "local": rounded(local.Is),
        "quadrant": [int(value) for value in local.q],
        "local_p": rounded((extreme + 1) / (PERMUTATIONS + 1)),
    }


def regression_answers(y: np.ndarray, x: np.ndarray) -> dict[str, object]:
    """Обычные и устойчивые (HC3) ошибки; распределение t с n − k степенями свободы, как у сайта."""
    design = sm.add_constant(x)
    plain = sm.OLS(y, design).fit()
    robust = sm.OLS(y, design).fit(cov_type="HC3", use_t=True)
    return {
        "params": rounded(plain.params),
        "bse": rounded(plain.bse),
        "tvalues": rounded(plain.tvalues),
        "pvalues": rounded(plain.pvalues),
        "r_squared": float(plain.rsquared),
        "adjusted": float(plain.rsquared_adj),
        "f_value": float(plain.fvalue),
        "f_p_value": float(plain.f_pvalue),
        "residual_std": math.sqrt(float(plain.mse_resid)),
        "nobs": int(plain.nobs),
        "robust_bse": rounded(robust.bse),
        "robust_tvalues": rounded(robust.tvalues),
        "robust_pvalues": rounded(robust.pvalues),
    }


def choice_answers(matrix: np.ndarray) -> dict[str, object]:
    """Веса и свёртка: на вход — матрица, уже приведённая к 0–1, как на сайте."""
    # Библиотека требует значения больше нуля: минимум столбца у матрицы 0–1 равен нулю.
    weights = pymcdm.weights.entropy_weights(matrix + 1e-12)
    critic = pymcdm.weights.critic_weights(matrix)
    method = pymcdm.methods.TOPSIS(normalization_function=lambda column, cost: column)  # noqa: ARG005
    equal = np.full(matrix.shape[1], 1 / matrix.shape[1])
    types = np.ones(matrix.shape[1], dtype=int)
    return {
        "entropy": rounded(weights),
        "critic": rounded(critic),
        "topsis_equal": rounded(method(matrix, equal, types)),
        "topsis_critic": rounded(method(matrix, critic, types)),
    }


def main() -> None:
    years = DATA["years"]
    income_last = DATA["income"][str(years["last"])]
    income_both = DATA["income"][str(years["both"])]
    income_first = DATA["income"][str(years["first"])]
    grp = DATA["grp"]
    codes = sorted(income_last)
    answers: dict[str, object] = {
        "about": (
            "Ответы библиотек на данные regions.json; "
            "собраны scripts/reference_libraries.py, в проверках библиотеки не нужны."
        ),
        "versions": {name: version(name) for name in ("esda", "libpysal", "statsmodels", "pymcdm")},
        "permutations": PERMUTATIONS,
        "spatial": spatial_answers(codes, np.array([income_last[code] for code in codes])),
    }

    both = sorted(code for code in income_both if code in grp and code in income_first)
    level_y = np.array([income_both[code] for code in both])
    level_x = np.array([grp[code] for code in both])
    start = np.array([income_first[code] for code in both])
    answers["regression"] = {
        "codes": both,
        "simple": regression_answers(level_y, level_x.reshape(-1, 1)),
        "logs": regression_answers(
            np.log(level_y), np.column_stack([np.log(level_x), np.log(start)])
        ),
    }

    raw = np.column_stack([level_y, level_x, start])
    scaled = (raw - raw.min(axis=0)) / (raw.max(axis=0) - raw.min(axis=0))
    answers["choice"] = {"codes": both, **choice_answers(scaled)}

    OUT.write_text(json.dumps(answers, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("записано:", OUT)


main()
