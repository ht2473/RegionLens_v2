"""
Таблицы из документов: Word (DOCX) и PDF с текстовым слоем.

Word — таблицы тела документа (вложенные в клетки — нет). Объединения раскрываются, как во
вставке с разметкой: `w:gridSpan` — по столбцам, `w:vMerge` — по строкам, текст — в первой
клетке, остальные пустые. Название таблицы — абзац перед ней («Таблица 3. Численность…»).
Таблица под абзацем «Продолжение табл. …» с теми же столбцами и повторённой шапкой — та же
таблица (так «Регионы России» делят субъектов по страницам); с другой шапкой — отдельная.

PDF — таблицы страниц (pdfplumber, по линиям таблицы). Таблица, продолженная на следующей
странице с тем же числом столбцов и без своего названия, — одна таблица без повторённой
шапки; рамки подписей внутри таблицы — её часть, а не отдельные таблицы. Расчерчена только
шапка (так верстают сборники Росстата и ведомств) — столбцы берутся по её линиям, строки — по
строкам текста под ней. Подпись, повёрнутая на 90°, читается по строке. Скан без текстового
слоя — понятный отказ; страниц и времени чтения — не больше пределов настроек.

Таблицы документа извлекаются один раз и хранятся рядом с файлом (JSON): выбор таблицы,
новый разбор и новая версия документ заново не читают. Выбранная таблица дальше идёт тем же
путём, что вставка из буфера, — файлом TSV.
"""

from __future__ import annotations

import bisect
import json
import re
import statistics
import time
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree  # nosec B405 — объявления сущностей отклоняются до разбора

from django.conf import settings
from django.utils.translation import gettext as _

from .ingest import RATIO_CHECK_FROM, IngestError

# Пространство имён разметки Word.
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
# Объединение больше этого — ошибка разметки, как во вставке.
MAX_SPAN = 200
# Название таблицы — абзац перед ней не длиннее этого.
CAPTION_LENGTH = 160
# Таблица — хотя бы две строки и два столбца с текстом.
MIN_ROWS = 2
MIN_COLUMNS = 2
SIDECAR = ".tables.json"


@dataclass(slots=True)
class DocumentTable:
    """Таблица документа: название и строки клеток текстом."""

    name: str
    rows: list[list[str]]


def sidecar(path: Path) -> Path:
    """Файл с таблицами документа рядом с ним."""
    return path.with_name(path.name + SIDECAR)


def save(path: Path, found: list[DocumentTable]) -> None:
    sidecar(path).write_text(
        json.dumps([asdict(item) for item in found], ensure_ascii=False), encoding="utf-8"
    )


def load(path: Path) -> list[DocumentTable]:
    """Таблицы документа, извлечённые при загрузке."""
    data = json.loads(sidecar(path).read_text(encoding="utf-8"))
    return [DocumentTable(name=str(item["name"]), rows=item["rows"]) for item in data]


# --- Word -----------------------------------------------------------------------------------


def word_tables(path: Path, name: str = "") -> list[DocumentTable]:
    """Таблицы документа Word; повреждённый или подозрительный файл — отказ."""
    from .paste import append

    label = name or "DOCX"
    try:
        with zipfile.ZipFile(path) as bundle:
            info = bundle.getinfo("word/document.xml")
            if info.file_size > settings.USERDATA_TABLE_MAX_BYTES:
                raise IngestError(
                    _("Документ «%(name)s» слишком большой.") % {"name": label}, "table_size"
                )
            ratio = info.file_size / max(info.compress_size, 1)
            if info.file_size > RATIO_CHECK_FROM and ratio > settings.USERDATA_ZIP_MAX_RATIO:
                raise IngestError(
                    _("Документ «%(name)s» сжат слишком сильно. Такой файл не принимается.")
                    % {"name": label},
                    "ratio",
                )
            data = bundle.read(info)
    except (zipfile.BadZipFile, KeyError) as error:
        raise IngestError(
            _("Документ «%(name)s» повреждён.") % {"name": label}, "broken"
        ) from error
    # Объявления типа документа и сущностей в документе Word не бывает: такой файл — подделка.
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        raise IngestError(_("Документ «%(name)s» повреждён.") % {"name": label}, "broken")
    try:
        root = ElementTree.fromstring(data)  # noqa: S314  # nosec B314 — сущности отклонены выше
    except ElementTree.ParseError as error:
        raise IngestError(
            _("Документ «%(name)s» повреждён.") % {"name": label}, "broken"
        ) from error
    body = root.find(f"{W}body")
    found: list[DocumentTable] = []
    caption = ""
    for element in body if body is not None else []:
        if element.tag == f"{W}p":
            text = _paragraph(element)
            if text:
                caption = text
        elif element.tag == f"{W}tbl":
            rows = _grid(element)
            if len(rows) >= MIN_ROWS and max(map(len, rows), default=0) >= MIN_COLUMNS:
                if found and _continues(found[-1], caption, rows):
                    found[-1].rows = append(found[-1].rows, rows)
                else:
                    title = _("Таблица %(number)s") % {"number": len(found) + 1}
                    found.append(DocumentTable(name=_titled(title, caption), rows=rows))
            caption = ""
    return found


