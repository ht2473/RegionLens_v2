"""
Вывод в Word (DOCX) без сторонних библиотек: документ Office Open XML из абзацев, таблиц
и картинок PNG, страница A4 с номерами внизу. Стили — свои (заголовки, подписи, пояснения),
цвета — светлая ветвь маркеров, как у PDF и XLSX: документ печатают и открывают вне сайта.
"""

from __future__ import annotations

import io
import re
import struct
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from xml.sax.saxutils import escape, quoteattr  # nosec B406 — только запись XML, не разбор

from .palette import document_colours

# Страница A4 и поля 2 см, в твипах (двадцатых долях точки).
PAGE_WIDTH = 11906
PAGE_HEIGHT = 16838
MARGIN = 1134
TEXT_WIDTH = PAGE_WIDTH - 2 * MARGIN
# Единицы чертежа Word: в твипе 635, в точке CSS (1/96 дюйма) 9525.
EMU_PER_TWIP = 635
EMU_PER_PIXEL = 9525
# Картинки со страницы — в двойном разрешении: точка CSS — два пикселя.
PIXEL_RATIO = 2
# Картинка не выше этой доли высоты полосы набора: подпись и источник остаются рядом.
IMAGE_HEIGHT_SHARE = 0.6
FONT = "Arial"
# Знаки, которых не бывает в XML 1.0: управляющие, кроме табуляции и перевода строки.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# Стили абзацев: имя, кегль (полуточки), полужирный, курсив, цвет («muted» — приглушённый),
# отступ до и после (твипы), держать со следующим, уровень оглавления.
_STYLES = (
    ("Title", "Название", 36, True, False, "", 0, 120, False, None),
    ("Heading1", "Заголовок 1", 28, True, False, "", 360, 120, True, 0),
    # Над заголовком карточки — строка её вида («Kicker»): отступ сверху даёт она.
    ("Heading2", "Заголовок 2", 24, True, False, "", 40, 80, True, 1),
    ("Heading3", "Заголовок 3", 20, True, False, "", 200, 60, True, 2),
    ("Kicker", "Вид карточки", 16, False, False, "muted", 360, 20, True, None),
    ("Lead", "Число", 26, True, False, "", 60, 60, False, None),
    ("Note", "Пояснение", 20, False, True, "", 0, 100, False, None),
    ("Meta", "Сведения", 17, False, False, "muted", 0, 60, False, None),
    ("Source", "Источник", 16, False, False, "muted", 60, 160, False, None),
    ("ListText", "Пункт списка", 20, False, False, "", 0, 40, False, None),
    ("TableText", "Текст таблицы", 17, False, False, "", 0, 0, False, None),
    ("Figure", "Рисунок", 20, False, False, "", 60, 60, True, None),
)


class WordError(ValueError):
    """Картинка не годится для документа: не PNG или испорчена."""


@dataclass(slots=True)
class Cell:
    """Клетка таблицы: текст, по правому краю (число), шапка, полужирный (итог)."""

    text: str
    numeric: bool = False
    header: bool = False
    bold: bool = False


@dataclass(slots=True)
class _Image:
    data: bytes
    width: int
    height: int


