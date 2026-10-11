"""Ступени цвета тепловой таблицы мест: первые места — самая насыщенная ступень."""

from __future__ import annotations

from itertools import pairwise

from apps.rankings.panel import RANK_STEPS, _rank_step, _rank_table


def test_steps_split_places_into_equal_groups() -> None:
    """85 мест — семь групп по 13: первое место — ступень 7, последнее — 1."""
    assert _rank_step(1, 85) == RANK_STEPS
    assert _rank_step(13, 85) == RANK_STEPS
    assert _rank_step(14, 85) == RANK_STEPS - 1
    assert _rank_step(85, 85) == 1


def test_table_legend_covers_every_place() -> None:
    """Шкала под таблицей покрывает места от первого до последнего без пропусков."""
    rows = [{"territory_code": f"RU-{i}", "name": str(i)} for i in range(1, 86)]
    places = {f"RU-{i}": {2023: i, 2024: 86 - i} for i in range(1, 86)}
    table = _rank_table(rows, [2023, 2024], places, 85, {"RU-1"})
    bounds = [(item["lower"], item["upper"]) for item in table["legend"]]
    assert bounds[0][0] == 1
    assert bounds[-1][1] == 85
    assert all(upper + 1 == lower for (_, upper), (lower, _) in pairwise(bounds))
    # Порядок — по последнему году: у RU-85 в 2024 году первое место.
    assert table["rows"][0]["code"] == "RU-85"
    assert next(row for row in table["rows"] if row["code"] == "RU-1")["marked"]
