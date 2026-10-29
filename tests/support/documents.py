"""
Документы для проверок чтения таблиц: DOCX с объединёнными клетками и PDF с таблицей
на линиях — собираются в памяти, без сторонних библиотек. И наименьший PNG — картинка
для отчёта Word.
"""

from __future__ import annotations

import io
import struct
import zipfile
import zlib
from collections.abc import Sequence
from xml.sax.saxutils import escape

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml"
 ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""
_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
    'relationships/officeDocument" Target="word/document.xml"/></Relationships>'
)
_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _paragraph(text: str) -> str:
    return f'<w:p><w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>'


def _cell(text: str, *, span: int = 1, merge: str = "") -> str:
    properties = ""
    if span > 1:
        properties += f'<w:gridSpan w:val="{span}"/>'
    if merge == "restart":
        properties += '<w:vMerge w:val="restart"/>'
    elif merge == "continue":
        properties += "<w:vMerge/>"
    head = f"<w:tcPr>{properties}</w:tcPr>" if properties else ""
    return f"<w:tc>{head}{_paragraph(text)}</w:tc>"


def _table(regions: Sequence[str], years: Sequence[int], first: int = 0) -> str:
    """Таблица: «Субъект» объединён по двум строкам, «Годы» — над столбцами лет."""
    header = (
        "<w:tr>"
        + _cell("Субъект", merge="restart")
        + _cell("Годы", span=len(years))
        + "</w:tr><w:tr>"
        + _cell("", merge="continue")
        + "".join(_cell(str(year)) for year in years)
        + "</w:tr>"
    )
    body = "".join(
        "<w:tr>"
        + _cell(region)
        + "".join(_cell(f"{index * 10 + offset + 1},5") for offset, _year in enumerate(years))
        + "</w:tr>"
        for index, region in enumerate(regions, start=first)
    )
    return f"<w:tbl>{header}{body}</w:tbl>"


def docx(
    caption: str,
    regions: Sequence[str],
    years: Sequence[int],
    *,
    split: int = 0,
    continued_years: Sequence[int] = (),
) -> bytes:
    """
    Документ Word: абзац-название и таблица значений по субъектам. С ``split`` субъекты
    с этого номера — во второй таблице под абзацем «Продолжение табл. 1», как в «Регионах
    России»; ``continued_years`` — у второй таблицы другие столбцы (правая часть широкой).
    """
    tables = _table(regions[:split] if split else regions, years)
    if split:
        tables += _paragraph("Продолжение табл. 1") + _table(
            regions[split:], continued_years or years, split
        )
    document = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="{_W}">'
        f"<w:body>{_paragraph('Пояснение к таблице.')}{_paragraph(caption)}"
        f"{tables}{_paragraph('Источник: расчёт.')}</w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("[Content_Types].xml", _CONTENT_TYPES)
        bundle.writestr("_rels/.rels", _RELS)
        bundle.writestr("word/document.xml", document)
    return buffer.getvalue()


# --- PDF ------------------------------------------------------------------------------------


def _text(x: float, y: float, value: str, *, turned: bool = False) -> str:
    safe = value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    if turned:
        # Поворот на 90° против часовой стрелки — как его пишет Excel, с погрешностью
        # в матрице: знаки встают столбиком снизу вверх.
        return f"BT /F1 9 Tf -0.000000004 1 -1 -0.000000004 {x:.1f} {y:.1f} Tm ({safe}) Tj ET"
    return f"BT /F1 9 Tf {x:.1f} {y:.1f} Td ({safe}) Tj ET"


def _table_stream(
    rows: Sequence[Sequence[str]],
    *,
    top: float = 760.0,
    text: bool = True,
    turned: bool = False,
    ruled: int | None = None,
    caption: str = "",
) -> str:
    """
    Таблица линиями: рамка каждой клетки и текст в ней (латиница — шрифт Helvetica).
    ``turned`` — подписи первой строки повёрнуты на 90° (строка выше); ``ruled`` — рамки
    только у стольких первых строк (шапка), остальные строки — текстом без линий;
    ``caption`` — строка названия над таблицей.
    """
    width, height, left = 110.0, 18.0, 50.0
    parts = ["0.5 w"]
    if caption:
        parts.append(_text(left, top + 8, caption))
    y = top
    for row_index, row in enumerate(rows):
        row_height = 60.0 if turned and row_index == 0 else height
        y -= row_height
        for column, value in enumerate(row):
            x = left + column * width
            if ruled is None or row_index < ruled:
                parts.append(f"{x:.1f} {y:.1f} {width:.1f} {row_height:.1f} re S")
            if text and value:
                if turned and row_index == 0:
                    parts.append(_text(x + 12, y + 4, value, turned=True))
                else:
                    parts.append(_text(x + 4, y + 5, value))
    return "\n".join(parts)


def pdf(
    pages: Sequence[Sequence[Sequence[str]]],
    *,
    text: bool = True,
    turned: bool = False,
    ruled: int | None = None,
    captions: Sequence[str] = (),
) -> bytes:
    """
    PDF: на каждой странице — таблица линиями; без текста — как скан. ``turned``, ``ruled``
    и ``captions`` (по странице) — см. ``_table_stream``.
    """
    objects: list[str] = []
    page_ids = [3 + index * 2 for index in range(len(pages))]
    font_id = 3 + len(pages) * 2
    objects.append("<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>")
    for index, rows in enumerate(pages):
        caption = captions[index] if index < len(captions) else ""
        stream = _table_stream(rows, text=text, turned=turned, ruled=ruled, caption=caption)
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {page_ids[index] + 1} 0 R >>"
        )
        objects.append(
            f"<< /Length {len(stream.encode('latin-1'))} >>\nstream\n{stream}\nendstream"
        )
    objects.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    output = io.BytesIO()
    output.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(output.tell())
        output.write(f"{number} 0 obj\n{body}\nendobj\n".encode("latin-1"))
    xref = output.tell()
    output.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("latin-1"))
    for offset in offsets:
        output.write(f"{offset:010d} 00000 n \n".encode("latin-1"))
    output.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode(
            "latin-1"
        )
    )
    return output.getvalue()


def png(width: int, height: int) -> bytes:
    """Настоящий PNG заданного размера, чёрный."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(kind + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)

    rows = b"".join(bytes(1 + 3 * width) for _ in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    signature = bytes((0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A))
    return (
        signature
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )
