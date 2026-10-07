"""
Разбор запроса по правилам: место — справочником территорий, признаки — шаблонами,
показатель — указателем BM25, вид ответа — по найденному. Языковой модели нет.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

from apps.search.index import Hit, glossary_index, methodology_index, series_index
from apps.search.places import Place, find_places
from apps.search.text import STOP_WORDS, normalize, stem, words

# Виды ответа.
RANK = "rank"
VALUE = "value"
COMPARE = "compare"
TREND = "trend"
RELATION = "relation"
REGION = "region"
DEFINE = "define"
METHOD = "method"
NONE = "none"

# Наименьшая доля содержательных слов запроса, нашедшихся в показателе, и его оценка.
MIN_COVERAGE = 0.5
MIN_SCORE = 1.0
# Сколько показателей предлагать на выбор («понято как … или …»).
ALTERNATIVES = 3
# Два показателя связи, два места сравнения.
PAIR = 2

# Признаки по полному тексту запроса.
_WHY = re.compile(r"\b(почему|зачем|отчего|why)\b")
_METHOD = re.compile(
    r"\bкак\s+(?:\w+\s+)?(?:счита|рассчитыва|вычисля|пересчитыва|определя|измеря)\w*"
    r"|\bметодик\w*|\bформул\w*"
    r"|\bhow\s+(?:is|are|do|does)\b.*\b(?:calculat|comput|measur|deriv)\w*|\bmethodolog\w*"
)
_DEFINE = re.compile(
    r"\bчто\s+(?:такое|значит|означает)\b|\bопределени\w*"
    r"|\bwhat\s+(?:is|are|does)\b|\bmeaning\b|\bdefin\w*"
)
_RELATION = re.compile(r"\b(?:связ|завис|корреляц|влия|relat|correlat|depend|affect)\w*")
_TREND = re.compile(
    r"\bкак\s+(?:\w+\s+)?(?:менял|изменял|изменил|поменял|рос|росл|растут|падал|снижал|увеличивал|сокращал)\w*"
    r"|\bдинамик\w*|\bизменени\w*|\bтренд\w*|\bпо\s+годам\b|\bза\s+\d+\s+(?:лет|год\w*)"
    r"|\bhow\s+(?:has|have|did)\b.*\b(?:chang|grow|grew|fall|fell|evolv)\w*|\btrends?\b"
    r"|\bover\s+time\b|\bchanged\b"
)
_YEAR = re.compile(r"\b(19[89]\d|20[0-4]\d)\b")
_SPAN = re.compile(r"\bза\s+\d+\s+(?:лет|год\w*)|\b\d+\s+(?:years?)\b")

# Слова признаков: в отбор показателя не идут.
_MARKER_WORD = re.compile(
    r"^(?:почему|зачем|отчего|why|как|how|что|такое|значит|означает|what|meaning|"
    r"определени\w*|defin\w*|методик\w*|methodolog\w*|формул\w*|"
    r"счита\w*|рассчитыва\w*|вычисля\w*|пересчитыва\w*|определя\w*|измеря\w*|"
    r"calculat\w*|comput\w*|measur\w*|"
    r"связ\w*|завис\w*|корреляц\w*|влия\w*|relat\w*|correlat\w*|depend\w*|affect\w*|"
    r"сравн\w*|против|vs|versus|compar\w*|"
    r"менял\w*|изменял\w*|изменил\w*|поменял\w*|изменени\w*|динамик\w*|тренд\w*|trends?|changed?|over|time|"
    r"росл\w*|рос|падал\w*|снижал\w*|увеличивал\w*|сокращал\w*|"
    r"has|have|did|grow|grew|fall|fell|evolv\w*|кто)$"
)
# Связки, делящие запрос о связи на два показателя.
_CONNECTORS = frozenset({"и", "с", "со", "от", "между", "and", "with", "to", "vs", "против"})


@dataclass(frozen=True, slots=True)
class Reading:
    """Как понят запрос: вид ответа, показатели, места, год, термин или раздел методики."""

    query: str
    kind: str
    series: tuple[str, ...] = ()
    alternatives: tuple[str, ...] = ()
    places: tuple[Place, ...] = ()
    year: int | None = None
    target: str = ""
    # «Почему…» — ответа в данных нет; найденное показывается как «ближе всего».
    why: bool = False
    content: tuple[str, ...] = field(default=())

    @property
    def codes(self) -> list[str]:
        """Коды мест по порядку."""
        return [place.code for place in self.places]


def parse(query: str) -> Reading:
    """Разобрать запрос."""
    text = normalize(query.strip())
    tokens = words(text)
    stemmed = [stem(token) for token in tokens]
    places = tuple(find_places(tokens))
    positions = _content_positions(text, tokens, places)
    content = tuple(stemmed[index] for index in positions)
    hits = _series_hits(content)
    found = _confident(hits, content)
    year = _YEAR.search(text)
    reading = Reading(
        query=query,
        kind=NONE,
        series=found,
        alternatives=tuple(hit.key for hit in hits[:ALTERNATIVES]) if found else (),
        places=places,
        year=int(year.group(1)) if year else None,
        content=content,
    )
    return _special(reading, text, tokens, stemmed, positions) or _plain(reading, text)


def _content_positions(text: str, tokens: list[str], places: tuple[Place, ...]) -> list[int]:
    """Номера содержательных слов: без мест, служебных слов, признаков и чисел."""
    taken = {index for place in places for index in range(place.start, place.end)}
    span = {word for match in _SPAN.finditer(text) for word in words(match.group(0))}
    return [
        index
        for index, token in enumerate(tokens)
        if index not in taken
        and token not in STOP_WORDS
        and token not in span
        and not token.isdigit()
        and not _MARKER_WORD.match(token)
    ]


def _special(
    reading: Reading, text: str, tokens: list[str], stemmed: list[str], positions: list[int]
) -> Reading | None:
    """Вопросы по признаку в тексте: «почему», методика, термин, связь показателей."""
    content = reading.content
    if _WHY.search(text):
        return replace(reading, why=True)
    if _METHOD.search(text):
        target = _best(methodology_index().search(list(content)), content)
        if target:
            return replace(reading, kind=METHOD, target=target, series=(), alternatives=())
    if _DEFINE.search(text):
        target = _best(glossary_index().search(list(content)), content)
        if target:
            return replace(reading, kind=DEFINE, target=target, series=(), alternatives=())
    if _RELATION.search(text):
        pair = _pair(tokens, stemmed, positions)
        if len(pair) == PAIR:
            return replace(reading, kind=RELATION, series=pair, alternatives=pair)
    return None


def _plain(reading: Reading, text: str) -> Reading:
    """Вид ответа по найденному: два места — сравнение, показатель — рейтинг или значение."""
    if len(reading.places) >= PAIR:
        return replace(reading, kind=COMPARE)
    if reading.series:
        if _TREND.search(text):
            return replace(reading, kind=TREND)
        return replace(reading, kind=VALUE if reading.places else RANK)
    if reading.places and not reading.content:
        return replace(reading, kind=REGION)
    # Термин без «что такое»: «разрыв сопоставимости», «медиана».
    target = _best(glossary_index().search(list(reading.content)), reading.content, strict=True)
    return replace(reading, kind=DEFINE, target=target) if target else reading


def reconsider(reading: Reading) -> str:
    """Вид ответа после уточнения показателя или мест: динамика остаётся динамикой."""
    if len(reading.series) >= PAIR:
        return RELATION
    if len(reading.places) >= PAIR:
        return COMPARE
    if reading.series and reading.kind == TREND:
        return TREND
    if reading.series:
        return VALUE if reading.places else RANK
    if reading.places:
        return REGION
    return reading.kind


def _series_hits(content: tuple[str, ...]) -> list[Hit]:
    """Показатели основного набора по содержательным словам."""
    if not content:
        return []
    return series_index().search(list(content), limit=ALTERNATIVES * 2)


def _confident(hits: list[Hit], content: tuple[str, ...]) -> tuple[str, ...]:
    """Первый показатель, если он покрывает запрос достаточно и оценён не случайно."""
    if not hits or not content:
        return ()
    top = hits[0]
    coverage = len(top.matched) / len(set(content))
    if coverage >= MIN_COVERAGE and top.score >= MIN_SCORE:
        return (top.key,)
    return ()


def _best(hits: list[Hit], content: tuple[str, ...], *, strict: bool = False) -> str:
    """Первый документ указателя; ``strict`` — только если в нём нашлись все слова запроса."""
    if not hits or not content:
        return ""
    top = hits[0]
    needed = len(set(content)) if strict else max(1, len(set(content)) // 2)
    return top.key if len(top.matched) >= needed and top.score >= MIN_SCORE else ""


def _pair(tokens: list[str], stemmed: list[str], positions: list[int]) -> tuple[str, ...]:
    """Два показателя запроса о связи: части до и после связки «и», «с», «от»."""
    connectors = [index for index, token in enumerate(tokens) if token in _CONNECTORS]
    for cut in connectors:
        left = tuple(stemmed[index] for index in positions if index < cut)
        right = tuple(stemmed[index] for index in positions if index > cut)
        first = _confident(_series_hits(left), left)
        second = _confident(_series_hits(right), right)
        if first and second and first != second:
            return first + second
    # Без связки: два лучших показателя с разными словами запроса.
    content = tuple(stemmed[index] for index in positions)
    hits = _series_hits(content)
    if len(hits) >= PAIR and not hits[0].matched & hits[1].matched:
        return (hits[0].key, hits[1].key)
    return ()
