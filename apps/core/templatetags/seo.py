"""Тег машиночитаемого описания страницы (JSON-LD)."""

from __future__ import annotations

import json
from typing import Any

from django import template
from django.utils.safestring import mark_safe

register = template.Library()

# Знаки, которыми можно выйти из блока данных; в JSON их запись \uXXXX значит то же.
# Два последних — разделители строк U+2028 и U+2029.
ESCAPES = {
    "<": "\\u003C",
    ">": "\\u003E",
    "&": "\\u0026",
    "\u2028": "\\u2028",
    "\u2029": "\\u2029",
}


@register.simple_tag
def json_ld(data: dict[str, Any] | None) -> str:
    """
    Вывести описание страницы блоком ``application/ld+json``.

    Блок этого типа не исполняется, и политика безопасности содержимого его пропускает.
    """
    if not data:
        return ""

    encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    for char, replacement in ESCAPES.items():
        encoded = encoded.replace(char, replacement)
    return mark_safe(  # noqa: S308 - содержимое собрано на сервере и экранировано выше
        f'<script type="application/ld+json">{encoded}</script>'
    )
