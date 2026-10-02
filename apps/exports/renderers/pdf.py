"""
Вывод отчёта в PDF потоком элементов Platypus.

Широкая таблица режется по столбцам, первый столбец повторяется в каждой части.
"""

from __future__ import annotations

import io

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    TableStyle,
)
from reportlab.platypus import (
    Table as PdfTable,
)

from ..reports.base import Column, ReportDocument, Table, format_value
from .fonts import register_report_fonts
from .palette import document_colours

# Начиная с этого числа столбцов страница разворачивается в альбомную ориентацию.
LANDSCAPE_COLUMN_LIMIT = 8

# Наибольшее число столбцов в одной части широкой таблицы, не считая повторяемого первого.
MAX_COLUMNS_PER_CHUNK = 11

BODY_FONT_SIZE = 7.5
HEADER_FONT_SIZE = 7.5
PAGE_MARGIN = 14 * mm


def render(document: ReportDocument) -> bytes:
    """Собрать документ PDF по отчёту."""
    regular, bold = register_report_fonts()
    styles = _build_styles(regular, bold)

    wide = any(len(table.columns) > LANDSCAPE_COLUMN_LIMIT for table in document.tables)
    page_size = landscape(A4) if wide else A4

    buffer = io.BytesIO()
    template = SimpleDocTemplate(
        buffer,
        pagesize=page_size,
        leftMargin=PAGE_MARGIN,
        rightMargin=PAGE_MARGIN,
        topMargin=PAGE_MARGIN,
        bottomMargin=PAGE_MARGIN + 6 * mm,
        title=document.title,
        author=document.footer,
    )

    story: list[object] = [
        Paragraph(_escape(document.title), styles["title"]),
    ]
    if document.subtitle:
        story.append(Paragraph(_escape(document.subtitle), styles["subtitle"]))
    story.append(Spacer(1, 6 * mm))

    if document.meta:
        story.append(_meta_table(document, styles, page_size))
        story.append(Spacer(1, 6 * mm))

    for index, section in enumerate(document.sections):
        if index:
            story.append(Spacer(1, 5 * mm))
        if section.heading:
            story.append(Paragraph(_escape(section.heading), styles["heading"]))
        for paragraph in section.paragraphs:
            story.append(Paragraph(_escape(paragraph), styles["body"]))
        for table in section.tables:
            story.extend(_table_flowables(table, styles, page_size))

    template.build(
        story,
        onFirstPage=lambda canvas, doc: _draw_footer(canvas, doc, document.footer, regular),
        onLaterPages=lambda canvas, doc: _draw_footer(canvas, doc, document.footer, regular),
    )
    return buffer.getvalue()


# ---------------------------------------------------------------------------------------
# Стили и элементы
# ---------------------------------------------------------------------------------------


def _colour(value: str) -> colors.Color:
    """Цвет reportlab по шестнадцатеричной записи без «#»."""
    return colors.HexColor(f"#{value}")


def _build_styles(regular: str, bold: str) -> dict[str, ParagraphStyle]:
    """Собрать набор стилей документа на подобранных шрифтах."""
    palette = document_colours()
    base = ParagraphStyle(
        "rl-base", parent=getSampleStyleSheet()["BodyText"], textColor=_colour(palette.text)
    )
    return {
        "title": ParagraphStyle(
            "rl-title", parent=base, fontName=bold, fontSize=15, leading=19, spaceAfter=2
        ),
        "subtitle": ParagraphStyle(
            "rl-subtitle",
            parent=base,
            fontName=regular,
            fontSize=10,
            leading=13,
            textColor=_colour(palette.muted),
        ),
        "heading": ParagraphStyle(
            "rl-heading",
            parent=base,
            fontName=bold,
            fontSize=11,
            leading=14,
            spaceBefore=4,
            spaceAfter=3,
        ),
        "body": ParagraphStyle(
            "rl-body", parent=base, fontName=regular, fontSize=9, leading=12, spaceAfter=3
        ),
        "note": ParagraphStyle(
            "rl-note",
            parent=base,
            fontName=regular,
            fontSize=8,
            leading=10,
            textColor=_colour(palette.muted),
        ),
        "cell": ParagraphStyle(
            "rl-cell", parent=base, fontName=regular, fontSize=BODY_FONT_SIZE, leading=9
        ),
        "cell-header": ParagraphStyle(
            "rl-cell-header",
            parent=base,
            fontName=bold,
            fontSize=HEADER_FONT_SIZE,
            leading=9,
            textColor=_colour(palette.header_text),
            alignment=TA_CENTER,
        ),
    }