def _continues(previous: DocumentTable, caption: str, rows: list[list[str]]) -> bool:
    """
    Таблица — продолжение предыдущей: над ней «Продолжение табл. …» или «Окончание табл. …»,
    столбцов столько же и шапка повторена целиком — после общих первых строк идёт строка
    значений с подписью, а не другая строка шапки (как у правой части широкой таблицы).
    """
    if not caption.casefold().startswith(("продолжение", "окончание")):
        return False
    if len(previous.rows[0]) != len(rows[0]):
        return False
    common = 0
    for before, after in zip(previous.rows, rows, strict=False):
        if _key(before) != _key(after):
            break
        common += 1
    return 0 < common < len(rows) and bool(rows[common][0].strip())


def _key(row: list[str]) -> str:
    return " ".join(cell.strip().lower() for cell in row if cell.strip())


def _titled(title: str, caption: str) -> str:
    """
    Название таблицы: номер и подпись над ней; подпись уже с номером («Таблица 3. …») —
    она сама; длинный абзац над таблицей подписью не считается.
    """
    if not caption or len(caption) > CAPTION_LENGTH:
        return title
    if caption.casefold().startswith(("таблица", "табл.", "table")):
        return caption
    return f"{title}: {caption}"


def _paragraph(element: ElementTree.Element) -> str:
    """Текст абзаца: прогоны, табуляции и переносы — пробелами, мягкий перенос — ничем."""
    parts: list[str] = []
    for node in element.iter():
        if node.tag == f"{W}t" and node.text:
            parts.append(node.text)
        elif node.tag in {f"{W}tab", f"{W}br", f"{W}cr"}:
            parts.append(" ")
        elif node.tag == f"{W}noBreakHyphen":
            parts.append("-")
    return " ".join("".join(parts).split())


def _cell(cell: ElementTree.Element) -> str:
    """Текст клетки — её абзацы через пробел; вложенные таблицы пропускаются."""
    texts = [_paragraph(item) for item in cell if item.tag == f"{W}p"]
    return " ".join(text for text in texts if text)


def _value(element: ElementTree.Element | None, tag: str) -> str | None:
    """Значение атрибута w:val свойства ``tag``; нет свойства — None, нет значения — пусто."""
    if element is None:
        return None
    found = element.find(f"{W}{tag}")
    if found is None:
        return None
    return found.get(f"{W}val", "")


def _span(raw: str | None) -> int:
    try:
        return min(max(int(raw or 1), 1), MAX_SPAN)
    except ValueError:
        return 1


def _grid(table: ElementTree.Element) -> list[list[str]]:
    """Строки таблицы с раскрытыми объединениями; пустые строки отброшены."""
    rows: list[list[str]] = []
    for row in table.findall(f"{W}tr"):
        cells: list[str] = []
        # Пропущенные клетки сетки в начале строки.
        before = _value(row.find(f"{W}trPr"), "gridBefore")
        if before:
            cells.extend([""] * _span(before))
        for cell in row.findall(f"{W}tc"):
            properties = cell.find(f"{W}tcPr")
            span = _span(_value(properties, "gridSpan"))
            merge = _value(properties, "vMerge")
            # Продолжение объединения по строкам — пустая клетка: текст — в первой.
            text = "" if merge is not None and merge != "restart" else _cell(cell)
            cells.extend([text] + [""] * (span - 1))
        rows.append(cells)
    width = max(map(len, rows), default=0)
    grid = [row + [""] * (width - len(row)) for row in rows]
    return [row for row in grid if any(cell.strip() for cell in row)]


# --- PDF ------------------------------------------------------------------------------------

# Название таблицы PDF — строка текста над ней в этой полосе (пунктов).
CAPTION_BAND = 40


