"""
Виды отчётов и форматы выгрузки: CSV и XLSX для чисел, PDF для печати.

Порядок форматов зависит от вида: паспорт — прежде всего документ, ряд — таблица.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.db import models
from django.utils.translation import gettext_lazy as _


class ExportFormat(models.TextChoices):
    """Формат файла выгрузки."""

    CSV = "csv", _("Числа таблицей (CSV)")
    XLSX = "xlsx", _("Таблица Excel (XLSX)")
    PDF = "pdf", _("Документ PDF")


class ReportKind(models.TextChoices):
    """Вид отчёта."""

    SERIES = "series", _("Показатель по регионам и годам")
    TERRITORY = "territory", _("Паспорт региона")
    RANKING = "ranking", _("Рейтинг регионов за год")


# Типы содержимого для заголовка ответа при выдаче файла.
CONTENT_TYPES: dict[str, str] = {
    ExportFormat.CSV: "text/csv; charset=utf-8",
    ExportFormat.XLSX: ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ExportFormat.PDF: "application/pdf",
}


@dataclass(frozen=True, slots=True)
class ReportDescriptor:
    """Описание вида отчёта для интерфейса и проверки параметров."""

    kind: str
    title: Any
    summary: Any
    required: tuple[str, ...]
    formats: tuple[str, ...]
    icon: str = "file-text"


REPORT_KINDS: tuple[ReportDescriptor, ...] = (
    ReportDescriptor(
        kind=ReportKind.SERIES,
        title=_("Показатель по регионам и годам"),
        summary=_(
            "Полная таблица значений выбранного ряда: строки — субъекты, столбцы — годы. "
            "Пропуски показаны явно, вместе со сводкой полноты данных."
        ),
        required=("series",),
        formats=(ExportFormat.XLSX, ExportFormat.PDF),
        icon="table",
    ),
    ReportDescriptor(
        kind=ReportKind.TERRITORY,
        title=_("Паспорт региона"),
        summary=_(
            "Сводка по субъекту: ключевые показатели с позициями в рейтингах, "
            "сильные и слабые стороны, полнота данных."
        ),
        required=("territory",),
        formats=(ExportFormat.PDF, ExportFormat.XLSX),
        icon="map-pin",
    ),
    ReportDescriptor(
        kind=ReportKind.RANKING,
        title=_("Рейтинг регионов за год"),
        summary=_(
            "Позиции субъектов по выбранному показателю за год с изменением "
            "к предыдущему году и отношением к среднему по стране."
        ),
        # Без года — последний год с рейтингом.
        required=("series",),
        formats=(ExportFormat.XLSX, ExportFormat.PDF),
        icon="ranking",
    ),
)

REPORT_KINDS_BY_CODE: dict[str, ReportDescriptor] = {item.kind: item for item in REPORT_KINDS}
