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
        # «В том числе» — не показатель: над столбцом встаёт подпись итога слева.
        assert result.indicators == ["Численность", "Численность | занятые"]

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
            "Численность | занятые | 2024 год",
            "Численность | занятые | 2025 год",
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


REGIONS = [
    "Белгородская область",
    "Брянская область",
    "Владимирская область",
    "Воронежская область",
    "Ивановская область",
    "Калужская область",
]


def _towns() -> list[list[Any]]:
    """Два моногорода на регион, как в выгрузке ЕМИСС: регион и город — в своих столбцах."""
    rows: list[list[Any]] = [
        ["Регион", "Классификатор территорий муниципальных образований (ОКТМО)", "2022", "2023"]
    ]
    for number, region in enumerate(REGIONS):
        rows.append([region, f"{14 + number}616153000 Город {number}а", "100", f"{110 + number}"])
        rows.append([region, f"{14 + number}616156000 Город {number}б", "200", f"{210 + number}"])
    return rows


class TestRepeatedRegions:
    """Регион в нескольких строках с разными числами у половины регионов — вопрос."""

    def test_towns_under_regions_are_asked(self) -> None:
        result = _recognize(_towns())
        assert (
            _roles(result).get("Классификатор территорий муниципальных образований (ОКТМО)") is None
        )
        assert result.repeats == {
            "regions": 6,
            "total": 6,
            "municipal": "Классификатор территорий муниципальных образований (ОКТМО)",
        }
        assert result.needs_repeats

    @pytest.mark.parametrize("answer", [recognize.REPEATS_PLACES, recognize.REPEATS_FIRST])
    def test_answer_is_taken_from_recipe(self, answer: str) -> None:
        result = _recognize(_towns(), {"repeats": answer})
        assert result.repeats_answer == answer
        assert not result.needs_repeats

    def test_real_slice_is_not_a_repeat(self) -> None:
        rows: list[list[Any]] = [["Регион", "Пол", "2023"]]
        for number, region in enumerate(REGIONS):
            rows += [[region, "мужчины", f"{number}1"], [region, "женщины", f"{number}2"]]
        result = _recognize(rows)
        assert _roles(result)["Пол"] == SLICE
        assert result.repeats is None

    def test_few_repeated_regions_only_warn(self) -> None:
        rows: list[list[Any]] = [["Регион", "2023"]]
        rows += [[region, f"{number}0"] for number, region in enumerate(REGIONS)]
        rows.append([REGIONS[0], "99"])
        result = _recognize(rows)
        assert len(result.duplicates) == 1
        assert result.repeats is None


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


