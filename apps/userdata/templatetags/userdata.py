"""Фильтры шаблонов своих данных."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from django import template
from django.utils.translation import get_language

register = template.Library()

_CYRILLIC = re.compile(r"[А-Яа-яЁё]")


@register.filter
def source_lang(text: Any) -> str:
    """«ru», если текст таблицы пользователя по-русски, а страница — нет; иначе пусто."""
    if get_language() != "ru" and _CYRILLIC.search(str(text or "")):
        return "ru"
    return ""


@register.filter
def is_number(text: Any) -> bool:
    """Клетка образца — число: такие выравниваются по правому краю."""
    from apps.userdata import cells

    return cells.parse(str(text or "")).status == cells.VALUE


@register.filter
def column_letters(table: Any) -> list[str]:
    """Буквы столбцов образца, как в электронной таблице: A … Z, AA …"""
    # Образец — у описания таблицы при приёме или в отчёте версии (словарь).
    sample = table.get("sample") if isinstance(table, Mapping) else getattr(table, "sample", None)
    width = max((len(row) for row in sample or []), default=0)
    letters = []
    for index in range(width):
        name = ""
        number = index + 1
        while number:
            number, rest = divmod(number - 1, 26)
            name = chr(ord("A") + rest) + name
        letters.append(name)
    return letters


@register.filter
def rows_text(table: Any) -> str:
    """Число строк таблицы: точное или «около»."""
    from apps.core.templatetags.formatting import ru_number

    rows = getattr(table, "rows", None)
    if rows is None:
        return ""
    if getattr(table, "rows_exact", False):
        return ru_number(rows, 0)
    rounded = round(rows, -3) if rows >= 10_000 else round(rows, -2)  # noqa: PLR2004
    return "≈" + ru_number(max(rounded, 1), 0)


@register.filter
def territory_name(code: Any) -> str:
    """Название территории справочника по коду на языке страницы."""
    from apps.userdata import matching

    reference = matching._reference()
    field = "name_en" if get_language() == "en" else "name_ru"
    records = [
        reference["country"],
        *reference["federal_districts"],
        *reference["regions"],
        *reference["aggregates"],
    ]
    return next((record[field] for record in records if record["code"] == code), str(code))
