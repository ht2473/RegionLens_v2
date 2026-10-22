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


# Разряды единицы — ступени по тысяче; масштаб показателя сдвигает разряд.
UNIT_ORDERS = ("", "тыс.", "млн", "млрд", "трлн")
_UNIT_ORDER = re.compile(
    r"^\s*(тысяч\w*|тыс\.?|миллион\w*|млн\.?|миллиард\w*|млрд\.?|триллион\w*|трлн\.?)"
    r"(?:\s+(.*)|\s*$)",
    re.IGNORECASE,
)
_ORDER_WORDS = (("тыс", 1), ("млн", 2), ("миллион", 2), ("млрд", 3), ("миллиард", 3))


def scaled_unit(unit: str, exponent: int) -> str:
    """
    Единица после умножения значений на 1000 в степени ``exponent``: «тыс. руб.» при −1 —
    «млн руб.», «человек» при −1 — «тыс. человек». За пределами разрядов — с множителем.
    """
    if not exponent:
        return unit
    text = " ".join((unit or "").split())
    match = _UNIT_ORDER.match(text)
    order, base = 0, text
    if match:
        word = match[1].casefold()
        order = next((value for prefix, value in _ORDER_WORDS if word.startswith(prefix)), 4)
        base = (match[2] or "").strip()
    wanted = order - exponent
    if 0 <= wanted < len(UNIT_ORDERS):
        return " ".join(part for part in (UNIT_ORDERS[wanted], base) if part)
    # За разрядами — с множителем: 5 руб. при e = 1 — 5000 «руб. × 0,001»; выше триллиона —
    # «трлн руб. × 1 000».
    if wanted < 0:
        factor = f"{1000.0**wanted:.9f}".rstrip("0").rstrip(".").replace(".", ",")
        return f"{base} × {factor}".strip()
    top = f"{1000 ** (wanted - len(UNIT_ORDERS) + 1):,}".replace(",", " ")
    return " ".join(part for part in (UNIT_ORDERS[-1], base, "×", top) if part)


def series_code(indicator: str, slices: Sequence[Sequence[str]], period: str) -> str:
    """Код ряда — отпечаток «показатель + значения разрезов + период года»."""
    parts = [_normalized(indicator)]
    parts += [f"{_normalized(header)}={_normalized(value)}" for header, value in slices]
    parts.append(period)
    digest = hashlib.sha1("|".join(parts).encode("utf-8"), usedforsecurity=False).hexdigest()
    return digest[:12]


# Хвост названия после последней запятой, похожий на единицу: «…, млрд руб.»,
# «…, % городского населения», «…, месяцев». Не длиннее полусотни знаков и без запятых.
_UNIT_TAIL = re.compile(
    r",\s*(?P<unit>(?:%|процент|(?:тыс|тысяч|млн|млрд|трлн)\.?\s|руб|долл|евро|чел\.|человек|"
    r"шт\.|штук|единиц|месяц|лет\b|дн(?:ей|я)\b|кг\b|т\b|тонн|га\b|км|м2|м3|м²|м³|кв\.\s?м|"
    r"куб\.\s?м)[^,;]{0,50})$",
    re.IGNORECASE,
)
# Год в названии: значения по годам и так в таблице. «за 2020-2021 годы», «на 1 января
# 2007 года», «в 2022 г.».
_YEARS = re.compile(
    r"\s*,?\s*(?:за|на|в)\s+(?:\d{1,2}\s+[а-яё]+\s+)?(?:19|20)\d{2}"
    r"(?:\s*[-–—]\s*(?:19|20)\d{2})?(?:\s*(?:год\w*|гг?\.))?(?=$|[\s,;:)])",
    re.IGNORECASE,
)
# Значения и так по субъектам: «в субъекте Российской Федерации», «субъектов РФ».
_SUBJECTS = re.compile(
    r"\s*(?:\b(?:в|по)\s+)?субъект\w*\s+(?:Российской\s+Федерации|РФ)\b", re.IGNORECASE
)
# Единица из столбца, которую хвост названия уточняет: «процент» → «% городского населения».
_GENERIC_UNITS = frozenset({"", "%", "процент", "проценты", "процентов"})


def short_title(name: str, unit: str) -> tuple[str, str]:
    """
    Название показателя без того, что видно и так, и его единица.

    Единица в хвосте названия переходит в единицу, если та пуста или общая («процент»);
    годы и «субъектов Российской Федерации» снимаются. Ничего не осталось — название как есть.
    """
    title = _YEARS.sub("", _SUBJECTS.sub("", " ".join(name.split())))
    found = _UNIT_TAIL.search(title)
    if found:
        tail = found["unit"].strip()
        if unit.strip().casefold() in _GENERIC_UNITS:
            unit = tail
        if unit.casefold() == tail.casefold():
            title = title[: found.start()]
    title = title.strip(" ,;:—–-")
    return (title or name), unit
