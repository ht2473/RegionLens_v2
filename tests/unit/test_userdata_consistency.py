"""Проверки данных до сборки: нули, похожие на пропуски, сумма регионов, разряд, части."""

from __future__ import annotations

import json
from collections.abc import Iterable

import pytest
from django.conf import settings

from apps.userdata import cells, consistency
from apps.userdata.extract import Collector

pytestmark = pytest.mark.unit

COMMA = cells.NumberStyle(decimal=",", decimals=1)


def _regions() -> list[str]:
    payload = json.loads((settings.REFERENCE_DIR / "territories.json").read_bytes())
    return [record["code"] for record in payload["regions"]]


REGIONS = _regions()


def _collector(
    rows: Iterable[tuple[str, tuple[tuple[str, str], ...], str, int, object]],
) -> Collector:
    """Наблюдения: показатель, разрезы, код территории, год, ячейка."""
    collector = Collector(limit=10**6)
    for indicator, slices, code, year, raw in rows:
        collector.add(
            indicator=indicator,
            slices=slices,
            period="year",
            unit="",
            place=(code, None),
            year=year,
            cell=cells.parse(raw, COMMA),
        )
    return collector


def _panel(indicator: str, years: dict[int, list[object]], slices=()) -> list[tuple]:
    return [
        (indicator, slices, code, year, value)
        for year, values in years.items()
        for code, value in zip(REGIONS, values, strict=False)
    ]


class TestZeros:
    def test_zero_between_ordinary_years_is_asked(self) -> None:
        # «Население в городах с высоким загрязнением»: Алтайский край 48 → 0 → 48.
        values = {2014: [48.0] * 30, 2015: [0.0] + [48.0] * 29, 2016: [48.0] * 30}
        found = consistency.review(_collector(_panel("Загрязнение", values)))["zeros"]
        assert found[0]["between"] == 1
        assert found[0]["example"]["before"] == 48.0
        assert not found[0]["suggested"]

    def test_small_neighbours_do_not_make_a_placeholder(self) -> None:
        # Пробы почв: 0,2 → 0 → 0,5 при медиане 10 — обычный ноль, а не заглушка.
        values = {2019: [0.2] + [10.0] * 29, 2020: [0.0] + [10.0] * 29, 2021: [0.5] + [10.0] * 29}
        assert consistency.review(_collector(_panel("Пробы", values)))["zeros"] == []

    def test_year_of_zeros_is_suggested(self) -> None:
        values = {2019: [5.0] * 30, 2020: [0.0] * 30, 2021: [6.0] * 30}
        found = consistency.review(_collector(_panel("Отходы", values)))["zeros"]
        assert found[0]["years"] == [2020]
        assert found[0]["suggested"]

    def test_many_zeros_are_asked(self) -> None:
        values = {2020: [0.0] * 20 + [3.0] * 10}
        found = consistency.review(_collector(_panel("Свалки", values)))["zeros"]
        assert found[0]["zeros"] == 20

    def test_dash_and_below_unit_are_not_placeholders(self) -> None:
        values = {2019: [5.0] * 30, 2020: ["–"] * 15 + ["0,0"] * 15, 2021: [6.0] * 30}
        assert consistency.review(_collector(_panel("Ввод", values)))["zeros"] == []


class TestChecks:
    def test_sum_of_regions_against_country(self) -> None:
        rows = _panel("Выбросы", {2020: [10.0] * 85})
        rows.append(("Выбросы", (), "RU", 2020, 800.0))
        checks = consistency.review(_collector(rows))["checks"]
        assert checks[0]["kind"] == consistency.COUNTRY
        assert checks[0]["gap"] == pytest.approx(0.0625)

    def test_country_in_larger_units_is_not_a_gap(self) -> None:
        rows = _panel("Выбросы", {2020: [10.0] * 85})
        rows.append(("Выбросы", (), "RU", 2020, 0.85))
        assert consistency.review(_collector(rows))["checks"] == []

    def test_scale_jump_between_years(self) -> None:
        rows = _panel("Доходы", {2020: [1.5] * 30, 2021: [1600.0] * 30})
        checks = consistency.review(_collector(rows))["checks"]
        assert checks[0]["kind"] == consistency.SCALE
        assert (checks[0]["step"], checks[0]["up"]) == (1000, True)

    def test_parts_above_total(self) -> None:
        rows = [
            *_panel("Население", {2020: [100.0] * 30}, (("Пол", "Всего"),)),
            *_panel("Население", {2020: [60.0] * 30}, (("Пол", "мужчины"),)),
            *_panel("Население", {2020: [50.0] * 30}, (("Пол", "женщины"),)),
            *_panel("Население", {2020: [40.0] * 30}, (("Пол", "из них пенсионеры"),)),
        ]
        checks = consistency.review(_collector(rows))["checks"]
        assert checks[0]["kind"] == consistency.PARTS
        assert checks[0]["places"] == 30

    def test_consistent_table_has_no_checks(self) -> None:
        rows = [
            *_panel("Население", {2020: [100.0] * 85}, (("Пол", "Всего"),)),
            *_panel("Население", {2020: [45.0] * 85}, (("Пол", "мужчины"),)),
            *_panel("Население", {2020: [55.0] * 85}, (("Пол", "женщины"),)),
            ("Население", (("Пол", "Всего"),), "RU", 2020, 8500.0),
        ]
        assert consistency.review(_collector(rows))["checks"] == []


class TestCollectorMarks:
    def test_secret_footnote_hides_the_value(self) -> None:
        collector = Collector(limit=100, secret_notes=frozenset({7}))
        for code, raw in (("RU-BEL", "…7)"), ("RU-BRY", "…"), ("RU-VLA", "–"), ("RU-VOR", "0,0")):
            collector.add(
                indicator="Добыча",
                slices=(),
                period="year",
                unit="",
                place=(code, None),
                year=2020,
                cell=cells.parse(raw, COMMA),
            )
        assert collector.qualities == [2, 1, 4, 0]
        assert collector.flags == [0, 0, 0, 16]
        assert dict(collector.marks) == {"hidden": 1, "absent": 1, "below_unit": 1}
