"""
Вывод отчёта в CSV для дальнейшего счёта (RFC 4180).

В файле только данные: строка перед шапкой ломает разбор в pandas и R. Разделитель — запятая,
десятичный — точка, пропуск — пустая ячейка.
"""

from __future__ import annotations

import csv
import io
from typing import Any

from ..reports.base import COLUMN_INTEGER, ReportDocument, Table

# Метка порядка байтов: без неё Excel читает файл в однобайтовой кодировке.
BOM = "﻿"

# Знаков после запятой: больше — след двоичного представления, а не измерения.
DECIMALS = 6


def render(document: ReportDocument) -> bytes:
    """Собрать CSV по первой таблице отчёта."""
    buffer = io.StringIO(newline="")
    buffer.write(BOM)
    writer = csv.writer(buffer, dialect="unix", quoting=csv.QUOTE_MINIMAL)

    # Из нескольких таблиц отчёта — первая, показанная на экране.
    tables = document.tables
    if tables:
        _write_table(writer, tables[0])

    return buffer.getvalue().encode("utf-8")


def _write_table(writer: Any, table: Table) -> None:
    """Записать шапку таблицы и её строки."""
    writer.writerow([column.title for column in table.columns])
    for row in table.rows:
        writer.writerow([_cell(row.get(column.key), column.kind) for column in table.columns])


def _cell(value: Any, kind: str) -> str:
    """Привести значение к записи для разбора; пропуск — пустая ячейка, а не тире."""
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return _number(value, kind)
    return str(value)


def _number(value: float, kind: str) -> str:
    """Записать дробное значение без показателя степени."""
    if kind == COLUMN_INTEGER:
        return str(round(value))

    text = f"{value:.{DECIMALS}f}".rstrip("0").rstrip(".")
    # Значение меньше шага округления — показателем степени, а не нулём.
    if text in {"", "-0", "0"} and value != 0:
        return repr(value)
    return text or "0"
