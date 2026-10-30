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

# Две соседние буквы, поменявшиеся местами, — одна опечатка.
SWAPPED_LETTERS = 2

# Служебные слова: в отбор показателя не идут. Слова-признаки («где», «больше», «как
# менялся») разбираются раньше, по полному тексту запроса.
_STOP_TEXT = (
    # Предлоги, союзы, частицы.
    "а в во на и или по с со за от до для из к ко о об у при про ли же бы не ни то это "
    # Вопросительные и местоимения.
    "как где что кто какой какая какое какие каком каких который откуда куда "
    # Степени без предмета: «самый», «больше всего».
    "самый самая самое самые самых самой самом самым самыми самого самому самую всего все весь "
    "всех больше меньше выше ниже очень "
    "лучше хуже сколько там тут так также есть был была были будет "
    # Слова, которые есть почти в каждом запросе о данных сайта.
    "регион регионе регионах регионы региона регионов региону регионом регионам регионами "
    "субъект субъектах субъекты субъектов субъектам область области областей областям областях "
    "российский российская российские российских российской российского российским "
    "россия россии рф году год года годах годам годы годов лет "
    # Обращения: «покажи», «подскажите», «хочу узнать» — о показателе не говорят.
    "покажи покажите показать подскажи подскажите скажи скажите хочу хотел хотела узнать "
    "найди найдите найти нужно нужна нужен можно пожалуйста каков какова каково каковы "
    # По-английски.
    "the a an of in on at to for by with and or is are was were be what which where who how "
    "do does did most least more less much many highest lowest top region regions russia "
    "please show tell me find give want know"
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


def _swapped(left: str, right: str) -> bool:
    """Слова одной длины различаются перестановкой двух соседних букв."""
    if len(left) != len(right):
        return False
    diff = [index for index, (a, b) in enumerate(zip(left, right, strict=True)) if a != b]
    return (
        len(diff) == SWAPPED_LETTERS
        and diff[1] == diff[0] + 1
        and left[diff[0]] == right[diff[1]]
        and left[diff[1]] == right[diff[0]]
    )


def one_typo(left: str, right: str) -> bool:
    """
    Слова различаются не больше чем одной опечаткой: буква заменена, пропущена, лишняя или
    две соседние переставлены. «Доставка» и «ставка» — уже две правки, не опечатка.
    """
    if left == right:
        return True
    if abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        return sum(a != b for a, b in zip(left, right, strict=True)) == 1 or _swapped(left, right)
    short, long = sorted((left, right), key=len)
    first = next(
        (index for index, (a, b) in enumerate(zip(short, long, strict=False)) if a != b), len(short)
    )
    return long[first + 1 :] == short[first:]


@lru_cache(maxsize=65536)
def swapped_stems(word: str) -> frozenset[str]:
    """
    Основы слова с переставленными соседними буквами. Стеммер режет слово с опечаткой иначе
    («зантяости» → «зантяост», «занятости» → «занят»), поэтому буквы меняются до стемминга;
    сравнивать начало основ нельзя — «выборы» совпали бы с «выбросами», «смартфон» со «смертью».
    """
    return frozenset(
        stem(word[:index] + word[index + 1] + word[index] + word[index + 2 :])
        for index in range(len(word) - 1)
    )


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