class TestOtherAgencies:
    """Вёрстки других ведомств из проверки 10.10.2026: ФТС, Банк России, Минфин, статьи."""

    def test_region_heading_with_rows_under_it(self) -> None:
        # ФТС: регион — строка без чисел, под ним строки разреза с числами.
        rows = [
            ["Федеральные округа и субъекты РФ", "2020 год", "2021 год"],
            ["ИТОГО", "", ""],
            ["Всего", "10", "12"],
            ["Микро предприятия", "3", "4"],
            ["Белгородская область", "", ""],
            ["Всего", "5", "6"],
            ["Микро предприятия", "1", "2"],
            ["Брянская область", "", ""],
            ["Всего", "2", "3"],
            ["Микро предприятия", "1", "1"],
            ["Курская область", "", ""],
            ["Всего", "4", "4"],
            ["Микро предприятия", "2", "2"],
        ]
        result = _recognize(rows)
        assert result.form == WIDE
        assert result.row_parts == ["Всего", "Микро предприятия"]
        assert {row.part for row in result.rows} == {"Всего", "Микро предприятия"}
        assert result.territories is not None
        assert {"RU-BEL", "RU-BRY", "RU-KRS"} <= set(result.territories.codes().values())
        # Регион в каждой строке разреза — не дубль.
        assert result.duplicates == []

    def test_regions_with_numbers_are_not_headings(self) -> None:
        rows = [
            ["Регион", "2020 год", "2021 год"],
            ["Белгородская область", "5", "6"],
            ["Всего", "2", "3"],
            ["Брянская область", "2", "3"],
            ["Курская область", "4", "4"],
        ]
        assert _recognize(rows).row_parts == []

    def test_year_in_long_header_makes_a_value(self) -> None:
        # Статья: «период» и «год» в названии не делают столбец чисел столбцом периода.
        rows = [
            [
                "Регион",
                "Период конвергенции в 2018 году (лет)",
                "Период конвергенции в 2022 году (лет)",
            ],
            ["Республика Адыгея", "-22,2", "385,5"],
            ["Республика Крым", "-23,6", "-20,2"],
            ["Краснодарский край", "-4,3", "-168,9"],
        ]
        result = _recognize(rows)
        assert result.form == INDICATORS
        assert [column.stamp["year"] for column in result.value_columns] == [2018, 2022]  # type: ignore[index]
        assert not result.needs_year

    def test_year_column_stays_a_period(self) -> None:
        rows = [
            ["Регион", "Год", "Значение"],
            ["Москва", "2020", "1"],
            ["Москва", "2021", "2"],
            ["Тверская область", "2020", "3"],
            ["Курская область", "2020", "4"],
        ]
        result = _recognize(rows)
        assert result.form == LONG
        assert _roles(result)["Год"] == PERIOD

    def test_table_by_federal_districts(self) -> None:
        rows = [
            ["Наименование федерального округа", "Объём кредитов"],
            ["Центральный федеральный округ", "100825"],
            ["Северо-Западный федеральный округ", "12510"],
            ["Южный федеральный округ", "445"],
            ["Приволжский федеральный округ", "186"],
        ]
        result = _recognize(rows)
        assert result.form == recognize.UNKNOWN
        assert result.districts

    def test_year_hint_from_date_in_file_name(self) -> None:
        rows = [
            ["Регион", "Кредиты", "Задолженность"],
            ["Москва", "13", "25"],
            ["Тверская область", "1,2", "0,5"],
            ["Курская область", "1,1", "0,6"],
        ]
        table = ingest.TableInfo(key="", name="t", kind=ingest.PASTE, size=0)
        loaded = Loaded(rows=rows, complete=True, total=len(rows))
        result = recognize.recognize(loaded, table, file_name="01_03_Loans_corp_20260901.xlsx")
        # Год из даты в имени файла подставлен без вопроса, с источником.
        assert not result.needs_year
        assert (result.year, result.year_auto) == (2026, True)
        assert result.year_from == recognize.YEAR_FROM_FILE

    @pytest.mark.parametrize(
        "file_name",
        [
            # Даты — версии набора и структуры, цифры кода набора — не год (возраст 20–24).
            "7708234640-healthregions-2021_data-20220624T1406-structure-20220624T1406.csv",
            "7708234640-totalnotmarried16th2024th_data-20161226T1712-structure-20161225T1712.csv",
            "data-20140221-structure-20140221.csv",
        ],
    )
    def test_open_data_file_name_gives_no_year(self, file_name: str) -> None:
        rows = [
            ["Регион", "Кредиты", "Задолженность"],
            ["Москва", "13", "25"],
            ["Тверская область", "1,2", "0,5"],
            ["Курская область", "1,1", "0,6"],
        ]
        table = ingest.TableInfo(key="", name="t", kind=ingest.PASTE, size=0)
        loaded = Loaded(rows=rows, complete=True, total=len(rows))
        result = recognize.recognize(loaded, table, file_name=file_name)
        assert result.year is None
        assert result.needs_year

    def test_numbers_named_with_period_or_level_are_values(self) -> None:
        rows = [
            ["Регион", "Количество карт на конец периода, ед.", "Уровень безработицы, %"],
            ["Москва", "40211953", "1,3"],
            ["Тверская область", "1622513", "2,9"],
            ["Курская область", "1490722", "3,1"],
        ]
        roles = _roles(_recognize(rows))
        assert roles["Количество карт на конец периода, ед."] == VALUE
        assert roles["Уровень безработицы, %"] == VALUE

    def test_small_numbers_named_month_or_level_are_not_values(self) -> None:
        rows = [
            ["Регион", "Месяц", "Уровень", "Значение"],
            ["Москва", "1", "1", "13"],
            ["Москва", "2", "2", "12"],
            ["Тверская область", "1", "1", "5"],
            ["Курская область", "2", "2", "6"],
        ]
        roles = _roles(_recognize(rows))
        assert roles.get("Уровень") != VALUE
        assert roles.get("Месяц") != VALUE

    def test_year_with_digit_group_space_is_not_value(self) -> None:
        # Выгрузка СФР пишет год с пробелом между разрядами: «2 025».
        rows = [
            ["Регион", "Численность, чел.", "Год"],
            ["Москва", "104 749", "2 025"],
            ["Тверская область", "11 977", "2 025"],
            ["Курская область", "30 867", "2 025"],
        ]
        roles = _roles(_recognize(rows))
        assert roles["Численность, чел."] == VALUE
        assert roles.get("Год") != VALUE

    @pytest.mark.parametrize(
        ("text", "year", "period"),
        [
            ("за I квартал 2023 года", 2023, "quarter:1"),
            ("2023г - 1 квартал", 2023, "quarter:1"),
            ("Число устройств на 1 января 2024 г.", 2024, "point:1"),
            ("январь–сентябрь 2025 года", 2025, "ytd:9"),
            ("по состоянию на 01.07.2026", 2026, "point:7"),
            ("Ввод жилья 2023, тыс. кв. м", 2023, "year:12"),
            ("t3_1_2025", 2025, "year:12"),
        ],
    )
    def test_period_in_free_text(self, text: str, year: int, period: str) -> None:
        found = recognize.context_stamp(text)
        assert found is not None
        assert (found.year, found.period.key) == (year, period)

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("Млн рублей | В % к январю-сентябрю 2023 г.", None),
            ("Млрд рублей | В % к | I полугодию 2025 г. | итогу", None),
            ("млн рублей | I полугодие 2026 г. в % к I полугодию 2025 г.", (2026, "half:1")),
            ("Тыс. голов | на 1 июля 2026 г.", (2026, "point:7")),
        ],
    )
    def test_period_in_header_skips_comparison_base(
        self, text: str, expected: tuple[int, str] | None
    ) -> None:
        found = recognize.context_stamp(text, whole_year=False)
        assert (None if found is None else (found.year, found.period.key)) == expected

    def test_every_year_source_has_a_note(self) -> None:
        from apps.userdata import describe

        sources = [
            value for name, value in vars(recognize).items() if name.startswith("YEAR_FROM_")
        ]
        for source in sources:
            assert describe.year_note(2024, "half:1", source, "текст").startswith("Год 2024")

    def test_file_year_is_asked_when_header_has_part_of_year(self) -> None:
        rows = [
            ["", "Добыча", "в % к январю–сентябрю прошлого года"],
            ["Москва", "13", "101,2"],
            ["Тверская область", "12", "99,5"],
            ["Курская область", "11", "100,4"],
        ]
        table = ingest.TableInfo(key="", name="t", kind=ingest.PASTE, size=0)
        loaded = Loaded(rows=rows, complete=True, total=len(rows))
        result = recognize.recognize(loaded, table, file_name="bul_09_2024.xlsx")
        # Год из имени файла есть, а период части года не разобрать — вопрос, с подсказкой.
        assert result.needs_year
        assert result.year_hint == 2024

    def test_sheet_name_gives_year_and_quarter(self) -> None:
        rows = [
            ["Регион", "Банкоматы", "Терминалы"],
            ["Москва", "13", "25"],
            ["Тверская область", "12", "5"],
            ["Курская область", "11", "6"],
        ]
        table = ingest.TableInfo(
            key="#2023г - 1 квартал", name="2023г - 1 квартал", kind=ingest.XLSX, size=0
        )
        loaded = Loaded(rows=rows, complete=True, total=len(rows))
        result = recognize.recognize(loaded, table, file_name="t.xlsx")
        assert (result.year, result.period) == (2023, "quarter:1")
        assert result.year_from == recognize.YEAR_FROM_SHEET

    def test_quiet_rows_are_not_asked(self) -> None:
        from apps.userdata import describe

        rows = [
            ["Субъект", "Сумма", "Доля"],
            ["1", "2", "3"],
            ["Москва", "13", "25"],
            ["Тверская область", "1,2", "0,5"],
            ["Курская область", "1,1", "0,6"],
            ["ВСЕГО:", "15,3", "26,1"],
            ["Мордор", "1", "1"],
        ]
        asked = [label for label, _match, _count in describe.unresolved(_recognize(rows))]
        assert asked == ["Мордор"]


