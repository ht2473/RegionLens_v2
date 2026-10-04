"""
Защита ячеек выгрузок от формул: текст, с которого электронная таблица начала бы формулу,
получает впереди апостроф. Названия приходят и из таблиц пользователей.
"""

from __future__ import annotations

from typing import Any

# С этих знаков Excel и LibreOffice начинают формулу или команду.
FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r", "\n")


def safe_text(value: str) -> str:
    """Текст ячейки, который не станет формулой."""
    return f"'{value}" if value.startswith(FORMULA_STARTS) else value


def safe_cell(value: Any) -> Any:
    """Значение ячейки: текст — без формул, числа и пропуски — как есть."""
    return safe_text(value) if isinstance(value, str) else value
