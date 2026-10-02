"""
Разбор таблиц Росстата вида «территории × периоды» из файлов xls и xlsx.

Шапка — несколько строк с объединёнными ячейками (год, период, группа показателя),
первый столбец — территории, под таблицей — сноски. Неожиданный вид таблицы
прерывает разбор с понятной причиной: смена формата должна ломать проверку, а не склад.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from python_calamine import CalamineWorkbook

from .territories import normalize, region_codes, territory_code

MONTHS = {
    "январь": 1,
    "февраль": 2,
    "март": 3,
    "апрель": 4,
    "май": 5,
    "июнь": 6,
    "июль": 7,
    "август": 8,
    "сентябрь": 9,
    "октябрь": 10,
    "ноябрь": 11,
    "декабрь": 12,
}
QUARTERS = {"i": 1, "ii": 2, "iii": 3, "iv": 4}

# Знаки на месте числа: многоточие, прочерк и крест — значение не публикуется или не имеет смысла.
HIDDEN_MARKS = frozenset({"…", "...", "-", "–", "—", "х", "x", "Х", "X"})

_YEAR_LABEL = re.compile(r"^((?:19|20)\d{2})\s*(?:год|г\.?)?\s*(\d{0,2})\)?\s*$")
# Сноска: номер, затем скобка, пробел или сразу текст: «1 Данные…», «2)Данные…»,
# «1Данные…», «1 2025 г. — …».
_FOOTNOTE_ROW = re.compile(r"^\s*(\d{1,2})(?:[)\.]\s*|\s+|(?=[^\d\s]))(\S.*)$", re.DOTALL)
_TRAILING_MARKS = re.compile(r"[\d¹²³⁴⁵⁶⁷⁸⁹*)\s]+$")
_NUMBER_MARKS = re.compile(r"[¹²³⁴⁵⁶⁷⁸⁹*)]+$")


class SheetFormatError(ValueError):
    """Таблица не того вида, что ожидался."""


@dataclass(frozen=True, slots=True)
class Period:
    """Период столбца: месяц, квартал, нарастающий итог с января или год целиком."""

    kind: str  # month | quarter | ytd | annual
    number: int  # месяц, квартал или последний месяц нарастающего итога; у года — 12


ANNUAL = Period("annual", 12)
YEAR_TO_DATE_FULL = Period("ytd", 12)


@dataclass(frozen=True, slots=True)
class Column:
    """Столбец данных: год, период и текст шапки над ним."""

    index: int
    year: int
    period: Period
    header: str
    notes: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class Record:
    """Значение таблицы: территория, год, период; скрытое значение — ``value=None``."""

    territory_code: str
    year: int
    period: Period
    value: float | None
    hidden: bool
    notes: tuple[int, ...] = ()


@dataclass(slots=True)
class Table:
    """Разобранный лист."""

    sheet: str
    title: str
    columns: list[Column]
    rows: list[tuple[str, str, list[Any]]]  # подпись, код территории, ячейки
    footnotes: dict[int, str] = field(default_factory=dict)
    unmatched: list[str] = field(default_factory=list)

    def records(
        self,
        *,
        header: str | None = None,
        country_unit: tuple[str, float] | None = None,
    ) -> list[Record]:
        """
        Значения выбранных столбцов по всем территориям.

        ``header`` — образец текста шапки (группа показателя), ``country_unit`` — отметка
        укрупнённой единицы в подписи строки страны и множитель к единице субъектов.
        """
        columns = self.columns
        if header is not None:
            pattern = re.compile(header, re.IGNORECASE)
            columns = [column for column in columns if pattern.search(column.header)]
            if not columns:
                raise SheetFormatError(f"лист «{self.sheet}»: нет столбцов «{header}»")

        records: list[Record] = []
        for label, code, cells in self.rows:
            scale = 1.0
            if code == "RU" and country_unit is not None and country_unit[0] in label.lower():
                scale = country_unit[1]
            for column in columns:
                raw = cells[column.index] if column.index < len(cells) else None
                value, hidden = parse_cell(raw, where=f"{self.sheet}: {label}")
                if value is None and not hidden:
                    continue
                records.append(
                    Record(
                        territory_code=code,
                        year=column.year,
                        period=column.period,
                        value=None if value is None else value * scale,
                        hidden=hidden,
                        notes=column.notes,
                    )
                )
        return records

    def check_regions(self, *, absent: frozenset[str] = frozenset()) -> None:
        """
        Строка каждого субъекта справочника на месте; иначе — ошибка формата с причиной.

        ``absent`` — субъекты, которых в этой таблице у источника нет.
        """
        present = {code for _label, code, _cells in self.rows}
        missing = sorted(region_codes() - present - absent)
        if missing:
            unmatched = "; ".join(self.unmatched) or "нет"
            raise SheetFormatError(
                f"лист «{self.sheet}»: нет строк субъектов {', '.join(missing)}; "
                f"не распознаны подписи: {unmatched}"
            )

    def note_text(self, numbers: tuple[int, ...]) -> str:
        """Тексты сносок с данными номерами одной строкой."""
        return " ".join(self.footnotes.get(number, "") for number in numbers).strip()


def open_sheets(path: Path) -> dict[str, list[list[Any]]]:
    """Прочитать все листы книги xls или xlsx как списки строк."""
    try:
        workbook = CalamineWorkbook.from_path(str(path))
    except Exception as error:  # библиотека сообщает о повреждении файла своими исключениями
        raise SheetFormatError(f"файл «{path.name}» не читается: {error}") from error
    return {
        name.strip(): workbook.get_sheet_by_name(name).to_python(skip_empty_area=False)
        for name in workbook.sheet_names
    }


def pick_sheet(
    sheets: dict[str, list[list[Any]]], pattern: str, *, by_title: bool = False
) -> tuple[str, list[list[Any]]]:
    """Лист, название или заголовок которого (первые строки) подходит под образец."""
    compiled = re.compile(pattern, re.IGNORECASE)
    for name, rows in sheets.items():
        text = " ".join(_text(row[0]) for row in rows[:3] if row) if by_title else name
        if compiled.search(re.sub(r"\s+", " ", text)):
            return name, rows
    listed = ", ".join(f"«{name}»" for name in sheets)
    raise SheetFormatError(f"нет листа «{pattern}»; в книге: {listed}")


def parse_table(sheet: str, rows: list[list[Any]], *, country: str | None = None) -> Table:
    """
    Разобрать лист: шапку, строки территорий и сноски.

    ``country`` — образец подписи строки страны, если она названа не «Российская Федерация».
    """
    country_label = re.compile(country, re.IGNORECASE) if country else None

    def code_of(label: str) -> str | None:
        if country_label is not None and country_label.search(label):
            return "RU"
        return territory_code(label)

    first_data = next(
        (index for index, row in enumerate(rows) if row and code_of(_text(row[0])) == "RU"),
        None,
    )
    if first_data is None:
        raise SheetFormatError(f"лист «{sheet}»: нет строки «Российская Федерация»")

    width = max((len(row) for row in rows), default=0)
    head = [row + [""] * (width - len(row)) for row in rows[:first_data]]
    header_rows = [row for row in head if any(_header_text(cell) for cell in row[1:])]
    if not header_rows:
        raise SheetFormatError(f"лист «{sheet}»: нет шапки над данными")

    title = next(
        (
            _text(row[0])
            for row in head
            if _text(row[0]) and _text(row[0]).lower() not in {"к содержанию", "продолжение"}
        ),
        "",
    )
    columns = _columns(header_rows, width)
    if not columns:
        raise SheetFormatError(f"лист «{sheet}»: в шапке нет годов")

    table = Table(sheet=sheet, title=title, columns=columns, rows=[])
    positions: dict[str, int] = {}
    last_note: int | None = None
    for row in rows[first_data:]:
        label = _text(row[0]) if row else ""
        if not label:
            continue
        code = code_of(label)
        if code is not None and last_note is None:
            if code in positions:
                # Бурятия и Забайкалье до 2018 года стоят в Сибирском округе, после —
                # в Дальневосточном: две строки дополняют друг друга по годам.
                index = positions[code]
                merged = _merge_rows(table.rows[index][2], list(row), sheet=sheet, label=label)
                table.rows[index] = (table.rows[index][0], code, merged)
            else:
                positions[code] = len(table.rows)
                table.rows.append((label, code, list(row)))
            continue
        footnote = _FOOTNOTE_ROW.match(label)
        if footnote is not None:
            last_note = int(footnote.group(1))
            table.footnotes[last_note] = re.sub(r"\s+", " ", footnote.group(2)).strip()
        elif last_note is not None:
            # Продолжение сноски на следующей строке.
            table.footnotes[last_note] += " " + re.sub(r"\s+", " ", label).strip()
        elif normalize(label):
            table.unmatched.append(label)
    return table


def _merge_rows(first: list[Any], second: list[Any], *, sheet: str, label: str) -> list[Any]:
    """Слить две строки одной территории; разные числа в одной клетке — ошибка формата."""
    width = max(len(first), len(second))
    merged = []
    for index in range(width):
        left = first[index] if index < len(first) else ""
        right = second[index] if index < len(second) else ""
        if index and _filled(left) and _filled(right) and left != right:
            raise SheetFormatError(f"лист «{sheet}»: «{label}» повторяется с другими значениями")
        merged.append(left if _filled(left) else right)
    return merged


def _filled(cell: Any) -> bool:
    return cell is not None and not (isinstance(cell, str) and not cell.strip())


def parse_cell(raw: Any, *, where: str = "") -> tuple[float | None, bool]:
    """Число ячейки и признак скрытого значения; пустая ячейка — ``(None, False)``."""
    if raw is None or isinstance(raw, bool):
        return None, False
    if isinstance(raw, int | float):
        return float(raw), False
    text = str(raw).replace("\xa0", "").replace(" ", "").strip()
    if not text:
        return None, False
    if text in HIDDEN_MARKS:
        return None, True
    text = _NUMBER_MARKS.sub("", text).replace(",", ".")
    try:
        return float(text), False
    except ValueError:
        raise SheetFormatError(f"{where}: не число «{raw}»") from None


def _columns(header_rows: list[list[Any]], width: int) -> list[Column]:
    """
    Годы и периоды столбцов по шапке.

    Объединённая ячейка хранит текст только в первой клетке: текст продлевается вправо,
    пока строка выше не начнёт новую группу.
    """
    filled: list[list[str]] = []
    for level, row in enumerate(header_rows):
        current = ""
        line = [""] * width
        for index in range(1, width):
            raw = _header_text(row[index])
            if raw:
                current = raw
            elif any(_header_text(upper[index]) for upper in header_rows[:level]):
                current = ""
            line[index] = current
        filled.append(line)

    columns: list[Column] = []
    for index in range(1, width):
        texts = [line[index] for line in filled if line[index]]
        year: int | None = None
        notes: tuple[int, ...] = ()
        rest: list[str] = []
        for text in texts:
            match = _YEAR_LABEL.match(text)
            if match is not None and year is None:
                year = int(match.group(1))
                notes = (int(match.group(2)),) if match.group(2) else ()
            else:
                rest.append(text)
        if year is None:
            continue
        period = next((found for text in rest if (found := _period(text)) is not None), ANNUAL)
        header = " | ".join(re.sub(r"\s+", " ", text) for text in rest)
        columns.append(Column(index=index, year=year, period=period, header=header, notes=notes))
    return columns


def _period(label: str) -> Period | None:
    """Период по подписи столбца."""
    text = re.sub(r"\s+", " ", label).strip().lower()
    text = _TRAILING_MARKS.sub("", text)
    text = re.sub(r"\s*-\s*", "-", text)
    if text in MONTHS:
        return Period("month", MONTHS[text])
    if text in {"год", "январь-декабрь"}:
        return YEAR_TO_DATE_FULL
    if text == "i полугодие":
        return Period("ytd", 6)
    ytd = re.fullmatch(r"январь-(\w+)", text)
    if ytd is not None and ytd.group(1) in MONTHS:
        return Period("ytd", MONTHS[ytd.group(1)])
    quarter = re.fullmatch(r"(i|ii|iii|iv) квартал", text)
    if quarter is not None:
        return Period("quarter", QUARTERS[quarter.group(1)])
    return None


def _header_text(cell: Any) -> str:
    """Текст клетки шапки: год бывает записан числом."""
    if isinstance(cell, int | float) and not isinstance(cell, bool) and float(cell).is_integer():
        return str(int(cell))
    return _text(cell)


def _text(cell: Any) -> str:
    """Текст ячейки без краевых пробелов; число — пустая строка."""
    return cell.strip() if isinstance(cell, str) else ""
