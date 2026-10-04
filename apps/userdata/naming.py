"""
Подписи рядов своих данных: период года словами, разрезы и полное название ряда.

Подписи из таблицы показываются как есть; на английской странице кириллица помечается
``lang="ru"`` в шаблонах.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence

from django.utils.translation import gettext

from apps.sources import periods

ROMAN = ("I", "II", "III", "IV")


def _month(number: int) -> str:
    """Месяц в именительном падеже."""
    names = (
        gettext("январь"),
        gettext("февраль"),
        gettext("март"),
        gettext("апрель"),
        gettext("май"),
        gettext("июнь"),
        gettext("июль"),
        gettext("август"),
        gettext("сентябрь"),
        gettext("октябрь"),
        gettext("ноябрь"),
        gettext("декабрь"),
    )
    return names[(number - 1) % 12]


def _month_of(number: int) -> str:
    """Месяц в родительном падеже: «на 1 января»."""
    names = (
        gettext("января"),
        gettext("февраля"),
        gettext("марта"),
        gettext("апреля"),
        gettext("мая"),
        gettext("июня"),
        gettext("июля"),
        gettext("августа"),
        gettext("сентября"),
        gettext("октября"),
        gettext("ноября"),
        gettext("декабря"),
    )
    return names[(number - 1) % 12]


def period_text(key: str) -> str:  # noqa: PLR0911 — по ветви на вид периода
    """Период года словами: «январь–июль», «II квартал», «на 1 января»; у года — пусто."""
    period = periods.Period.from_key(key)
    if period.kind == periods.YEAR:
        return ""
    if period.kind == periods.MONTH:
        return _month(period.number)
    if period.kind == periods.QUARTER:
        return gettext("%(roman)s квартал") % {
            "roman": ROMAN[period.number - 1],
            "number": period.number,
        }
    if period.kind == periods.HALF:
        return gettext("II полугодие")
    if period.kind == periods.YTD:
        return f"{_month(1)}–{_month(period.number)}"
    if period.kind == periods.WINDOW:
        start = (period.number - period.span) % 12 + 1
        return f"{_month(start)}–{_month(period.number)}"
    if period.kind == periods.POINT:
        return gettext("на 1 %(month)s") % {"month": _month_of(period.number)}
    return ""


def slices_text(slices: Sequence[Sequence[str]]) -> str:
    """Значения разрезов через запятую: «Мужчины, 15–19 лет»."""
    return ", ".join(str(value) for _header, value in slices if str(value).strip())


def subsection(slices: Sequence[Sequence[str]], period: str) -> str:
    """Уточнение ряда после названия показателя: разрезы и период года."""
    parts = [part for part in (slices_text(slices), period_text(period)) if part]
    return ", ".join(parts)


def full_title(title: str, slices: Sequence[Sequence[str]], period: str) -> str:
    """Полное название ряда: «Показатель — разрезы, период»."""
    detail = subsection(slices, period)
    return f"{title} — {detail}" if detail else title


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", str(text)).strip().lower()


def series_code(indicator: str, slices: Sequence[Sequence[str]], period: str) -> str:
    """Код ряда — отпечаток «показатель + значения разрезов + период года»."""
    parts = [_normalized(indicator)]
    parts += [f"{_normalized(header)}={_normalized(value)}" for header, value in slices]
    parts.append(period)
    digest = hashlib.sha1("|".join(parts).encode("utf-8"), usedforsecurity=False).hexdigest()
    return digest[:12]