def _meta_table(
    document: ReportDocument,
    styles: dict[str, ParagraphStyle],
    page_size: tuple[float, float],
) -> PdfTable:
    """Построить таблицу реквизитов отчёта."""
    available = page_size[0] - 2 * PAGE_MARGIN
    rows = [
        [Paragraph(_escape(label), styles["note"]), Paragraph(_escape(value), styles["note"])]
        for label, value in document.meta
    ]
    table = PdfTable(rows, colWidths=[available * 0.28, available * 0.72], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("LINEBELOW", (0, 0), (-1, -2), 0.25, _colour(document_colours().rule)),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    return table


def _table_flowables(
    table: Table,
    styles: dict[str, ParagraphStyle],
    page_size: tuple[float, float],
) -> list[object]:
    """Построить элементы одной таблицы отчёта, при необходимости разрезав её."""
    flowables: list[object] = []
    if table.note:
        flowables.append(Paragraph(_escape(table.note), styles["note"]))
        flowables.append(Spacer(1, 2 * mm))

    chunks = _split_columns(table.columns)
    for index, chunk in enumerate(chunks):
        if index:
            flowables.append(PageBreak())
            flowables.append(
                Paragraph(
                    _escape(f"{table.title} — продолжение ({index + 1} из {len(chunks)})"),
                    styles["heading"],
                )
            )
        flowables.append(_render_chunk(table, chunk, styles, page_size))

    flowables.append(Spacer(1, 4 * mm))
    return flowables


def _render_chunk(
    table: Table,
    columns: list[Column],
    styles: dict[str, ParagraphStyle],
    page_size: tuple[float, float],
) -> PdfTable:
    """Построить таблицу из подмножества столбцов."""
    data = [[Paragraph(_escape(column.title), styles["cell-header"]) for column in columns]]
    for row in table.rows:
        data.append(
            [
                Paragraph(_escape(format_value(row.get(column.key), column)), styles["cell"])
                for column in columns
            ]
        )

    available = page_size[0] - 2 * PAGE_MARGIN
    total_width = sum(column.width for column in columns) or 1
    widths = [available * column.width / total_width for column in columns]

    palette = document_colours()
    pdf_table = PdfTable(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    pdf_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), _colour(palette.header)),
                ("LINEBELOW", (0, 0), (-1, 0), 0.6, _colour(palette.header_rule)),
                ("LINEBELOW", (0, 1), (-1, -1), 0.25, _colour(palette.rule)),
                (
                    "ROWBACKGROUNDS",
                    (0, 1),
                    (-1, -1),
                    [_colour(palette.paper), _colour(palette.stripe)],
                ),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return pdf_table


def _split_columns(columns: list[Column]) -> list[list[Column]]:
    """Разрезать перечень столбцов на части по странице, повторяя первый в каждой."""
    if len(columns) <= MAX_COLUMNS_PER_CHUNK + 1:
        return [columns]

    first, rest = columns[0], columns[1:]
    return [
        [first, *rest[start : start + MAX_COLUMNS_PER_CHUNK]]
        for start in range(0, len(rest), MAX_COLUMNS_PER_CHUNK)
    ]


def _draw_footer(canvas: object, doc: object, text: str, font: str) -> None:
    """Нарисовать колонтитул с реквизитами системы и номером страницы."""
    canvas.saveState()  # type: ignore[attr-defined]
    canvas.setFont(font, 7)  # type: ignore[attr-defined]
    canvas.setFillColor(_colour(document_colours().muted))  # type: ignore[attr-defined]
    width = doc.pagesize[0]  # type: ignore[attr-defined]
    canvas.drawString(PAGE_MARGIN, 8 * mm, text)  # type: ignore[attr-defined]
    canvas.drawRightString(  # type: ignore[attr-defined]
        width - PAGE_MARGIN,
        8 * mm,
        f"с. {doc.page}",  # type: ignore[attr-defined]
    )
    canvas.restoreState()  # type: ignore[attr-defined]


def _escape(value: str) -> str:
    """Экранировать ``&``, ``<`` и ``>`` для разметки Paragraph."""
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
