"""
Помесячный слой: значения по месяцам и годовые значения рядов проекта, справочник
рядов источников, подписи периодов и сравнение с тем же периодом прошлого года.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import pytest
from django.utils import translation

from apps.catalog.monthly import MonthlySpec, compare, monthly_spec, period_text, value_text
from apps.sources.base import PARSED_COLUMNS
from apps.sources.registry import POPULATION, registry
from apps.sources.territories import region_codes
from apps.warehouse.etl import monthly
from apps.warehouse.queries import featured_set

REGION = "RU-TOM"


def _rows(measure: str, values: dict[tuple[str, int, int], float | None], **extra: Any) -> list:
    return [
        {
            "measure": measure,
            "territory_code": code,
            "year": year,
            "period_kind": "month",
            "period": month,
            "value": value,
            "hidden": extra.get("hidden", False),
            "preliminary": False,
        }
        for (code, year, month), value in values.items()
    ]


def _frame(*groups: list) -> pd.DataFrame:
    return pd.DataFrame([row for group in groups for row in group], columns=list(PARSED_COLUMNS))


def _series(*keys: str) -> list:
    book = registry()
    return [book.by_key[key] for key in keys]


def _population(years: dict[int, float], code: str = REGION) -> pd.DataFrame:
    return pd.DataFrame(
        {"territory_code": code, "year": list(years), "population": list(years.values())}
    )


class TestRegistry:
    """Справочник рядов источников согласован с основным набором и сам с собой."""

    def test_ratios_refer_to_known_series(self) -> None:
        book = registry()
        for item in book.series:
            if item.ratio is None:
                continue
            for key in (item.ratio.numerator, item.ratio.denominator):
                assert key == POPULATION or key in book.by_key, (item.key, key)
            if item.weights:
                assert item.weights in book.by_key, item.key

    def test_featured_project_series_are_known(self) -> None:
        book = registry()
        featured = [item for item in featured_set().series if item.key.startswith("RL_")]
        assert len(featured) == 10
        assert all(item.key in book.by_key for item in featured)
        assert {item.theme for item in featured} == {"credit", "business"}

    def test_now_rows_are_known(self) -> None:
        book = registry()
        for key in book.now:
            assert monthly_spec(key) is not None, key

    def test_sections_and_units(self) -> None:
        book = registry()
        assert {item.section for item in book.series} <= {item.code for item in book.sections}
        assert all(item.unit.code in book.units for item in book.series)


class TestMonths:
    """Значения по месяцам одного выпуска."""

    def test_year_to_date_and_gap(self) -> None:
        values = {(REGION, 2025, month): 10.0 for month in range(1, 13)}
        values.update({(REGION, 2026, 1): 5.0, (REGION, 2026, 3): 7.0})
        months = monthly.build_months(
            _frame(_rows("mortgage_count", values)),
            _series("RL_MORTGAGE_COUNT:00"),
            pd.DataFrame(),
        )["RL_MORTGAGE_COUNT:00"].set_index(["year", "month"])
        assert months.loc[(2025, 12), "ytd"] == pytest.approx(120.0)
        assert months.loc[(2026, 1), "ytd"] == pytest.approx(5.0)
        # Февраля нет: нарастающий итог дальше не считается.
        assert np.isnan(months.loc[(2026, 3), "ytd"])

    def test_ratio_to_population_freezes_after_last_year(self) -> None:
        values = {(REGION, 2024, 12): 2.0, (REGION, 2025, 12): 3.0}
        months = monthly.build_months(
            _frame(_rows("mortgage_debt", values)),
            _series("RL_MORTGAGE_DEBT:00", "RL_MORTGAGE_DEBT_PC:00"),
            _population({2023: 900_000.0, 2024: 1_000_000.0}),
        )["RL_MORTGAGE_DEBT_PC:00"].set_index("year")
        # Млн руб. на человека × 10⁶ — рублей на жителя.
        assert months.loc[2024, "level"] == pytest.approx(2.0)
        assert not months.loc[2024, "frozen"]
        assert months.loc[2025, "level"] == pytest.approx(3.0)
        assert months.loc[2025, "frozen"]
        assert months["computed"].all()

    def test_ratio_of_two_series(self) -> None:
        month = {(REGION, 2026, 7): None}
        frame = _frame(
            _rows("mortgage_debt", dict.fromkeys(month, 200.0)),
            _rows("mortgage_overdue", dict.fromkeys(month, 3.0)),
        )
        months = monthly.build_months(
            frame,
            _series(
                "RL_MORTGAGE_DEBT:00", "RL_MORTGAGE_OVERDUE:00", "RL_MORTGAGE_OVERDUE_SHARE:00"
            ),
            pd.DataFrame(),
        )
        assert months["RL_MORTGAGE_OVERDUE_SHARE:00"]["level"].iloc[0] == pytest.approx(1.5)

    def test_country_is_the_sum_of_regions(self) -> None:
        codes = sorted(region_codes())
        values: dict[tuple[str, int, int], float | None] = {(code, 2026, 9): 1.0 for code in codes}
        values[("RU", 2026, 9)] = 999.0
        months = monthly.build_months(
            _frame(_rows("sme_count", values)), _series("RL_SME_COUNT:00"), pd.DataFrame()
        )["RL_SME_COUNT:00"].set_index("territory_code")
        assert months.loc["RU", "level"] == pytest.approx(len(codes))
        assert months.loc["RU", "computed"]
        # Без одного субъекта суммы по России нет.
        values.pop((codes[0], 2026, 9))
        months = monthly.build_months(
            _frame(_rows("sme_count", values)), _series("RL_SME_COUNT:00"), pd.DataFrame()
        )["RL_SME_COUNT:00"].set_index("territory_code")
        assert np.isnan(months.loc["RU", "level"])

    def test_unknown_reference_is_an_error(self) -> None:
        with pytest.raises(monthly.SourceSeriesError, match="неизвестные"):
            monthly.build_months(_frame(), _series("RL_MORTGAGE_OVERDUE_SHARE:00"), pd.DataFrame())


class TestAnnual:
    """Годовые значения из месяцев."""

    def _annual(self, keys: tuple[str, ...], frame: pd.DataFrame, population: Any = None) -> Any:
        items = _series(*keys)
        population = population if population is not None else pd.DataFrame()
        months = monthly.build_months(frame, items, population)
        return monthly.build_annual(months, items, population)

    def test_sum_needs_the_whole_year(self) -> None:
        values = {(REGION, 2025, month): 10.0 for month in range(1, 13)}
        values.update({(REGION, 2026, month): 1.0 for month in range(1, 8)})
        annual = self._annual(("RL_MORTGAGE_COUNT:00",), _frame(_rows("mortgage_count", values)))
        rows = annual["RL_MORTGAGE_COUNT:00"].set_index("year")
        assert list(rows.index) == [2025]
        assert rows.loc[2025, "value"] == pytest.approx(120.0)
        assert rows.loc[2025, "computed"]

    def test_december_and_register_in_january(self) -> None:
        debt = {(REGION, 2025, 11): 1.0, (REGION, 2025, 12): 2.0}
        annual = self._annual(("RL_MORTGAGE_DEBT:00",), _frame(_rows("mortgage_debt", debt)))
        assert annual["RL_MORTGAGE_DEBT:00"].set_index("year").loc[2025, "value"] == 2.0

        register = {(REGION, 2026, 1): 50.0, (REGION, 2026, 2): 51.0}
        annual = self._annual(("RL_SME_COUNT:00",), _frame(_rows("sme_count", register)))
        rows = annual["RL_SME_COUNT:00"]
        rows = rows[rows["territory_code"] == REGION]
        assert list(rows["year"]) == [2025]
        assert rows["value"].iloc[0] == 50.0

    def test_weighted_mean(self) -> None:
        volume = {(REGION, 2025, month): (3.0 if month == 1 else 1.0) for month in range(1, 13)}
        rate = {(REGION, 2025, month): (10.0 if month == 1 else 6.0) for month in range(1, 13)}
        annual = self._annual(
            ("RL_MORTGAGE_VOLUME:00", "RL_MORTGAGE_RATE:00"),
            _frame(_rows("mortgage_volume", volume), _rows("mortgage_rate", rate)),
        )
        # (3 × 10 + 11 × 6) / 14
        assert annual["RL_MORTGAGE_RATE:00"]["value"].iloc[0] == pytest.approx(96 / 14)

    def test_ratio_of_annual_values(self) -> None:
        count = {(REGION, 2025, month): 2.0 for month in range(1, 13)}
        volume = {(REGION, 2025, month): 10.0 for month in range(1, 13)}
        annual = self._annual(
            ("RL_MORTGAGE_COUNT:00", "RL_MORTGAGE_VOLUME:00", "RL_MORTGAGE_AVERAGE:00"),
            _frame(_rows("mortgage_count", count), _rows("mortgage_volume", volume)),
        )
        # 120 млн руб. на 24 кредита — 5 000 тыс. руб.
        assert annual["RL_MORTGAGE_AVERAGE:00"]["value"].iloc[0] == pytest.approx(5000.0)

    def test_hidden_month_hides_the_year(self) -> None:
        values = {(REGION, 2025, month): 10.0 for month in range(1, 13)}
        frame = _frame(_rows("mortgage_count", values))
        frame.loc[frame["period"] == 5, "hidden"] = True
        annual = self._annual(("RL_MORTGAGE_COUNT:00",), frame)
        assert (
            annual["RL_MORTGAGE_COUNT:00"].empty
            or annual["RL_MORTGAGE_COUNT:00"]["value"].isna().all()
        )


class TestDatasetLayer:
    """Помесячный слой рядов набора из показателя бюллетеня."""

    def test_january_counts_as_year_to_date(self) -> None:
        rows = [
            {
                "measure": "industry_index",
                "territory_code": "RU",
                "year": 2026,
                "period_kind": kind,
                "period": period,
                "value": value,
                "hidden": False,
                "preliminary": period == 7,
            }
            for kind, period, value in (("month", 1, 99.0), ("ytd", 2, 99.5), ("ytd", 7, 100.1))
        ]
        entry = next(item for item in registry().dataset_monthly if item.kind == "yoy")
        layer = monthly._dataset_layer(_frame(rows), entry, "rel_x")
        assert list(layer["month"]) == [1, 2, 7]
        assert set(layer["kind"]) == {"yoy"}
        assert list(layer["flags"]) == [0, 0, 1]


def _spec(**changes: Any) -> MonthlySpec:
    values: dict[str, Any] = {
        "key": "X",
        "title": "Показатель",
        "unit": "ед.",
        "precision": 1,
        "compare": "percent",
        "kind": "level",
        "chart_kind": "level",
        "style": "month",
        "polarity": "positive",
    }
    values.update(changes)
    return MonthlySpec(**values)


class TestWords:
    """Подписи периодов и сравнение с тем же периодом прошлого года."""

    @pytest.mark.parametrize(
        ("style", "year", "month", "text"),
        [
            ("month", 2026, 6, "июнь 2026"),
            ("ytd", 2026, 7, "январь — июль 2026"),
            ("ytd", 2026, 1, "январь 2026"),
            ("ytd_index", 2026, 7, "январь — июль 2026 к декабрю 2025"),
            ("yoy", 2026, 7, "январь — июль 2026 к тому же периоду 2025 года"),
            ("stock", 2026, 7, "на 1 августа 2026"),
            ("stock", 2025, 12, "на 1 января 2026"),
            ("register", 2026, 9, "на 10 сентября 2026"),
            ("moving3", 2026, 7, "май — июль 2026, в среднем"),
            ("moving3", 2026, 1, "ноябрь 2025 — январь 2026, в среднем"),
        ],
    )
    def test_period_text(self, style: str, year: int, month: int, text: str) -> None:
        with translation.override("ru"):
            assert period_text(style, year, month) == text

    def test_index_is_shown_as_growth(self) -> None:
        spec = _spec(style="ytd_index", compare="points", polarity="negative", unit="%")
        with translation.override("ru"):
            assert value_text(spec, 104.8) == "+4,8"
            text, tone = compare(spec, 104.8, 105.9)
        assert "+5,9" in text
        # Цены росли медленнее, чем год назад, — к лучшему для ряда с отрицательной направленностью.
        assert tone == "positive"

    def test_percent_and_points(self) -> None:
        with translation.override("ru"):
            text, tone = compare(_spec(), 110.0, 100.0)
            assert text == "+10,0 % за год"
            assert tone == "positive"
            text, tone = compare(_spec(style="ytd"), 90.0, 100.0)
            assert text == "−10,0 % к тому же периоду прошлого года"
            assert tone == "negative"
            text, tone = compare(_spec(compare="points", polarity="negative"), 1.4, 0.9)
            assert text == "+0,5 п. за год"
            assert tone == "negative"

    def test_money_and_small_changes_are_not_judged(self) -> None:
        assert compare(_spec(unit="руб."), 120.0, 100.0)[1] == "neutral"
        assert compare(_spec(), 100.1, 100.0)[1] == "neutral"
        assert compare(_spec(polarity="neutral"), 150.0, 100.0)[1] == "neutral"
        assert compare(_spec(), 100.0, None) == ("", "neutral")

    def test_specs_of_series(self) -> None:
        assert monthly_spec("RL_MORTGAGE_COUNT_PC:00").style == "ytd"
        assert monthly_spec("RL_MORTGAGE_COUNT_PC:00").kind == "ytd"
        assert monthly_spec("RL_MORTGAGE_OVERDUE_SHARE:00").style == "stock"
        assert monthly_spec("RL_MORTGAGE_RATE:00").style == "month"
        assert monthly_spec("RL_SME_COUNT_PC:00").style == "register"
        assert monthly_spec("Y477110418:00").style == "moving3"
        assert monthly_spec("Y477110111:00").style == "ytd_index"
        assert monthly_spec("Y477110126:00").style == "yoy"
        assert monthly_spec("Y477110461:00") is None
