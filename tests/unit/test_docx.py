"""
Проверки вывода в Word: файл — правильный архив с правильным XML, картинки вкладываются
по размеру из PNG, таблицы ровные; разметка карточки превращается в абзацы, пункты,
таблицы и картинки так же, как её печатают.
"""

from __future__ import annotations

import io
import zipfile
from xml.etree import ElementTree

import pytest

from apps.exports.renderers.docx import PNG_SIGNATURE, Cell, Word, WordError, png_size
from apps.userdata.report import _CardReader
from tests.support.documents import png

pytestmark = pytest.mark.unit

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def parts(document: Word) -> dict[str, bytes]:
    """Части собранного файла по именам."""
    with zipfile.ZipFile(io.BytesIO(document.render("Проверка"))) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def paragraphs(document: Word) -> list[tuple[str, str]]:
    """Абзацы тела документа вне таблиц: стиль и текст."""
    body = ElementTree.fromstring(parts(document)["word/document.xml"]).find(f"{W}body")
    assert body is not None
    found = []
    for node in body.findall(f"{W}p"):
        style = node.find(f"{W}pPr/{W}pStyle")
        text = "".join(item.text or "" for item in node.iter(f"{W}t"))
        tabbed = "\t" if node.find(f".//{W}tab") is not None else ""
        if text:
            found.append((style.get(f"{W}val") if style is not None else "", tabbed + text))
    return found


class TestWord:
    """Файл Word без сторонних библиотек."""

    def test_every_part_is_well_formed(self) -> None:
        """Каждая часть XML разбирается; есть всё, без чего Word файл не откроет."""
        document = Word()
        document.heading("Заголовок", 0)
        document.paragraph("Текст & <знаки>\nвторая строка", "Note")
        document.item("пункт", "1.")
        document.table(
            [[Cell("Субъект", header=True)], [Cell("Москва"), Cell("1,0", numeric=True)]]
        )
        document.image(png(40, 20), "Карта")
        found = parts(document)
        for name in (
            "[Content_Types].xml",
            "_rels/.rels",
            "word/document.xml",
            "word/styles.xml",
            "word/_rels/document.xml.rels",
            "word/footer1.xml",
            "docProps/core.xml",
        ):
            assert name in found
        for name, data in found.items():
            if name.endswith((".xml", ".rels")):
                ElementTree.fromstring(data)
        assert found["word/media/image1.png"].startswith(PNG_SIGNATURE)

    def test_table_rows_are_padded_to_the_widest(self) -> None:
        """Короткая строка дополняется пустыми клетками: таблица ровная."""
        document = Word()
        document.table([[Cell("a", header=True), Cell("b", header=True)], [Cell("c")]])
        xml = ElementTree.fromstring(parts(document)["word/document.xml"])
        rows = list(xml.iter(f"{W}tr"))
        assert [len(row.findall(f"{W}tc")) for row in rows] == [2, 2]
        # Шапка повторяется на каждой странице.
        assert rows[0].find(f"{W}trPr/{W}tblHeader") is not None

    def test_image_size_fits_the_text_column(self) -> None:
        """Широкая картинка ужимается до полосы набора, пропорции сохраняются."""
        document = Word()
        document.image(png(4000, 2000))
        xml = parts(document)["word/document.xml"].decode()
        extent = xml.split("<wp:extent ", 1)[1].split("/>", 1)[0]
        cx, cy = (int(value.split('"')[1]) for value in extent.split())
        assert cx == 9638 * 635
        assert cx == pytest.approx(2 * cy, rel=0.01)

    def test_not_a_png_is_refused(self) -> None:
        """Не PNG — ошибка: в документ уходят только картинки PNG."""
        with pytest.raises(WordError):
            png_size(b"GIF89a" + bytes(30))
        assert png_size(png(7, 3)) == (7, 3)

    def test_control_characters_are_dropped(self) -> None:
        """Управляющий знак из таблицы пользователя не ломает XML."""
        document = Word()
        document.paragraph("до\x0bпосле")
        assert paragraphs(document) == [("", "допосле")]