def pdf_tables(path: Path, name: str = "") -> list[DocumentTable]:
    """Таблицы PDF с текстовым слоем; скан, пароль, повреждение и пределы — отказ."""
    import pdfplumber

    from .paste import append

    label = name or "PDF"
    try:
        document = pdfplumber.open(path)
    except Exception as error:  # библиотека сообщает о пароле и повреждении своими исключениями
        if "password" in type(error).__name__.lower() or "password" in str(error).lower():
            raise IngestError(
                _("PDF защищён паролем. Снимите защиту и загрузите снова."), "password"
            ) from error
        raise IngestError(_("PDF «%(name)s» повреждён.") % {"name": label}, "broken") from error
    with document:
        if len(document.pages) > settings.USERDATA_PDF_MAX_PAGES:
            raise IngestError(
                _(
                    "В PDF больше %(limit)s страниц. Сохраните страницы с нужной таблицей "
                    "отдельным файлом и загрузите его."
                )
                % {"limit": settings.USERDATA_PDF_MAX_PAGES},
                "pdf_pages",
            )
        started = time.monotonic()
        found: list[DocumentTable] = []
        # Страница, на которой кончилась последняя таблица, и название над ней.
        last_page = 0
        last_caption = ""
        texts = 0
        for number, page in enumerate(document.pages, start=1):
            if time.monotonic() - started > settings.USERDATA_PDF_SECONDS:
                raise IngestError(
                    _(
                        "PDF читается слишком долго. Сохраните страницы с нужной таблицей "
                        "отдельным файлом и загрузите его."
                    ),
                    "pdf_time",
                )
            texts += bool(page.chars)
            found_here = page.find_tables()
            # Рамки подписей внутри большой таблицы pdfplumber находит отдельными таблицами.
            outer = [
                table
                for table in found_here
                if not any(other is not table and _inside(table, other) for other in found_here)
            ]
            first_here = True
            for table in outer:
                below = [item.bbox[1] for item in outer if item.bbox[1] > table.bbox[3]]
                cells = _cells(page, table)
                rows = _unruled_body(page, table, cells, min(below, default=page.height)) or cells
                rows = [row for row in rows if any(row)]
                if len(rows) < MIN_ROWS or max(map(len, rows), default=0) < MIN_COLUMNS:
                    continue
                previous = found[-1] if found else None
                caption = _caption(page, table.bbox)
                if (
                    previous is not None
                    and first_here
                    and last_page == number - 1
                    and _continues_page(previous, last_caption, caption, rows)
                ):
                    # Продолжение таблицы с прошлой страницы: повторённая шапка отбрасывается.
                    previous.rows = append(previous.rows, rows)
                else:
                    title = _("Таблица %(number)s, стр. %(page)s") % {
                        "number": len(found) + 1,
                        "page": number,
                    }
                    found.append(DocumentTable(name=_titled(title, caption), rows=rows))
                    last_caption = caption
                last_page = number
                first_here = False
            page.close()
        if not texts:
            raise IngestError(
                _(
                    "Это скан: в PDF нет текстового слоя, таблицу из него не прочитать. Нужен PDF, "
                    "сохранённый из Word или Excel, или сама таблица."
                ),
                "scan",
            )
    return found


def _continues_page(
    previous: DocumentTable, previous_caption: str, caption: str, rows: list[list[str]]
) -> bool:
    """
    Первая таблица страницы — продолжение последней таблицы прошлой: столбцов столько же,
    а своего названия нет, оно «Продолжение…», или название и шапка те же. Таблица с другим
    названием — другая, даже той же ширины (так идут «Свиньи» и «Молоко» в сборнике Росстата).
    """
    if len(previous.rows[0]) != len(rows[0]):
        return False
    if not caption or caption.casefold().startswith(("продолжение", "окончание")):
        return True
    return caption == previous_caption and _key(previous.rows[0]) == _key(rows[0])


def _caption(page: object, bbox: tuple[float, float, float, float]) -> str:
    """Последняя строка текста над таблицей — её название, если оно короткое."""
    top = bbox[1]
    if top <= 0:
        return ""
    try:
        band = page.within_bbox((0, max(0.0, top - CAPTION_BAND), page.width, top))  # type: ignore[attr-defined]
        lines = [line.strip() for line in (band.extract_text() or "").splitlines() if line.strip()]
    except ValueError:
        return ""
    caption = " ".join(lines[-1].split()) if lines else ""
    return caption if len(caption) <= CAPTION_LENGTH else ""