def test_not_a_table_of_regions() -> None:
    result = _recognize([["Товар", "Цена"], ["Хлеб", "50"], ["Молоко", "80"]])
    assert result.form == recognize.UNKNOWN


def test_years_in_some_columns_keep_the_rest() -> None:
    # Сводка территориального органа: зарплата и индекс цен без года в шапке, безработные —
    # «2026» и «2025». Годами в столбцах таблица потеряла бы зарплату и цены: показатели
    # в столбцах, у столбца с годом в шапке — свой год.
    rows = [
        ["", "Зарплата", "", "Индекс цен, %", "Безработные, тыс. человек", ""],
        ["", "рублей", "в % к июлю 2025", "", "2026", "2025"],
        ["Белгородская область", "85 348", "104,3", "103,9", "2,0", "1,8"],
        ["Тверская область", "104 344", "106,5", "104,3", "2,4", "2,4"],
        ["Курская область", "102 947", "109,8", "103,8", "4,5", "4,6"],
        ["Липецкая область", "85 640", "99,3", "105,2", "3,1", "3,1"],
    ]
    result = _recognize(rows)
    assert result.form == INDICATORS
    assert len(result.value_columns) == 5
    stamps = {column.index: column.stamp for column in result.value_columns if column.stamp}
    assert {index: stamp["year"] for index, stamp in stamps.items()} == {4: 2026, 5: 2025}


