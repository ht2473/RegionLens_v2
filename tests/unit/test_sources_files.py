"""Архив выпусков, распаковка ZIP и RAR, обращения к сайтам источников."""

from __future__ import annotations

import io
import os
import stat
import urllib.error
import zipfile
from datetime import UTC, datetime
from email.message import Message
from pathlib import Path
from typing import Any

import pytest

from apps.sources import archive, network, parsed, unpack
from apps.sources.base import PARSED_COLUMNS


@pytest.fixture
def archive_dir(settings: Any, tmp_path: Path) -> Path:
    settings.SOURCE_ARCHIVE_DIR = tmp_path / "archive"
    settings.SOURCE_PARSED_DIR = tmp_path / "sources"
    return settings.SOURCE_ARCHIVE_DIR


class TestArchive:
    """Файл кладётся один раз, опись сходится с файлами."""

    def test_store_once(self, archive_dir: Path) -> None:
        moment = datetime(2026, 9, 24, 7, 30, tzinfo=UTC)
        record, is_new = archive.store(
            "rosstat_bulletin",
            "info-stat-07-2026.zip",
            b"release",
            url="https://x",
            captured_at=moment,
        )
        assert is_new
        assert record.path == "rosstat_bulletin/2026-09-24_info-stat-07-2026.zip"
        assert record.absolute_path.read_bytes() == b"release"
        assert not os.access(record.absolute_path, os.W_OK)

        again, is_new = archive.store("rosstat_bulletin", "copy.zip", b"release")
        assert not is_new
        assert again == record
        assert len(archive.entries()) == 1

    def test_same_name_other_content(self, archive_dir: Path) -> None:
        moment = datetime(2026, 9, 24, tzinfo=UTC)
        first, _ = archive.store("rosstat_grp", "VRP_s_1998.xlsx", b"one", captured_at=moment)
        second, _ = archive.store("rosstat_grp", "VRP_s_1998.xlsx", b"two", captured_at=moment)
        assert first.path != second.path
        assert [item.sha256 for item in archive.entries("rosstat_grp")] == [
            first.sha256,
            second.sha256,
        ]
        assert archive.entries("rosstat_bulletin") == []

    def test_verify(self, archive_dir: Path) -> None:
        record, _ = archive.store("rosstat_grp", "VRP_s_1998.xlsx", b"one")
        assert archive.verify() == []
        path = record.absolute_path
        path.chmod(stat.S_IWUSR | stat.S_IRUSR)
        path.write_bytes(b"changed")
        assert archive.verify() == [f"{record.path}: хэш не совпадает с описью"]
        path.unlink()
        assert archive.verify() == [f"{record.path}: файла нет"]


class TestParsedFiles:
    """Разобранный выпуск хранит сведения о себе."""

    def test_roundtrip_and_order(self, archive_dir: Path) -> None:
        import pandas as pd

        frame = pd.DataFrame(
            [["wage", "RU", 2025, "month", 12, 1.0, False, False]], columns=list(PARSED_COLUMNS)
        )
        for code, day in (("07-2026", "2026-09-02"), ("03-2026", "2026-05-08")):
            parsed.write(
                parsed.ReleaseInfo(
                    source="rosstat_bulletin",
                    code=code,
                    title=code,
                    reference_year=2026,
                    published_on=day,
                    fetched_at=f"{day}T10:00:00+00:00",
                    sha256=code.replace("-", "") * 8,
                    url="",
                    parser_version=1,
                ),
                frame,
            )
        found = parsed.releases("rosstat_bulletin")
        assert [info.code for info, _path in found] == ["03-2026", "07-2026"]
        info, path = found[-1]
        assert info.edition_code.startswith("rel_rosstat_bulletin_07-2026_")
        assert parsed.read(path)["value"].tolist() == [1.0]
        assert parsed.read_notes(path) == []
        parsed.remove("rosstat_bulletin", info.code, info.sha256)
        assert len(parsed.releases()) == 1

    def test_notes_go_with_the_release(self, archive_dir: Path) -> None:
        """Сноски к показателям записываются в файл выпуска и читаются в том же порядке."""
        import pandas as pd

        frame = pd.DataFrame(
            [["grp", "RU", 2024, "annual", 12, 1.0, False, False]], columns=list(PARSED_COLUMNS)
        )
        notes = [("grp", "Данные динамического ряда, начиная с 2016 года…"), ("grp_index", "Без…")]
        path = parsed.write(
            parsed.ReleaseInfo(
                source="rosstat_grp",
                code="2026-03-06",
                title="по состоянию на 06.03.2026",
                reference_year=2026,
                published_on="2026-03-06",
                fetched_at="2026-03-10T10:00:00+00:00",
                sha256="a" * 64,
                url="",
                parser_version=2,
            ),
            frame,
            notes,
        )
        assert parsed.read_notes(path) == notes


