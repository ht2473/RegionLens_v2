"""
Расчёты анализа против эталона: замороженные данные склада по 85 субъектам и ответы,
посчитанные независимо формулами из учебников (``scripts/reference_fixture.py``).

Средний денежный доход на человека и ВРП на душу. Расхождение больше 10⁻⁹ в долях — ошибка
расчёта, а не округление.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from apps.analytics.core import convergence, correlation, inequality, normalization, spatial
from apps.maps.classification import classify

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "reference" / "regions.json"
DATA: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
EXPECTED = DATA["expected"]
YEARS = DATA["years"]
LAST = DATA["income"][str(YEARS["last"])]
CODES = sorted(LAST)
VALUES = [LAST[code] for code in CODES]


def close(found: float | None, expected: float) -> bool:
    return found is not None and found == pytest.approx(expected, rel=1e-9, abs=1e-12)


def test_fixture_covers_all_regions() -> None:
    assert len(CODES) == 85
    assert YEARS["first"] < YEARS["both"] <= YEARS["last"]


@pytest.mark.parametrize("measure", ["gini", "theil", "atkinson", "cv", "decile"])
def test_inequality(measure: str) -> None:
    found = inequality.measure_set(VALUES)["by_code"][measure].value
    assert close(found, EXPECTED["inequality"][measure])


def test_correlation() -> None:
    income, grp = DATA["income"][str(YEARS["both"])], DATA["grp"]
    common = [code for code in sorted(income) if code in grp]
    first, second = [income[code] for code in common], [grp[code] for code in common]
    spearman = correlation.correlate(first, second, "spearman")
    pearson = correlation.correlate(first, second, "pearson")
    assert close(spearman.coefficient, EXPECTED["correlation"]["spearman"])
    assert close(spearman.p_value, EXPECTED["correlation"]["spearman_p"])
    assert close(pearson.coefficient, EXPECTED["correlation"]["pearson"])


def test_benjamini_hochberg() -> None:
    raw = EXPECTED["correlation"]["bh_raw"]
    entries = [
        spatial.LocalMoran(
            code=str(number),
            name="",
            value=0.0,
            standardized=0.0,
            spatial_lag=0.0,
            local_index=0.0,
            p_value=p_value,
            quadrant="",
            neighbours=1,
        )
        for number, p_value in enumerate(raw)
    ]
    found = [entry.p_adjusted for entry in spatial.adjusted(entries)]
    assert all(
        close(a, b) for a, b in zip(found, EXPECTED["correlation"]["bh_adjusted"], strict=True)
    )


def test_moran() -> None:
    weights = spatial.build_matrix(CODES, {code: DATA["neighbours"][code] for code in CODES})
    found = spatial.global_moran(np.array(VALUES), weights)
    assert found is not None
    assert close(found.value, EXPECTED["spatial"]["moran"])
    assert close(found.expected, EXPECTED["spatial"]["expected"])


def test_convergence() -> None:
    first, last = YEARS["first"], YEARS["last"]
    start, end = DATA["income"][str(first)], DATA["income"][str(last)]
    observations = [
        {"code": code, "start": start[code], "end": end[code]}
        for code in sorted(start)
        if code in end
    ]
    beta = convergence.beta_convergence(observations, first_year=first, last_year=last)
    assert beta is not None
    assert close(beta.beta, EXPECTED["convergence"]["beta"])
    assert close(beta.speed, EXPECTED["convergence"]["speed"])
    sigma = convergence.sigma_series({last: list(end.values())})
    assert close(sigma[-1].std_log, EXPECTED["convergence"]["sigma_last"])


def test_map_classes() -> None:
    equal = classify(VALUES, method="equal", class_count=5)
    assert close(equal.breaks[1] - equal.breaks[0], EXPECTED["maps"]["equal_width"])
    # Верхний класс из одного выброса — свой: граница его начала совпадает с максимумом.
    jenks = classify(VALUES, method="jenks", class_count=5)
    expected = EXPECTED["maps"]["jenks"]
    assert len(jenks.breaks) == len(expected)
    assert all(close(a, b) for a, b in zip(jenks.breaks, expected, strict=True))


@pytest.mark.parametrize("method", ["minmax", "zscore"])
def test_normalization(method: str) -> None:
    found = normalization.normalize(VALUES, method=method).values[0]
    assert close(found, EXPECTED["normalization"][f"{method}_first"])
