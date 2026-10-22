"""
Отчёт исследования. Word — карточки в том виде, в каком их печатают (вид, название, числа,
списки и таблицы, пояснение, источник), картинки графиков и карт со страницы, заметки,
пояснения расчётов из каталога и перечень источников. Excel — данные всех карточек: лист
на карточку, субъекты по строкам, годы по столбцам.

Карточку собирает та же разметка, что и страницу (``study_cards`` и шаблоны карточек):
отчёт не расходится с полем. Из разметки берётся то, что видно при печати, — без меню,
подсказок «Дальше» и скрытых подписей. Картинки рисует страница (графики — в браузере),
сервер только вкладывает их; без них в отчёте остаются числа и таблицы.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

from django.http import Http404, HttpRequest
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _

from apps.exports.renderers import xlsx
from apps.exports.renderers.docx import Cell, Word
from apps.exports.reports.base import COLUMN_NUMBER, Column, ReportDocument, Section, Table
from apps.warehouse import routing
from apps.warehouse.duckdb_client import WarehouseNotBuiltError

from . import studies, study_cards
from .models import Study

# Картинок на карточку не больше этого: у карточки один график или одна карта.
IMAGES_PER_CARD = 2
# Знаков в названии листа Excel — предел самой программы.
SHEET_TITLE = 31


def included(study: Study) -> list[dict[str, Any]]:
    """Блоки, входящие в отчёт: всё, кроме карточек, отмеченных «не в отчёт»."""
    return [block for block in studies.blocks_of(study) if block.get("report", True)]


def _cards(request: HttpRequest, study: Study) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Блоки отчёта с собранными карточками; у заметки карточки нет — пустой словарь."""
    found: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for block in included(study):
        if block["kind"] == studies.TEXT:
            found.append((block, {}))
            continue
        year, territories = studies.card_common(block, study.year, list(study.territories or []))
        found.append(
            (block, study_cards.render(request, block, year=year, territories=territories))
        )
    return found


# --- Word ------------------------------------------------------------------------------------


def word(request: HttpRequest, study: Study, images: dict[str, list[bytes]]) -> bytes:
    """
    Документ Word по исследованию; ``images`` — картинки карточек со страницы по порядку
    их мест в разметке карточки (``data-report-image``).
    """
    document = Word()
    document.heading(study.title, 0)
    if study.description:
        document.paragraph(study.description)
    document.paragraph(_lead(study), "Meta")
    sources: list[str] = []
    keys: list[str] = []
    for block, card in _cards(request, study):
        if block["kind"] == studies.TEXT:
            document.paragraph(block.get("text", ""))
            continue
        html = render_to_string(
            "userdata/cards/_card.html",
            {**card, "card": card, "study": study, "owner": False},
            request=request,
        )
        reader = _CardReader(document, images.get(block["id"], []))
        reader.feed(html)
        reader.close()
        if card.get("source") and card["source"] not in sources:
            sources.append(card["source"])
        keys.extend(key for key in studies.series_keys(block) if key not in keys)
    methods = _methods(keys)
    if methods:
        document.heading(_("Как посчитаны показатели"), 1)
        for title, text in methods:
            document.paragraph(title, "Heading3")
            document.paragraph(text)
    if sources:
        document.heading(_("Источники"), 1)
        for number, source in enumerate(sources, start=1):
            document.item(source, f"{number}.")
    return document.render(study.title)


def _lead(study: Study) -> str:
    """Строка под названием: что это и когда собрано, общий год исследования."""
    moment = date_format(timezone.localdate(), "DATE_FORMAT")
    parts = [_("Исследование RegionLens, %(date)s") % {"date": moment}]
    if study.year:
        parts.append(_("общий год — %(year)s") % {"year": study.year})
    return ", ".join(parts)


def _methods(keys: list[str]) -> list[tuple[str, str]]:
    """
    Как посчитаны свои ряды карточек: пересчёт сборки или расчёт каталога — его метод,
    своя формула — она сама с названиями рядов.
    """
    from . import formula_store, formulas, operations
    from .series import user_series_many

    found = user_series_many([key for key in keys if routing.is_user_key(key)])
    recounts = {item.recount: item for item in operations.OPERATIONS if item.recount}
    methods: list[tuple[str, str]] = []
    definitions: dict[int, dict[str, Any]] = {}
    for key in keys:
        series = found.get(key)
        if series is None:
            continue
        record = series.record
        if record.derived in recounts:
            methods.append((series.full_title, str(recounts[record.derived].method)))
            continue
        version = record.version
        if version.pk not in definitions:
            definitions[version.pk] = {
                item.code: item for item in formula_store.definitions(version)
            }
        definition = definitions[version.pk].get(record.code)
        if definition is None:
            continue
        operation = operations.BY_CODE.get(definition.operation)
        titles = {
            **definition.labels,
            **formula_store.titles(series.dataset, definition.keys),
        }
        expression = formulas.display(definition.expression, titles)
        text = f"{operation.method} {_('Формула')}: {expression}" if operation else expression
        methods.append((series.full_title, text))
    return methods


