"""Промежуточное представление отчёта, общее для всех форматов вывода."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Виды столбцов: от них зависят выравнивание, формат числа и ширина.
COLUMN_TEXT = "text"
COLUMN_NUMBER = "number"
COLUMN_INTEGER = "integer"
COLUMN_PERCENT = "percent"


@dataclass(frozen=True, slots=True)
class Column:
    """Столбец таблицы отчёта."""

    key: str
    title: str
    kind: str = COLUMN_TEXT
    width: int = 16

    @property
    def is_numeric(self) -> bool:
        """Признак числового столбца."""
        return self.kind in {COLUMN_NUMBER, COLUMN_INTEGER, COLUMN_PERCENT}


@dataclass(slots=True)
class Table:
    """Таблица отчёта."""

    columns: list[Column]
    rows: list[dict[str, Any]]
    title: str = ""
    note: str = ""

    @property
    def row_count(self) -> int:
        """Число строк таблицы."""
        return len(self.rows)


@dataclass(slots=True)
class Section:
    """Раздел отчёта: заголовок, пояснения и таблицы."""

    heading: str = ""
    paragraphs: list[str] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)


@dataclass(slots=True)
class ReportDocument:
    """Готовый к выводу отчёт; реквизиты ``meta`` — в начале документа."""

    title: str
    subtitle: str = ""
    meta: list[tuple[str, str]] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    footer: str = ""

    @property
    def row_count(self) -> int:
        """Общее число строк во всех таблицах отчёта."""
        return sum(table.row_count for section in self.sections for table in section.tables)

    @property
    def tables(self) -> list[Table]:
        """Все таблицы отчёта по порядку."""
        return [table for section in self.sections for table in section.tables]


def format_value(value: Any, column: Column) -> str:
    """Привести значение к строке для текстовых форматов; пропуск — тире."""
    if value is None or value == "":
        return "—"
    if column.kind == COLUMN_INTEGER:
        return f"{int(value):,}".replace(",", " ")
    if column.kind == COLUMN_PERCENT:
        return f"{float(value) * 100:.1f} %"
    if column.kind == COLUMN_NUMBER:
        return f"{float(value):,.2f}".replace(",", " ").replace(".", ",")
    return str(value)
