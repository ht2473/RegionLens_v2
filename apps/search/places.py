"""Места в запросе: субъекты и округа по названиям, кратким формам, столицам и прозвищам."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache

from apps.search.index import synonyms
from apps.search.text import same_stem, stem, words

# Короткие написания (НАО, КБР, СПб) сравниваются целым словом: основа «нао» совпала бы
# с предлогом «на».
SHORT_WORD = 4
SHORT_STEM = 3

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


@cache
def _phrases() -> tuple[tuple[tuple[str, ...], tuple[str, ...]], ...]:
    """Написания мест основами слов → коды; длинные — первыми."""
    from apps.userdata.matching import matcher

    found: dict[tuple[str, ...], tuple[str, ...]] = {}

    def put(text: str, codes: tuple[str, ...]) -> None:
        key = tuple(_element(word) for word in words(text))
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


def _element(word: str) -> str:
    """Элемент написания: короткое слово целиком (со знаком «=»), длинное — основой."""
    return f"={word}" if len(word) <= SHORT_WORD else stem(word)


def _same(token: str, element: str) -> bool:
    """Слово запроса совпадает с элементом написания."""
    if element.startswith("="):
        raw = element[1:]
        # Падеж короткого названия («в Тыве», «в Туле») — по основе не короче трёх букв.
        return token == raw or (len(stem(raw)) >= SHORT_STEM and stem(token) == stem(raw))
    return same_stem(stem(token), element)


def find_places(tokens: list[str]) -> list[Place]:
    """
    Найти места в словах запроса: сначала самые длинные написания, без наложения.

    ``tokens`` — все слова запроса по порядку, со служебными.
    """
    taken = [False] * len(tokens)
    places: list[Place] = []
    for key, codes in _phrases():
        size = len(key)
        for start in range(len(tokens) - size + 1):
            span = range(start, start + size)
            if any(taken[index] for index in span):
                continue
            if all(_same(tokens[index], key[offset]) for offset, index in enumerate(span)):
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
