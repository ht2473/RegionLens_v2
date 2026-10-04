"""Сопоставление подписей строк в таблицах источников с кодами справочника территорий."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from functools import lru_cache

from django.conf import settings

# В таблицах Росстата «Архангельская область» и «Тюменская область» без уточнения —
# итог вместе с автономными округами; без округов — отдельной строкой «… без …».
ALIASES: dict[str, str] = {
    "архангельская область": "RU-ARK-AGG",
    "тюменская область": "RU-TYU-AGG",
    "чувашская республика-чувашия": "RU-CU",
}

# Субъект без входящих в него автономных округов: «… без …», «… кроме …».
_WITHOUT_OKRUGS = {"архангельская область": "RU-ARK", "тюменская область": "RU-TYU"}

# Вводные слова перед названием: «в том числе:», «в т.ч.».
_LEAD_IN = re.compile(r"^(в том числе|в т\.\s*ч\.|из них|включая)\s*:?\s*", re.IGNORECASE)
_CITY_PREFIX = re.compile(r"^г\.\s*")
_FOOTNOTE_TAIL = re.compile(r"[\s\d,)*¹²³⁴⁵⁶⁷⁸⁹]+$")
# Единица, приписанная к строке страны: «Российская Федерация, млрд. рублей».
_COUNTRY_UNIT = re.compile(r"^(российская федерация)\b.*$")

# Латинские буквы, неотличимые от кириллических, в русских словах таблиц Росстата
# («Калинингpадская», «Hовгородская», «г. Cанкт-Петербург»).
_LOOKALIKES = str.maketrans("aceopxyACEHKMOPTXB", "асеорхуАСЕНКМОРТХВ")
_CYRILLIC = re.compile(r"[а-яё]", re.IGNORECASE)
# Слова, слипшиеся при переносе таблицы из Word: «областьбез», «Кавказскийфедеральный».
_GLUED = re.compile(r"(?<=[а-яё])(?=федеральн|автономн|област|округ|без\b|республик|край\b)")
_OKRUG_ABBR = re.compile(r"\bао\b")


def normalize(label: str) -> str:
    """Привести подпись к виду для сравнения: без сносок, скобок, «г.» и вводных слов."""
    text = label.replace("\xa0", " ").replace("ё", "е").replace("Ё", "Е").strip()
    text = _FOOTNOTE_TAIL.sub("", text)
    text = re.sub(r"[–—−]", "-", text)
    text = re.sub(r"\s*-\s*", "-", text)
    text = text.replace("(", " ").replace(")", " ")
    text = re.sub(r"\s+", " ", text).strip().lower()
    text = _LEAD_IN.sub("", text)
    text = _CITY_PREFIX.sub("", text)
    text = re.sub(r"\bавт\.\s*области\b", "автономной области", text)
    text = re.sub(r"\bавт\.\s*область\b", "автономная область", text)
    text = re.sub(r"\bавт\.\s*округа\b", "автономного округа", text)
    text = re.sub(r"\bавт\.\s*округов\b", "автономных округов", text)
    text = re.sub(r"\bавт\.\s*округ\b", "автономный округ", text)
    # «Республика Адыгея (Адыгея)»: после снятия скобок последнее слово повторяется.
    text = re.sub(r"\b(\w+) \1$", r"\1", text)
    return _COUNTRY_UNIT.sub(r"\1", text)


def repaired(label: str) -> str:
    """
    Подпись после безопасных исправлений: латиница в русских словах, слипшиеся слова, «АО».

    Исправления не угадывают: они только приводят написание к тому, что есть в справочнике.
    """
    text = split_glued(normalize(fold_lookalikes(label)))
    return _OKRUG_ABBR.sub("автономный округ", text)


def fold_lookalikes(label: str) -> str:
    """Латинские буквы, похожие на кириллические, заменить в словах, где есть кириллица."""
    return re.sub(
        r"\S+",
        lambda word: (
            word.group().translate(_LOOKALIKES) if _CYRILLIC.search(word.group()) else word.group()
        ),
        label,
    )


def split_glued(text: str) -> str:
    """Разделить слова, слипшиеся при переносе таблицы: «областьбез» → «область без»."""
    return _GLUED.sub(" ", text)


@lru_cache(maxsize=1)
def _known_names() -> dict[str, str]:
    """Нормализованные названия справочника → код территории."""
    path = settings.REFERENCE_DIR / "territories.json"
    payload = json.loads(path.read_bytes().decode("utf-8"))
    records = [
        payload["country"],
        *payload["federal_districts"],
        *payload["regions"],
        *payload["aggregates"],
    ]
    names: dict[str, str] = {}
    for record in records:
        # Название набора точнее: «Архангельская область (без автономного округа)».
        names[normalize(record["source_name"])] = record["code"]
        names.setdefault(normalize(record["name_ru"]), record["code"])
    # Прямые названия субъектов с округами уступают правилу Росстата.
    names.update(ALIASES)
    return names


def territory_code(label: str, aliases: Mapping[str, str] | None = None) -> str | None:
    """
    Код территории по подписи строки; ``None`` — подпись территорией не является.

    ``aliases`` — нормализованные названия, которые источник понимает не так, как Росстат.
    Исправленное написание (``repaired``) пробуется, только если подпись не узнана как есть.
    """
    text = normalize(label)
    found = _lookup(text, aliases)
    if found is None:
        fixed = repaired(label)
        if fixed != text:
            found = _lookup(fixed, aliases)
    return found


def _lookup(text: str, aliases: Mapping[str, str] | None) -> str | None:
    if aliases and text in aliases:
        return aliases[text]
    for name, code in _WITHOUT_OKRUGS.items():
        if text.startswith(name) and re.search(r"\b(без|кроме)\b", text):
            return code
    return _known_names().get(text)


@lru_cache(maxsize=1)
def region_codes() -> frozenset[str]:
    """Коды субъектов справочника: в таблице источника должна быть строка каждого."""
    path = settings.REFERENCE_DIR / "territories.json"
    payload = json.loads(path.read_bytes().decode("utf-8"))
    return frozenset(record["code"] for record in payload["regions"])
