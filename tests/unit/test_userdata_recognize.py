"""Распознавание таблиц своих данных: форма, роли, шапка, периоды, разрезы, строки листов."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from apps.userdata import ingest, recognize, tables
from apps.userdata.recognize import (
    INDICATOR,
    INDICATORS,
    LONG,
    PERIOD,
    SLICE,
    TERRITORY,
    TERRITORY_CODE,
    UNIT,
    VALUE,
    WIDE,
)
from apps.userdata.tables import Loaded

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "userdata"
BULLETIN = Path(__file__).resolve().parent.parent / "fixtures" / "sources" / "bulletin"


def _recognize(
    rows: list[list[Any]], recipe: dict[str, Any] | None = None
) -> recognize.Recognition:
    table = ingest.TableInfo(key="", name="t", kind=ingest.PASTE, size=0)
    return recognize.recognize(Loaded(rows=rows, complete=True, total=len(rows)), table, recipe)


def _file(name: str, folder: Path = FIXTURES) -> recognize.Recognition:
    path = folder / name
    table = next(table for table in ingest.inspect(path).tables if table.best)
    return recognize.recognize(tables.load(path, table), table, file_name=name)


def _roles(result: recognize.Recognition) -> dict[str, str]:
    return {column.header: column.role for column in result.columns if column.role != "skip"}


class TestEbtTables:
    """Длинные и широкие таблицы ЕБТ: роли по названиям столбцов и профилю ячеек."""

    def test_long_with_codes_and_notes(self) -> None:
        result = _file("environment.csv")
        roles = _roles(result)
        assert result.form == LONG
        assert roles["object_name"] == TERRITORY
        assert roles["object_oktmo"] == TERRITORY_CODE
        assert roles["year"] == PERIOD
        assert roles["indicator_value"] == VALUE
        assert roles["indicator_name"] == INDICATOR
        assert roles["indicator_unit"] == UNIT
        assert roles["indicator_section"] == "note"
        assert result.periods["first"] == 2014

    def test_slices_with_totals_preselected(self) -> None:
        result = _file("despair.csv")
        slices = {info["header"]: info["selected"] for info in result.slices.values()}
        assert slices["age"] == ["Всего", "до 1 года"]
        assert set(slices["gender"]) == {"F", "M"}
        assert "reason_code" not in slices
        assert set(slices) == {"age", "settlement_type", "gender", "reason_name"}

    def test_small_integer_column_is_a_slice(self) -> None:
        roles = _roles(_file("mortality_ex.csv"))
        assert roles["age"] == SLICE
        assert roles["sex"] == SLICE

    def test_years_in_columns(self) -> None:
        result = _file("crime_wide.csv")
        assert result.form == WIDE
        assert len(result.value_columns) == 12
        assert result.value_columns[0].stamp == {
            "year": 2011,
            "period": "year:12",
            "group": "",
            "notes": [],
        }
        assert [item.label for item in result.territories.nested] == [  # type: ignore[union-attr]
            "Архангельская область",
            "Тюменская область",
        ]

    def test_workbook(self) -> None:
        result = _file("hiv.xlsx")
        assert result.form == LONG
        assert _roles(result)["reason_na"] == "missing_reason"


class TestSheets:
    """Листы Росстата: шапка в несколько строк, периоды, сноски, повторы строк."""

    def test_bulletin_months(self) -> None:
        result = _file("12-01 среднемесячная заработная плата.xlsx", BULLETIN)
        assert result.form == WIDE
        assert len(result.header_rows) == 2
        assert result.periods["kinds"]["month:1"] >= 1
        assert result.territories is not None
        assert len(result.territories.codes()) >= 94

    def test_window_under_whole_header(self) -> None:
        rows = [
            ["продолжение", "", ""],
            ["", "Численность", "в том числе"],
            ["", "", "занятые"],
            ["", "май 2026 г. - июль 2026 г. (в среднем за период)", ""],
            *[
                [name, "1", "2"]
                for name in (
                    "Российская Федерация",
                    "Москва",
                    "Тверская область",
                    "Курская область",
                )
            ],
        ]
        result = _recognize(rows)
        assert result.form == WIDE
        assert {column.stamp["period"] for column in result.value_columns} == {"window:7:3"}  # type: ignore[index]
        assert result.indicators == ["Численность", "в том числе | занятые"]

    def test_country_row_named_as_indicator(self) -> None:
        rows = [
            ["", "2022", "2023"],
            ["Валовой региональный продукт по субъектам Российской Федерации", "100", "110"],
            ["Москва", "30", "33"],
            ["Тверская область", "1", "1,1"],
            ["Курская область", "1", "1,2"],
        ]
        result = _recognize(rows)
        assert result.data_start == 1
        label = rows[1][0]
        assert result.territories.matches[label].candidates == ("RU",)  # type: ignore[union-attr]

    def test_footnotes_and_lead_ins(self) -> None:
        rows = [
            ["", "2023"],
            ["Архангельская область", "10"],
            ["в том числе:", ""],
            ["Ненецкий автономный округ", "1"],
            ["Архангельская область без автономного округа", "9"],
            ["Москва", "30"],
            ["1) Данные предварительные.", ""],
        ]
        result = _recognize(rows)
        assert result.footnotes == {1: "Данные предварительные."}
        assert result.territories.nested == []  # type: ignore[union-attr]
        assert result.territories.codes()["Архангельская область"] == "RU-ARK-AGG"  # type: ignore[union-attr]

    def test_split_labels_from_word(self) -> None:
        rows = [
            ["", "2022", "2023"],
            ["Центральный", "", ""],
            ["федеральный округ", "1", "2"],
            ["Москва", "1", "2"],
            ["Ханты-Мансийский", "3", "4"],
            ["автономный округ –", "", ""],
            ["Югра", "", ""],
            ["Республика Саха", "", ""],
            ["(Якутия)", "5", "6"],
            ["Тверская область", "1", "1"],
        ]
        result = _recognize(rows)
        labels = [row.label for row in result.rows]
        assert labels == [
            "Центральный федеральный округ",
            "Москва",
            "Ханты-Мансийский автономный округ – Югра",
            "Республика Саха (Якутия)",
            "Тверская область",
        ]

    def test_two_halves(self) -> None:
        rows = [
            ["", "2023", "", "2023"],
            ["Москва", "1", "Курская область", "4"],
            ["Тверская область", "2", "Орловская область", "5"],
            ["Брянская область", "3", "Липецкая область", "6"],
        ]
        result = _recognize(rows)
        assert result.territory_columns == [0, 2]
        assert len(result.rows) == 6
        assert result.duplicates == []

    def test_merged_header_stops_at_service_columns(self) -> None:
        rows = [
            ["", "Численность", "", "в том числе", "", "", ""],
            ["", "", "", "занятые", "", "", ""],
            ["", "2024 год", "2025 год", "2024 год", "2025 год", "", ""],
            *[
                [name, "1", "2", "1", "2", "105", "0"]
                for name in ("Москва", "Тверская область", "Курская область")
            ],
        ]
        result = _recognize(rows)
        assert [column.header for column in result.value_columns] == [
            "Численность | 2024 год",
            "Численность | 2025 год",
            "в том числе | занятые | 2024 год",
            "в том числе | занятые | 2025 год",
        ]
        assert result.same_headers == []

    def test_word_cells_shifted_under_header(self) -> None:
        rows = [
            ["", "2020", "2022", "", "2023"],
            ["", "Болезни органов дыхания", "", "", ""],
            *[
                [name, "1", "", "2", "3"]
                for name in ("Москва", "Тверская область", "Курская область")
            ],
        ]
        result = _recognize(rows)
        assert [column.stamp["year"] for column in result.value_columns] == [2020, 2022, 2023]  # type: ignore[index]
        assert result.indicators == ["Болезни органов дыхания"]

    def test_header_shifted_from_numbers(self) -> None:
        rows = [
            ["", "", "Безработные", "", "", "", "", "Потенциальная", "", ""],
            ["", "", "2022", "", "2023", "", "2024", "2022", "2023", "2024"],
            *[
                [name, "1", "", "2", "", "3", "", "4", "5", "6"]
                for name in ("Москва", "Тверская область", "Курская область")
            ],
        ]
        result = _recognize(rows)
        assert [(column.index, column.header) for column in result.value_columns] == [
            (1, "Безработные | 2022"),
            (3, "Безработные | 2023"),
            (5, "Безработные | 2024"),
            (7, "Потенциальная | 2022"),
            (8, "Потенциальная | 2023"),
            (9, "Потенциальная | 2024"),
        ]

    def test_sections_under_years(self) -> None:
        rows = [
            ["", "2022", "2023", "2022", "2023"],
            ["", "Всего", "", "Земельные участки", ""],
            *[
                [name, "1", "2", "3", "4"]
                for name in ("Москва", "Тверская область", "Курская область")
            ],
        ]
        result = _recognize(rows)
        assert result.same_headers == []
        assert result.indicators == ["Всего", "Земельные участки"]

    def test_same_headers_are_reported(self) -> None:
        rows = [
            ["", "2025 год", "", "2025 год", ""],
            ["", "I квартал", "II квартал", "I квартал", "II квартал"],
            *[
                [name, "1", "2", "3", "4"]
                for name in ("Москва", "Тверская область", "Курская область")
            ],
        ]
        result = _recognize(rows)
        assert result.same_headers == [
            {"header": "2025 год | I квартал", "columns": [2, 4]},
            {"header": "2025 год | II квартал", "columns": [3, 5]},
        ]
        assert result.duplicates == []

    def test_long_footnote_with_check_sums(self) -> None:
        note = (
            "1 Условия отнесения к субъектам малого и среднего предпринимательства "
            "изменены с 2016 года."
        )
        rows = [
            ["", "2023", "2024"],
            ["Москва", "1", "2"],
            ["Тверская область", "1", "1"],
            ["Курская область", "1", "1"],
            [note, "0,1", "-0,1"],
        ]
        result = _recognize(rows)
        assert [row.label for row in result.rows] == [
            "Москва",
            "Тверская область",
            "Курская область",
        ]
        assert result.footnotes[1].startswith("Условия отнесения")

    def test_complementary_rows_are_not_duplicates(self) -> None:
        rows = [
            ["", "2017", "2019"],
            ["Москва", "1", "1"],
            ["Республика Бурятия", "5", "-"],
            ["Тверская область", "2", "2"],
            ["Республика Бурятия", "-", "6"],
            ["Курская область", "3", "3"],
        ]
        assert _recognize(rows).duplicates == []
        rows[4][1] = "7"
        assert len(_recognize(rows).duplicates) == 1


class TestIndicatorsInColumns:
    """Показатели в столбцах: год спрашивается, если его нет в названиях столбцов."""

    def test_year_question(self) -> None:
        rows = [
            ["Регион", "Население", "ВРП"],
            ["Москва", "13", "25"],
            ["Тверская область", "1,2", "0,5"],
            ["Курская область", "1,1", "0,6"],
        ]
        result = _recognize(rows)
        assert result.form == INDICATORS
        assert result.needs_year
        assert not _recognize(rows, {"year": 2023}).needs_year

    def test_year_in_column_names(self) -> None:
        rows = [
            [
                "",
                "Численность населения на 1 января 2024 г.",
                "Валовой региональный продукт в 2023 г.",
            ],
            ["Москва", "13", "25"],
            ["Тверская область", "1,2", "0,5"],
            ["Курская область", "1,1", "0,6"],
        ]
        result = _recognize(rows)
        stamps = [column.stamp for column in result.value_columns]
        assert [stamp["year"] for stamp in stamps] == [2024, 2023]  # type: ignore[index]
        assert stamps[0]["period"] == "point:1"  # type: ignore[index]
        assert not result.needs_year

    def test_role_override(self) -> None:
        rows = [
            ["Регион", "Код", "2023"],
            ["Москва", "77", "1"],
            ["Тверская область", "69", "2"],
            ["Курская область", "46", "3"],
        ]
        result = _recognize(rows, {"roles": {"1": "skip"}})
        assert result.columns[1].role == "skip"


def test_not_a_table_of_regions() -> None:
    result = _recognize([["Товар", "Цена"], ["Хлеб", "50"], ["Молоко", "80"]])
    assert result.form == recognize.UNKNOWN
