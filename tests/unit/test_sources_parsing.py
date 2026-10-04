"""
Разбор выпусков Росстата: таблицы «территории × периоды», подписи территорий, сноски,
бюллетень и таблица ВРП на настоящих образцах.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from apps.sources import rosstat_bulletin, rosstat_grp
from apps.sources.sheets import Period, SheetFormatError, parse_cell, parse_table
from apps.sources.territories import normalize, region_codes, territory_code

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "sources"


def _region_rows(values: list[Any]) -> list[list[Any]]:
    """Строки всех субъектов справочника с одинаковыми значениями."""
    from apps.sources.territories import _known_names

    names = {code: name for name, code in _known_names().items() if code in region_codes()}
    return [[names[code].capitalize(), *values] for code in sorted(names)]


class TestTerritories:
    """Подписи строк Росстата сопоставляются с кодами справочника."""

    @pytest.mark.parametrize(
        ("label", "code"),
        [
            ("Российская Федерация", "RU"),
            ("Российская Федерация  млрд. рублей ", "RU"),
            ("Российская Федерация, млн. кв. метров", "RU"),
            ("Дальневосточный федеральный округ2", "FD-DFO"),
            ("Южный                   федеральный округ4", "FD-YUFO"),
            ("г. Москва", "RU-MOW"),
            ("г.Санкт-Петербург", "RU-SPE"),
            ("в том числе: Ханты-Мансийский автономный округ - Югра", "RU-KHM"),
            ("в т.ч. Ненецкий авт. округ", "RU-NEN"),
            ("Еврейская авт. область", "RU-YEV"),
            ("Чукотский авт. округ", "RU-CHU"),
            ("Кемеровская область - Кузбасс", "RU-KEM"),
            ("Архангельская область", "RU-ARK-AGG"),
            ("Архангельская область без автономного округа", "RU-ARK"),
            ("     Архангельская область без Ненецкого авт.округа", "RU-ARK"),
            ("Тюменская область", "RU-TYU-AGG"),
            (
                "Тюменская область (без Ханты-Мансийского авт.округа-Югра "
                "и Ямало-Ненецкого авт.округа)",
                "RU-TYU",
            ),
            ("Тюменская область кроме Ханты-Мансийского автономного округа-Югры", "RU-TYU"),
            # Латиница в русских словах, слипшиеся слова и «АО» — безопасные исправления.
            ("Калинингpадская область", "RU-KGD"),
            ("г. Cанкт-Петербург", "RU-SPE"),
            ("Северо-Кавказскийфедеральный округ", "FD-SKFO"),
            ("Архангельская областьбез автономного округа", "RU-ARK"),
            ("Ямало-Ненецкий АО", "RU-YAN"),
            ("в т.ч. Ханты-Мансийский АО-Югра", "RU-KHM"),
        ],
    )
    def test_known_labels(self, label: str, code: str) -> None:
        assert territory_code(label) == code

    @pytest.mark.parametrize("label", ["Татарстан", "Алтай", "Пермская область", "Moscow"])
    def test_collection_stays_strict(self, label: str) -> None:
        assert territory_code(label) is None

    def test_notes_and_headings_are_not_territories(self) -> None:
        assert territory_code("в том числе:") is None
        assert territory_code("1 Данные за 2025 г. - предварительные данные") is None
        assert normalize("в том числе:") == ""

    def test_reference_has_85_regions(self) -> None:
        assert len(region_codes()) == 85


class TestSheet:
    """Разбор листа: шапка с объединёнными клетками, значения, сноски."""

    def _rows(self) -> list[list[Any]]:
        head = [
            ["Среднемесячная заработная плата", "", "", "", "", ""],
            ["Рублей", "", "", "", "", ""],
            ["", "2025 год1", "", "2026 год", "", ""],
            ["", "январь", "январь-\nфевраль ", "I квартал3", "I полугодие", "год"],
        ]
        body = [
            ["Российская Федерация", 100.0, 200.0, 300.0, 400.0, 500.0],
            ["Центральный федеральный округ", 1, 2, 3, 4, 5],
            *_region_rows([1.0, "1 234,5", "…", "-", ""]),
            ["1 Данные за 2025 г. - предварительные данные", "", "", "", "", ""],
            ["продолжение сноски", "", "", "", "", ""],
            ["3Данные за I квартал уточнены", "", "", "", "", ""],
        ]
        return head + body

    def test_columns_and_periods(self) -> None:
        table = parse_table("рублей", self._rows())
        periods = [(column.year, column.period) for column in table.columns]
        assert periods == [
            (2025, Period("month", 1)),
            (2025, Period("ytd", 2)),
            (2026, Period("quarter", 1)),
            (2026, Period("ytd", 6)),
            (2026, Period("ytd", 12)),
        ]
        assert table.columns[0].notes == (1,)
        assert table.title == "Среднемесячная заработная плата"

    def test_values_hidden_and_footnotes(self) -> None:
        table = parse_table("рублей", self._rows())
        table.check_regions()
        records = {(item.territory_code, item.year, item.period): item for item in table.records()}
        moscow = records[("RU-MOW", 2025, Period("ytd", 2))]
        assert moscow.value == pytest.approx(1234.5)
        assert records[("RU-MOW", 2026, Period("quarter", 1))].hidden
        assert records[("RU-MOW", 2026, Period("ytd", 6))].hidden
        # Пустая клетка — не значение и не скрытое.
        assert ("RU-MOW", 2026, Period("ytd", 12)) not in records
        assert table.footnotes[1].endswith("продолжение сноски")
        assert table.footnotes[3].startswith("Данные за I квартал")
        assert table.note_text((1,)).startswith("Данные за 2025 г.")

    def test_country_unit_is_scaled(self) -> None:
        rows = self._rows()
        rows[4][0] = "Российская Федерация  млрд. рублей"
        table = parse_table("рублей", rows)
        country = table.records(country_unit=("млрд", 1000.0))
        assert next(item for item in country if item.territory_code == "RU").value == 100_000.0

    def test_repeated_region_rows_are_merged(self) -> None:
        rows = self._rows()
        # Строка в другом округе заполняет годы, пустые в основной.
        rows.insert(5, ["Республика Бурятия", "", "", "", "", 7.0])
        table = parse_table("рублей", rows)
        values = {
            (item.year, item.period.number): item.value
            for item in table.records()
            if item.territory_code == "RU-BU" and item.value is not None
        }
        assert values[(2026, 12)] == 7.0
        assert values[(2025, 2)] == pytest.approx(1234.5)

    def test_conflicting_repeated_rows_break_parsing(self) -> None:
        rows = self._rows()
        rows.insert(5, ["Республика Бурятия", 7.0, 9.0, "", "", ""])
        with pytest.raises(SheetFormatError, match="повторяется"):
            parse_table("рублей", rows)

    def test_missing_region_row_breaks_parsing(self) -> None:
        rows = [row for row in self._rows() if row[0] != "Магаданская область"]
        table = parse_table("рублей", rows)
        with pytest.raises(SheetFormatError, match="RU-MAG"):
            table.check_regions()
        table.check_regions(absent=frozenset({"RU-MAG"}))

    def test_unrecognised_label_is_reported(self) -> None:
        rows = [row for row in self._rows() if row[0] != "Магаданская область"]
        rows.insert(10, ["Магаданская обл-ть", 1, 2, 3, 4, 5])
        table = parse_table("рублей", rows)
        with pytest.raises(SheetFormatError, match="Магаданская обл-ть"):
            table.check_regions()

    def test_sheet_without_country_row_breaks_parsing(self) -> None:
        rows = [row for row in self._rows() if row[0] != "Российская Федерация"]
        with pytest.raises(SheetFormatError, match="Российская Федерация"):
            parse_table("рублей", rows)

    def test_group_headers_select_columns(self) -> None:
        head = [
            ["Численность и состав рабочей силы", "", "", "", ""],
            ["", "Уровень занятости", "", "Уровень безработицы", ""],
            ["", "2024 год", "2025 год", "2024 год", "2025 год"],
        ]
        rows = [*head, ["Российская Федерация", 67.2, 67.7, 2.5, 2.2], *_region_rows([1, 2, 3, 4])]
        table = parse_table("2016-2025 гг.", rows)
        unemployment = [
            item
            for item in table.records(header="уровень безработицы")
            if item.territory_code == "RU"
        ]
        assert [(item.year, item.value, item.period.kind) for item in unemployment] == [
            (2024, 2.5, "annual"),
            (2025, 2.2, "annual"),
        ]
        with pytest.raises(SheetFormatError, match="нет столбцов"):
            table.records(header="уровень участия")

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (12, (12.0, False)),
            ("1 234,5", (1234.5, False)),
            ("56,7*", (56.7, False)),
            ("…", (None, True)),
            ("х", (None, True)),
            ("", (None, False)),
            (None, (None, False)),
        ],
    )
    def test_cells(self, raw: Any, expected: tuple[float | None, bool]) -> None:
        assert parse_cell(raw) == expected

    def test_text_in_value_cell_breaks_parsing(self) -> None:
        with pytest.raises(SheetFormatError, match="не число"):
            parse_cell("см. примечание")


class TestPreliminaryNotes:
    """Сноска отмечает предварительные значения только своего года."""

    @pytest.mark.parametrize(
        ("note", "year", "expected"),
        [
            ("Данные за 2025 г. - предварительные данные", 2025, True),
            ("Данные за 2026 г. - оценка", 2026, True),
            (
                "За 2017-2024 гг. - третья оценка, за периоды 2025 г. - вторая оценка "
                "в соответствии с Регламентом оценки",
                2024,
                False,
            ),
            (
                "За 2017-2024 гг. - третья оценка, за периоды 2025 г. - вторая оценка "
                "в соответствии с Регламентом оценки",
                2025,
                True,
            ),
            ("2025 г. - предварительные данные, 2026 г. - оценка.", 2026, True),
            ("Данные с января 2025 года по июнь 2026 г. уточнены", 2025, False),
            ("Предварительные данные.", 2025, True),
            ("", 2025, False),
        ],
    )
    def test_clause_of_the_year_decides(self, note: str, year: int, expected: bool) -> None:
        assert rosstat_bulletin.is_preliminary(note, year) is expected


@pytest.fixture(scope="module")
def parsed() -> Any:
    """Бюллетень, разобранный один раз на модуль."""
    return rosstat_bulletin.parse(FIXTURES / "bulletin")


class TestBulletin:
    """Бюллетень на урезанных настоящих таблицах выпуска за январь — июль 2026 года."""

    def test_all_measures_and_regions(self, parsed: Any) -> None:
        frame: pd.DataFrame = parsed.frame
        assert set(frame["measure"]) == {spec.measure for spec in rosstat_bulletin.TABLES} | {
            rosstat_bulletin.RECENT_UNEMPLOYMENT[1]
        }
        for measure, info in parsed.report["tables"].items():
            assert info["regions"] >= 85, measure
            assert info["unmatched"] == [], measure

    def test_known_values(self, parsed: Any) -> None:
        frame: pd.DataFrame = parsed.frame

        def value(measure: str, code: str, year: int, kind: str, number: int) -> float:
            rows = frame[
                (frame["measure"] == measure)
                & (frame["territory_code"] == code)
                & (frame["year"] == year)
                & (frame["period_kind"] == kind)
                & (frame["period"] == number)
            ]
            return float(rows["value"].iloc[0])

        assert value("wage", "RU", 2025, "month", 12) == 139_727
        assert value("unemployment_rate", "RU", 2025, "annual", 12) == pytest.approx(2.2098)
        assert value("price_primary", "RU", 2025, "quarter", 4) == 215_282
        # Строка страны в млрд руб. и млн м² приводится к единице субъектов.
        assert value("retail", "RU", 2024, "ytd", 12) > 50_000_000
        assert value("housing", "RU", 2024, "ytd", 12) == pytest.approx(107_767.526)

    def test_preliminary_years_from_footnotes(self, parsed: Any) -> None:
        tables = parsed.report["tables"]
        assert tables["income"]["preliminary_years"] == [2025, 2026]
        assert tables["investment"]["preliminary_years"] == [2025]
        assert tables["wage"]["preliminary_years"] == []

    def test_changed_layout_breaks_parsing(self, tmp_path: Path) -> None:
        for path in (FIXTURES / "bulletin").iterdir():
            if not path.name.startswith("09-06"):
                (tmp_path / path.name).write_bytes(path.read_bytes())
        with pytest.raises(SheetFormatError, match="нет таблицы 09-06"):
            rosstat_bulletin.parse(tmp_path)

    def test_identify_and_next(self) -> None:
        candidate = rosstat_bulletin.identify(
            "info-stat-07-2026.zip", "https://x/info-stat-07-2026.zip"
        )
        assert (candidate.release_code, candidate.reference_year) == ("07-2026", 2026)
        assert candidate.title == "январь — июль 2026 г."
        following = rosstat_bulletin.next_candidates("12-2025")
        assert [item.url.rsplit("/", 1)[-1] for item in following] == [
            "info-stat-01-2026.zip",
            "info-stat-01-2026.rar",
        ]
        with pytest.raises(ValueError, match="не похоже"):
            rosstat_bulletin.identify("Pril_Region_Pokaz_2025.rar")

    def test_discover_reads_page(self, monkeypatch: pytest.MonkeyPatch) -> None:
        page = (FIXTURES / "bulletin_page.html").read_bytes().decode("utf-8")
        monkeypatch.setattr(rosstat_bulletin.network, "page", lambda url: page)
        found = rosstat_bulletin.discover()
        assert [item.release_code for item in found] == ["07-2026", "12-2016"]
        assert found[0].published_on is not None
        assert found[0].published_on.isoformat() == "2026-09-02"
        assert found[0].url == "https://rosstat.gov.ru/storage/mediabank/info-stat-07-2026.zip"


class TestGrp:
    """Таблица ВРП: ранние и поздние листы, итоговая строка под своим названием."""

    def test_parse(self, tmp_path: Path) -> None:
        (tmp_path / "VRP_s_1998.xlsx").write_bytes((FIXTURES / "VRP_s_1998.xlsx").read_bytes())
        parsed = rosstat_grp.parse(tmp_path)
        frame = parsed.frame
        grp = frame[(frame["measure"] == "grp") & (frame["territory_code"] == "RU")]
        assert set(grp["year"]) == set(range(1998, 2025))
        assert float(grp[grp["year"] == 2024]["value"].iloc[0]) == pytest.approx(186_545_082.1)
        per_person = frame[(frame["measure"] == "grp_per_capita") & (frame["year"] == 2024)]
        assert {"RU-ARK", "RU-NEN", "RU-TYU", "RU-KHM"} <= set(per_person["territory_code"])
        index = frame[(frame["measure"] == "grp_index") & (frame["territory_code"] == "RU")]
        assert min(index["year"]) == 1998
        assert float(index[index["year"] == 2024]["value"].iloc[0]) == pytest.approx(104.5)

    def test_footnotes_go_with_measures(self, tmp_path: Path) -> None:
        """Сноски листов — к показателям этих листов, без повторов."""
        (tmp_path / "VRP_s_1998.xlsx").write_bytes((FIXTURES / "VRP_s_1998.xlsx").read_bytes())
        notes = rosstat_grp.parse(tmp_path).notes
        assert len(notes) == len(set(notes))
        since_2016 = {measure for measure, text in notes if "начиная с 2016 года" in text}
        since_2017 = {measure for measure, text in notes if "начиная с 2017 года" in text}
        assert since_2016 == since_2017 == {"grp", "grp_per_capita", "grp_index"}
        census = {measure for measure, text in notes if "ВПН-2020" in text}
        assert census == {"grp_per_capita"}