@dataclass(slots=True)
class _Open:
    """Открытый элемент разметки: имя, стиль абзаца, пропускается ли содержимое."""

    tag: str
    style: str = ""
    skip: bool = False
    block: bool = False


# Элементы-блоки: их конец завершает абзац.
_BLOCKS = frozenset(
    {"p", "div", "section", "header", "footer", "h1", "h2", "h3", "h4", "li", "summary", "ul", "ol"}
)
# Чего нет на печати и в документе.
_SKIP_TAGS = frozenset(
    {
        "script",
        "style",
        "template",
        "form",
        "button",
        "input",
        "select",
        "textarea",
        "svg",
        "caption",
    }
)
_SKIP_CLASSES = frozenset({"no-print", "visually-hidden", "chart-save-group"})
# Стиль абзаца по классу элемента; первый подходящий.
_STYLE_CLASSES = (
    ("study-card__title", "Heading2"),
    ("study-card__kicker", "Kicker"),
    ("study-card__note", "Note"),
    ("study-card__source", "Source"),
    ("answer__value", "Lead"),
    ("answer__label", "Heading3"),
    ("multiples-group__title", "Heading3"),
    ("answer__meta", "Meta"),
    ("card-head__note", "Meta"),
    ("study-card__hint", "Meta"),
)
_VOID = frozenset({"br", "img", "input", "meta", "link", "hr", "wbr", "source", "col"})
# Разрыв строки от <br>: переводы строк самой разметки — пробелы.
_BREAK = ""


@dataclass(slots=True)
class _TableState:
    rows: list[list[Cell]] = field(default_factory=list)
    head: bool = False
    total: bool = False
    cell: Cell | None = None


class _CardReader(HTMLParser):
    """Разметка карточки → абзацы, пункты, таблицы и картинки документа Word."""

    def __init__(self, document: Word, images: list[bytes]) -> None:
        super().__init__(convert_charrefs=True)
        self.document = document
        self.images = list(images[:IMAGES_PER_CARD])
        self.stack: list[_Open] = []
        self.text: list[str] = []
        self.style = ""
        self.table: _TableState | None = None
        self.lists: list[int | None] = []

    # Пропуск: внутри пропускаемого элемента всё пропускается.
    def _skipping(self) -> bool:
        return any(item.skip for item in self.stack)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name: value or "" for name, value in attrs}
        classes = set(attributes.get("class", "").split())
        if tag in _VOID:
            if tag == "br" and not self._skipping():
                self._write(_BREAK)
            return
        if self._skipping():
            self.stack.append(_Open(tag, skip=True))
            return
        if "data-report-image" in attributes:
            self._flush()
            if self.images:
                self._picture(attributes)
                self.stack.append(_Open(tag, skip=True))
                return
            # Без картинки карта бессмысленна, малые графики — названия и числа.
            self.stack.append(_Open(tag, skip=tag == "rl-choropleth"))
            return
        if tag in _SKIP_TAGS or classes & _SKIP_CLASSES:
            self.stack.append(_Open(tag, skip=True))
            return
        if self._table_tag(tag, classes):
            self.stack.append(_Open(tag))
            return
        style = next((code for name, code in _STYLE_CLASSES if name in classes), "")
        if tag in {"h3", "h4", "summary"} and not style:
            style = "Heading3"
        # Утверждения строки сведений — отдельными строками: на странице их делит оформление.
        statement = tag == "span" and bool(self.stack) and self.stack[-1].style == "Meta"
        block = tag in _BLOCKS or statement
        if block:
            self._flush()
            if tag == "ul":
                self.lists.append(None)
            elif tag == "ol":
                self.lists.append(0)
            if style:
                self.style = style
        self.stack.append(_Open(tag, style=style, block=block))

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID:
            return
        # Незакрытые элементы внутри закрываются вместе с ним.
        while self.stack:
            item = self.stack.pop()
            if not item.skip:
                self._close(item)
            if item.tag == tag:
                break

    def handle_data(self, data: str) -> None:
        if self._skipping():
            return
        self._write(data)

    def close(self) -> None:
        super().close()
        self._flush()

    # --- Внутреннее ---

    def _write(self, text: str) -> None:
        if self.table is not None:
            if self.table.cell is not None:
                self.table.cell.text += text
            return
        self.text.append(text)

    def _close(self, item: _Open) -> None:
        if item.tag in {"td", "th"} and self.table is not None and self.table.cell is not None:
            lines = self.table.cell.text.split(_BREAK)
            self.table.cell.text = "\n".join(" ".join(line.split()) for line in lines).strip()
            self.table.rows[-1].append(self.table.cell)
            self.table.cell = None
        elif item.tag == "thead" and self.table is not None:
            self.table.head = False
        elif item.tag == "table" and self.table is not None:
            self.document.table(self.table.rows)
            self.table = None
        if not item.block:
            return
        self._flush(listed=item.tag == "li")
        if item.tag in {"ul", "ol"} and self.lists:
            self.lists.pop()
        # Стиль возвращается к ближайшему открытому со своим стилем.
        self.style = next((open_.style for open_ in reversed(self.stack) if open_.style), "")

    def _table_tag(self, tag: str, classes: set[str]) -> bool:
        """Элементы таблицы: строки и клетки собираются и выводятся целиком в конце."""
        if tag == "table":
            self._flush()
            self.table = _TableState()
            return True
        if self.table is None:
            return False
        if tag == "thead":
            self.table.head = True
        elif tag == "tr":
            self.table.rows.append([])
            self.table.total = "is-total" in classes
        elif tag in {"td", "th"}:
            self.table.cell = Cell(
                "",
                numeric=bool(classes & {"is-numeric", "numeric"}),
                header=self.table.head,
                bold=self.table.total,
            )
        return tag in {"thead", "tbody", "tfoot", "tr", "td", "th"}

    def _flush(self, *, listed: bool = False) -> None:
        """Записать накопленный текст абзацем; внутри пункта списка — пунктом."""
        lines = ("".join(self.text)).split(_BREAK)
        text = "\n".join(" ".join(line.split()) for line in lines).strip()
        self.text = []
        if not text:
            return
        if self.lists and (listed or any(item.tag == "li" for item in self.stack)):
            marker = "•"
            if self.lists[-1] is not None:
                self.lists[-1] += 1
                marker = f"{self.lists[-1]}."
            self.document.item(text, marker)
            return
        self.document.paragraph(text, self.style)

    def _picture(self, attributes: dict[str, str]) -> None:
        from apps.exports.renderers.docx import WordError

        data = self.images.pop(0)
        title = attributes.get("data-image-title", "")
        try:
            self.document.image(data, title)
        except WordError:
            return