@dataclass(slots=True)
class Word:
    """Документ по порядку: абзацы, таблицы, картинки; ``render`` собирает файл."""

    body: list[str] = field(default_factory=list)
    images: list[_Image] = field(default_factory=list)

    def paragraph(self, text: str, style: str = "", *, bold: bool = False) -> None:
        """Абзац; перевод строки в тексте — разрыв строки внутри абзаца."""
        self.body.append(_paragraph(text, style, bold=bold))

    def heading(self, text: str, level: int = 1) -> None:
        """Заголовок уровня 1–3; уровень 0 — название документа."""
        self.paragraph(text, "Title" if level == 0 else f"Heading{min(level, 3)}")

    def item(self, text: str, marker: str = "•") -> None:
        """Пункт списка с отступом: маркер или номер, табуляция, текст."""
        self.body.append(_paragraph(f"{marker}\t{text}", "ListText"))

    def table(self, rows: list[list[Cell]]) -> None:
        """Таблица; строки короче самой длинной дополняются пустыми клетками."""
        rows = [row for row in rows if row]
        if not rows:
            return
        width = max(len(row) for row in rows)
        grid = "".join(f'<w:gridCol w:w="{TEXT_WIDTH // width}"/>' for _ in range(width))
        parts = [
            "<w:tbl><w:tblPr>"
            '<w:tblStyle w:val="ReportTable"/><w:tblW w:w="0" w:type="auto"/>'
            '<w:tblLook w:val="04A0" w:firstRow="1" w:lastRow="0" w:firstColumn="1"'
            ' w:lastColumn="0" w:noHBand="1" w:noVBand="1"/>'
            f"</w:tblPr><w:tblGrid>{grid}</w:tblGrid>"
        ]
        colours = document_colours()
        for row in rows:
            cells = row + [Cell("")] * (width - len(row))
            head = all(cell.header for cell in row)
            parts.append("<w:tr>")
            if head:
                parts.append("<w:trPr><w:tblHeader/><w:cantSplit/></w:trPr>")
            else:
                parts.append("<w:trPr><w:cantSplit/></w:trPr>")
            for cell in cells:
                shade = (
                    f'<w:shd w:val="clear" w:color="auto" w:fill="{colours.header}"/>'
                    if cell.header
                    else ""
                )
                align = '<w:jc w:val="right"/>' if cell.numeric else ""
                parts.append(
                    f"<w:tc><w:tcPr>{shade}</w:tcPr>"
                    f'<w:p><w:pPr><w:pStyle w:val="TableText"/>{align}</w:pPr>'
                    f"{_runs(cell.text, bold=cell.bold or cell.header)}</w:p></w:tc>"
                )
            parts.append("</w:tr>")
        parts.append("</w:tbl>")
        self.body.append("".join(parts))
        # После таблицы — пустой абзац: две таблицы подряд Word склеил бы в одну.
        self.body.append(_paragraph("", "TableText"))

    def image(self, data: bytes, description: str = "") -> None:
        """Картинка PNG во всю полосу набора, не выше её доли; ``description`` — замена."""
        width, height = png_size(data)
        self.images.append(_Image(data, width, height))
        number = len(self.images)
        cx = width * EMU_PER_PIXEL // PIXEL_RATIO
        cy = height * EMU_PER_PIXEL // PIXEL_RATIO
        scale = min(
            1.0,
            TEXT_WIDTH * EMU_PER_TWIP / cx,
            (PAGE_HEIGHT - 2 * MARGIN) * IMAGE_HEIGHT_SHARE * EMU_PER_TWIP / cy,
        )
        cx, cy = int(cx * scale), int(cy * scale)
        name = f"image{number}.png"
        self.body.append(
            '<w:p><w:pPr><w:pStyle w:val="Figure"/></w:pPr><w:r><w:drawing>'
            '<wp:inline distT="0" distB="0" distL="0" distR="0">'
            f'<wp:extent cx="{cx}" cy="{cy}"/>'
            f"<wp:docPr id={quoteattr(str(number))} name={quoteattr(name)}"
            f" descr={quoteattr(_clean(description))}/>"
            '<wp:cNvGraphicFramePr><a:graphicFrameLocks noChangeAspect="1"/></wp:cNvGraphicFramePr>'
            '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            f'<pic:pic><pic:nvPicPr><pic:cNvPr id="{number}" name="{name}"/><pic:cNvPicPr/>'
            f'</pic:nvPicPr><pic:blipFill><a:blip r:embed="rIdImage{number}"/>'
            "<a:stretch><a:fillRect/></a:stretch></pic:blipFill>"
            f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic>'
            "</a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>"
        )

    def render(self, title: str, *, creator: str = "RegionLens", language: str = "ru-RU") -> bytes:
        """Собрать файл DOCX."""
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
            archive.writestr("_rels/.rels", _ROOT_RELS)
            archive.writestr("docProps/core.xml", _core(title, creator))
            archive.writestr("docProps/app.xml", _APP)
            archive.writestr("word/document.xml", self._document())
            archive.writestr("word/styles.xml", _styles(language))
            archive.writestr("word/settings.xml", _SETTINGS)
            archive.writestr("word/footer1.xml", _FOOTER)
            archive.writestr("word/_rels/document.xml.rels", self._relations())
            for number, picture in enumerate(self.images, start=1):
                archive.writestr(f"word/media/image{number}.png", picture.data)
        return buffer.getvalue()

    def _document(self) -> str:
        section = (
            '<w:sectPr><w:footerReference w:type="default" r:id="rIdFooter"/>'
            f'<w:pgSz w:w="{PAGE_WIDTH}" w:h="{PAGE_HEIGHT}"/>'
            f'<w:pgMar w:top="{MARGIN}" w:right="{MARGIN}" w:bottom="{MARGIN}" w:left="{MARGIN}"'
            ' w:header="567" w:footer="567" w:gutter="0"/></w:sectPr>'
        )
        return (
            f"{_XML}<w:document {_NAMESPACES}><w:body>{''.join(self.body)}{section}"
            "</w:body></w:document>"
        )

    def _relations(self) -> str:
        office = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
        items = [
            f'<Relationship Id="rIdStyles" Type="{office}/styles" Target="styles.xml"/>',
            f'<Relationship Id="rIdSettings" Type="{office}/settings" Target="settings.xml"/>',
            f'<Relationship Id="rIdFooter" Type="{office}/footer" Target="footer1.xml"/>',
        ]
        items += [
            f'<Relationship Id="rIdImage{number}" Type="{office}/image"'
            f' Target="media/image{number}.png"/>'
            for number in range(1, len(self.images) + 1)
        ]
        return f'{_XML}<Relationships xmlns="{_PACKAGE_RELS}">{"".join(items)}</Relationships>'


