"""
Числовые правила текстов читателя: методики и глоссария.

Одна мысль на предложение: средняя длина — до 15 слов, самое длинное — до 25. Без цепочек
через точку с запятой и тире: «;» в тексте нет, тире в предложении — не больше одного.
Объём: «Подробнее» раздела методики — до 120 слов, определение термина — до 40.
Правила проверяют русский оригинал: перевод следует за ним.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

# Пределы правил.
SECTION_BODY_WORDS = 120
OWN_DATA_BODY_WORDS = 80
SUMMARY_WORDS = 30
TERM_SHORT_WORDS = 25
TERM_DEFINITION_WORDS = 40
AVERAGE_SENTENCE_WORDS = 15
LONGEST_SENTENCE_WORDS = 25
LIMITATIONS = (2, 3)
DASHES_PER_SENTENCE = 1

# Раздел методики о своих данных: остальное о них — справкой в самой лаборатории.
OWN_DATA_SECTION = "own-data"

WORD = re.compile(r"[0-9A-Za-zА-Яа-яЁё]+(?:[-.,’'][0-9A-Za-zА-Яа-яЁё]+)*")
DASH = re.compile(r"\s—\s")
# Точка после сокращения предложение не заканчивает. Исключения перед заглавной буквой:
# «п. п.» и год с «г.» стоят в конце фразы («0,15 п. п. При…», «с 2017 г. Ряд…»).
ABBREVIATION = re.compile(
    r"\b(?:г|гг|т|е|п|руб|млн|млрд|тыс|обл|респ|см|др|пр|ок|им|ст|ч|ed|Vol|Art|Ch|et al)\.$"
)
SENTENCE_END_ABBREVIATION = re.compile(r"(?:\bп\. п|\d\s?гг?)\.$")
SENTENCE_GAP = re.compile(r"(?<=[.!?…])[»”)]?\s+(?=[«“(]?[A-ZА-ЯЁ0-9])")


@dataclass(frozen=True, slots=True)
class Measure:
    """Числовые признаки текста."""

    words: int
    sentences: int
    longest: int
    semicolons: int
    dashed: int

    @property
    def average(self) -> float:
        """Средняя длина предложения в словах."""
        return self.words / self.sentences if self.sentences else 0.0


def words(text: str) -> int:
    """Число слов: буквы и цифры, число с запятой — одно слово."""
    return len(WORD.findall(text))


def sentences(text: str) -> list[str]:
    """Предложения текста; абзацы — границы предложений."""
    found: list[str] = []
    for raw in re.split(r"\n\s*\n|\n", text):
        paragraph = raw.strip()
        if not paragraph:
            continue
        start = 0
        for gap in SENTENCE_GAP.finditer(paragraph):
            head = paragraph[start : gap.start()].rstrip("»”)")
            if ABBREVIATION.search(head) and not SENTENCE_END_ABBREVIATION.search(head):
                continue
            found.append(paragraph[start : gap.start()].strip())
            start = gap.end()
        found.append(paragraph[start:].strip())
    return [sentence for sentence in found if words(sentence)]


def measure(text: str) -> Measure:
    """Признаки текста для правил."""
    parts = sentences(text)
    return Measure(
        words=words(text),
        sentences=len(parts),
        longest=max((words(part) for part in parts), default=0),
        semicolons=text.count(";"),
        dashed=sum(len(DASH.findall(part)) > DASHES_PER_SENTENCE for part in parts),
    )


def prose_problems(text: str, *, limit: int) -> list[str]:
    """Нарушения правил у связного текста с пределом объёма ``limit``."""
    if not text.strip():
        return []
    found = measure(text)
    problems = []
    if found.words > limit:
        problems.append(f"{found.words} слов, предел {limit}")
    if found.average > AVERAGE_SENTENCE_WORDS:
        problems.append(f"средняя длина предложения {found.average:.1f} слова")
    if found.longest > LONGEST_SENTENCE_WORDS:
        problems.append(f"предложение в {found.longest} слов")
    if found.semicolons:
        problems.append(f"точек с запятой: {found.semicolons}")
    if found.dashed:
        problems.append(f"предложений с несколькими тире: {found.dashed}")
    return problems


def one_sentence(text: str, *, limit: int) -> list[str]:
    """Нарушения у краткого описания: одно предложение до ``limit`` слов, без «;»."""
    problems = []
    count = len(sentences(text))
    if count != 1:
        problems.append(f"предложений: {count}, нужно одно")
    if words(text) > limit:
        problems.append(f"{words(text)} слов, предел {limit}")
    if ";" in text:
        problems.append("точка с запятой")
    return problems


def section_problems(item: dict[str, object]) -> list[str]:
    """Нарушения у раздела методики в виде записи наполнения."""
    code = str(item["code"])
    limit = OWN_DATA_BODY_WORDS if code == OWN_DATA_SECTION else SECTION_BODY_WORDS
    problems = [f"кратко: {p}" for p in one_sentence(str(item["summary"]), limit=SUMMARY_WORDS)]
    problems += [f"подробнее: {p}" for p in prose_problems(str(item["body"]), limit=limit)]

    lines = [line for line in str(item.get("limitations", "")).splitlines() if line.strip()]
    low, high = LIMITATIONS
    if not low <= len(lines) <= high:
        problems.append(f"ограничений: {len(lines)}, нужно {low}–{high}")
    for line in lines:
        problems += [f"ограничение: {p}" for p in one_sentence(line, limit=LONGEST_SENTENCE_WORDS)]
    return problems


def term_problems(item: dict[str, object]) -> list[str]:
    """Нарушения у термина глоссария в виде записи наполнения."""
    problems = [f"кратко: {p}" for p in one_sentence(str(item["short"]), limit=TERM_SHORT_WORDS)]
    problems += [
        f"определение: {p}"
        for p in prose_problems(str(item["definition"]), limit=TERM_DEFINITION_WORDS)
    ]
    return problems


def report(sections: Iterable[dict[str, object]], terms: Iterable[dict[str, object]]) -> list[str]:
    """Все нарушения наполнения строками «код: нарушение»."""
    lines = [
        f"{item['code']}: {problem}" for item in sections for problem in section_problems(item)
    ]
    lines += [f"{item['slug']}: {problem}" for item in terms for problem in term_problems(item)]
    return lines
