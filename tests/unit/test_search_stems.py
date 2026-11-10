"""Основы слов поиска вопросом при одновременных запросах."""

from __future__ import annotations

import itertools
from concurrent.futures import ThreadPoolExecutor

import pytest

from apps.search import text

pytestmark = pytest.mark.unit

ROOTS = ("безработиц", "численност", "заработн", "населени", "промышленн", "строительств")
ENDINGS = ("а", "ы", "е", "у", "ой", "ами", "ах", "ого", "ему", "ыми", "ость", "ости")


def test_parallel_stemming_matches_sequential() -> None:
    """
    Стеммер Snowball хранит слово в себе: общий для потоков, он портил слова соседних
    запросов, и поиск падал с IndexError. Разбор в восемь потоков должен совпасть
    с последовательным и пройти без исключений.
    """
    words = [
        f"{root}{ending}{suffix}"
        for root, ending, suffix in itertools.product(ROOTS, ENDINGS, ("", "ия", "ностями"))
    ] * 40
    text.stem.cache_clear()
    expected = [text.stem(word) for word in words]
    text.stem.cache_clear()

    def run(chunk: list[str]) -> list[str]:
        result = []
        for word in chunk:
            text.stem.cache_clear()
            result.append(text.stem(word))
        return result

    chunks = [words[index::8] for index in range(8)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(run, chunks))

    for index, chunk_result in enumerate(results):
        assert chunk_result == expected[index::8]