class TestUnpack:
    """ZIP с именами cp866, защита от путей наружу, RAR без распаковщика."""

    def test_cp866_names(self, tmp_path: Path) -> None:
        # Имя пишется латинской заглушкой той же длины и подменяется байтами cp866:
        # так архив получается без флага UTF-8, как у Росстата.
        name = "12 заработная плата/12-01 зарплата.xlsx"
        placeholder = "q" * len(name)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as bundle:
            bundle.writestr(placeholder, b"x")
            bundle.writestr("../../escape.txt", b"no")
        content = buffer.getvalue().replace(placeholder.encode(), name.encode("cp866"))
        source = tmp_path / "release.zip"
        source.write_bytes(content)
        target = unpack.unpack(source, tmp_path / "out")
        files = sorted(
            path.relative_to(target).as_posix() for path in target.rglob("*") if path.is_file()
        )
        assert files == ["12 заработная плата/12-01 зарплата.xlsx"]
        assert not (tmp_path / "escape.txt").exists()

    def test_broken_zip(self, tmp_path: Path) -> None:
        source = tmp_path / "release.zip"
        source.write_bytes(b"not a zip")
        with pytest.raises(unpack.UnpackError, match="повреждённый"):
            unpack.unpack(source, tmp_path / "out")

    def test_rar_without_tool(
        self, settings: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(unpack, "bsdtar", lambda: None)
        source = tmp_path / "release.rar"
        source.write_bytes(b"Rar!")
        with pytest.raises(unpack.UnpackError, match="bsdtar"):
            unpack.unpack(source, tmp_path / "out")

    def test_other_file_is_copied(self, tmp_path: Path) -> None:
        source = tmp_path / "VRP_s_1998.xlsx"
        source.write_bytes(b"xlsx")
        target = unpack.unpack(source, tmp_path / "out")
        assert (target / "VRP_s_1998.xlsx").read_bytes() == b"xlsx"


class _Response:
    def __init__(self, body: bytes, headers: dict[str, str], status: int = 200) -> None:
        self._body = body
        self.status = status
        self.headers = Message()
        for key, value in headers.items():
            self.headers[key] = value

    def read(self, limit: int = -1) -> bytes:
        return self._body

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        return None


class TestNetwork:
    """Повторы при обрыве, отсутствующий файл, сведения из заголовков."""

    @pytest.fixture(autouse=True)
    def _fast(self, monkeypatch: pytest.MonkeyPatch, settings: Any) -> None:
        monkeypatch.setattr(network.time, "sleep", lambda seconds: None)
        settings.SOURCE_HTTP_ATTEMPTS = 3

    def _opener(self, monkeypatch: pytest.MonkeyPatch, outcomes: list[Any]) -> list[Any]:
        calls: list[Any] = []

        class Opener:
            def open(self, request: Any, timeout: int) -> Any:
                calls.append(request)
                outcome = outcomes.pop(0)
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome

        monkeypatch.setattr(network, "_opener", Opener)
        return calls

    def test_retry_then_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        headers = {
            "Content-Length": "4",
            "Last-Modified": "Fri, 06 Mar 2026 07:21:15 GMT",
            "ETag": '"69aa806b-37df3"',
        }
        calls = self._opener(
            monkeypatch,
            [urllib.error.URLError("handshake timed out"), _Response(b"data", headers)],
        )
        content, remote = network.download("https://rosstat.gov.ru/storage/mediabank/x.xlsx")
        assert content == b"data"
        assert remote.size == 4
        assert remote.etag == "69aa806b-37df3"
        assert remote.modified is not None
        assert remote.modified.date().isoformat() == "2026-03-06"
        assert len(calls) == 2
        assert calls[0].get_header("User-agent").startswith("RegionLens/")

    def test_gives_up(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._opener(monkeypatch, [TimeoutError(), TimeoutError(), TimeoutError()])
        with pytest.raises(network.FetchError, match="3 попыток"):
            network.page("https://rosstat.gov.ru/")

    def test_truncated_download_is_retried(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._opener(
            monkeypatch,
            [
                _Response(b"da", {"Content-Length": "4"}),
                _Response(b"data", {"Content-Length": "4"}),
            ],
        )
        assert network.download("https://x/file")[0] == b"data"

    def test_missing_file(self, monkeypatch: pytest.MonkeyPatch) -> None:
        error = urllib.error.HTTPError("https://x/f", 404, "Not Found", Message(), None)
        self._opener(monkeypatch, [error])
        assert network.head("https://x/f").status == 404

    def test_ca_bundle_is_loaded(self, settings: Any) -> None:
        assert settings.SOURCE_EXTRA_CA_FILE.exists()
        network._ssl_context.cache_clear()
        context = network._ssl_context()
        subjects = [
            dict(item[0] for item in certificate["subject"]).get("commonName")
            for certificate in context.get_ca_certs()
        ]
        assert "Russian Trusted Root CA" in subjects
