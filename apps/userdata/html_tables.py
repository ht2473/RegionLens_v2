"""
Таблицы из разметки HTML: вставка из Word, Excel и со страниц сайтов, таблицы «xls»,
сохранённые как HTML.

Объединённые клетки раскрываются: текст — в первой клетке, остальные пустые, как при
чтении книги Excel. Сценарии, стили и прочая разметка пропускаются.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

# Объединение больше этого — ошибка разметки, а не таблица.
MAX_SPAN = 200
_SPACES = re.compile(r"\s+")
_BREAKS = frozenset({"br", "p", "div", "li"})
_SKIPPED = frozenset({"script", "style", "head", "title"})


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[tuple[str, int, int]]]] = []
        self._stack: list[list[list[tuple[str, int, int]]]] = []
        self._cell: list[str] | None = None
        self._span = (1, 1)
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIPPED:
            self._skip += 1
        elif tag == "table":
            self._stack.append([])
        elif tag == "tr" and self._stack:
            self._stack[-1].append([])
        elif tag in {"td", "th"} and self._stack:
            values = dict(attrs)
            self._cell = []
            self._span = (_span(values.get("colspan")), _span(values.get("rowspan")))
            if not self._stack[-1]:
                self._stack[-1].append([])
        elif tag in _BREAKS and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIPPED:
            self._skip = max(0, self._skip - 1)
        elif tag in {"td", "th"} and self._cell is not None and self._stack:
            text = _SPACES.sub(" ", "".join(self._cell)).strip()
            self._stack[-1][-1].append((text, *self._span))
            self._cell = None
        elif tag == "table" and self._stack:
            self.tables.append(self._stack.pop())

    def handle_data(self, data: str) -> None:
        if self._cell is not None and not self._skip:
            self._cell.append(data)


def _span(raw: str | None) -> int:
    try:
        return min(max(int(raw or 1), 1), MAX_SPAN)
    except ValueError:
        return 1


def tables(markup: str) -> list[list[list[str]]]:
    """Все таблицы разметки строками клеток."""
    parser = _TableParser()
    parser.feed(markup)
    parser.close()
    return [rows for rows in (_expand(table) for table in parser.tables) if rows]


def _expand(table: list[list[tuple[str, int, int]]]) -> list[list[str]]:
    """Раскрыть объединения по строкам и столбцам в прямоугольную сетку."""
    grid: dict[tuple[int, int], str] = {}
    width = 0
    for row_index, row in enumerate(table):
        column = 0
        for text, colspan, rowspan in row:
            while (row_index, column) in grid:
                column += 1
            for down in range(rowspan):
                for across in range(colspan):
                    grid[row_index + down, column + across] = text if not (down or across) else ""
            column += colspan
            width = max(width, column)
    height = max((row for row, _column in grid), default=-1) + 1
    rows = [[grid.get((row, column), "") for column in range(width)] for row in range(height)]
    return [row for row in rows if any(cell for cell in row)]
