"""Годовые значения выпуска: опубликованные, средние, реальная зарплата, на жителя."""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from apps.sources.annual import Link, derive, links
from apps.sources.base import PARSED_COLUMNS


def _frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    defaults = {"territory_code": "RU-MOW", "hidden": False, "preliminary": False}
    return pd.DataFrame([{**defaults, **row} for row in rows], columns=list(PARSED_COLUMNS))


def _months(
    measure: str, year: int, values: list[float | None], **extra: Any
) -> list[dict[str, Any]]:
    return [
        {
            "measure": measure,
            "year": year,
            "period_kind": "month",
            "period": month,
            "value": value,
            "hidden": value is None,
            **extra,
        }
        for month, value in enumerate(values, start=1)
    ]


def _link(method: str, measure: str = "wage", **extra: Any) -> Link:
    return Link(series="X:00", source="rosstat_bulletin", measure=measure, method=method, **extra)


class TestPublished:
    """Опубликованное годовое значение берётся как есть."""

    def test_year_total_and_december(self) -> None:
        frame = _frame(
            [
                {"measure": "wage", "year": 2025, "period_kind": "ytd", "period": 11, "value": 1.0},
                {"measure": "wage", "year": 2025, "period_kind": "ytd", "period": 12, "value": 2.0},
                {
                    "measure": "wage",
                    "year": 2025,
                    "period_kind": "month",
                    "period": 12,
                    "value": 3.0,
                },
            ]
        )
        total = derive(frame, _link("year_total"))
        assert total[["year", "value", "computed"]].to_dict("records") == [
            {"year": 2025, "value": 2.0, "computed": False}
        ]
        assert derive(frame, _link("december"))["value"].tolist() == [3.0]

    def test_hidden_value_stays_hidden(self) -> None:
        frame = _frame(
            [
                {
                    "measure": "wage",
                    "year": 2025,
                    "period_kind": "quarter",
                    "period": 4,
                    "value": None,
                    "hidden": True,
                }
            ]
        )
        result = derive(frame, _link("quarter_end"))
        assert bool(result["hidden"].iloc[0])
        assert pd.isna(result["value"].iloc[0])


class TestMeans:
    """Среднее считается только по полному году."""

    def test_mean_of_months(self) -> None:
        frame = _frame(_months("wage", 2025, [float(month) for month in range(1, 13)]))
        result = derive(frame, _link("mean_months"))
        assert result["value"].tolist() == [6.5]
        assert bool(result["computed"].iloc[0])

    def test_incomplete_year_is_dropped(self) -> None:
        frame = _frame(_months("wage", 2026, [1.0] * 7))
        assert derive(frame, _link("mean_months")).empty

    def test_hidden_month_hides_the_year(self) -> None:
        frame = _frame(_months("wage", 2025, [1.0] * 11 + [None]))
        result = derive(frame, _link("mean_months"))
        assert bool(result["hidden"].iloc[0])
        assert pd.isna(result["value"].iloc[0])

    def test_preliminary_month_marks_the_year(self) -> None:
        rows = _months("wage", 2025, [1.0] * 12)
        rows[-1]["preliminary"] = True
        assert bool(derive(_frame(rows), _link("mean_months"))["preliminary"].iloc[0])


class TestRealWage:
    """Рост номинальной зарплаты, делённый на рост среднегодовых цен."""

    def test_formula(self) -> None:
        # Цены за год выросли ровно на 10 %: декабрь к декабрю — 110, каждый месяц —
        # тот же уровень, что год назад, умноженный на 1,1.
        before = [100.0 + month for month in range(1, 13)]
        now = [(100.0 + month) * 1.1 / 1.12 for month in range(1, 13)]
        rows = [
            *_months("wage", 2024, [100.0] * 12),
            *_months("wage", 2025, [121.0] * 12),
            *_months("cpi_december", 2024, before),
            *_months("cpi_december", 2025, now),
        ]
        result = derive(_frame(rows), _link("real_wage", deflator="cpi_december"))
        assert result["year"].tolist() == [2025]
        # 121 / 100 = 1,21 номинально; цены в среднем +10 % → реально 110 %.
        assert result["value"].iloc[0] == pytest.approx(110.0)

    def test_needs_both_years(self) -> None:
        rows = [*_months("wage", 2025, [1.0] * 12), *_months("cpi_december", 2025, [101.0] * 12)]
        assert derive(_frame(rows), _link("real_wage", deflator="cpi_december")).empty


class TestPerCapita:
    """На жителя — численность набора; после последнего её года знаменатель заморожен."""

    def test_frozen_denominator(self) -> None:
        frame = _frame(
            [
                {
                    "measure": "retail",
                    "year": year,
                    "period_kind": "ytd",
                    "period": 12,
                    "value": value,
                }
                for year, value in ((2024, 200.0), (2025, 300.0))
            ]
        )
        population = pd.DataFrame(
            {"territory_code": ["RU-MOW", "RU-MOW"], "year": [2023, 2024], "population": [1.9, 2.0]}
        )
        result = derive(frame, _link("per_capita", measure="retail"), population).set_index("year")
        assert result.loc[2024, "value"] == pytest.approx(100.0)
        assert not bool(result.loc[2024, "frozen"])
        assert result.loc[2025, "value"] == pytest.approx(150.0)
        assert bool(result.loc[2025, "frozen"])
        assert result.loc[2025, "base_year"] == 2024

    def test_without_population_nothing(self) -> None:
        frame = _frame(
            [{"measure": "retail", "year": 2025, "period_kind": "ytd", "period": 12, "value": 1.0}]
        )
        assert derive(frame, _link("per_capita", measure="retail"), None).empty


class TestLinks:
    """Справочник связей и допуск сверки."""

    def test_reference_file(self) -> None:
        found = links()
        keys = [link.series for link in found]
        assert len(keys) == len(set(keys))
        assert "Y477110421:00" not in keys  # занятость: другое определение
        per_capita = [link for link in found if link.method == "per_capita"]
        assert per_capita
        assert all(link.total_series for link in per_capita)
        assert all(link.deflator for link in found if link.method == "real_wage")
        assert all(
            (link.tolerance_points is None) != (link.tolerance_relative is None) for link in found
        )

    def test_tolerance(self) -> None:
        relative = _link("year_total", tolerance_relative=0.01)
        assert relative.within(100.9, 100.0)
        assert not relative.within(101.1, 100.0)
        assert relative.deviation(101.0, 100.0) == pytest.approx(0.01)
        points = _link("annual", tolerance_points=0.15)
        assert points.within(2.6, 2.5)
        assert not points.within(2.7, 2.5)
        assert points.deviation(2.7, 2.5) == pytest.approx(0.2)
