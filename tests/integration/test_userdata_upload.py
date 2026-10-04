"""
Приём своих таблиц через мастер: файл и вставка, перечень таблиц, выбор, доступ.

Файлы наборов пишутся во временный каталог; чужой набор — 404 при любом обращении.
"""

from __future__ import annotations

import io
import shutil
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from apps.userdata import ingest
from apps.userdata.models import Dataset

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "userdata"


@pytest.fixture(autouse=True)
def userdata_dir(settings: Any, tmp_path: Path) -> Iterator[Path]:
    """Каталог таблиц теста."""
    settings.USERDATA_DIR = tmp_path / "userdata"
    yield settings.USERDATA_DIR
    shutil.rmtree(settings.USERDATA_DIR, ignore_errors=True)


def _upload(client: Client, name: str, data: bytes) -> Any:
    return client.post(
        reverse("userdata:upload"),
        {"action": "upload", "file": SimpleUploadedFile(name, data)},
    )


def _ebt_archive() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("data/environment.csv", (FIXTURES / "environment.csv").read_bytes())
        bundle.writestr("data/crime_wide.csv", (FIXTURES / "crime_wide.csv").read_bytes())
        bundle.writestr("data/description.pdf", b"%PDF-1.4")
        bundle.writestr("__MACOSX/data/._environment.csv", b"\x00")
    return buffer.getvalue()


@pytest.mark.django_db
class TestUpload:
    """Файл принимается, перечень таблиц показывается, непригодный — отказ без следов."""

    def test_page_opens(self, client: Client) -> None:
        response = client.get(reverse("userdata:upload"))
        assert response.status_code == 200
        assert 'data-max-bytes="26214400"' in response.text
        assert 'enctype="multipart/form-data"' in response.text

    def test_archive_lists_tables(self, client: Client, userdata_dir: Path) -> None:
        response = _upload(client, "data_environment.zip", _ebt_archive())
        dataset = Dataset.objects.get()
        assert response.status_code == 302
        assert response["Location"] == reverse("userdata:file", args=[dataset.public_id])
        version = dataset.current_version
        assert version is not None
        assert (version.file_kind, version.number) == (ingest.ZIP, 1)
        assert len(version.sha256) == 64
        assert dataset.guest_key and dataset.owner is None and dataset.expires_at
        assert (version.directory / "source.zip").exists()

        page = client.get(response["Location"])
        assert page.status_code == 200
        assert "data/environment.csv" in page.text
        assert "data/crime_wide.csv" in page.text
        assert "Узнано субъектов: 85 из 85" in page.text
        assert "description.pdf" in page.text
        assert "__MACOSX" not in page.text

    def test_choose_table_extracts_only_it(self, client: Client) -> None:
        _upload(client, "data.zip", _ebt_archive())
        dataset = Dataset.objects.get()
        response = client.post(
            reverse("userdata:file", args=[dataset.public_id]),
            {"action": "choose", "table": "data/crime_wide.csv", "encoding": ""},
        )
        assert response.status_code == 302
        assert response["Location"] == reverse("userdata:table", args=[dataset.public_id])
        version = dataset.versions.get()
        assert version.recipe["table"]["key"] == "data/crime_wide.csv"
        assert version.recipe["table"]["regions"] == 85
        assert sorted(path.name for path in version.directory.iterdir()) == [
            "source.zip",
            "table.csv",
        ]
        assert client.get(response["Location"]).status_code == 200

    @pytest.mark.parametrize(
        ("name", "data", "text"),
        [
            ("report.pdf", b"%PDF-1.7 ...", "Это не таблица"),
            ("archive.rar", b"Rar!\x1a\x07\x01\x00", "RAR"),
            ("photo.jpg", b"\xff\xd8\xff\xe0", "изображение"),
        ],
    )
    def test_not_a_table_leaves_nothing(
        self, client: Client, userdata_dir: Path, name: str, data: bytes, text: str
    ) -> None:
        response = _upload(client, name, data)
        assert response.status_code == 200
        assert text in response.text
        assert not Dataset.objects.exists()
        assert not any(userdata_dir.glob("*/*")) if userdata_dir.exists() else True

    def test_upload_too_large(self, client: Client, settings: Any) -> None:
        settings.USERDATA_UPLOAD_MAX_BYTES = 100
        response = _upload(client, "big.csv", b"a;b\n" * 100)
        assert "Файл больше 0 МБ" in response.text
        assert not Dataset.objects.exists()

    def test_windows_1251_csv(self, client: Client) -> None:
        source = (FIXTURES / "crime_wide.csv").read_bytes().decode("utf-8")
        _upload(client, "выгрузка из 1С.csv", source.encode("cp1251", errors="replace"))
        dataset = Dataset.objects.get()
        assert dataset.title == "выгрузка из 1С"
        table = dataset.current_version.report["inspection"]["tables"][0]  # type: ignore[union-attr]
        assert (table["encoding"], table["regions"]) == ("cp1251", 85)

    def test_encoding_can_be_chosen(self, client: Client) -> None:
        _upload(client, "t.csv", "Регион;2023\nМосква;1,5\n".encode("cp1251"))
        dataset = Dataset.objects.get()
        url = reverse("userdata:file", args=[dataset.public_id])
        assert "Текст читается с ошибками" in client.get(url, {"encoding": "utf-8"}).text
        assert "Москва" in client.get(url, {"encoding": "cp1251"}).text