# --- Excel -----------------------------------------------------------------------------------


def excel(request: HttpRequest, study: Study) -> bytes:
    """Книга Excel: лист реквизитов и лист данных на каждую карточку с рядами."""
    from apps.analytics.selectors import region_rows

    from . import operations

    regions = region_rows()
    tables: list[Table] = []
    sources: list[str] = []
    for block, card in _cards(request, study):
        keys = studies.series_keys(block)
        if block["kind"] == studies.TEXT or not keys:
            continue
        if card.get("source") and card["source"] not in sources:
            sources.append(card["source"])
        table = _card_table(keys, regions, operations.info_of)
        if table is not None:
            # Лист — номер и вид карточки: названия карточек одного ряда совпадают.
            kind = str(card.get("target_title", ""))
            table.title = f"{len(tables) + 1}. {kind}"[:SHEET_TITLE]
            table.note = card.get("title") or kind
            tables.append(table)
    report = ReportDocument(
        title=study.title,
        subtitle=_lead(study),
        meta=[
            (_("Карточек с данными"), str(len(tables))),
            *((_("Источник"), source) for source in sources),
        ],
        sections=[Section(tables=tables)],
        footer=_("Значения — как на складе в момент выгрузки; пустая клетка — значения нет."),
    )
    return xlsx.render(report)


def _card_table(keys: list[str], regions: list[dict[str, Any]], info_of: Any) -> Table | None:
    """Данные карточки: субъект и показатель по строкам, годы по столбцам."""
    from apps.warehouse.queries import region_panel

    panels: dict[str, dict[int, dict[str, float]]] = {}
    for key in keys:
        try:
            panels[key] = region_panel(key)
        except Http404, WarehouseNotBuiltError:
            continue
    years = sorted({year for panel in panels.values() for year in panel})
    if not years:
        return None
    columns = [
        Column("region", _("Субъект"), width=34),
        Column("code", _("Код"), width=10),
        Column("series", _("Показатель"), width=40),
        Column("unit", _("Единица"), width=16),
        *(Column(str(year), str(year), COLUMN_NUMBER, width=12) for year in years),
    ]
    rows: list[dict[str, Any]] = []
    for key, panel in panels.items():
        info = info_of(key)
        for region in regions:
            values = {
                str(year): panel[year][region["code"]]
                for year in years
                if region["code"] in panel.get(year, {})
            }
            if not values:
                continue
            rows.append(
                {
                    "region": region["name"],
                    "code": region["code"],
                    "series": info.title if info else key,
                    "unit": info.unit if info else "",
                    **values,
                }
            )
    return Table(columns=columns, rows=rows)