# Знаки одной вертикальной строки — в этой полосе по x (пунктов).
TURNED_TOLERANCE = 1.5
# Повёрнутая подпись — хотя бы столько букв.
TURNED_MIN = 3
# Слова одной строки текста — в этой полосе по высоте (пунктов).
LINE_TOLERANCE = 3
# Строки без линий кончаются на пустой полосе выше стольких высот строки.
GAP_LINES = 2.5
# Число в клетке: «97,1», «-35,7», «1 856», «12%».
_NUMBER = re.compile(r"[-–−+]?\d[\d\s]*(?:[.,]\d+)?\s*%?")
# Число из этих — год в шапке, а не значение.
_YEARS = range(1900, 2101)


def _inside(table: Any, other: Any) -> bool:
    x0, top, x1, bottom = table.bbox
    left, upper, right, lower = other.bbox
    smaller = (x1 - x0) * (bottom - top) < (right - left) * (lower - upper)
    return smaller and left <= x0 and upper <= top and x1 <= right and bottom <= lower


def _crop(page: Any, bbox: tuple[float, float, float, float]) -> Any:
    """Часть страницы; рамка, чуть выходящая за край страницы, обрезается по нему."""
    x0, top, x1, bottom = bbox
    left, upper, right, lower = page.bbox
    return page.crop((max(x0, left), max(top, upper), min(x1, right), min(bottom, lower)))


def _cells(page: Any, table: Any) -> list[list[str]]:
    """Клетки таблицы текстом; подпись, повёрнутая на 90°, — по строке, а не по букве."""
    rows = []
    for row, texts in zip(table.rows, table.extract(), strict=False):
        cells = []
        for bbox, text in zip(row.cells, texts, strict=False):
            turned = _turned(page, bbox) if bbox and text and "\n" in text else None
            cells.append(" ".join((turned or text or "").split()))
        rows.append(cells)
    return rows


def _turned(page: Any, bbox: tuple[float, float, float, float]) -> str | None:
    """
    Текст клетки, повёрнутый на 90°: знаки стоят столбиком (pdfplumber читает по знаку
    в строке). Против часовой стрелки — снизу вверх, строки слева направо; по часовой —
    сверху вниз, строки справа налево. Направление — по заглавной букве в начале.
    """
    chars = _crop(page, bbox).chars
    if sum(1 for char in chars if char["text"].strip()) < TURNED_MIN:
        return None
    columns: list[list[dict[str, Any]]] = []
    for char in sorted(chars, key=lambda item: item["x0"]):
        if columns and char["x0"] - columns[-1][-1]["x0"] <= TURNED_TOLERANCE:
            columns[-1].append(char)
        else:
            columns.append([char])
    # Горизонтальный текст даёт по столбику на знак; повёрнутый — столбики из многих знаков.
    if any(len(column) == 1 for column in columns) or len(columns) * TURNED_MIN > len(chars):
        return None
    upward = " ".join(
        "".join(char["text"] for char in sorted(column, key=lambda item: -item["top"]))
        for column in columns
    )
    downward = " ".join(
        "".join(char["text"] for char in sorted(column, key=lambda item: item["top"]))
        for column in reversed(columns)
    )
    first_up, first_down = upward.strip()[:1], downward.strip()[:1]
    if first_down.isupper() and not first_up.isupper():
        return downward
    return upward


def _is_number(text: str) -> bool:
    return bool(text) and bool(_NUMBER.fullmatch(text))


def _has_values(row: list[str]) -> bool:
    """
    Строка значений, а не шапки: число вне столбца подписей, кроме годов и нумерации
    столбцов («1 2 3» под шапкой формы).
    """
    numbers = [cell.replace(" ", "") for cell in row[1:] if _is_number(cell)]
    years = [item for item in numbers if item.isdigit() and int(item) in _YEARS]
    if len(numbers) == len(years):
        return False
    counted = [int(item) for item in numbers if item.isdigit()]
    return not (
        len(counted) == len(numbers)
        and counted == list(range(counted[0], counted[0] + len(counted)))
    )


