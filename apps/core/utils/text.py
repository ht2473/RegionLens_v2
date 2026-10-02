"""Нормализация текста из источника и построение слагов."""

from __future__ import annotations

import re
import unicodedata

from slugify import slugify as _slugify

# Символы, которые в исходных данных используются как замена обычным.
_DASH_VARIANTS = {
    "‐": "-",  # дефис
    "‑": "-",  # неразрывный дефис
    "‒": "-",  # цифровое тире
    "–": "-",  # короткое тире
    "—": "—",  # длинное тире сохраняем: оно значимо в названиях («Северная Осетия — Алания»)
    "−": "-",  # знак минус
}

_SPACE_VARIANTS = {
    " ": " ",  # неразрывный пробел
    " ": " ",  # пробел цифровой ширины
    " ": " ",  # узкий неразрывный пробел
    " ": " ",  # тонкий пробел
    "\t": " ",
}

_MULTIPLE_SPACES = re.compile(r"\s{2,}")


def normalize_text(value: object) -> str:
    """
    Привести строку к каноническому виду: Юникод, пробелы, дефисы, края.

    Нестроковое значение (``NaN`` из pandas) считается пустым.
    """
    if not isinstance(value, str) or not value:
        return ""

    text = unicodedata.normalize("NFKC", value)
    for source, replacement in {**_SPACE_VARIANTS, **_DASH_VARIANTS}.items():
        text = text.replace(source, replacement)
    text = _MULTIPLE_SPACES.sub(" ", text)
    return text.strip()


def make_slug(value: object, max_length: int = 80) -> str:
    """Построить слаг из русского названия транслитерацией."""
    return _slugify(normalize_text(value), max_length=max_length, word_boundary=True)


def truncate(value: object, limit: int = 120, suffix: str = "…") -> str:
    """Сократить строку до указанной длины по границе слова."""
    text = normalize_text(value)
    if len(text) <= limit:
        return text
    cut = text[: limit - len(suffix)].rsplit(" ", 1)[0]
    return f"{cut}{suffix}"
