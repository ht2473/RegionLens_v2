"""
Таблица, вставленная из буфера: из разметки (Word, Excel, страницы сайтов) или из текста
с табуляциями; вторая часть дописывается к первой («Добавить строки»).

Вставка хранится файлом TSV в UTF-8 — это исходный файл её версии.
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path

from .html_tables import tables
from .ingest import detect_delimiter

FILE_NAME = "paste.tsv"
_CONTINUATION = re.compile(r"(продолжение|окончание)(\s+табл(ицы|\.)?)?[\s\d.,]*")


def rows_of(text: str, markup: str = "") -> list[list[str]]:
    """Строки вставленной таблицы; разметка точнее текста — в ней видны объединения клеток."""
    if markup and "<table" in markup.lower():
        found = tables(markup)
        if found:
            return max(found, key=lambda rows: sum(map(len, rows)))
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    if not text.strip():
        return []
    delimiter = "\t" if "\t" in text else detect_delimiter(text)
    rows = csv.reader(io.StringIO(text), delimiter=delimiter)
    return [[cell.strip() for cell in row] for row in rows if any(cell.strip() for cell in row)]


def append(existing: list[list[str]], added: list[list[str]]) -> list[list[str]]:
    """Дописать вторую часть: повторённая шапка и «Продолжение таблицы» отбрасываются."""
    head = {_key(row) for row in existing[:10]}
    start = 0
    for row in added:
        key = _key(row)
        if key in head or _CONTINUATION.fullmatch(key):
            start += 1
            continue
        break
    return existing + added[start:]


def write(rows: list[list[str]], target: Path) -> int:
    """Записать строки файлом TSV; вернуть его размер."""
    buffer = io.StringIO()
    csv.writer(buffer, delimiter="\t", lineterminator="\n").writerows(rows)
    data = buffer.getvalue().encode("utf-8")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return len(data)


def read(path: Path) -> list[list[str]]:
    """Строки записанной вставки."""
    return list(csv.reader(io.StringIO(path.read_bytes().decode("utf-8")), delimiter="\t"))


def _key(row: list[str]) -> str:
    return " ".join(cell.strip().lower() for cell in row if cell.strip())
