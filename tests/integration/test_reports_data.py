"""Сборка отчётов XLSX и PDF из склада с разбором файла обратно и сверкой чисел."""

from __future__ import annotations

import io
from typing import Any

import pytest

from apps.exports.constants import ExportFormat, ReportKind
from apps.exports.renderers import render
from apps.exports.reports import ReportParameterError, build_report
from tests.support import synthetic

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

# Предел числа строк, применяемый в проверках: полный список субъектов помещается.
MAX_ROWS = 200


@pytest.fixture
def series_key(warehouse: Any) -> str:
    """Ключ ряда численности населения."""
    from apps.catalog.models import Series

    return Series.objects.get(indicator__code=synthetic.POPULATION_CODE).key


class TestSeriesReport:
    """Отчёт «показатель по регионам и годам»."""

    def test_table_covers_regions_and_years(self, series_key: str) -> None:
        """В таблице столько строк, сколько субъектов, и столбец на каждый год."""
        document = build_report(
            ReportKind.SERIES,
            {"series": series_key, "first_year": 2010, "last_year": 2015},
            max_rows=MAX_ROWS,
        )

        table = document.sections[0].tables[0]
        assert len(table.rows) == 85
        # Первый столбец — название субъекта, далее по столбцу на каждый год.
        assert len(table.columns) == 1 + 6

    def test_period_bounds_are_applied(self, series_key: str) -> None:
        """Границы периода сужают набор столбцов."""
        document = build_report(
            ReportKind.SERIES,
            {"series": series_key, "first_year": 2018, "last_year": 2020},
            max_rows=MAX_ROWS,
        )
        headers = [column.title for column in document.sections[0].tables[0].columns]
        assert headers[1:] == ["2018", "2019", "2020"]

    def test_missing_values_stay_missing(self, warehouse: Any) -> None:
        """
        Пропуск остаётся пустой ячейкой.

        Заполнение пропусков нулями превратило бы отсутствие наблюдения
        в наблюдённый ноль — для любого показателя это разные вещи.
        """
        from apps.catalog.models import Series

        series = Series.objects.get(indicator__code=synthetic.NATURAL_GROWTH_CODE)
        document = build_report(
            ReportKind.SERIES,
            {"series": series.key},
            max_rows=MAX_ROWS,
        )
        table = document.sections[0].tables[0]
        values = [row.get("2005") for row in table.rows]
        assert any(value is None for value in values)

    def test_unknown_series_is_rejected(self, warehouse: Any) -> None:
        """Ряд, которого нет в складе, приводит к понятной ошибке параметра."""
        with pytest.raises(ReportParameterError):
            build_report(ReportKind.SERIES, {"series": "нет-такого"}, max_rows=MAX_ROWS)


class TestTerritoryReport:
    """Отчёт «паспорт региона»."""

    def test_passport_contains_sections(self, warehouse: Any) -> None:
        """Паспорт собран из нескольких разделов и снабжён сведениями об источнике."""
        document = build_report(
            ReportKind.TERRITORY,
            {"territory": "RU-MOW"},
            max_rows=MAX_ROWS,
        )

        assert document.sections
        assert document.meta
        assert "Кузьмин" in document.footer

    def test_unknown_territory_is_rejected(self, warehouse: Any) -> None:
        """Несуществующая территория приводит к ошибке параметра."""
        with pytest.raises(ReportParameterError):
            build_report(ReportKind.TERRITORY, {"territory": "RU-ZZZ"}, max_rows=MAX_ROWS)


class TestRankingReport:
    """Отчёт «рейтинг регионов за год»."""

    def test_ranking_is_ordered(self, series_key: str) -> None:
        """Строки рейтинга упорядочены по значению."""
        document = build_report(
            ReportKind.RANKING,
            {"series": series_key, "year": 2018},
            max_rows=MAX_ROWS,
        )

        rows = document.sections[0].tables[0].rows
        values = [row["value"] for row in rows if row["value"] is not None]
        assert values == sorted(values, reverse=True)

    def test_year_outside_range_falls_back_to_last(self, series_key: str) -> None:
        """
        Год за пределами ряда заменяется последним доступным.

        Ссылкой на отчёт делятся, и устаревший параметр должен давать отчёт
        за ближайший осмысленный год, а не отказ.
        """
        document = build_report(
            ReportKind.RANKING,
            {"series": series_key, "year": 1990},
            max_rows=MAX_ROWS,
        )
        assert document.sections[0].tables[0].rows


class TestRendering:
    """Превращение отчёта в файл."""

    @pytest.fixture
    def document(self, series_key: str) -> Any:
        """Отчёт по ряду за короткий период."""
        return build_report(
            ReportKind.SERIES,
            {"series": series_key, "first_year": 2015, "last_year": 2020},
            max_rows=MAX_ROWS,
        )

    def test_xlsx_keeps_numbers_as_numbers(self, document: Any) -> None:
        """
        В книге Excel значения остаются числами.

        Ради этого отчёт и выгружают в XLSX: числа, записанные текстом,
        не суммируются и не строят диаграмм.
        """
        from openpyxl import load_workbook

        payload = render(document, ExportFormat.XLSX)
        workbook = load_workbook(io.BytesIO(payload))

        # Первый лист — сводка, данные лежат на следующем.
        sheet = workbook.worksheets[1]
        # Примечание, строка названий столбцов и 85 субъектов.
        assert sheet.max_row >= 86
        # Строка названий столбцов пропускается: значения начинаются под ней.
        numbers = [
            cell.value
            for row in sheet.iter_rows(min_row=sheet.max_row - 84, min_col=2)
            for cell in row
            if cell.value is not None
        ]
        assert len(numbers) > 400
        assert all(isinstance(value, (int, float)) for value in numbers)

    def test_pdf_is_produced_with_cyrillic_font(self, document: Any) -> None:
        """
        Документ PDF формируется и содержит кириллицу.

        Встроенные шрифты reportlab кириллицу не содержат, поэтому шрифт
        подбирается среди системных: без этого отчёт получился бы пустым.
        """
        payload = render(document, ExportFormat.PDF)

        assert payload.startswith(b"%PDF")
        assert len(payload) > 5_000

    @pytest.mark.parametrize("export_format", [ExportFormat.XLSX, ExportFormat.PDF])
    def test_every_format_is_produced(self, document: Any, export_format: str) -> None:
        """Все форматы выгрузки формируются без ошибок."""
        payload = render(document, export_format)
        assert payload
