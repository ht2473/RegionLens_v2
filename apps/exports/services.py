"""Сборка документов выгрузки в ответ на запрос, без входа и без очереди."""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from typing import Any

from .constants import CONTENT_TYPES, REPORT_KINDS_BY_CODE, ExportFormat, ReportKind
from .renderers import render
from .reports import build_report

# Предел строк в документе — с запасом, против ошибки в построителе.
MAX_ROWS = 5_000


class DocumentRequestError(ValueError):
    """Документ не может быть собран: вид или формат неизвестен, параметры неполны."""


@dataclass(frozen=True, slots=True)
class Document:
    """Собранный файл вместе с тем, что нужно для его выдачи."""

    payload: bytes
    filename: str
    content_type: str

    @property
    def disposition(self) -> str:
        """Заголовок выдачи файла: имя обычным полем и полем с кодировкой."""
        quoted = urllib.parse.quote(self.filename)
        return f"attachment; filename=\"{self.filename}\"; filename*=UTF-8''{quoted}"


def build_document(
    kind: str, export_format: str, parameters: dict[str, Any], *, site_url: str = ""
) -> Document:
    """Собрать документ указанного вида и формата; отказы — исключениями построителя."""
    descriptor = REPORT_KINDS_BY_CODE.get(kind)
    if descriptor is None:
        raise DocumentRequestError("Неизвестный вид выгрузки")
    if export_format != ExportFormat.CSV and export_format not in descriptor.formats:
        raise DocumentRequestError("Этот вид выгрузки не собирается в выбранном формате")

    missing = [name for name in descriptor.required if not parameters.get(name)]
    if missing:
        raise DocumentRequestError(f"Не заданы обязательные параметры: {', '.join(missing)}")

    report = build_report(kind, parameters, max_rows=MAX_ROWS)
    if site_url:
        report.footer = f"{report.footer} · {site_url}"
    return Document(
        payload=render(report, export_format),
        filename=download_name(kind, export_format, parameters),
        content_type=CONTENT_TYPES[export_format],
    )


def download_name(kind: str, export_format: str, parameters: dict[str, Any]) -> str:
    """Имя файла: вид выгрузки, ряд, территория и год."""
    parts = ["regionlens", kind]
    # Ключ ряда содержит двоеточие, недопустимое в имени файла в Windows.
    series = str(parameters.get("series", "")).replace(":", "-")
    territory = str(parameters.get("territory", ""))
    year = str(parameters.get("year", ""))
    parts.extend(part for part in (series, territory, year) if part)
    return "-".join(parts) + f".{export_format}"


def default_parameters(kind: str, source: Any) -> dict[str, Any]:
    """Собрать параметры документа из строки запроса страницы, где стоит кнопка."""
    parameters: dict[str, Any] = {}
    if kind in {ReportKind.SERIES, ReportKind.RANKING}:
        parameters["series"] = source.get("series", "")
    if kind == ReportKind.SERIES:
        for name in ("first_year", "last_year"):
            if source.get(name):
                parameters[name] = source.get(name)
    if kind == ReportKind.RANKING:
        if source.get("year"):
            parameters["year"] = source.get("year")
        if source.get("order") == "asc" or source.get("ascending"):
            parameters["ascending"] = True
    if kind == ReportKind.TERRITORY:
        parameters["territory"] = source.get("territory", "")
    return parameters


def available_formats(kind: str) -> list[tuple[str, str]]:
    """Форматы документа указанного вида; CSV — первым."""
    descriptor = REPORT_KINDS_BY_CODE.get(kind)
    if descriptor is None:
        return []
    labels = dict(ExportFormat.choices)
    return [(item, str(labels[item])) for item in (ExportFormat.CSV, *descriptor.formats)]
