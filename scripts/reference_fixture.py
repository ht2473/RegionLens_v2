"""
Эталон расчётов анализа: замороженные данные склада и ответы, посчитанные независимо.

Данные — средний денежный доход на человека и ВРП на душу по субъектам за годы, где
значения есть хотя бы у 80 субъектов, и соседство субъектов. Ответы — формулами
из учебников на numpy и scipy, без функций сайта: индекс Джини, Тейла и Аткинсона,
коэффициент вариации, децильный коэффициент, корреляция Спирмена и Пирсона, поправка
Бенджамини — Хохберга, индекс Морана, бета- и сигма-сближение, границы классов карты,
нормировка. Проверка ``tests/unit/test_reference_calculations.py`` сверяет с ними функции
сайта. Пересобирать — только если сменились сами данные эталона:

    .venv/Scripts/python.exe -X utf8 scripts/reference_fixture.py
"""

from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")

import django

django.setup()

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy import stats  # noqa: E402

from apps.analytics.selectors import neighbour_map, region_rows  # noqa: E402
from apps.warehouse.queries import region_panel  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "reference" / "regions.json"
INCOME = "Y477110374:00"
GRP = "Y477110006:00"
MIN_REGIONS = 80
# Сырые p-значения для поправки Бенджамини — Хохберга: пример из учебного разбора.
RAW_P = [0.001, 0.008, 0.039, 0.041, 0.042, 0.06, 0.074, 0.205, 0.212, 0.216]


def full_years(panel: dict[int, dict[str, float | None]], codes: list[str]) -> list[int]:
    return sorted(
        year
        for year, row in panel.items()
        if sum(1 for code in codes if row.get(code) is not None) >= MIN_REGIONS
    )


def jenks(data: np.ndarray, classes: int) -> list[float]:
    """Точное разбиение Дженкса перебором по матрицам сумм квадратов (O(k n²))."""
    size = data.size
    csum = np.concatenate(([0.0], np.cumsum(data)))
    csq = np.concatenate(([0.0], np.cumsum(data * data)))
    ssd = np.full((size + 1, size + 1), np.inf)
    for i in range(size):
        for j in range(i + 1, size + 1):
            total = csum[j] - csum[i]
            ssd[i, j] = csq[j] - csq[i] - total * total / (j - i)
    best = np.full((classes + 1, size + 1), np.inf)
    arg = np.zeros((classes + 1, size + 1), dtype=int)
    best[0, 0] = 0.0
    for count in range(1, classes + 1):
        for j in range(count, size + 1):
            candidates = best[count - 1, :j] + ssd[:j, j]
            arg[count, j] = int(np.argmin(candidates))
            best[count, j] = candidates[arg[count, j]]
    cuts, j = [], size
    for count in range(classes, 0, -1):
        cuts.append(float(data[arg[count, j]]))
        j = arg[count, j]
    return [*sorted(cuts), float(data[-1])]


