"""
Таблицы из документов: Word (DOCX) с объединёнными клетками и PDF с текстовым слоем проходят
обычный путь — от загрузки до собранной таблицы; таблица PDF, продолженная на второй
странице, — одна, а под своим названием — другая; шапка с линиями над строками без линий
и повёрнутые подписи читаются; скан и документ старого вида — понятный отказ.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from apps.catalog.models import Territory
from apps.userdata import documents as reader
from apps.userdata.models import Dataset
from tests.support import documents
from tests.support.userdata import (
    build,
    describe,
    userdata_dir,  # noqa: F401 — приспособление модуля
    version_of,
)

pytestmark = [pytest.mark.integration, pytest.mark.django_db]


def _upload(client: Client, name: str, content: bytes) -> Any:
    return client.post(
        reverse("userdata:upload"), {"action": "upload", "file": SimpleUploadedFile(name, content)}
    )


# Области с автономными округами и сами округа: о них мастер спрашивает отдельно.
NESTED = ("RU-ARK", "RU-NEN", "RU-TYU", "RU-KHM", "RU-YAN")


def _regions(count: int) -> list[Territory]:
    return list(Territory.objects.comparable().exclude(code__in=NESTED).order_by("code")[:count])


def test_word_table_is_read_and_built(client: Client, warehouse: Any) -> None:
    regions = _regions(25)
    content = documents.docx(
        "Таблица 3. Выпуск продукции, млн руб.",
        [territory.name_ru for territory in regions],
        [2022, 2023],
    )
    _upload(client, "отчёт.docx", content)
    dataset = Dataset.objects.get()
    version = version_of(dataset)
    (table,) = version.report["inspection"]["tables"]
    assert table["name"] == "Таблица 3. Выпуск продукции, млн руб."
    assert table["regions"] == 25
    # Одна таблица — выбирается сама; выбранная хранится файлом TSV, как вставка.
    step = client.get(reverse("userdata:file", args=[dataset.public_id]))
    assert step.status_code == 302
    version.refresh_from_db()
    assert version.recipe["table"]["kind"] == "csv"
    assert version.recipe["file"] == "table.tsv"
    describe(client, dataset)
    assert build(client, dataset).status_code == 302
    version.refresh_from_db()
    assert version.regions_count == 25
    assert (version.first_year, version.last_year) == (2022, 2023)


def test_word_continuation_is_one_table(client: Client) -> None:
    # «Регионы России»: субъекты таблицы продолжаются во второй таблице Word под абзацем
    # «Продолжение табл. 1» с той же шапкой — это одна таблица.
    names = [territory.name_ru for territory in _regions(30)]
    content = documents.docx("Таблица 1. Выпуск, млн руб.", names, [2022, 2023], split=12)
    _upload(client, "регионы.docx", content)
    (table,) = version_of(Dataset.objects.get()).report["inspection"]["tables"]
    assert table["regions"] == 30
    assert table["rows"] == 32


def test_word_right_part_stays_apart(client: Client) -> None:
    # Продолжение с другими столбцами — правая часть широкой таблицы: отдельная таблица.
    names = [territory.name_ru for territory in _regions(30)]
    content = documents.docx(
        "Таблица 1. Выпуск, млн руб.", names, [2022, 2023], split=12, continued_years=[2024, 2025]
    )
    _upload(client, "регионы.docx", content)
    tables = version_of(Dataset.objects.get()).report["inspection"]["tables"]
    assert [table["regions"] for table in tables] == [12, 18]


def test_pdf_table_across_pages(client: Client, warehouse: Any) -> None:
    regions = _regions(30)
    header = ["Region", "2022", "2023"]
    rows = [
        [territory.code, f"{index}.5", f"{index + 1}.5"] for index, territory in enumerate(regions)
    ]
    content = documents.pdf([[header, *rows[:20]], [header, *rows[20:]]])
    _upload(client, "bulletin.pdf", content)
    dataset = Dataset.objects.get()
    version = version_of(dataset)
    (table,) = version.report["inspection"]["tables"]
    # Продолжение на второй странице — та же таблица, повторённая шапка отброшена.
    assert table["rows"] == 31
    assert table["regions"] == 30
    client.get(reverse("userdata:file", args=[dataset.public_id]))
    describe(client, dataset)
    assert build(client, dataset).status_code == 302
    assert version_of(dataset).regions_count == 30


def test_pdf_ruled_header_and_plain_rows(client: Client, warehouse: Any) -> None:
    # Сборники Росстата и ведомств: линии только у шапки, строки субъектов — текстом без
    # линий. pdfplumber видит одну шапку; столбцы берутся по её линиям, строки — по тексту.
    regions = _regions(30)
    rows = [
        [territory.code, f"{index}.5", f"{index + 1}.5"] for index, territory in enumerate(regions)
    ]
    _upload(client, "bulletin.pdf", documents.pdf([[["Region", "2022", "2023"], *rows]], ruled=1))
    dataset = Dataset.objects.get()
    (table,) = version_of(dataset).report["inspection"]["tables"]
    assert (table["rows"], table["regions"]) == (31, 30)
    client.get(reverse("userdata:file", args=[dataset.public_id]))
    describe(client, dataset)
    assert build(client, dataset).status_code == 302
    assert version_of(dataset).regions_count == 30


def test_pdf_turned_header_reads_as_words(tmp_path: Path) -> None:
    # Подписи шапки, повёрнутые на 90° (Excel так пишет названия субъектов над столбцами):
    # pdfplumber отдаёт их по знаку в строке задом наперёд, читаются — словом.
    path = tmp_path / "turned.pdf"
    path.write_bytes(
        documents.pdf([[["", "RU-MOW", "RU-SPE"], ["2022", "1.5", "2.5"]]], turned=True)
    )
    (table,) = reader.pdf_tables(path)
    assert table.rows[0] == ["", "RU-MOW", "RU-SPE"]


def test_pdf_tables_with_own_titles_stay_apart(tmp_path: Path) -> None:
    # Таблицы одной ширины на соседних страницах, каждая под своим названием («Свиньи»,
    # «Молоко» в сборнике Росстата), — разные таблицы; без названия — продолжение.
    header = ["Region", "2022", "2023"]
    pages = [[header, ["RU-MOW", "1.5", "2.5"]], [header, ["RU-SPE", "3.5", "4.5"]]]
    path = tmp_path / "titled.pdf"
    path.write_bytes(documents.pdf(pages, captions=["Pigs", "Milk"]))
    assert [table.name for table in reader.pdf_tables(path)] == [
        "Таблица 1, стр. 1: Pigs",
        "Таблица 2, стр. 2: Milk",
    ]
    path.write_bytes(documents.pdf(pages, captions=["Pigs"]))
    (table,) = reader.pdf_tables(path)
    assert len(table.rows) == 3


def test_wrapped_labels_are_joined() -> None:
    # Подпись, перенесённая на несколько строк текста: значения — у последней строки,
    # продолжение после значений — с тире; «в том числе:» — своя строка.
    rows = [
        ["Южный", "", ""],
        ["федеральный округ", "1", "2"],
        ["в том числе:", "", ""],
        ["Республика Адыгея", "3", "4"],
        ["Республика Северная Осетия –", "5", "6"],
        ["Алания", "", ""],
        ["Кабардино-Балкарская", "", ""],
        ["Республика", "7", "8"],
    ]
    assert reader._joined(rows) == [
        ["Южный федеральный округ", "1", "2"],
        ["в том числе:", "", ""],
        ["Республика Адыгея", "3", "4"],
        ["Республика Северная Осетия – Алания", "5", "6"],
        ["Кабардино-Балкарская Республика", "7", "8"],
    ]


def test_scan_and_old_word_are_refused(client: Client) -> None:
    scan = documents.pdf([[["Region", "2022"], ["RU-MOW", "1"]]], text=False)
    page = _upload(client, "scan.pdf", scan)
    assert "Это скан" in page.text
    old = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + "WordDocument".encode("utf-16-le") + b"\x00" * 512
    page = _upload(client, "old.doc", old)
    assert "Сохраните его в Word как DOCX" in page.text
    assert not Dataset.objects.exists()