def test_years_in_indicator_names_from_the_table_keep_the_rest() -> None:
    # Сводка Петростата: год в названиях зарплаты и цен — тот же, что у «Безработные | 2026 |
    # 2025»: это показатели того же периода, а не чужой ряд; таблица их не теряет.
    rows = [
        [
            "",
            "Заработная плата в июне 2026",
            "",
            "Индекс цен в июле 2026 в % к декабрю 2025",
            "Безработные, тыс. человек",
            "",
        ],
        ["", "рублей", "в % к июню 2025", "", "2026", "2025"],
        ["Белгородская область", "85 348", "104,3", "103,9", "2,0", "1,8"],
        ["Тверская область", "104 344", "106,5", "104,3", "2,4", "2,4"],
        ["Курская область", "102 947", "109,8", "103,8", "4,5", "4,6"],
        ["Липецкая область", "85 640", "99,3", "105,2", "3,1", "3,1"],
    ]
    result = _recognize(rows)
    assert result.form == INDICATORS
    assert len(result.value_columns) == 5


@pytest.mark.parametrize(
    "header",
    [
        # Сборник Росстата по округу: справочные кварталы без года.
        [["", "I полугодие 2026 г.", "Справочно", ""], ["", "", "I квартал", "II квартал"]],
        # Бюллетень по ПФО: столбец без названия и справочный.
        [["", "На 1 июля 2024 г.", "", "Справочно на 1 июля 2023 г."], ["", "", "", ""]],
        # Минфин: год в названии не из годов таблицы — столбец получил бы чужой год.
        [
            ["", "Доля налога", "", ""],
            ["", "2023 г.", "Потенциал на 2026 год", "Потенциал на 2027 год"],
        ],
        # Годы над всей таблицей и индексы к ним — годы в столбцах, как были.
        [["", "2024", "2025", "2025 в % к 2024"], ["", "", "", ""]],
    ],
)
def test_reference_columns_are_not_indicators(header: list[list[str]]) -> None:
    # Справочные столбцы, части года без года («I квартал»), столбцы без названия и с годом
    # в названии — не показатели без года: таблица остаётся «годы в столбцах», как была.
    rows = [
        *header,
        ["Белгородская область", "109,7", "101,0", "108,7"],
        ["Тверская область", "109,8", "99,0", "110,9"],
        ["Курская область", "103,5", "101,9", "101,5"],
        ["Липецкая область", "95,6", "100,1", "95,5"],
    ]
    result = _recognize(rows)
    assert result.form == WIDE
    assert result.value_columns
    assert all(column.stamp for column in result.value_columns)