def main() -> None:
    codes = [row["code"] for row in region_rows()]
    income, grp = region_panel(INCOME), region_panel(GRP)
    years = full_years(income, codes)
    first, last = years[0], years[-1]
    both = max(year for year in full_years(grp, codes) if year in years)
    keep = sorted({first, both, last})
    panel = {
        str(year): {
            code: income[year][code] for code in codes if income[year].get(code) is not None
        }
        for year in keep
    }
    grp_year = {code: grp[both][code] for code in codes if grp[both].get(code) is not None}
    adjacency = neighbour_map("all")
    neighbours = {code: sorted(adjacency.get(code, [])) for code in codes}

    # Неравенство — по последнему году.
    values = panel[str(last)]
    x = np.array([values[code] for code in sorted(values)], dtype=float)
    n = x.size
    ratio = x / x.mean()
    inequality = {
        "gini": float(np.abs(x[:, None] - x[None, :]).sum() / (2 * n * n * x.mean())),
        "theil": float(np.mean(ratio * np.log(ratio))),
        "atkinson": 1 - float(np.mean(ratio**0.5)) ** 2,
        # Выборочная σ (делитель n − 1), как везде на сайте без весов.
        "cv": float(np.std(x, ddof=1) / x.mean()),
        "decile": float(np.percentile(x, 90) / np.percentile(x, 10)),
    }

    # Связь дохода и ВРП на душу.
    common = [code for code in codes if code in panel[str(both)] and code in grp_year]
    a = np.array([panel[str(both)][code] for code in common])
    b = np.array([grp_year[code] for code in common])
    rho = float(np.corrcoef(pd.Series(a).rank(), pd.Series(b).rank())[0, 1])
    t = rho * math.sqrt((len(a) - 2) / (1 - rho * rho))
    order = np.argsort(RAW_P)
    adjusted = [0.0] * len(RAW_P)
    running = 1.0
    for rank in range(len(RAW_P), 0, -1):
        index = int(order[rank - 1])
        running = min(running, RAW_P[index] * len(RAW_P) / rank)
        adjusted[index] = running
    correlation = {
        "spearman": rho,
        "spearman_p": float(2 * stats.t.sf(abs(t), len(a) - 2)),
        "pearson": float(np.corrcoef(a, b)[0, 1]),
        "bh_raw": RAW_P,
        "bh_adjusted": adjusted,
    }

    # Индекс Морана: веса по соседству, строки нормированы к единице.
    moran_codes = sorted(values)
    index = {code: number for number, code in enumerate(moran_codes)}
    w = np.zeros((len(moran_codes), len(moran_codes)))
    for code in moran_codes:
        found = [other for other in neighbours[code] if other in index]
        for other in found:
            w[index[code], index[other]] = 1 / len(found)
    z = np.array([values[code] for code in moran_codes]) - np.mean(list(values.values()))
    spatial = {
        "moran": float(len(moran_codes) / w.sum() * float(z @ w @ z) / float(z @ z)),
        "expected": -1 / (len(moran_codes) - 1),
    }

    # Сближение: бета — наклон роста по логарифму начального уровня, сигма — σ(ln x).
    start, end = panel[str(first)], panel[str(last)]
    both_years = [code for code in codes if code in start and code in end]
    x0 = np.log([start[code] for code in both_years])
    growth = (np.log([end[code] for code in both_years]) - x0) / (last - first)
    slope = float(stats.linregress(x0, growth).slope)
    convergence = {
        "beta": slope,
        "speed": -math.log(1 + slope * (last - first)) / (last - first),
        "sigma_last": float(np.std(np.log(list(end.values())), ddof=1)),
    }

    # Классы карты и нормировка — по последнему году.
    ordered = np.sort(x)
    maps = {
        "equal_width": float((ordered[-1] - ordered[0]) / 5),
        "jenks": jenks(ordered, 5),
    }
    first_code = sorted(values)[0]
    normalization = {
        "minmax_first": float(100 * (values[first_code] - x.min()) / (x.max() - x.min())),
        "zscore_first": float(
            50 + 50 * np.clip((values[first_code] - x.mean()) / x.std(ddof=1), -3, 3) / 3
        ),
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "about": "Эталон расчётов анализа: данные склада и независимые ответы "
        "(scripts/reference_fixture.py).",
        "series": {"income": INCOME, "grp": GRP},
        "years": {"first": first, "both": both, "last": last},
        "income": panel,
        "grp": grp_year,
        "neighbours": neighbours,
        "expected": {
            "inequality": inequality,
            "correlation": correlation,
            "spatial": spatial,
            "convergence": convergence,
            "maps": maps,
            "normalization": normalization,
        },
    }
    OUT.write_text(json.dumps(document, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"эталон: {OUT}, субъектов {n}, годы {first}, {both}, {last}")


if __name__ == "__main__":
    main()
