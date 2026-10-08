"""Теги и комментарии шаблонов — в одну строку: многострочные Django выводит текстом."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = sorted(
    path
    for base in (ROOT / "templates", ROOT / "apps")
    for path in base.rglob("*.html")
    if "templates" in path.parts
)
# Начало тега или комментария без конца в той же строке.
OPEN_TAG = re.compile(r"\{%(?![^\n]*%\})")
OPEN_COMMENT = re.compile(r"\{#(?![^\n]*#\})")


def test_templates_are_found() -> None:
    """Перечень шаблонов не пуст: иначе проверка ниже ничего не проверяет."""
    assert len(TEMPLATES) > 100


def test_tags_and_comments_close_on_their_line() -> None:
    """
    Тег ``{% … %}`` и комментарий ``{# … #}`` закрываются в своей строке.

    Разбор шаблона Django ищет их построчно: разбитые на две строки, они попадают
    на страницу текстом, и ни ответ сервера, ни линтер этого не замечают.
    """
    found = [
        f"{path.relative_to(ROOT)}:{number}"
        for path in TEMPLATES
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if OPEN_TAG.search(line) or OPEN_COMMENT.search(line)
    ]
    assert not found, "\n".join(found)
