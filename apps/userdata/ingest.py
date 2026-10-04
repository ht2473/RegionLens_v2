"""
Приём файла: вид по первым байтам, таблицы внутри, кодировка, разделитель, образец строк.

Ни один файл не выполняется и не открывается внешней программой, формулы Excel
не вычисляются (читаются сохранённые значения). Архив проверяется до распаковки:
пути, шифрование, число файлов, размеры и сжатие; распаковывается только выбранная таблица.
"""

from __future__ import annotations

import codecs
import contextlib
import csv
import io
import re
import zipfile
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import IO, Any

from django.conf import settings
from django.utils.translation import gettext as _
from python_calamine import CalamineWorkbook, PasswordError

from apps.sources.unpack import _safe_relative

from . import matching

# Виды файлов.
CSV = "csv"
XLSX = "xlsx"
XLS = "xls"
ODS = "ods"
PARQUET = "parquet"
HTML = "html"
ZIP = "zip"
PASTE = "paste"
WORKBOOK_KINDS = frozenset({XLSX, XLS, ODS})

# Сколько байт таблицы читается для образца и проверки кодировки.
HEAD_BYTES = 256 * 1024
# Сколько строк показывается в образце и просматривается в поисках столбца территорий.
SAMPLE_ROWS = 12
SCAN_ROWS = 2000
SCAN_COLUMNS = 60
# Столбец с большим числом разных значений — не столбец территорий.
SCAN_DISTINCT = 3000
# Сжатие проверяется только у заметных файлов: у маленьких оно бывает любым.
RATIO_CHECK_FROM = 1024 * 1024
DELIMITERS = (";", "\t", ",", "|")
# Кодировки на выбор человеку, если текст прочитан с ошибками.
ENCODINGS = {"utf-8": "UTF-8", "cp1251": "Windows-1251", "utf-16": "UTF-16"}

BOM = chr(0xFEFF)
_OLE2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ENCRYPTED_PACKAGE = "EncryptedPackage".encode("utf-16-le")
_WORD_DOCUMENT = "WordDocument".encode("utf-16-le")
_SKIPPED_NAMES = re.compile(r"(^|/)(__MACOSX/|\.DS_Store$|Thumbs\.db$|desktop\.ini$|\._)", re.I)
_TABLE_SUFFIXES = {
    ".csv": CSV,
    ".tsv": CSV,
    ".txt": CSV,
    ".xlsx": XLSX,
    ".xlsm": XLSX,
    ".xls": XLS,
    ".ods": ODS,
    ".parquet": PARQUET,
    ".htm": HTML,
    ".html": HTML,
}
_DOCUMENT_SUFFIXES = frozenset({".pdf", ".doc", ".docx", ".rtf", ".odt", ".md"})
_ARCHIVE_SUFFIXES = frozenset({".zip", ".rar", ".7z", ".gz"})


class IngestError(ValueError):
    """Файл не принят; сообщение — для человека, ``code`` — для проверок и журнала."""

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(slots=True)
class TableInfo:
    """Таблица в файле: где лежит, как читается и что в ней видно."""

    key: str  # «data.csv», «book.xlsx#Лист1», «#Лист1», «»
    name: str
    kind: str
    size: int
    encoding: str = ""
    delimiter: str = ""
    rows: int | None = None
    rows_exact: bool = False
    sample: list[list[str]] = field(default_factory=list)
    territory_column: int | None = None
    regions: int = 0
    best: bool = False

    @property
    def member(self) -> str:
        """Имя файла в архиве (пусто, если таблица — сам файл)."""
        return self.key.partition("#")[0]

    @property
    def sheet(self) -> str:
        """Лист книги."""
        return self.key.partition("#")[2]


