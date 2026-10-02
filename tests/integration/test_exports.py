"""
Проверки выгрузки документов; подменяется только шаг «данные → описание документа».
"""

from __future__ import annotations

from typing import Any

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from apps.exports import services, views
from apps.exports.constants import ExportFormat, ReportKind
from apps.exports.reports import ReportParameterError
from apps.exports.reports.base import Column, ReportDocument, Section, Table
from apps.warehouse.duckdb_client import WarehouseNotBuiltError

pytestmark = [pytest.mark.integration, pytest.mark.django_db]


def sample_document() -> ReportDocument:
    """Небольшой отчёт, заменяющий обращение к складу."""
    return ReportDocument(
        title="Тестовый отчёт",
        subtitle="Проверка подготовки файла",
        meta=[("Источник данных", "Росстат")],
        sections=[
            Section(
                heading="Значения",
                tables=[
                    Table(
                        columns=[Column("territory", "Субъект"), Column("value", "Значение")],
                        rows=[
                            {"territory": "Республика Адыгея", "value": 1.5},
                            {"territory": "Алтайский край", "value": None},
                        ],
                        title="Значения",
                    )
                ],
            )
        ],
        footer="RegionLens",
    )


@pytest.fixture
def stub_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    """Подменить сборку отчёта из склада на готовое описание."""
    monkeypatch.setattr(services, "build_report", lambda *args, **kwargs: sample_document())


