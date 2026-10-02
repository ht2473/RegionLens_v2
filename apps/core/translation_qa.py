"""Проверка переводов справочника: кириллица, числа и термины оригинала, отпечаток оригинала."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

CYRILLIC = re.compile(r"[А-Яа-яЁё]")

# Разделитель разрядов: в русском тексте — пробел (в том числе неразрывный), в английском —
# запятая; «10 000» и «10,000» — одно число.
_SPACES = " " + chr(0xA0) + chr(0x202F)
_GROUP_RU = re.compile(rf"(?<=\d)[{_SPACES}](?=\d{{3}}(?!\d))")
_GROUP_EN = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")
_DECIMAL_RU = re.compile(r"(?<=\d),(?=\d)")
# Дата «01.01.2017» в переводе пишется словами («1 January 2017»): сверяется только год.
_DATE = re.compile(r"\b\d{1,2}\.\d{1,2}\.(\d{4})\b")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
# Не числа оригинала: степень в «м2», «км3» и единица в «на 1 работника», «1 кв. метра»,
# «1 раз» (по-английски — «per worker», «per square metre», «once»).
_UNIT_POWER = re.compile(r"(?<=[мМ])[23](?!\d)")
_PER_ONE = re.compile(r"\b(?:на|за) 1(?:-(?:го|ю|ую|му))?(?= [а-яё])|\b1(?= кв\.| раз\b)")
# «10 тыс.» по-английски — и «10 thousand», и «10,000».
_THOUSANDS = re.compile(r"(\d+) ?тыс(?:\.|яч)", re.IGNORECASE)


@dataclass(frozen=True)
class Term:
    """Термин словаря: встретился в оригинале — должен быть и в переводе."""

    label: str
    ru: re.Pattern[str]
    en: re.Pattern[str]


def _numbers(text: str) -> set[str]:
    """Числа текста без ведущих нулей."""
    found = set()
    for value in _NUMBER.findall(_DATE.sub(r"\1", text)):
        whole, _, fraction = value.partition(".")
        found.add(str(int(whole)) + (f".{fraction}" if fraction else ""))
    return found


def numbers_ru(text: str) -> set[str]:
    """Числа русского текста: разряды через пробел, дробная часть через запятую."""
    text = _PER_ONE.sub("", _UNIT_POWER.sub("", text))
    return _numbers(_DECIMAL_RU.sub(".", _GROUP_RU.sub("", text)))


def _in_thousands(text: str) -> set[str]:
    """Числа русского текста, записанные тысячами («10 тыс.»)."""
    return {str(int(value)) for value in _THOUSANDS.findall(_GROUP_RU.sub("", text))}


def numbers_en(text: str) -> set[str]:
    """Числа английского текста: разряды через запятую."""
    return _numbers(_GROUP_EN.sub("", text))


def load_terms(path: Path) -> tuple[Term, ...]:
    """Прочитать словарь терминов (``terms_en.json``)."""
    document = json.loads(path.read_text(encoding="utf-8"))
    return tuple(
        Term(
            label=item["label"],
            ru=re.compile(item["ru"], re.IGNORECASE),
            en=re.compile(item["en"], re.IGNORECASE),
        )
        for item in document["terms"]
    )


def problems(
    source: str, target: str, terms: tuple[Term, ...] = (), *, allow_cyrillic: bool = False
) -> list[str]:
    """Замечания к переводу ``target`` русского текста ``source``; пусто — перевод годен."""
    if not target.strip():
        return ["пустой перевод"]
    found = []
    if not allow_cyrillic and CYRILLIC.search(target):
        found.append("кириллица в переводе")
    in_target = numbers_en(target)
    thousands = _in_thousands(source)
    missing = {
        number
        for number in numbers_ru(source) - in_target
        if not (number in thousands and str(int(number) * 1000) in in_target)
    }
    if missing:
        found.append("нет чисел оригинала: " + ", ".join(sorted(missing)))
    found.extend(
        f"нет термина «{term.label}»"
        for term in terms
        if term.ru.search(source) and not term.en.search(target)
    )
    return found


def source_hash(*parts: str) -> str:
    """Отпечаток русского оригинала: изменился оригинал — перевод устарел."""
    return hashlib.sha1("\x1f".join(parts).encode("utf-8"), usedforsecurity=False).hexdigest()
