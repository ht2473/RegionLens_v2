"""Фильтры форматирования чисел, долей, изменений и годов по русской типографике."""

from __future__ import annotations

from typing import Any

from django import template
from django.utils.safestring import mark_safe
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _

register = template.Library()

# Неразрывный пробел: разделённое им число не переносится по строкам.
NBSP = " "

# Порог, начиная с которого дробная часть значения не показывается.
LARGE_VALUE_THRESHOLD = 1000

# Порог, ниже которого показываются два знака после запятой.
SMALL_VALUE_THRESHOLD = 10

# Покрытие ниже этого порога считается недостаточным и отмечается цветом внимания.
LOW_COVERAGE_THRESHOLD = 0.8


@register.filter
def ru_number(value: Any, digits: int | str | None = None) -> str:
    """Отформатировать число по-русски; без точности — по порядку величины."""
    if value is None or value == "":
        return "—"

    try:
        number = float(value)
    except TypeError, ValueError:
        return str(value)

    if digits is None or digits == "":
        magnitude = abs(number)
        precision = (
            0
            if magnitude >= LARGE_VALUE_THRESHOLD
            else (1 if magnitude >= SMALL_VALUE_THRESHOLD else 2)
        )
    else:
        precision = int(digits)

    separator = NBSP
    formatted = f"{number:,.{precision}f}".replace(",", separator)
    if get_language() != "en":
        formatted = formatted.replace(".", ",")
    return formatted


# Меньший уровень значимости пишется «< 0,001», а не «0,000».
P_LEVEL_FLOOR = 0.001


@register.filter
def p_level(value: Any, name: str = "") -> str:
    """
    Отформатировать уровень значимости: три знака, меньше тысячной — «< 0,001».

    С аргументом-обозначением возвращается запись целиком: «p = 0,217», «p < 0,001».
    """
    if value is None or value == "":
        return "—"
    try:
        number = float(value)
    except TypeError, ValueError:
        return str(value)

    if number < P_LEVEL_FLOOR:
        text = f"<{NBSP}{ru_number(P_LEVEL_FLOOR, 3)}"
        return f"{name}{NBSP}{text}" if name else text
    text = ru_number(number, 3)
    return f"{name}{NBSP}={NBSP}{text}" if name else text


@register.filter
def ru_delta(value: Any, digits: int | str | None = 1) -> str:
    """Отформатировать изменение показателя со знаком."""
    if value is None:
        return "—"
    try:
        number = float(value)
    except TypeError, ValueError:
        return str(value)

    sign = "+" if number > 0 else ""
    return f"{sign}{ru_number(number, digits)}"


@register.filter
def percent(value: Any, digits: int | str | None = 1) -> str:
    """Представить долю в процентах."""
    if value is None:
        return "—"
    try:
        number = float(value) * 100
    except TypeError, ValueError:
        return str(value)
    return f"{ru_number(number, digits)}{NBSP}%"


@register.filter
def percent_delta(value: Any, digits: int | str | None = 1) -> str:
    """Представить относительное изменение в процентах со знаком."""
    if value is None:
        return "—"
    try:
        number = float(value) * 100
    except TypeError, ValueError:
        return str(value)
    sign = "+" if number > 0 else ""
    return f"{sign}{ru_number(number, digits)}{NBSP}%"


@register.filter
def position_share(value: Any, wording: str = "above") -> str:
    """
    Записать положение субъекта в распределении словами — долей оставшихся позади.

    ``above`` — доля субъектов с меньшим значением, ``better`` — с учётом направленности.
    """
    if value is None:
        return "—"
    try:
        share = round(float(value) * 100)
    except TypeError, ValueError:
        return str(value)

    text = f"{share}{NBSP}%"
    if wording == "better":
        return str(_("лучше %(share)s субъектов") % {"share": text})
    return str(_("выше %(share)s субъектов") % {"share": text})


@register.filter
def dict_value(mapping: Any, key: Any) -> Any:
    """Получить значение словаря по ключу из переменной шаблона."""
    if mapping is None:
        return ""
    try:
        return mapping.get(key, "")
    except AttributeError, TypeError:
        return ""


@register.filter
def quality_label(quality: Any) -> str:
    """Вернуть подпись к признаку качества наблюдения."""
    labels = {
        0: "наблюдаемое значение",
        1: "нет данных в источнике",
        2: "значение скрыто источником",
        3: "неприменимо",
    }
    try:
        return labels.get(int(quality), "неизвестно")
    except TypeError, ValueError:
        return "неизвестно"


@register.filter
def coverage_bar(share: Any) -> str:
    """Построить полосу покрытия ряда готовой разметкой: в длинных списках так быстрее."""
    try:
        value = max(0.0, min(1.0, float(share)))
    except TypeError, ValueError:
        value = 0.0

    modifier = " coverage-meter__fill--low" if value < LOW_COVERAGE_THRESHOLD else ""
    return mark_safe(  # noqa: S308  # nosec - разметка собрана из числа, не из ввода
        '<span class="coverage-meter">'
        '<span class="coverage-meter__track">'
        f'<span class="coverage-meter__fill{modifier}" style="width: {value * 100:.0f}%"></span>'
        "</span>"
        f'<span class="coverage-meter__value">{value * 100:.0f}%</span>'
        "</span>"
    )


@register.filter
def year(value: Any) -> str:
    """Вывести год целым числом без разделителя разрядов; пустое значение — прочерком."""
    if value in (None, ""):
        return "—"
    try:
        return str(int(value))
    except TypeError, ValueError:
        return str(value)