@dataclass(slots=True)
class Inspection:
    """Что нашлось в файле: таблицы и файлы, которые таблицами не являются."""

    kind: str
    tables: list[TableInfo]
    skipped: list[tuple[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Inspection:
        return cls(
            kind=payload["kind"],
            tables=[TableInfo(**table) for table in payload["tables"]],
            skipped=[(str(name), str(reason)) for name, reason in payload.get("skipped", [])],
        )


# --- Вид файла ---------------------------------------------------------------------------------


def sniff(head: bytes, name: str = "") -> str:
    """Вид файла по первым байтам; расширение различает только текстовые виды."""
    if head.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        return ZIP
    if head.startswith(_OLE2):
        if _ENCRYPTED_PACKAGE in head:
            raise IngestError(_password_text(), "password")
        if _WORD_DOCUMENT in head:
            raise IngestError(_refusal_text("word"), "word")
        return XLS
    if head.startswith(b"PAR1"):
        return PARQUET
    for signature, code in _REFUSED_SIGNATURES:
        if head.startswith(signature):
            raise IngestError(_refusal_text(code), code)
    if b"\x00" in head[:4096] and not _looks_utf16(head):
        raise IngestError(_refusal_text("binary"), "binary")
    text = head[:4096].decode("latin-1").lower()
    if re.search(r"<(html|table)\b", text) or Path(name).suffix.lower() in {".html", ".htm"}:
        return HTML
    return CSV


_REFUSED_SIGNATURES = (
    (b"%PDF", "pdf"),
    (b"\x89PNG", "image"),
    (b"\xff\xd8\xff", "image"),
    (b"GIF8", "image"),
    (b"Rar!", "rar"),
    (b"7z\xbc\xaf\x27\x1c", "rar"),
    (b"\x1f\x8b", "gzip"),
    (b"{\\rtf", "word"),
)


def zip_kind(bundle: zipfile.ZipFile) -> str:
    """ZIP — книга XLSX или ODS, документ Word или архив с файлами."""
    names = set(bundle.namelist())
    if "xl/workbook.xml" in names:
        return XLSX
    if "mimetype" in names:
        if b"opendocument.spreadsheet" in bundle.read("mimetype")[:100]:
            return ODS
        raise IngestError(_refusal_text("word"), "word")
    if "word/document.xml" in names:
        raise IngestError(_refusal_text("word"), "word")
    if "ppt/presentation.xml" in names:
        raise IngestError(_refusal_text("binary"), "binary")
    return ZIP


def _looks_utf16(head: bytes) -> bool:
    """Текст в UTF-16 без метки: каждый второй байт нулевой."""
    if head.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return True
    sample = head[:2000]
    half = len(sample) // 2
    if half < 2:  # noqa: PLR2004 — хотя бы два знака
        return False
    odd, even = sample[1::2].count(0) / half, sample[0::2].count(0) / half
    return max(odd, even) > 0.4 and min(odd, even) < 0.1  # noqa: PLR2004


# --- Кодировка и разделитель --------------------------------------------------------------------


def detect_encoding(head: bytes) -> str:
    """Кодировка текста: метка порядка байтов, строгий UTF-8, Windows-1251, иначе по частотам."""
    if head.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    if head.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return "utf-16"
    if _looks_utf16(head):
        return "utf-16-le" if head[1::2].count(0) > head[0::2].count(0) else "utf-16-be"
    try:
        complete_utf8(head).decode("utf-8")
    except UnicodeDecodeError:
        pass
    else:
        return "utf-8"
    if _looks_russian(head.decode("cp1251", errors="replace")):
        return "cp1251"
    from charset_normalizer import from_bytes

    best = from_bytes(head[:65536]).best()
    return codecs.lookup(best.encoding).name if best is not None else "cp1251"


def complete_utf8(head: bytes) -> bytes:
    """Начало файла без знака UTF-8, оборванного на границе прочитанного."""
    for back in range(1, min(4, len(head)) + 1):
        byte = head[-back]
        if byte & 0xC0 == 0xC0:  # noqa: PLR2004 — первый байт многобайтового знака
            length = 2 if byte < 0xE0 else 3 if byte < 0xF0 else 4  # noqa: PLR2004
            return head[:-back] if back < length else head
        if byte & 0x80 == 0:
            return head
    return head


def _looks_russian(text: str) -> bool:
    """Строчные и частые русские буквы преобладают — текст прочитан верно."""
    cyrillic = [char for char in text if "а" <= char.lower() <= "я" or char in "ёЁ"]
    if len(cyrillic) < 20:  # noqa: PLR2004 — слишком мало, чтобы судить
        return bool(cyrillic)
    lower = sum(char.islower() for char in cyrillic) / len(cyrillic)
    common = sum(char.lower() in "оеаинтсрвл" for char in cyrillic) / len(cyrillic)
    return lower > 0.5 and common > 0.3  # noqa: PLR2004


def detect_delimiter(text: str) -> str:
    """Разделитель по образцу: тот, что чаще всего даёт одинаковое число столбцов."""
    best, best_score = ";", 0.0
    lines = text.splitlines(keepends=True)[:200]
    sample = "".join(lines[:-1] if len(lines) > 1 else lines)
    for order, delimiter in enumerate(DELIMITERS):
        try:
            rows = [row for row in csv.reader(io.StringIO(sample), delimiter=delimiter) if row]
        except csv.Error:
            continue
        if not rows:
            continue
        width, count = Counter(len(row) for row in rows).most_common(1)[0]
        if width < 2:  # noqa: PLR2004 — один столбец — не таблица
            continue
        score = count / len(rows) + min(width, 50) / 1000 - order / 10000
        if score > best_score:
            best, best_score = delimiter, score
    return best


# --- Просмотр файла ------------------------------------------------------------------------------


def inspect(path: Path, file_name: str = "", *, encoding: str = "") -> Inspection:
    """
    Таблицы файла с образцом строк и числом узнанных субъектов; лучшая отмечена.

    ``encoding`` — кодировка текстовых таблиц, выбранная человеком вместо найденной.
    """
    with path.open("rb") as handle:
        head = handle.read(HEAD_BYTES)
    kind = sniff(head, file_name or path.name)
    if kind == ZIP:
        try:
            with zipfile.ZipFile(path) as bundle:
                inner = zip_kind(bundle)
                if inner == ZIP:
                    inspection = _inspect_archive(path, bundle, encoding)
                else:
                    inspection = Inspection(inner, _workbook_tables(path, inner, ""))
        except zipfile.BadZipFile as error:
            raise IngestError(_("Архив повреждён."), "broken") from error
    elif kind in WORKBOOK_KINDS:
        inspection = Inspection(kind, _workbook_tables(path, kind, ""))
    elif kind == PARQUET:
        inspection = Inspection(kind, [_parquet_table(path, "")])
    else:
        table = _text_table(
            head, path.stat().st_size, key="", kind=kind, name=file_name, encoding=encoding
        )
        _count_regions(path, table)
        inspection = Inspection(kind, [table])
    if not inspection.tables:
        raise IngestError(_("В файле нет таблиц."), "no_tables")
    best = max(inspection.tables, key=lambda table: (table.regions, -table.size))
    best.best = True
    return inspection


def _inspect_archive(path: Path, bundle: zipfile.ZipFile, encoding: str) -> Inspection:
    inspection = Inspection(ZIP, [])
    for info in check_archive(bundle):
        name = member_name(info)
        kind = _TABLE_SUFFIXES.get(PurePosixPath(name).suffix.lower())
        if kind is None:
            inspection.skipped.append((name, _skip_reason(name)))
            continue
        if kind in {CSV, HTML}:
            with bundle.open(info) as member:
                head = member.read(HEAD_BYTES)
            table = _text_table(
                head, info.file_size, key=name, kind=kind, name=name, encoding=encoding
            )
            _count_regions(path, table)
            inspection.tables.append(table)
            continue
        with bundle.open(info) as member:
            data = io.BytesIO(member.read(settings.USERDATA_TABLE_MAX_BYTES + 1))
        if kind == PARQUET:
            inspection.tables.append(_parquet_table(data, name))
        else:
            inspection.tables.extend(_workbook_tables(data, kind, name))
    if not inspection.tables:
        documents = inspection.skipped and all(
            reason == "document" for _name, reason in inspection.skipped
        )
        message = (
            _("В архиве нет таблиц, только описание в PDF.")
            if documents
            else _refusal_text("empty_archive")
        )
        raise IngestError(message, "no_tables")
    return inspection


def check_archive(bundle: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    """Файлы архива после проверок: пути, шифрование, число, размеры и сжатие."""
    entries = []
    for info in bundle.infolist():
        if info.is_dir() or _SKIPPED_NAMES.search(info.filename):
            continue
        if _safe_relative(member_name(info)) is None:
            raise IngestError(
                _("В архиве есть файлы с путями вне архива. Такой архив не принимается."), "path"
            )
        if info.flag_bits & 0x1:
            raise IngestError(_password_text(), "password")
        entries.append(info)
    if len(entries) > settings.USERDATA_ZIP_MAX_FILES:
        raise IngestError(
            _("В архиве больше %(count)s файлов. Загрузите архив только с нужными таблицами.")
            % {"count": settings.USERDATA_ZIP_MAX_FILES},
            "files",
        )
    for info in entries:
        if info.file_size > settings.USERDATA_TABLE_MAX_BYTES:
            raise IngestError(_table_size_text(member_name(info)), "table_size")
        ratio = info.file_size / max(info.compress_size, 1)
        if info.file_size > RATIO_CHECK_FROM and ratio > settings.USERDATA_ZIP_MAX_RATIO:
            raise IngestError(
                _("Файл «%(name)s» в архиве сжат слишком сильно. Такой архив не принимается.")
                % {"name": member_name(info)},
                "ratio",
            )
    return entries


def extract_member(path: Path, member: str, target: Path) -> Path:
    """Распаковать одну таблицу архива; размер считается по ходу, а не по заголовку."""
    limit = min(settings.USERDATA_TABLE_MAX_BYTES, 1 << 40)
    with zipfile.ZipFile(path) as bundle:
        info = next((item for item in check_archive(bundle) if member_name(item) == member), None)
        if info is None:
            raise IngestError(_("В архиве нет файла «%(name)s».") % {"name": member}, "member")
        target.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        with bundle.open(info) as source, target.open("wb") as output:
            while chunk := source.read(1 << 20):
                written += len(chunk)
                if written > limit or written > info.file_size:
                    break
                output.write(chunk)
        if written > limit or written > info.file_size:
            target.unlink(missing_ok=True)
            raise IngestError(_table_size_text(member), "table_size")
    return target


def member_name(info: zipfile.ZipInfo) -> str:
    """Имя файла в архиве: ZIP из Windows пишет имена в cp866 без флага UTF-8."""
    name = info.filename
    if not info.flag_bits & 0x800:
        with contextlib.suppress(UnicodeError):
            name = name.encode("cp437").decode("cp866")
    return name


def _skip_reason(name: str) -> str:
    suffix = PurePosixPath(name).suffix.lower()
    if suffix in _DOCUMENT_SUFFIXES:
        return "document"
    if suffix in _ARCHIVE_SUFFIXES:
        return "archive"
    return "other"


# --- Таблицы разных видов ------------------------------------------------------------------------


def _text_table(
    head: bytes, size: int, *, key: str, kind: str, name: str, encoding: str = ""
) -> TableInfo:
    encoding = encoding or detect_encoding(head)
    complete = size <= len(head)
    text = decode(complete_utf8(head) if encoding.startswith("utf-8") else head, encoding)
    table = TableInfo(key=key, name=name or _("Таблица"), kind=kind, size=size, encoding=encoding)
    if kind == HTML:
        rows = html_rows(text)
    else:
        table.delimiter = detect_delimiter(text)
        rows = _csv_rows(text, table.delimiter, complete=complete)
    _describe(table, rows)
    table.rows, table.rows_exact = len(rows), complete
    if not complete and rows:
        consumed = len(text.encode(codecs.lookup(encoding).name, errors="replace")) or 1
        table.rows = round(len(rows) * size / consumed)
    return table


def decode(raw: bytes, encoding: str) -> str:
    """Текст без метки порядка байтов; неверные байты — знаком замены."""
    return raw.decode(encoding, errors="replace").removeprefix(BOM)


def _csv_rows(text: str, delimiter: str, *, complete: bool) -> list[list[str]]:
    lines = text.splitlines(keepends=True)
    if not complete:
        lines = lines[:-1]
    rows: list[list[str]] = []
    with contextlib.suppress(csv.Error):
        rows.extend(csv.reader(io.StringIO("".join(lines)), delimiter=delimiter))
    # Строка в кавычках, оборванная на границе образца, не показывается.
    return rows if complete else rows[:-1] or rows


def _workbook_tables(source: Path | IO[bytes], kind: str, prefix: str) -> list[TableInfo]:
    if kind in {XLSX, ODS}:
        check_workbook(source, kind, prefix)
    try:
        book = (
            CalamineWorkbook.from_path(str(source))
            if isinstance(source, Path)
            else CalamineWorkbook.from_filelike(source)
        )
    except PasswordError as error:
        raise IngestError(_password_text(), "password") from error
    except Exception as error:  # библиотека сообщает о повреждении своими исключениями
        raise IngestError(
            _("Файл «%(name)s» не читается как таблица Excel.") % {"name": prefix or kind.upper()},
            "broken",
        ) from error
    tables = []
    for meta in book.sheets_metadata:
        if "Visible" not in str(meta.visible) or "Hidden" in str(meta.visible):
            continue
        rows = book.get_sheet_by_name(meta.name).to_python()
        table = TableInfo(
            key=f"{prefix}#{meta.name}",
            name=f"{prefix} — {meta.name}" if prefix else meta.name,
            kind=kind,
            size=0,
            rows=len(rows),
            rows_exact=True,
        )
        # В текст переводится только просматриваемое начало листа; субъекты — по всему столбцу.
        _describe(table, [[cell_text(cell) for cell in row] for row in rows[:SCAN_ROWS]])
        if table.territory_column is not None:
            table.regions = len(matching.subjects_in(set(_column(rows, table.territory_column))))
        if table.sample:
            tables.append(table)
    return tables


def check_workbook(source: Path | IO[bytes], kind: str, name: str = "") -> None:
    """Размер листов XLSX и ODS проверяется до чтения: книга — тоже архив."""
    try:
        with zipfile.ZipFile(source) as bundle:
            sheets = [
                info
                for info in bundle.infolist()
                if info.filename.startswith("xl/worksheets/") or info.filename == "content.xml"
            ]
    except zipfile.BadZipFile as error:
        raise IngestError(
            _("Файл «%(name)s» повреждён.") % {"name": name or kind.upper()}, "broken"
        ) from error
    finally:
        if not isinstance(source, Path):
            source.seek(0)
    total = sum(info.file_size for info in sheets)
    packed = sum(info.compress_size for info in sheets)
    if total > settings.USERDATA_TABLE_MAX_BYTES:
        raise IngestError(_table_size_text(name or kind.upper()), "table_size")
    if total > RATIO_CHECK_FROM and total / max(packed, 1) > settings.USERDATA_ZIP_MAX_RATIO:
        raise IngestError(
            _("Листы книги «%(name)s» сжаты слишком сильно. Такой файл не принимается.")
            % {"name": name or kind.upper()},
            "ratio",
        )


def _parquet_table(source: Path | IO[bytes], key: str) -> TableInfo:
    import pyarrow.parquet as pq

    try:
        parquet = pq.ParquetFile(source)
    except Exception as error:  # pyarrow сообщает о повреждении своими исключениями
        raise IngestError(_("Файл Parquet повреждён."), "broken") from error
    metadata = parquet.metadata
    size = sum(
        metadata.row_group(index).total_byte_size for index in range(metadata.num_row_groups)
    )
    if size > settings.USERDATA_TABLE_MAX_BYTES:
        raise IngestError(_table_size_text(key or "Parquet"), "table_size")
    names = [str(name) for name in parquet.schema_arrow.names]
    rows = [names]
    for batch in parquet.iter_batches(batch_size=SCAN_ROWS):
        rows.extend([cell_text(value) for value in record.values()] for record in batch.to_pylist())
        break
    table = TableInfo(
        key=key,
        name=key or "Parquet",
        kind=PARQUET,
        size=size,
        rows=metadata.num_rows + 1,
        rows_exact=True,
    )
    _describe(table, rows)
    if table.territory_column is not None:
        column = parquet.read(columns=[names[table.territory_column]]).column(0)
        table.regions = len(matching.subjects_in(column.unique().to_pylist()))
    return table


def _describe(table: TableInfo, rows: list[list[str]]) -> None:
    """Образец строк и столбец, где узнаётся больше всего субъектов."""
    rows = [row for row in rows if any(cell.strip() for cell in row)]
    table.sample = [row[:SCAN_COLUMNS] for row in rows[:SAMPLE_ROWS]]
    scanned = rows[:SCAN_ROWS]
    width = min(max((len(row) for row in scanned), default=0), SCAN_COLUMNS)
    best = (0, -1)
    for column in range(width):
        values = set(_column(scanned, column))
        if 0 < len(values) <= SCAN_DISTINCT:
            best = max(best, (len(matching.subjects_in(values)), -column))
    if best[0]:
        table.territory_column = -best[1]
        table.regions = best[0]


def _column(rows: list[list[Any]], column: int) -> Iterator[str]:
    for row in rows:
        if column < len(row) and isinstance(row[column], str) and row[column].strip():
            yield row[column].strip()


def _count_regions(path: Path, table: TableInfo) -> None:
    """Субъекты по всему столбцу территорий текстовой таблицы — потоковым чтением."""
    if table.kind != CSV or table.territory_column is None or table.rows_exact:
        return
    values = column_values(path, table, table.territory_column)
    table.regions = len(matching.subjects_in(values))


def column_values(path: Path, table: TableInfo, column: int) -> set[str]:
    """Разные значения одного столбца CSV целиком (из архива — без распаковки на диск)."""
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.csv as pacsv

    name = f"c{column}"
    width = max(len(row) for row in table.sample)
    options = {
        "read_options": pacsv.ReadOptions(
            column_names=[f"c{index}" for index in range(width)],
            encoding=table.encoding.replace("-sig", "") or "utf-8",
            block_size=8 << 20,
        ),
        "parse_options": pacsv.ParseOptions(
            delimiter=table.delimiter or ";",
            newlines_in_values=True,
            invalid_row_handler=lambda _row: "skip",
        ),
        "convert_options": pacsv.ConvertOptions(
            include_columns=[name], column_types={name: pa.string()}
        ),
    }
    values: set[str] = set()
    with _open_table(path, table) as stream:
        for batch in pacsv.open_csv(stream, **options):
            values.update(value for value in pc.unique(batch.column(0)).to_pylist() if value)
    return values


@contextmanager
def _open_table(path: Path, table: TableInfo) -> Iterator[IO[bytes]]:
    """Поток таблицы: сам файл или файл архива без распаковки на диск."""
    if not table.member:
        with path.open("rb") as handle:
            yield handle
        return
    with zipfile.ZipFile(path) as bundle:
        info = next(item for item in bundle.infolist() if member_name(item) == table.member)
        with bundle.open(info) as stream:
            yield stream


def cell_text(cell: Any) -> str:
    if cell is None:
        return ""
    if isinstance(cell, float) and cell.is_integer():
        return str(int(cell))
    return str(cell)


# --- Чтение строк выбранной таблицы --------------------------------------------------------------


def iter_rows(path: Path, table: TableInfo, *, limit: int | None = None) -> Iterator[list[Any]]:
    """Строки таблицы: значения ячеек Excel — как есть, текст — строками."""
    if table.kind in WORKBOOK_KINDS:
        book = CalamineWorkbook.from_path(str(path))
        yield from book.get_sheet_by_name(table.sheet).to_python(skip_empty_area=False, nrows=limit)
        return
    if table.kind == PARQUET:
        yield from _parquet_rows(path, limit)
        return
    with path.open("rb") as handle:
        text = io.TextIOWrapper(
            handle, encoding=table.encoding or "utf-8", errors="replace", newline=""
        )
        if table.kind == HTML:
            yield from html_rows(text.read())[:limit]
            return
        for index, row in enumerate(csv.reader(text, delimiter=table.delimiter or ";")):
            if limit is not None and index >= limit:
                return
            if index == 0 and row:
                row[0] = row[0].removeprefix(BOM)
            yield row


def _parquet_rows(path: Path, limit: int | None) -> Iterator[list[Any]]:
    import pyarrow.parquet as pq

    parquet = pq.ParquetFile(path)
    names = list(parquet.schema_arrow.names)
    yield names
    produced = 0
    for batch in parquet.iter_batches(batch_size=50_000):
        for record in batch.to_pylist():
            if limit is not None and produced >= limit:
                return
            produced += 1
            yield [record.get(name) for name in names]


def html_rows(markup: str) -> list[list[str]]:
    """Самая большая таблица разметки с раскрытыми объединёнными клетками."""
    from .html_tables import tables

    found = tables(markup)
    return max(found, key=lambda rows: sum(map(len, rows))) if found else []


# --- Тексты отказов ------------------------------------------------------------------------------


def _password_text() -> str:
    return _("Файл защищён паролем. Снимите защиту в Excel и загрузите снова.")


def _table_size_text(name: str) -> str:
    return _(
        "Таблица «%(name)s» в распакованном виде больше %(limit)s МБ. Оставьте в ней только "
        "нужные показатели или годы."
    ) % {"name": name, "limit": settings.USERDATA_TABLE_MAX_BYTES // (1024 * 1024)}


def _refusal_text(code: str) -> str:
    accepted = _("Подходят CSV, TXT, XLSX, XLS, ODS, Parquet и ZIP с ними.")
    texts = {
        "pdf": _("Это не таблица.")
        + " "
        + accepted
        + " "
        + _("Таблицу из Word или PDF можно скопировать и вставить в поле «Вставить таблицу»."),
        "word": _(
            "Это документ, а не таблица. Скопируйте таблицу из документа и вставьте её "
            "в поле «Вставить таблицу»."
        ),
        "image": _("Это изображение, а не таблица.") + " " + accepted,
        "rar": _("Архивы RAR и 7z не принимаются. Распакуйте архив и загрузите таблицу или ZIP."),
        "gzip": _("Сжатые файлы GZIP не принимаются. Распакуйте файл или загрузите архив ZIP."),
        "binary": _("Это не таблица.") + " " + accepted,
        "empty_archive": _("В архиве нет таблиц.") + " " + accepted,
    }
    return texts[code]