class TestDocuments:
    """Документы Excel и PDF отдаются сразу и без входа."""

    @pytest.fixture(autouse=True)
    def fresh_limits(self) -> None:
        """Счётчики ограничения частоты не переходят из проверки в проверку."""
        cache.clear()

    @pytest.mark.parametrize(
        ("export_format", "signature"),
        [(ExportFormat.XLSX, b"PK"), (ExportFormat.PDF, b"%PDF")],
    )
    def test_guest_receives_the_document(
        self, client: Client, stub_builder: None, export_format: str, signature: bytes
    ) -> None:
        """Гость получает файл нужного формата с именем, по которому его можно узнать."""
        response = client.get(
            reverse("exports:document"),
            {"kind": ReportKind.RANKING, "series": "E000000001:00", "format": export_format},
        )

        assert response.status_code == 200
        assert response.content.startswith(signature)
        assert "attachment" in response["Content-Disposition"]
        assert f".{export_format}" in response["Content-Disposition"]

    def test_unknown_kind_is_refused(self, client: Client) -> None:
        """Неизвестный вид выгрузки не подбирается наугад."""
        response = client.get(reverse("exports:document"), {"kind": "нет-такого"})

        assert response.status_code == 404

    def test_unknown_format_is_refused(self, client: Client) -> None:
        """Неизвестный формат не подменяется другим."""
        response = client.get(
            reverse("exports:document"),
            {"kind": ReportKind.SERIES, "series": "E000000001:00", "format": "rtf"},
        )

        assert response.status_code == 404

    def test_missing_required_parameter_is_explained(
        self, client: Client, stub_builder: None
    ) -> None:
        """Паспорт региона без региона не собирается, и причина названа текстом."""
        response = client.get(
            reverse("exports:document"),
            {"kind": ReportKind.TERRITORY, "format": ExportFormat.PDF},
        )

        assert response.status_code == 400
        assert "territory" in response.content.decode()

    def test_warehouse_failure_is_explained(
        self, client: Client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        Отказ склада объясняется текстом.

        Пустой файл неотличим от выгрузки, в которой действительно нет строк.
        """

        def failing(*args: Any, **kwargs: Any) -> ReportDocument:
            raise WarehouseNotBuiltError("Склад не собран: выполните etl_build")

        monkeypatch.setattr(services, "build_report", failing)
        response = client.get(
            reverse("exports:document"),
            {"kind": ReportKind.RANKING, "series": "E000000001:00", "format": ExportFormat.XLSX},
        )

        assert response.status_code == 503
        assert response.content.decode() == "Данные ещё не загружены"

    def test_frequent_requests_are_limited(
        self, client: Client, stub_builder: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Поток выгрузок с одного адреса упирается в предел и получает объяснение."""
        monkeypatch.setattr(views, "DOCUMENT_LIMIT", 2)
        query = {"kind": ReportKind.RANKING, "series": "E000000001:00"}

        codes = [client.get(reverse("exports:document"), query).status_code for _ in range(3)]

        assert codes == [200, 200, 429]


class TestFormats:
    """Выбор форматов и параметров."""

    def test_numbers_come_first(self) -> None:
        """Числа таблицей предлагаются первыми и есть у любого вида документа."""
        for kind in ReportKind.values:
            formats = [code for code, _label in services.available_formats(kind)]
            assert formats[0] == ExportFormat.CSV
            assert ExportFormat.XLSX in formats

    def test_unknown_kind_has_no_formats(self) -> None:
        """Для неизвестного вида форматов нет."""
        assert services.available_formats("несуществующий") == []

    def test_default_parameters_are_taken_from_the_page(self) -> None:
        """Параметры документа берутся из строки запроса страницы."""
        parameters = services.default_parameters(
            ReportKind.RANKING,
            {"series": "E000000001:00", "year": "2023", "order": "asc"},
        )
        assert parameters == {"series": "E000000001:00", "year": "2023", "ascending": True}

    def test_file_name_describes_the_document(self) -> None:
        """Имя файла называет вид, ряд и год; двоеточие ключа заменено."""
        name = services.download_name(
            ReportKind.RANKING, ExportFormat.XLSX, {"series": "E000000001:00", "year": "2023"}
        )
        assert name == "regionlens-ranking-E000000001-00-2023.xlsx"


class TestDataDownload:
    """Выгрузка чисел в CSV: строение, кодировка, запись пропуска и дробного значения."""

    def _build(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Подменить обращение к складу готовым описанием отчёта."""
        monkeypatch.setattr(services, "build_report", lambda *args, **kwargs: sample_document())

    def test_guest_receives_the_file(self, client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
        """Файл отдаётся без входа."""
        self._build(monkeypatch)
        response = client.get(
            reverse("exports:data-csv"), {"kind": ReportKind.RANKING, "series": "X:1"}
        )

        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/csv")
        assert "attachment" in response["Content-Disposition"]

    def test_file_holds_only_the_table(
        self, client: Client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        В файле шапка и строки, без реквизитов перед ними.

        Строка любого вида перед шапкой ломает разбор по умолчанию: `read.csv`
        не пропускает комментарии, а `read_csv` принимает первую строку за шапку.
        """
        self._build(monkeypatch)
        response = client.get(
            reverse("exports:data-csv"), {"kind": ReportKind.RANKING, "series": "X:1"}
        )

        text = response.content.decode("utf-8-sig")
        lines = text.splitlines()
        assert lines[0] == "Субъект,Значение"
        assert lines[1] == "Республика Адыгея,1.5"
        # Пропуск — пустая ячейка: тире сделало бы столбец текстовым, и первое же
        # действие с выгрузкой — среднее по столбцу — отказало бы.
        assert lines[2] == "Алтайский край,"

    def test_file_starts_with_the_byte_order_mark(
        self, client: Client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Метка порядка байтов на месте: без неё Excel портит русские названия."""
        self._build(monkeypatch)
        response = client.get(
            reverse("exports:data-csv"), {"kind": ReportKind.RANKING, "series": "X:1"}
        )

        assert response.content.startswith(b"\xef\xbb\xbf")

    def test_unknown_kind_is_refused(self, client: Client) -> None:
        """Неизвестный вид выгрузки не подбирается наугад."""
        response = client.get(reverse("exports:data-csv"), {"kind": "нет-такого"})

        assert response.status_code == 404

    def test_missing_parameter_is_explained(
        self, client: Client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        Отказ объясняется текстом, а не пустым файлом.

        Пустой CSV неотличим от выгрузки, в которой действительно нет строк.
        """

        def refuse(*args: Any, **kwargs: Any) -> ReportDocument:
            raise ReportParameterError("Ряд наблюдений не найден")

        monkeypatch.setattr(services, "build_report", refuse)
        response = client.get(
            reverse("exports:data-csv"), {"kind": ReportKind.RANKING, "series": "НЕТ"}
        )

        assert response.status_code == 400
        assert "не найден" in response.content.decode()
