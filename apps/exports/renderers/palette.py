"""Цвета документов PDF и XLSX — светлая ветвь маркеров ``static/css/tokens.css``."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from django.conf import settings

# Значение переменной: цвет или первая (светлая) ветвь light-dark().
_DECLARATION = re.compile(r"(--[\w-]+):\s*(?:light-dark\(\s*)?#([0-9a-fA-F]{6})\b")


@dataclass(frozen=True, slots=True)
class DocumentColours:
    """Цвета таблиц и подписей документа: шесть шестнадцатеричных знаков без «#»."""

    text: str
    muted: str
    header: str
    header_text: str
    header_rule: str
    rule: str
    paper: str
    stripe: str


@cache
def document_colours() -> DocumentColours:
    """
    Прочитать цвета документа из блока ``:root`` файла маркеров.

    Документ печатают и открывают вне сайта, поэтому тема всегда светлая. Шапка таблицы
    повторяет шапку таблиц сайта (``.data-table thead th``).
    """
    text = (Path(settings.BASE_DIR) / "static" / "css" / "tokens.css").read_text(encoding="utf-8")
    start = text.index(":root {")
    values = dict(_DECLARATION.findall(text[start : text.index("\n}", start)]))

    def token(name: str) -> str:
        return values[name].upper()

    return DocumentColours(
        text=token("--text-primary"),
        muted=token("--text-muted"),
        header=token("--surface-sunken"),
        header_text=token("--text-secondary"),
        header_rule=token("--border-strong"),
        rule=token("--border-subtle"),
        paper=token("--surface-raised"),
        stripe=token("--surface-base"),
    )
