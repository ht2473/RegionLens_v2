"""Вывод собранного отчёта в файл выбранного формата."""

from __future__ import annotations

from collections.abc import Callable

from ..constants import ExportFormat
from ..reports.base import ReportDocument
from . import pdf, table_csv, xlsx

RENDERERS: dict[str, Callable[[ReportDocument], bytes]] = {
    ExportFormat.CSV: table_csv.render,
    ExportFormat.XLSX: xlsx.render,
    ExportFormat.PDF: pdf.render,
}


def render(document: ReportDocument, export_format: str) -> bytes:
    """Собрать файл отчёта в указанном формате."""
    renderer = RENDERERS.get(export_format)
    if renderer is None:  # pragma: no cover - формат ограничен перечислением
        raise ValueError(f"Неизвестный формат выгрузки: {export_format}")
    return renderer(document)


__all__ = ["RENDERERS", "render"]
