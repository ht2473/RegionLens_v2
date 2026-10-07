"""Слова запроса и названий: нормализация, основы (Snowball) для русского и английского."""

from __future__ import annotations

import re
from functools import lru_cache

import snowballstemmer

_WORD_RE = re.compile(r"[0-9a-zа-я²³%]+", re.IGNORECASE)
_CYRILLIC_RE = re.compile(r"[а-я]")

# Наименьшая общая часть основ, сопоставляемых по началу: «татарста» и «татарстан».
PREFIX_MIN = 5
# Наибольшая разница длины основ, сопоставляемых по началу.
PREFIX_SLACK = 3

# Служебные слова: в отбор показателя не идут. Слова-признаки («где», «больше», «как
# менялся») разбираются раньше, по полному тексту запроса.
_STOP_TEXT = (
    # Предлоги, союзы, частицы.
    "а в во на и или по с со за от до для из к ко о об у при про ли же бы не ни то это "
    # Вопросительные и местоимения.
    "как где что кто какой какая какое какие каком каких который "
    # Степени без предмета: «самый», «больше всего».
    "самый самая самое самые самых самой самом всего все весь всех больше меньше выше ниже "
    "лучше хуже сколько там тут так также есть был была были будет "
    # Слова, которые есть почти в каждом запросе о данных сайта.
    "регион регионе регионах регионы региона регионов субъект субъектах россия россии рф "
    "году год года годах лет "
    # По-английски.
    "the a an of in on at to for by with and or is are was were be what which where who how "
    "do does did most least more less much many highest lowest top region regions russia"
)
STOP_WORDS = frozenset(_STOP_TEXT.split())

_STEMMERS = {
    "ru": snowballstemmer.stemmer("russian"),
    "en": snowballstemmer.stemmer("english"),
}


def normalize(text: str) -> str:
    """Строчные буквы, «ё» как «е»."""
    return text.lower().replace("ё", "е")


def words(text: str) -> list[str]:
    """Слова текста в нормальном виде; дефис и знаки делят слова."""
    return _WORD_RE.findall(normalize(text))


@lru_cache(maxsize=65536)
def stem(word: str) -> str:
    """Основа слова: русским или английским стеммером по алфавиту, число — как есть."""
    if word.isdigit():
        return word
    language = "ru" if _CYRILLIC_RE.search(word) else "en"
    return str(_STEMMERS[language].stemWord(word))


def stems(text: str, *, keep_stop: bool = False) -> list[str]:
    """Основы слов текста; служебные слова отбрасываются, если не сказано иное."""
    return [stem(word) for word in words(text) if keep_stop or word not in STOP_WORDS]


def same_stem(left: str, right: str) -> bool:
    """Основы совпадают или одна начинает другую: имена собственные стеммер режет неровно."""
    if left == right:
        return True
    short, long = sorted((left, right), key=len)
    return (
        len(short) >= PREFIX_MIN
        and len(long) - len(short) <= PREFIX_SLACK
        and long.startswith(short)
    )