class TestCardReader:
    """Разметка карточки → документ, как при печати."""

    def read(self, html: str, images: list[bytes] | None = None) -> Word:
        document = Word()
        reader = _CardReader(document, images or [])
        reader.feed(html)
        reader.close()
        return document

    def test_styles_follow_card_classes(self) -> None:
        """Вид карточки, название, пояснение, крупное число и источник — своими стилями."""
        document = self.read(
            '<div class="study-card__body"><header><p class="kicker study-card__kicker">'
            '<svg><use href="#i"></use></svg>Карта · 2022</p>'
            '<h2 class="study-card__title">Выбросы</h2>'
            '<p class="study-card__note">Без Москвы</p></header>'
            '<p class="answer__value"><span class="numeric">+106,0</span> за пять лет</p>'
            '<footer><p class="study-card__source">Данные: Росстат</p>'
            '<a class="study-card__open no-print" href="/x">Открыть вид</a></footer></div>'
        )
        assert paragraphs(document) == [
            ("Kicker", "Карта · 2022"),
            ("Heading2", "Выбросы"),
            ("Note", "Без Москвы"),
            ("Lead", "+106,0 за пять лет"),
            ("Source", "Данные: Росстат"),
        ]

    def test_lists_tables_and_statements(self) -> None:
        """Нумерованный список — номерами, строка сведений — утверждениями, таблица — целиком."""
        document = self.read(
            '<ol class="answer-list"><li><span>Тверская</span> <span>+3</span></li>'
            "<li><span>Тульская</span> <span>−2</span></li></ol>"
            '<p class="answer__meta"><span>регионов: <span class="numeric">85</span></span>'
            "<span>коэффициент Спирмена</span></p>"
            '<table class="data-table"><caption class="visually-hidden">скрыто</caption>'
            '<thead><tr><th>Субъект</th><th class="is-numeric">%</th></tr></thead>'
            '<tbody><tr class="is-total"><th scope="row">Россия</th>'
            '<td class="is-numeric">77,3</td></tr>'
            '<tr><th scope="row">Тверская</th><td class="is-numeric">60,1</td></tr></tbody></table>'
        )
        assert paragraphs(document) == [
            ("ListText", "\t1.Тверская +3"),
            ("ListText", "\t2.Тульская −2"),
            ("Meta", "регионов: 85"),
            ("Meta", "коэффициент Спирмена"),
        ]
        xml = ElementTree.fromstring(parts(document)["word/document.xml"])
        rows = [
            [
                "".join(node.text or "" for node in cell.iter(f"{W}t"))
                for cell in row.findall(f"{W}tc")
            ]
            for row in xml.iter(f"{W}tr")
        ]
        assert rows == [["Субъект", "%"], ["Россия", "77,3"], ["Тверская", "60,1"]]
        assert "скрыто" not in parts(document)["word/document.xml"].decode()

    def test_picture_takes_the_place_of_the_chart(self) -> None:
        """Место картинки — картинка; без неё карта пропускается, малые графики — текстом."""
        chart = '<rl-chart data-report-image data-image-title="Линии"></rl-chart>'
        with_image = self.read(chart, [png(10, 10)])
        assert "word/media/image1.png" in parts(with_image)
        assert "Карта" not in str(
            paragraphs(self.read("<rl-choropleth data-report-image><p>Карта</p></rl-choropleth>"))
        )
        multiples = self.read(
            '<div class="multiples" data-report-image><ul><li>Тверская 3,1</li></ul></div>'
        )
        assert paragraphs(multiples) == [("ListText", "\t•Тверская 3,1")]

    def test_line_break_is_kept_and_source_newlines_are_not(self) -> None:
        """<br> — разрыв строки, переводы строк разметки — пробелы."""
        document = self.read("<p>первая\n   строка<br>вторая</p>")
        xml = parts(document)["word/document.xml"].decode()
        assert "первая строка" in xml
        assert "<w:br/>" in xml
