"""
Заведомо плохие файлы в загрузке «Своих данных»: отказ словами, без ошибки сервера,
без следов на диске и без чтения огромных частей в память.
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
from openpyxl import Workbook

from apps.sources.unpack import _safe_relative
from apps.userdata.models import Dataset

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

ROWS = "Субъект;2024\nПсковская область;1,5\nТверская область;2,5\n".encode()


@pytest.fixture(autouse=True)
def userdata_dir(settings: Any, tmp_path: Path) -> Iterator[Path]:
    """Каталог таблиц теста."""
    settings.USERDATA_DIR = tmp_path / "userdata"
    yield settings.USERDATA_DIR
    shutil.rmtree(settings.USERDATA_DIR, ignore_errors=True)


def upload(client: Client, name: str, data: bytes) -> Any:
    return client.post(
        reverse("userdata:upload"), {"action": "upload", "file": SimpleUploadedFile(name, data)}
    )


def archive(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name, data in members.items():
            bundle.writestr(name, data)
    return buffer.getvalue()


def workbook_with_shared_strings(shared: bytes) -> bytes:
    """Книга XLSX с одной строкой текста, у которой подменён файл общих строк."""
    book = Workbook()
    book.active["A1"] = "Субъект"
    book.active["B1"] = 1
    buffer = io.BytesIO()
    book.save(buffer)
    source = zipfile.ZipFile(io.BytesIO(buffer.getvalue()))
    members = {item.filename: source.read(item.filename) for item in source.infolist()}
    members["xl/sharedStrings.xml"] = shared
    return archive(members)


def refused(response: Any, text: str) -> bool:
    return response.status_code == 200 and text in response.text and not Dataset.objects.exists()


class TestArchives:
    """Пути наружу, пароль и сжатие проверяются до распаковки."""

    @pytest.mark.parametrize(
        "name", ["../../evil.csv", "/etc/evil.csv", "C:/Windows/evil.csv", "..\\..\\evil.csv"]
    )
    def test_path_outside_is_refused(self, client: Client, userdata_dir: Path, name: str) -> None:
        """Файл с путём наружу не принимается, и на диске ничего не остаётся."""
        response = upload(client, "data.zip", archive({name: ROWS}))

        assert refused(response, "путями вне архива")
        assert not list(userdata_dir.parent.rglob("evil.csv"))

    def test_encrypted_member_is_refused(self, client: Client) -> None:
        """Файл под паролем отклоняется по флагу архива, не читаясь."""
        data = bytearray(archive({"secret.csv": ROWS}))
        data[data.find(b"PK\x03\x04") + 6] |= 0x1
        data[data.find(b"PK\x01\x02") + 8] |= 0x1

        assert refused(upload(client, "data.zip", bytes(data)), "паролем")

    def test_zip_bomb_is_refused(self, client: Client) -> None:
        """Сжатый в сотни раз файл не распаковывается."""
        response = upload(client, "data.zip", archive({"data.csv": b"0" * (3 * 1024 * 1024)}))

        assert refused(response, "сжат слишком сильно")


class TestWorkbooks:
    """Книга XLSX — тоже архив: её части проверяются до чтения."""

    def test_shared_strings_bomb_is_refused(self, client: Client) -> None:
        """Огромный файл общих строк при маленьком листе не читается в память."""
        shared = (
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            + b"<si><t>xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx</t></si>" * 100_000
            + b"</sst>"
        )

        response = upload(client, "book.xlsx", workbook_with_shared_strings(shared))

        assert refused(response, "сжаты слишком сильно")

    @pytest.mark.parametrize(
        "doctype",
        [
            b'<!DOCTYPE sst [<!ENTITY x SYSTEM "file:///etc/passwd">]>',
            b'<!DOCTYPE sst [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">]>',
        ],
        ids=["external-entity", "entity-expansion"],
    )
    def test_entities_are_not_resolved(self, client: Client, doctype: bytes) -> None:
        """Объявления сущностей в книге не раскрываются: такая книга не читается."""
        shared = (
            b'<?xml version="1.0" encoding="UTF-8"?>' + doctype + b'<sst xmlns="http://schemas.'
            b'openxmlformats.org/spreadsheetml/2006/main"><si><t>&x;&b;</t></si></sst>'
        )

        response = upload(client, "book.xlsx", workbook_with_shared_strings(shared))

        assert refused(response, "не читается как таблица Excel")
        assert "root:" not in response.text


class TestText:
    """Текст, который читается иначе, чем выглядит."""

    @pytest.mark.parametrize(
        "content",
        [ROWS + b'"' + b"a" * (3 * 1024 * 1024), b'"' * (3 * 1024 * 1024)],
        ids=["after-rows", "whole-file"],
    )
    def test_unclosed_quote_is_explained(self, client: Client, content: bytes) -> None:
        """
        Незакрытая кавычка склеивает остаток файла в одно значение длиннее предела модуля csv
        или строки DuckDB: страница таблицы говорит, что разобрать не удалось, а не падает.
        """
        response = upload(client, "data.csv", content)
        dataset = Dataset.objects.get()
        version = dataset.current_version
        assert response.status_code == 302 and version is not None
        key = version.report["inspection"]["tables"][0]["key"]
        chosen = client.post(
            reverse("userdata:file", args=[dataset.public_id]),
            {"action": "choose", "table": key, "encoding": ""},
        )

        page = client.get(chosen["Location"])

        assert page.status_code == 200
        assert "Разобрать таблицу не удалось" in page.text


class TestDocuments:
    """PDF разбирается во время запроса: тяжёлая страница отклоняется до разбора."""

    def test_overloaded_page_is_refused(self, client: Client, settings: Any) -> None:
        from reportlab.pdfgen import canvas

        settings.USERDATA_PDF_PAGE_OBJECTS = 50
        buffer = io.BytesIO()
        page = canvas.Canvas(buffer)
        page.drawString(40, 800, "Субъект 2024")
        for index in range(200):
            page.rect(40 + index % 20 * 20, 100 + index // 20 * 20, 20, 20)
        page.showPage()
        page.save()

        response = upload(client, "report.pdf", buffer.getvalue())

        assert refused(response, "слишком много линий и знаков")


class TestSafePath:
    """Путь внутри архива — только относительный."""

    @pytest.mark.parametrize(
        "name", ["/etc/passwd", "\\\\server\\share\\a.csv", "../a.csv", "a/../../b.csv", "C:/a.csv"]
    )
    def test_outside_paths_are_dropped(self, name: str) -> None:
        assert _safe_relative(name) is None

    def test_inner_path_is_kept(self) -> None:
        assert _safe_relative("data/2024/a.csv") == Path("data/2024/a.csv")
