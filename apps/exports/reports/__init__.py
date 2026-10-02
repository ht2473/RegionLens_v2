"""Промежуточное представление отчёта (``base``) и его сборка из данных склада (``builders``)."""

from __future__ import annotations

from .base import Column, ReportDocument, Section, Table
from .builders import ReportParameterError, build_report

__all__ = [
    "Column",
    "ReportDocument",
    "ReportParameterError",
    "Section",
    "Table",
    "build_report",
]