@pytest.mark.django_db
class TestPaste:
    """Таблица из Word вставляется двумя частями."""

    def test_two_parts(self, client: Client) -> None:
        first = (FIXTURES / "regions_population_part1.txt").read_text(encoding="utf-8")
        markup = (FIXTURES / "regions_population_part1.html").read_text(encoding="utf-8")
        response = client.post(
            reverse("userdata:upload"), {"action": "paste", "text": first, "markup": markup}
        )
        dataset = Dataset.objects.get()
        assert response.status_code == 302
        version = dataset.versions.get()
        assert version.file_kind == ingest.PASTE
        first_regions = version.report["inspection"]["tables"][0]["regions"]
        assert 30 < first_regions < 85

        second = (FIXTURES / "regions_population_part2.txt").read_text(encoding="utf-8")
        response = client.post(
            reverse("userdata:file", args=[dataset.public_id]), {"action": "append", "text": second}
        )
        assert response.status_code == 302
        version.refresh_from_db()
        assert version.report["inspection"]["tables"][0]["regions"] == 85

    def test_empty_paste(self, client: Client) -> None:
        response = client.post(
            reverse("userdata:upload"), {"action": "paste", "text": "одна строка"}
        )
        assert "Таблица не распознана" in response.text
        assert not Dataset.objects.exists()


@pytest.mark.django_db
class TestAccess:
    """Таблицу видит только владелец; гость — по ключу своего сеанса."""

    def test_other_guest_gets_404(self, client: Client) -> None:
        _upload(client, "t.csv", (FIXTURES / "crime_wide.csv").read_bytes())
        dataset = Dataset.objects.get()
        stranger = Client()
        for name in ("userdata:file", "userdata:table"):
            assert stranger.get(reverse(name, args=[dataset.public_id])).status_code == 404
        response = stranger.post(
            reverse("userdata:file", args=[dataset.public_id]), {"action": "choose", "table": ""}
        )
        assert response.status_code == 404

    def test_owner_and_other_user(self, member_client: Client, make_user: Any) -> None:
        _upload(member_client, "t.csv", (FIXTURES / "crime_wide.csv").read_bytes())
        dataset = Dataset.objects.get()
        assert dataset.owner is not None and not dataset.guest_key
        url = reverse("userdata:file", args=[dataset.public_id])
        assert member_client.get(url).status_code == 200
        other = Client()
        other.force_login(make_user(email="other@example.com"))
        assert other.get(url).status_code == 404

    def test_unknown_dataset(self, client: Client) -> None:
        url = reverse("userdata:file", args=["00000000-0000-0000-0000-000000000000"])
        assert client.get(url).status_code == 404