def png_size(data: bytes) -> tuple[int, int]:
    """Ширина и высота PNG в пикселях — из заголовка IHDR; не PNG — ошибка."""
    if len(data) < 24 or not data.startswith(PNG_SIGNATURE) or data[12:16] != b"IHDR":  # noqa: PLR2004
        raise WordError("not a PNG image")
    width, height = struct.unpack(">II", data[16:24])
    if not width or not height:
        raise WordError("empty PNG image")
    return width, height


def _clean(text: str) -> str:
    return _CONTROL.sub("", text)


def _runs(text: str, *, bold: bool = False) -> str:
    """Текст отрезками: перевод строки — разрыв, табуляция — табуляция."""
    properties = "<w:rPr><w:b/></w:rPr>" if bold else ""
    pieces = []
    for number, line in enumerate(_clean(text).split("\n")):
        if number:
            pieces.append("<w:br/>")
        for index, chunk in enumerate(line.split("\t")):
            if index:
                pieces.append("<w:tab/>")
            if chunk:
                pieces.append(f'<w:t xml:space="preserve">{escape(chunk)}</w:t>')
    return f"<w:r>{properties}{''.join(pieces)}</w:r>" if pieces else ""


def _paragraph(text: str, style: str = "", *, bold: bool = False) -> str:
    style_tag = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f"<w:p>{style_tag}{_runs(text, bold=bold)}</w:p>"


def _core(title: str, creator: str) -> str:
    moment = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return (
        f"{_XML}<cp:coreProperties"
        ' xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"'
        ' xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/"'
        ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f"<dc:title>{escape(_clean(title))}</dc:title><dc:creator>{escape(creator)}</dc:creator>"
        f'<dcterms:created xsi:type="dcterms:W3CDTF">{moment}</dcterms:created>'
        f'<dcterms:modified xsi:type="dcterms:W3CDTF">{moment}</dcterms:modified>'
        "</cp:coreProperties>"
    )


