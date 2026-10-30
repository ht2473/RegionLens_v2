"""Места в запросе: субъекты и округа по названиям, кратким формам, столицам и прозвищам."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from itertools import groupby

from apps.search.index import synonyms
from apps.search.text import one_typo, same_stem, stem, swapped_stems, words

# Короткие написания (НАО, КБР, СПб) сравниваются целым словом: основа «нао» совпала бы
# с предлогом «на».
SHORT_WORD = 4
SHORT_STEM = 3
# Окончание имени из гласных, «й» и «ь»: падеж узнаётся по остатку, который стеммер режет
# неровно — «Адыгея» → «адыге», но «Адыгеи» → «адыг»; «Алтай» → «алта», но «Алтая» → «алт».
_ENDING = re.compile(r"[аеиоуыэюяйь]+$")
ROOT_MIN = 3
# Опечатка узнаётся только в длинном названии: короткие основы легко совпадают со словами
# запроса («курс» — не Курская область). Перестановка букв («моксва») — редкая случайность.
TYPO_MIN = 6
SWAP_MIN = 5

# Страна — не место запроса: «регионы России» — это все регионы, а значение по стране
# стоит в каждом ответе.
COUNTRY_CODE = "RU"


@dataclass(frozen=True, slots=True)
class Place:
    """Найденное место: коды (больше одного — если написание неоднозначно) и слова запроса."""

    codes: tuple[str, ...]
    start: int
    end: int

    @property
    def code(self) -> str:
        """Код места; у неоднозначного — первый из возможных."""
        return self.codes[0]


# Слово написания: элемент сравнения (короткое слово целиком со знаком «=», длинное — основой)
# и остаток без окончания.
Part = tuple[str, str]


@cache
def _phrases() -> tuple[tuple[tuple[Part, ...], tuple[str, ...]], ...]:
    """Написания мест по словам → коды; длинные — первыми."""
    from apps.userdata.matching import matcher

    found: dict[tuple[Part, ...], tuple[str, ...]] = {}

    def put(text: str, codes: tuple[str, ...]) -> None:
        key = tuple(_part(word) for word in words(text))
        if key and COUNTRY_CODE not in codes:
            found.setdefault(key, codes)

    current = matcher()
    for text, (code, _rule) in current.exact.items():
        put(text, (code,))
    for text, code in current.nested.items():
        put(text, (code,))
    for text, code in current.short.items():
        put(text, (code,))
    for text, code in current.capitals.items():
        put(text, (code,))
    for text, codes in current.ambiguous.items():
        put(text, tuple(codes))
    for code, names in synonyms().get("places", {}).items():
        for name in names:
            put(name, (code,))
    return tuple(sorted(found.items(), key=lambda item: -len(item[0])))


def _part(word: str) -> Part:
    """Слово написания: элемент сравнения и остаток без окончания."""
    element = f"={word}" if len(word) <= SHORT_WORD else stem(word)
    return element, _ENDING.sub("", word)


def _same(token: str, part: Part, *, strict: bool) -> bool:
    """
    Слово запроса совпадает со словом написания: ``strict`` — только точно; иначе ещё по
    началу основы, по остатку без окончания и с одной опечаткой в длинном слове.
    """
    element, root = part
    if element.startswith("="):
        raw = element[1:]
        # Падеж короткого названия («в Тыве», «в Туле») — по основе не короче трёх букв.
        return token == raw or (len(stem(raw)) >= SHORT_STEM and stem(token) == stem(raw))
    token_stem = stem(token)
    if strict:
        return token_stem == element
    if same_stem(token_stem, element):
        return True
    if len(root) >= ROOT_MIN and token.startswith(root) and _ENDING.fullmatch(token[len(root) :]):
        return True
    if len(element) >= SWAP_MIN and element in swapped_stems(token_stem):
        return True
    return len(element) >= TYPO_MIN and one_typo(token_stem, element)


def find_places(tokens: list[str]) -> list[Place]:
    """
    Найти места в словах запроса: сначала самые длинные написания, без наложения; среди
    написаний одной длины — сперва точные совпадения («Sakhalin» — Сахалин, а не Саха).

    ``tokens`` — все слова запроса по порядку, со служебными.
    """
    taken = [False] * len(tokens)
    places: list[Place] = []
    for size, group in groupby(_phrases(), key=lambda item: len(item[0])):
        phrases = list(group)
        for strict in (True, False):
            for key, codes in phrases:
                for start in range(len(tokens) - size + 1):
                    span = range(start, start + size)
                    if any(taken[index] for index in span):
                        continue
                    if all(
                        _same(tokens[index], key[offset], strict=strict)
                        for offset, index in enumerate(span)
                    ):
                        for index in span:
                            taken[index] = True
                        places.append(Place(codes=codes, start=start, end=start + size))
    places.sort(key=lambda place: place.start)
    # Одно место дважды («Москва, Москва») — один раз.
    unique: list[Place] = []
    for place in places:
        if all(place.codes != seen.codes for seen in unique):
            unique.append(place)
    return unique
