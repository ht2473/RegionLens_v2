"""
Периоды в подписях таблиц: год, месяц, квартал, полугодие, «с начала года», скользящее
окно, «на 1 января». Общие правила сбора выпусков и своих данных.

Подпись без года («январь», «I квартал») — период шапки, год к нему даёт строка выше.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

# Виды периодов.
YEAR = "year"
MONTH = "month"
QUARTER = "quarter"
HALF = "half"
YTD = "ytd"  # с начала года по месяц включительно
WINDOW = "window"  # скользящие месяцы, оканчивающиеся месяцем
POINT = "point"  # на начало месяца: «на 1 января»

MONTH_STEMS = (
    ("январ", 1),
    ("янв", 1),
    ("феврал", 2),
    ("фев", 2),
    ("март", 3),
    ("мар", 3),
    ("апрел", 4),
    ("апр", 4),
    ("ма", 5),
    ("июн", 6),
    ("июл", 7),
    ("август", 8),
    ("авг", 8),
    ("сентябр", 9),
    ("сен", 9),
    ("октябр", 10),
    ("окт", 10),
    ("ноябр", 11),
    ("ноя", 11),
    ("декабр", 12),
    ("дек", 12),
    ("jan", 1),
    ("feb", 2),
    ("mar", 3),
    ("apr", 4),
    ("may", 5),
    ("jun", 6),
    ("jul", 7),
    ("aug", 8),
    ("sep", 9),
    ("oct", 10),
    ("nov", 11),
    ("dec", 12),
)
ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4}

_MONTH_WORD = (
    r"(январ[а-я]*|янв\.?|феврал[а-я]*|февр?\.?|март[а-я]*|мар\.?|апрел[а-я]*|апр\.?|ма[йя]|"
    r"июн[а-я]*|июл[а-я]*|август[а-я]*|авг\.?|сентябр[а-я]*|сент?\.?|октябр[а-я]*|окт\.?|"
    r"ноябр[а-я]*|нояб?\.?|декабр[а-я]*|дек\.?|"
    r"jan[a-z]*\.?|feb[a-z]*\.?|mar[a-z]*\.?|apr[a-z]*\.?|may|jun[a-z]*\.?|jul[a-z]*\.?|"
    r"aug[a-z]*\.?|sep[a-z]*\.?|oct[a-z]*\.?|nov[a-z]*\.?|dec[a-z]*\.?)"
)
_YEAR = r"((?:19|20)\d{2})"
_SHORT_YEAR = r"(\d{2})"
_YEAR_WORD = r"(?:\s*(?:г\.?|гг\.?|год[а-я]*|year))?"
# Номер сноски после подписи: «2016 год1», «20163)», «2024*».
_NOTE = r"(?:\s*(\d{1,2})\)?|\s*[*¹²³⁴⁵⁶⁷⁸⁹]+)?"
_FOOTNOTE_TAIL = re.compile(r"[\s*¹²³⁴⁵⁶⁷⁸⁹]+$")
_YEARS = range(1900, 2101)
_NUMBER = re.compile(r"\s*-?\d+(?:[.,]\d+)?\s*\Z")
_QUALIFIER = re.compile(r"\s*\([^()]*\)\s*$")

_YEAR_ONLY = re.compile(rf"^(?:year\s*)?{_YEAR}{_YEAR_WORD}(?:\s*(\d{{1,2}})\)?)?\*?$")
_SCHOOL_YEAR = re.compile(
    r"^((?:19|20)\d{2})\s*/\s*((?:19|20)\d{2})(?:\s*(?:уч\.?|учебн\w*)?\s*(?:г\.?|год\w*)?)?$"
)
_POINT = re.compile(rf"^на\s+1\s+{_MONTH_WORD}\s*{_YEAR}?{_YEAR_WORD}$")
_YEAR_END = re.compile(rf"^на\s+конец\s+{_YEAR}?{_YEAR_WORD}$")
_YEAR_START = re.compile(rf"^на\s+начало\s+{_YEAR}?{_YEAR_WORD}$")
_MONTH_YEAR = re.compile(rf"^{_MONTH_WORD}\s*{_YEAR}{_YEAR_WORD}$")
_MONTH_SHORT_YEAR = re.compile(rf"^{_MONTH_WORD}\s*[.\-/']?\s*{_SHORT_YEAR}$")
_YEAR_MONTH = re.compile(rf"^{_YEAR}{_YEAR_WORD}\s*{_MONTH_WORD}$")
_ISO_MONTH = re.compile(r"^((?:19|20)\d{2})[-./](\d{1,2})$")
_MONTH_DOT_YEAR = re.compile(r"^(\d{1,2})[./]((?:19|20)\d{2})$")
_DATE = re.compile(
    r"^(\d{1,2})[./](\d{1,2})[./]((?:19|20)\d{2})$|^((?:19|20)\d{2})-(\d{1,2})-(\d{1,2})$"
)
_QUARTER = re.compile(
    rf"^(?:(i{{1,3}}|iv|[1-4])\s*-?\s*(?:й\s*)?(?:квартал|кв\.?)|q([1-4]))\s*{_YEAR}?{_YEAR_WORD}$"
)
_YEAR_QUARTER = re.compile(
    rf"^{_YEAR}{_YEAR_WORD}\s*(?:q([1-4])|(i{{1,3}}|iv|[1-4])\s*(?:квартал|кв\.?))$"
)
_HALF = re.compile(rf"^(i{{1,2}}|[12])\s*-?\s*(?:е\s*)?полугодие\s*{_YEAR}?{_YEAR_WORD}$")
_NINE_MONTHS = re.compile(rf"^(\d{{1,2}})\s+месяц[а-я]*\s*{_YEAR}?{_YEAR_WORD}$")
_RANGE = re.compile(
    rf"^{_MONTH_WORD}\s*(?:{_YEAR}{_YEAR_WORD})?\s*-\s*{_MONTH_WORD}\s*{_YEAR}?{_YEAR_WORD}$"
)


@dataclass(frozen=True, slots=True)
class Period:
    """Период года: вид и номер (месяц, квартал, полугодие, последний месяц итога или окна)."""

    kind: str
    number: int = 12
    span: int = 0  # длина скользящего окна в месяцах

    @property
    def key(self) -> str:
        """Ключ периода для рецепта и кода ряда."""
        return f"{self.kind}:{self.number}" + (f":{self.span}" if self.span else "")

    @classmethod
    def from_key(cls, key: str) -> Period:
        kind, number, *span = key.split(":")
        return cls(kind, int(number), int(span[0]) if span else 0)


@dataclass(frozen=True, slots=True)
class Stamp:
    """Период с годом (год может прийти из другой строки шапки) и номера сносок подписи."""

    year: int | None
    period: Period
    notes: tuple[int, ...] = ()


ANNUAL = Period(YEAR)


def clean(label: str) -> str:
    """Подпись для разбора: строчные, тире — дефис, без лишних пробелов и знаков сносок."""
    text = re.sub(r"\s+", " ", label.replace("\xa0", " ")).strip().lower()
    text = re.sub(r"[–—−]", "-", text)
    text = re.sub(r"\s*-\s*", "-", text)
    text = re.sub(r"(?<=[а-яa-z.])-(?=[а-яa-z])", " - ", text)
    # «за октябрь - декабрь 2024 г.», «в среднем за 2024 год».
    text = re.sub(r"^(в среднем )?за\s+", "", text)
    return _FOOTNOTE_TAIL.sub("", text).strip()


def month_number(word: str) -> int | None:
    """Номер месяца по слову или сокращению: «июля», «июл.», «Jul»."""
    stem = word.lower().rstrip(".")
    if stem in {"май", "мая", "мае"}:
        return 5
    for prefix, number in MONTH_STEMS:
        if stem.startswith(prefix) and not (prefix == "ма" and stem.startswith("мар")):
            return number
    return None


def stamp(value: Any) -> Stamp | None:  # noqa: PLR0911 — по ветви на запись периода
    """Период с годом или без него из клетки или подписи; ``None`` — это не период."""
    if isinstance(value, datetime | date):
        return _from_date(value.day, value.month, value.year)
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        number = float(value)
        if number.is_integer() and 1900 <= number <= 2100:  # noqa: PLR2004
            return Stamp(int(number), ANNUAL)
        return None
    raw = str(value)
    if _NUMBER.match(raw) and not _MONTH_DOT_YEAR.match(raw.strip()):
        # Число — период, только если это год.
        number = float(raw.replace(",", "."))
        return Stamp(int(number), ANNUAL) if number.is_integer() and int(number) in _YEARS else None
    text = clean(raw.replace("year", "year "))
    if not text:
        return None
    year_only = _YEAR_ONLY.match(text)
    if year_only:
        notes = (int(year_only.group(2)),) if year_only.group(2) else ()
        return Stamp(int(year_only.group(1)), ANNUAL, notes)
    # Учебный год «2022/2023» — по году его начала.
    school = _SCHOOL_YEAR.match(text)
    if school and int(school.group(2)) == int(school.group(1)) + 1:
        return Stamp(int(school.group(1)), ANNUAL)
    found = _with_year(text)
    if found is not None:
        return found
    period = period_of(text)
    if period is not None:
        return Stamp(None, period)
    # «май 2026 г. - июль 2026 г. (в среднем за период)»: пояснение в скобках — не период.
    bare = _QUALIFIER.sub("", text)
    return stamp(bare) if bare != text and bare else None


def period_of(label: str) -> Period | None:
    """Период без года: «январь», «I квартал», «январь-июль», «год»; иначе ``None``."""
    text = clean(label)
    if text in {"год", "за год", "в целом за год", "январь-декабрь", "year"}:
        return ANNUAL
    words = text.split()
    if (
        len(words) == 1
        and month_number(words[0]) is not None
        and re.fullmatch(_MONTH_WORD, words[0])
    ):
        return Period(MONTH, month_number(words[0]) or 0)
    found = _with_year(text)
    if found is not None and found.year is None:
        return found.period
    return None


def _with_year(text: str) -> Stamp | None:  # noqa: PLR0911, PLR0912 — по ветви на запись периода
    """Периоды, которые могут нести год в той же подписи."""
    point = _POINT.match(text)
    if point:
        return Stamp(_int(point.group(2)), Period(POINT, month_number(point.group(1)) or 1))
    for pattern, at_end in ((_YEAR_START, False), (_YEAR_END, True)):
        found = pattern.match(text)
        if found:
            year = _int(found.group(1))
            # «На конец 2023 года» — то же, что «на 1 января 2024».
            if at_end:
                return Stamp(year + 1 if year else None, Period(POINT, 1))
            return Stamp(year, Period(POINT, 1))
    date_match = _DATE.match(text)
    if date_match:
        groups = date_match.groups()
        if groups[0]:
            return _from_date(int(groups[0]), int(groups[1]), int(groups[2]))
        return _from_date(int(groups[5]), int(groups[4]), int(groups[3]))
    for pattern, order in ((_MONTH_YEAR, "my"), (_YEAR_MONTH, "ym")):
        found = pattern.match(text)
        if found:
            month_word, year_text = (
                (found.group(1), found.group(2))
                if order == "my"
                else (found.group(2), found.group(1))
            )
            month = month_number(month_word)
            if month:
                return Stamp(int(year_text), Period(MONTH, month))
    short = _MONTH_SHORT_YEAR.match(text)
    if short and month_number(short.group(1)):
        return Stamp(2000 + int(short.group(2)), Period(MONTH, month_number(short.group(1)) or 0))
    for pattern, year_index, month_index in ((_ISO_MONTH, 1, 2), (_MONTH_DOT_YEAR, 2, 1)):
        found = pattern.match(text)
        if found and 1 <= int(found.group(month_index)) <= 12:  # noqa: PLR2004
            return Stamp(int(found.group(year_index)), Period(MONTH, int(found.group(month_index))))
    quarter = _QUARTER.match(text)
    if quarter:
        number = _quarter_number(quarter.group(1) or quarter.group(2))
        return Stamp(_int(quarter.group(3)), Period(QUARTER, number))
    year_quarter = _YEAR_QUARTER.match(text)
    if year_quarter:
        number = _quarter_number(year_quarter.group(2) or year_quarter.group(3))
        return Stamp(int(year_quarter.group(1)), Period(QUARTER, number))
    half = _HALF.match(text)
    if half:
        number = ROMAN.get(half.group(1)) or int(half.group(1))
        period = Period(YTD, 6) if number == 1 else Period(HALF, 2)
        return Stamp(_int(half.group(2)), period)
    months = _NINE_MONTHS.match(text)
    if months and 1 <= int(months.group(1)) <= 12:  # noqa: PLR2004
        count = int(months.group(1))
        return Stamp(_int(months.group(2)), ANNUAL if count == 12 else Period(YTD, count))  # noqa: PLR2004
    return _range(text)


def _range(text: str) -> Stamp | None:
    """«Январь-июль 2026» — с начала года; «май 2026 г. - июль 2026 г.» — скользящее окно."""
    found = _RANGE.match(text)
    if not found:
        return None
    first, first_year, last, last_year = found.groups()
    start, end = month_number(first), month_number(last)
    if not start or not end:
        return None
    year = _int(last_year) or _int(first_year)
    span = (end - start) % 12 + 1
    if start == 1 and end >= start and not (first_year and last_year and first_year != last_year):
        return Stamp(year, ANNUAL if end == 12 else Period(YTD, end))  # noqa: PLR2004
    if span == 3 and end % 3 == 0 and end > start:  # noqa: PLR2004
        return Stamp(year, Period(QUARTER, end // 3))
    return Stamp(year, Period(WINDOW, end, span))


def _from_date(day: int, month: int, year: int) -> Stamp | None:
    if not 1 <= month <= 12:  # noqa: PLR2004
        return None
    if day == 1:
        return Stamp(year, Period(POINT, month))
    if day >= 28 and month == 12:  # noqa: PLR2004
        return Stamp(year + 1, Period(POINT, 1))
    return Stamp(year, Period(MONTH, month))


def _quarter_number(raw: str) -> int:
    return ROMAN.get(raw) or int(raw)


def _int(raw: str | None) -> int | None:
    return int(raw) if raw else None