def _styles(language: str) -> str:
    colours = document_colours()
    paragraph_styles = []
    for code, name, size, bold, italic, tone, before, after, keep, level in _STYLES:
        colour = colours.muted if tone == "muted" else colours.text
        paragraph = (
            f'<w:spacing w:before="{before}" w:after="{after}"/>'
            + ("<w:keepNext/>" if keep else "")
            + (f'<w:outlineLvl w:val="{level}"/>' if level is not None else "")
        )
        if code == "ListText":
            paragraph += '<w:tabs><w:tab w:val="left" w:pos="357"/></w:tabs>'
            paragraph += '<w:ind w:left="357" w:hanging="357"/>'
        run = (
            ("<w:b/>" if bold else "")
            + ("<w:i/>" if italic else "")
            + f'<w:color w:val="{colour}"/><w:sz w:val="{size}"/><w:szCs w:val="{size}"/>'
        )
        paragraph_styles.append(
            f'<w:style w:type="paragraph" w:styleId="{code}"><w:name w:val="{name}"/>'
            '<w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/>'
            f"<w:pPr>{paragraph}</w:pPr><w:rPr>{run}</w:rPr></w:style>"
        )
    rule = f'w:val="single" w:sz="4" w:space="0" w:color="{colours.rule}"'
    return (
        f'{_XML}<w:styles xmlns:w="{_W}"><w:docDefaults><w:rPrDefault><w:rPr>'
        f'<w:rFonts w:ascii="{FONT}" w:hAnsi="{FONT}" w:eastAsia="{FONT}" w:cs="{FONT}"/>'
        f'<w:sz w:val="20"/><w:szCs w:val="20"/><w:lang w:val="{language}"/>'
        '</w:rPr></w:rPrDefault><w:pPrDefault><w:pPr><w:spacing w:after="100" w:line="264"'
        ' w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>'
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/>'
        f'<w:qFormat/><w:rPr><w:color w:val="{colours.text}"/></w:rPr></w:style>'
        + "".join(paragraph_styles)
        + '<w:style w:type="table" w:default="1" w:styleId="TableNormal">'
        '<w:name w:val="Normal Table"/>'
        '<w:tblPr><w:tblInd w:w="0" w:type="dxa"/><w:tblCellMar><w:top w:w="0" w:type="dxa"/>'
        '<w:left w:w="108" w:type="dxa"/><w:bottom w:w="0" w:type="dxa"/>'
        '<w:right w:w="108" w:type="dxa"/></w:tblCellMar></w:tblPr></w:style>'
        '<w:style w:type="table" w:styleId="ReportTable"><w:name w:val="Таблица отчёта"/>'
        '<w:basedOn w:val="TableNormal"/><w:tblPr><w:tblBorders>'
        f"<w:top {rule}/><w:left {rule}/><w:bottom {rule}/><w:right {rule}/>"
        f"<w:insideH {rule}/><w:insideV {rule}/></w:tblBorders><w:tblCellMar>"
        '<w:top w:w="28" w:type="dxa"/><w:left w:w="85" w:type="dxa"/>'
        '<w:bottom w:w="28" w:type="dxa"/><w:right w:w="85" w:type="dxa"/>'
        "</w:tblCellMar></w:tblPr></w:style></w:styles>"
    )


_XML = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_PACKAGE_RELS = "http://schemas.openxmlformats.org/package/2006/relationships"
_NAMESPACES = (
    f'xmlns:w="{_W}"'
    ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    ' xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"'
    ' xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
    ' xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"'
)
_CONTENT_TYPES = (
    f"{_XML}"
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/'
    'vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Default Extension="png" ContentType="image/png"/>'
    '<Override PartName="/word/document.xml" ContentType="application/'
    'vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    '<Override PartName="/word/styles.xml" ContentType="application/'
    'vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
    '<Override PartName="/word/settings.xml" ContentType="application/'
    'vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/>'
    '<Override PartName="/word/footer1.xml" ContentType="application/'
    'vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>'
    '<Override PartName="/docProps/core.xml" ContentType="application/'
    'vnd.openxmlformats-package.core-properties+xml"/>'
    '<Override PartName="/docProps/app.xml" ContentType="application/'
    'vnd.openxmlformats-officedocument.extended-properties+xml"/>'
    "</Types>"
)
_ROOT_RELS = (
    f'{_XML}<Relationships xmlns="{_PACKAGE_RELS}">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
    'relationships/officeDocument" Target="word/document.xml"/>'
    '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/'
    'metadata/core-properties" Target="docProps/core.xml"/>'
    '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
    'relationships/extended-properties" Target="docProps/app.xml"/>'
    "</Relationships>"
)
_APP = (
    f"{_XML}<Properties"
    ' xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
    "<Application>RegionLens</Application></Properties>"
)
_SETTINGS = (
    f'{_XML}<w:settings xmlns:w="{_W}"><w:defaultTabStop w:val="709"/>'
    '<w:characterSpacingControl w:val="doNotCompress"/><w:compat>'
    '<w:compatSetting w:name="compatibilityMode" w:uri="http://schemas.microsoft.com/office/word"'
    ' w:val="15"/></w:compat></w:settings>'
)
# Номер страницы внизу по центру — полем PAGE.
_FOOTER = (
    f'{_XML}<w:ftr xmlns:w="{_W}"><w:p><w:pPr><w:pStyle w:val="Meta"/><w:jc w:val="center"/>'
    '</w:pPr><w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText xml:space="preserve">'
    ' PAGE </w:instrText></w:r><w:r><w:fldChar w:fldCharType="separate"/></w:r>'
    '<w:r><w:t>1</w:t></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r></w:p></w:ftr>'
)