def test_year_series_in_names_is_not_indicators() -> None:
    # Минфин: «Потенциал на 2025 год», «… на 2026 год» разнятся только годом — ряд одного
    # показателя по годам; показателями без года они получили бы один год таблицы на всех.
    rows = [
        ["", "Прогноз", "", "Потенциал на 2025 год", "Потенциал на 2026 год"],
        ["", "2025 год", "2026 год", "", ""],
        ["Белгородская область", "1,5", "1,7", "51 441 671", "57 519 088"],
        ["Тверская область", "2,5", "2,6", "12 595 613", "14 094 121"],
        ["Курская область", "3,5", "3,4", "27 304 812", "30 553 283"],
        ["Липецкая область", "4,5", "4,1", "33 104 812", "35 553 283"],
    ]
    result = _recognize(rows)
    assert result.form == WIDE
    assert all(column.stamp for column in result.value_columns)


REGION_ROWS = [
    ["Белгородская область", "67,0", "114,9", "17,0"],
    ["Тверская область", "25,8", "103,0", "6,6"],
    ["Курская область", "57,2", "111,8", "14,5"],
    ["Липецкая область", "41,9", "99,3", "13,9"],
]


def test_comparison_base_under_its_word_is_not_a_period() -> None:
    # «В % к» над «июню 2025 г.» — база сравнения, а не период: столбец не получает год
    # 2025, таблица — «показатели в столбцах», год спрашивается (сборник Росстата по округу).
    header = [
        ["", "Численность незанятых, тыс. человек", "В % к", ""],
        ["", "", "июню 2025 г.", "итогу"],
    ]
    result = _recognize([*header, *REGION_ROWS])
    assert result.form == INDICATORS
    assert not any(column.stamp for column in result.columns)
    assert result.needs_year


def test_document_year_from_caption_not_from_comparison_base() -> None:
    # Год таблицы документа — из подписи «в I квартале 2026 года», а не из базы сравнения
    # «В % к | I кварталу 2025 г.» в шапке: шапка читается по столбцам. По строкам между «к»
    # и «I кварталу 2025» встала бы «Структура инвестиций…», и база сошла бы за год.
    header = [
        ["", "Млн рублей", "В % к", "", "Структура, в % к общему объему"],
        ["", "", "I кварталу 2025 г.", "итогу", "собственные средства"],
    ]
    rows = [*header, *([*row, "62,7"] for row in REGION_ROWS)]
    table = ingest.TableInfo(
        key="", name="Таблица 42, стр. 35: в I квартале 2026 года", kind=ingest.PDF, size=0
    )
    result = recognize.recognize(Loaded(rows=rows, complete=True, total=len(rows)), table)
    assert (result.year, result.year_from) == (2026, recognize.YEAR_FROM_CAPTION)


def test_series_limit_without_slices_is_the_columns() -> None:
    # Предел рядов: при разрезах — отбор их значений (500), у таблицы без разрезов ряды —
    # её столбцы показателей (открытые данные Минкультуры — до 600).
    result = recognize.Recognition(form=INDICATORS, columns=[], header_rows=[], data_start=1)
    result.series = 599
    assert (result.series_limit, result.too_many) == (recognize.MAX_COLUMN_SERIES, False)
    result.slices = {3: {"header": "Пол", "values": {}}}
    assert (result.series_limit, result.too_many) == (recognize.MAX_SERIES, True)
