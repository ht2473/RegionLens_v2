"""
Разбор выпусков Банка России и ФНС и листа безработицы за три месяца бюллетеня Росстата
на урезанных настоящих ответах.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from apps.sources import cbr, fns_sme, network, rosstat_bulletin
from apps.sources.sheets import SheetFormatError, open_sheets
from apps.sources.territories import region_codes, territory_code
from apps.sources.unpack import unpack

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "sources"
CBR_FILE = FIXTURES / "cbr" / "cbr-dataservice-2026-09-08.zip"


def _cbr_files(tmp_path: Path, change: Any = None) -> Path:
    """Распакованный снимок Банка России; ``change(name, payload)`` правит ответ."""
    target = tmp_path / "cbr"
    unpack(CBR_FILE, target)
    if change is not None:
        for path in target.iterdir():
            payload = json.loads(path.read_bytes().decode("utf-8"))
            change(path.name, payload)
            path.write_bytes(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    return target


def _fns_files(tmp_path: Path, day: str, change: Any = None) -> Path:
    """Каталог с одним ответом реестра МСП; ``change(rows)`` правит строки."""
    target = tmp_path / "fns"
    target.mkdir()
    rows = json.loads((FIXTURES / "fns" / f"rmsp-statistics-{day}.json").read_bytes())
    if change is not None:
        change(rows)
    (target / f"rmsp-statistics-{day}.json").write_bytes(
        json.dumps(rows, ensure_ascii=False).encode("utf-8")
    )
    return target


class TestTerritoryNames:
    """Подписи Банка России и ФНС сопоставляются со справочником."""

    @pytest.mark.parametrize(
        ("label", "code"),
        [
            ("Республика Адыгея (Адыгея)", "RU-AD"),
            ("Республика Татарстан (Татарстан)", "RU-TA"),
            ("Чувашская Республика - Чувашия", "RU-CU"),
            ("г.Санкт-Петербург", "RU-SPE"),
        ],
    )
    def test_variants(self, label: str, code: str) -> None:
        assert territory_code(label) == code

    def test_source_aliases_come_first(self) -> None:
        assert territory_code("Архангельская область") == "RU-ARK-AGG"
        assert territory_code("Архангельская область", fns_sme.ALIASES) == "RU-ARK"
        assert territory_code("Тюменская область", fns_sme.ALIASES) == "RU-TYU"


@pytest.fixture(scope="module")
def parsed(tmp_path_factory: pytest.TempPathFactory) -> Any:
    """Разобранный снимок Банка России."""
    return cbr.parse(_cbr_files(tmp_path_factory.mktemp("cbr")))


class TestCbr:
    """Снимок сервиса данных Банка России."""

    def test_all_measures_and_regions(self, parsed: Any) -> None:
        frame = parsed.frame
        assert set(frame["measure"]) == {spec.measure for spec in cbr.MEASURES}
        for measure, info in parsed.report["tables"].items():
            assert info["regions"] == len(region_codes()), measure
            assert info["unmatched"] == [], measure
            assert info["last"] == "2026-07", measure
        assert set(frame["period_kind"]) == {"month"}
        assert {
            (int(year), int(month))
            for year, month in zip(frame["year"], frame["period"], strict=True)
        } == {
            (2025, 12),
            (2026, 1),
            (2026, 7),
        }

    def test_okrug_rows(self, parsed: Any) -> None:
        """«…, в том числе Ненецкий автономный округ» — сам округ: вместе с областью — итог."""
        frame = parsed.frame
        rows = frame[
            (frame["measure"] == "mortgage_debt") & (frame["year"] == 2026) & (frame["period"] == 7)
        ].set_index("territory_code")["value"]
        assert rows["RU-NEN"] + rows["RU-ARK"] == pytest.approx(rows["RU-ARK-AGG"])
        assert rows["RU-KHM"] + rows["RU-YAN"] + rows["RU-TYU"] == pytest.approx(rows["RU-TYU-AGG"])
        assert {"RU", "FD-CFO"} <= set(rows.index)

    def test_changed_unit_is_a_format_error(self, tmp_path: Path) -> None:
        def change(name: str, payload: Any) -> None:
            if name == "datasetsEx_21.json":
                for unit in payload["units"]:
                    if unit["name"] == "млн руб.":
                        unit["name"] = "млрд руб."

        with pytest.raises(SheetFormatError, match="единица"):
            cbr.parse(_cbr_files(tmp_path, change))

    def test_missing_region_is_a_format_error(self, tmp_path: Path) -> None:
        def change(name: str, payload: Any) -> None:
            if name == "datasetsEx_21.json":
                for measure in payload["measures_1"]:
                    if measure["name"] == "Чукотский автономный округ":
                        measure["name"] = "Чукотский край"

        with pytest.raises(SheetFormatError, match="RU-CHU"):
            cbr.parse(_cbr_files(tmp_path, change))

    def test_period_labels(self) -> None:
        assert cbr.period_of("Июль 2026") == (2026, 7)
        assert cbr.period_of("Январь 2019") == (2019, 1)
        with pytest.raises(SheetFormatError):
            cbr.period_of("II квартал 2026")

    def test_queries_group_indicators(self) -> None:
        queries = cbr.queries()
        assert (21, 35, (44, 45, 46, 47)) in queries
        assert (21, 36, (48,)) in queries
        assert (15, 36, (34,)) in queries
        assert sum(len(indicators) for _pub, _element, indicators in queries) == len(cbr.MEASURES)

    def test_bundle_is_reproducible(self) -> None:
        files = {"b.json": b"[2]", "a.json": b"[1]"}
        first = cbr.bundle(files)
        assert first == cbr.bundle(dict(reversed(list(files.items()))))
        with zipfile.ZipFile(io.BytesIO(first)) as archive:
            assert archive.namelist() == ["a.json", "b.json"]

    def test_discover_takes_latest_update(self, monkeypatch: pytest.MonkeyPatch) -> None:
        updates = {21: "2026-08-31T00:00:00", 15: "2026-09-08T00:00:00"}

        def download(url: str) -> tuple[bytes, network.RemoteFile]:
            publication = int(url.rsplit("=", 1)[1])
            wanted = [spec.indicator for spec in cbr.MEASURES if spec.publication == publication]
            listing = [
                {"id": indicator, "updated_time": updates.get(publication, "2026-09-01T00:00:00")}
                for indicator in wanted
            ]
            listing.append({"id": 999, "updated_time": "2027-01-01T00:00:00"})
            content = json.dumps(listing).encode()
            return content, network.RemoteFile(url, 200, len(content), None, "")

        monkeypatch.setattr(network, "download", download)
        [candidate] = cbr.discover()
        assert candidate.release_code == "2026-09-08"
        assert candidate.published_on == date(2026, 9, 8)
        assert candidate.file_name == "cbr-dataservice-2026-09-08.zip"

    def test_identify(self) -> None:
        candidate = cbr.identify("cbr-dataservice-2026-09-08.zip")
        assert candidate.release_code == "2026-09-08"
        with pytest.raises(ValueError, match="снимок"):
            cbr.identify("dataEx.json")


class TestFns:
    """Сведения реестра МСП на дату."""

    def test_recent_date(self, tmp_path: Path) -> None:
        parsed = fns_sme.parse(_fns_files(tmp_path, "2026-09-10"))
        frame = parsed.frame
        assert set(frame["measure"]) == {"sme_count", "sme_workers"}
        assert set(frame["period"]) == {9}
        assert set(frame["year"]) == {2026}
        regions = set(frame["territory_code"]) & region_codes()
        assert regions == region_codes()
        assert parsed.report["tables"]["sme_count"]["regions"] == len(region_codes())
        outside = {code for code in frame["territory_code"] if code.startswith("ext:")}
        assert len(outside) == 5  # четыре субъекта и их группа
        count = frame[frame["measure"] == "sme_count"].set_index("territory_code")["value"]
        # Россия у ФНС — сумма всех субъектов, включая субъекты вне справочника.
        subjects = [
            code
            for code in count.index
            if code in region_codes() or (code.startswith("ext:") and "субъекты" not in code)
        ]
        assert len(subjects) == len(region_codes()) + 4
        assert count["RU"] == pytest.approx(sum(count[code] for code in subjects))

    def test_early_date_without_workers_and_okrug(self, tmp_path: Path) -> None:
        parsed = fns_sme.parse(_fns_files(tmp_path, "2016-09-10"))
        assert set(parsed.frame["measure"]) == {"sme_count"}
        assert "RU-NEN" not in set(parsed.frame["territory_code"])

    def test_outside_rows_without_workers(self, tmp_path: Path) -> None:
        parsed = fns_sme.parse(_fns_files(tmp_path, "2022-12-10"))
        workers = parsed.frame[parsed.frame["measure"] == "sme_workers"]
        missing = workers[workers["value"].isna()]["territory_code"]
        assert all(code.startswith("ext:") for code in missing)
        assert len(missing) > 0

    def test_missing_field_of_own_region_is_a_format_error(self, tmp_path: Path) -> None:
        def change(rows: list[dict[str, Any]]) -> None:
            for row in rows:
                if row["cnt_name"] == "Белгородская область":
                    del row["cnt_worker_total"]

        with pytest.raises(SheetFormatError, match="Белгородская"):
            fns_sme.parse(_fns_files(tmp_path, "2026-09-10", change))

    def test_missing_region_is_a_format_error(self, tmp_path: Path) -> None:
        def change(rows: list[dict[str, Any]]) -> None:
            rows[:] = [row for row in rows if row["cnt_name"] != "Ненецкий автономный округ"]

        with pytest.raises(SheetFormatError, match="RU-NEN"):
            fns_sme.parse(_fns_files(tmp_path, "2026-09-10", change))

    def test_discover_reads_the_date_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        page = (FIXTURES / "fns" / "statistics_page.html").read_bytes().decode("utf-8")
        monkeypatch.setattr(network, "page", lambda url: page)
        candidates = fns_sme.discover()
        assert candidates[0].release_code == "2026-09-10"
        assert candidates[-1].release_code == "2016-08-01"
        assert len(candidates) == 122

    def test_fetch_posts_the_date(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sent: dict[str, Any] = {}

        def post(url: str, form: dict[str, str]) -> tuple[bytes, network.RemoteFile]:
            sent.update(url=url, form=form)
            return b"[]", network.RemoteFile(url, 200, 2, None, "")

        monkeypatch.setattr(network, "post", post)
        assert fns_sme.fetch(fns_sme.identify("rmsp-statistics-2026-09-10.json")) == b"[]"
        assert sent == {"url": fns_sme.DATA_URL, "form": {"statDate": "10.09.2026"}}

    def test_collects_history(self) -> None:
        assert fns_sme.collect_history
        assert fns_sme.max_per_check is None


class TestRecentUnemployment:
    """Лист «в среднем за период» таблицы 13-01 бюллетеня."""

    def test_sheet_of_july_release(self) -> None:
        path = next((FIXTURES / "bulletin").glob("13-01*.xlsx"))
        frame, details = rosstat_bulletin.recent_unemployment(open_sheets(path), "unemployment_3m")
        assert details["period"] == "2026-07"
        assert details["regions"] == len(region_codes())
        country = frame[frame["territory_code"] == "RU"].iloc[0]
        assert (int(country["year"]), int(country["period"])) == (2026, 7)
        assert country["value"] == pytest.approx(2.2065)

    @pytest.mark.parametrize(
        ("label", "expected"),
        [
            ("за октябрь - декабрь 2024 г. (в среднем за период)", (2024, 12)),
            ("январь 2026 г. -март 2026 г. (в среднем за период)", (2026, 3)),
            ("май 2026 г. - июль 2026 г. (в среднем за пе", (2026, 7)),
            ("ноябрь 2025 г. - январь 2026 г. (в среднем за период)", (2026, 1)),
        ],
    )
    def test_period_end(self, label: str, expected: tuple[int, int]) -> None:
        assert rosstat_bulletin._period_end(label) == expected

    def test_without_the_sheet(self, tmp_path: Path) -> None:
        with pytest.raises(SheetFormatError, match="в среднем за период"):
            rosstat_bulletin.recent_unemployment({"2016-2025 гг.": [["", ""]]}, "unemployment_3m")