def _unruled_body(
    page: Any, table: Any, cells: list[list[str]], limit: float
) -> list[list[str]] | None:
    """
    Таблица с расчерченной шапкой и строками без линий: pdfplumber находит одну шапку.
    Столбцы — по вертикальным линиям шапки, края — по её горизонтальным линиям (боковых
    рамок у таких таблиц нет); строки — строки текста под шапкой до пустой полосы или
    следующей таблицы. Нет хотя бы двух строк с числами — это не такая таблица. ``cells`` —
    клетки найденной таблицы: со строкой значений это обычная таблица.
    """
    if any(_has_values(row) for row in cells):
        return None
    x0, top, x1, bottom = table.bbox
    band = [edge for edge in page.horizontal_edges if top - 1 <= edge["top"] <= bottom + 1]
    left = min([x0, *(edge["x0"] for edge in band)])
    right = max([x1, *(edge["x1"] for edge in band)])
    lines = _lines_below(page, left, right, bottom, limit)
    if not lines:
        return None
    left = min(left, *(word["x0"] for line in lines for word in line)) - 1
    right = max(right, *(word["x1"] for line in lines for word in line)) + 1
    bounds = [left]
    for edge in sorted(page.vertical_edges, key=lambda item: item["x0"]):
        # Линия, нарисованная тонким прямоугольником, даёт два края рядом — берётся первый.
        apart = edge["x0"] - bounds[-1] > LINE_TOLERANCE and right - edge["x0"] > LINE_TOLERANCE
        if apart and edge["top"] < bottom - 1 and edge["bottom"] > top + 1:
            bounds.append(edge["x0"])
    bounds.append(right)
    width = len(bounds) - 1
    if width < MIN_COLUMNS:
        return None
    body = [_placed(line, bounds) for line in lines]
    if sum(1 for row in body if _has_values(row)) < MIN_ROWS:
        return None
    settings = {"vertical_strategy": "explicit", "explicit_vertical_lines": bounds}
    head = _crop(page, (left, top, right, bottom)).find_table(settings)
    header = _cells(page, head) if head is not None else []
    return [*((row + [""] * width)[:width] for row in header), *_joined(body)]


def _lines_below(
    page: Any, left: float, right: float, bottom: float, limit: float
) -> list[list[dict[str, Any]]]:
    """Строки слов под шапкой в её ширину — до пустой полосы выше нескольких строк."""
    if limit - bottom < 1:
        return []
    words = [
        word
        for word in _crop(page, (0, bottom, page.width, limit)).extract_words()
        if word["x1"] > left and word["x0"] < right
    ]
    lines: list[list[dict[str, Any]]] = []
    for word in sorted(words, key=lambda item: (item["top"], item["x0"])):
        if lines and abs(word["top"] - lines[-1][0]["top"]) <= LINE_TOLERANCE:
            lines[-1].append(word)
        else:
            lines.append([word])
    if not lines:
        return []
    height = statistics.median(word["bottom"] - word["top"] for line in lines for word in line)
    kept: list[list[dict[str, Any]]] = []
    edge = bottom
    for line in lines:
        if line[0]["top"] - edge > GAP_LINES * height:
            break
        kept.append(line)
        edge = max(word["bottom"] for word in line)
    return kept


def _placed(line: list[dict[str, Any]], bounds: list[float]) -> list[str]:
    """Слова строки — по столбцам: столбец — тот, где середина слова."""
    cells: list[list[str]] = [[] for _ in range(len(bounds) - 1)]
    for word in sorted(line, key=lambda item: item["x0"]):
        middle = (word["x0"] + word["x1"]) / 2
        column = min(max(bisect.bisect(bounds, middle) - 1, 0), len(cells) - 1)
        cells[column].append(word["text"])
    return [" ".join(parts) for parts in cells]


def _joined(rows: list[list[str]]) -> list[list[str]]:
    """
    Подпись, перенесённая на несколько строк: строки с одной подписью присоединяются
    к следующей строке со значениями, если она продолжает их (со строчной буквы, после
    переноса или так, что вместе получается название территории), — значения в таких
    таблицах стоят у последней строки подписи. Подпись с двоеточием («в том числе:») —
    своя строка; продолжение после значений («– Югра») — к прошлой строке.
    """
    from .matching import quick_match

    result: list[list[str]] = []
    pending: list[str] = []
    for row in rows:
        label, rest = row[0], row[1:]
        if any(rest):
            while pending and _continues_label(pending[-1], label, quick_match):
                label = _glued(pending.pop(), label)
            result.extend([text, *[""] * len(rest)] for text in pending)
            pending = []
            result.append([label, *rest])
        elif result and not pending and (label[:1] in "–-(" or result[-1][0].endswith(("-", "–"))):
            result[-1][0] = _glued(result[-1][0], label)
        else:
            pending.append(label)
    result.extend([text, *[""] * (len(rows[0]) - 1)] for text in pending)
    return result


def _continues_label(before: str, after: str, quick_match: Any) -> bool:
    if before.endswith(":"):
        return False
    if before.endswith(("-", "–")) or after[:1].islower():
        return True
    return quick_match(_glued(before, after)).is_territory and not quick_match(after).is_territory


def _glued(before: str, after: str) -> str:
    """Перенос со знаком «-» в конце — без пробела, иначе — через пробел."""
    return f"{before}{after}" if before.endswith("-") else f"{before} {after}"
