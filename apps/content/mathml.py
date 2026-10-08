"""
Формулы методики: запись MathML, пропущенная через белый список.

Формулу правит и редактор в панели, поэтому на страницу попадают только элементы и атрибуты
MathML из перечня, текст экранируется, теги закрываются по порядку. Строка поля — одна
формула: подпись и ``<math>…</math>``; строка без MathML показывается как текст.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import escape
from html.parser import HTMLParser

from django.utils.safestring import SafeString, mark_safe

# Элементы представления MathML Core.
ELEMENTS = frozenset(
    {
        "math",
        "mrow",
        "mi",
        "mn",
        "mo",
        "ms",
        "mtext",
        "mspace",
        "msub",
        "msup",
        "msubsup",
        "mfrac",
        "msqrt",
        "mroot",
        "munder",
        "mover",
        "munderover",
        "mstyle",
        "mpadded",
        "mphantom",
        "mtable",
        "mtr",
        "mtd",
        "semantics",
        "annotation",
    }
)
EMPTY = frozenset({"mspace"})
RAW_TEXT = frozenset({"script", "style"})
ATTRIBUTES = frozenset(
    {
        "display",
        "displaystyle",
        "mathvariant",
        "stretchy",
        "form",
        "fence",
        "separator",
        "lspace",
        "rspace",
        "largeop",
        "movablelimits",
        "accent",
        "accentunder",
        "linethickness",
        "scriptlevel",
        "width",
        "columnalign",
        "encoding",
    }
)
# Значения атрибутов — слова, числа и единицы длины, без кавычек и скобок.
VALUE = re.compile(r"^[A-Za-z0-9 .%/-]{0,40}$")
MATH_START = re.compile(r"<math\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class FormulaLine:
    """Одна формула раздела: подпись и разметка MathML или текст без неё."""

    label: str
    math: SafeString | None
    text: str


class _Cleaner(HTMLParser):
    """Пересборка разметки: только элементы и атрибуты перечня, текст экранирован."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.open: list[str] = []
        # Внутри сценария или стиля текст отбрасывается целиком.
        self.skipping = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in RAW_TEXT:
            self.skipping += 1
            return
        if tag not in ELEMENTS or (tag != "math" and not self.open):
            return
        if tag == "math" and self.open:
            return
        kept = "".join(
            f' {name}="{escape(value, quote=True)}"'
            for name, value in attrs
            if name in ATTRIBUTES and value is not None and VALUE.match(value)
        )
        if tag in EMPTY:
            self.parts.append(f"<{tag}{kept}></{tag}>")
            return
        self.parts.append(f"<{tag}{kept}>")
        self.open.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in EMPTY:
            self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag in RAW_TEXT:
            self.skipping = max(0, self.skipping - 1)
            return
        if tag not in self.open:
            return
        while self.open:
            closing = self.open.pop()
            self.parts.append(f"</{closing}>")
            if closing == tag:
                break

    def handle_data(self, data: str) -> None:
        if self.open and not self.skipping:
            self.parts.append(escape(data, quote=False))

    def result(self) -> str:
        self.close()
        while self.open:
            self.parts.append(f"</{self.open.pop()}>")
        return "".join(self.parts)


def clean(markup: str) -> SafeString:
    """Разметка MathML, сведённая к белому списку; всё вне ``<math>`` отбрасывается."""
    cleaner = _Cleaner()
    cleaner.feed(markup)
    return mark_safe(cleaner.result())  # noqa: S308  # nosec B308 B703 - собрано по белому списку


def formula_lines(text: str) -> list[FormulaLine]:
    """Строки поля формулы: подпись до ``<math>`` и сама формула."""
    lines = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        start = MATH_START.search(line)
        if start is None:
            lines.append(FormulaLine(label="", math=None, text=line))
            continue
        label = line[: start.start()].strip().rstrip(":").strip()
        lines.append(FormulaLine(label=label, math=clean(line[start.start() :]), text=""))
    return lines


def plain(text: str) -> str:
    """Формула без разметки — для поиска и сравнения текстов."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text or "")).strip()
