"""
Указатели поиска с ранжированием BM25: показатели основного набора, термины, разделы методики.

Поля документа весят по-разному (название и синонимы — больше описания темы); слово
запроса совпадает с основой точно, по началу (имена собственные) или с опечаткой.
"""

from __future__ import annotations

import difflib
import heapq
import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from functools import cache, lru_cache
from typing import Any

from django.conf import settings
from django.db.models import Max

from apps.search.text import same_stem, stems

# Параметры BM25: насыщение частоты и поправка на длину документа.
K1 = 1.2
B = 0.3

# Вес совпадения по началу основы и с опечаткой относительно точного.
PREFIX_WEIGHT = 0.8
TYPO_WEIGHT = 0.6
# Порог сходства основ для опечатки и наименьшая длина исправляемой основы.
TYPO_CUTOFF = 0.8
TYPO_MIN_LENGTH = 5

# Веса полей показателя.
SERIES_FIELDS = {"title": 3.0, "synonyms": 2.5, "question": 2.0, "full": 1.0, "theme": 0.5}
# Перевес главных показателей набора (плитки главной): «зарплата» — средняя, а не медианная.
HEADLINE_PRIOR = 1.15


@dataclass(frozen=True, slots=True)
class Hit:
    """Документ указателя с оценкой и словами запроса, которые в нём нашлись."""

    key: str
    score: float
    matched: frozenset[str]


@dataclass(slots=True)
class Index:
    """Указатель BM25 по взвешенным полям документов; ``prior`` — множитель оценки документа."""

    weights: dict[str, dict[str, float]] = field(default_factory=dict)
    lengths: dict[str, float] = field(default_factory=dict)
    frequency: Counter[str] = field(default_factory=Counter)
    prior: dict[str, float] = field(default_factory=dict)

    def add(self, key: str, fields: list[tuple[str, float]], prior: float = 1.0) -> None:
        """Добавить документ: тексты полей с весами."""
        counts: dict[str, float] = defaultdict(float)
        for text, weight in fields:
            for token in stems(text):
                counts[token] += weight
        if not counts:
            return
        self.weights[key] = counts
        self.lengths[key] = sum(counts.values())
        self.prior[key] = prior
        self.frequency.update(counts.keys())

    @property
    def vocabulary(self) -> list[str]:
        """Основы указателя."""
        return list(self.frequency)

    def expand(self, token: str) -> list[tuple[str, float]]:
        """Основы указателя для слова запроса с весом совпадения."""
        if token in self.frequency:
            return [(token, 1.0)]
        near = [(other, PREFIX_WEIGHT) for other in self.frequency if same_stem(token, other)]
        if near or len(token) < TYPO_MIN_LENGTH or token.isdigit():
            return near
        close = difflib.get_close_matches(token, self.vocabulary, n=2, cutoff=TYPO_CUTOFF)
        return [(other, TYPO_WEIGHT) for other in close]

    def search(self, tokens: list[str], limit: int = 10) -> list[Hit]:
        """Документы по убыванию оценки; без единого совпадения — пусто."""
        if not self.weights:
            return []
        count = len(self.weights)
        average = sum(self.lengths.values()) / count
        scores: dict[str, float] = defaultdict(float)
        matched: dict[str, set[str]] = {}
        for token in dict.fromkeys(tokens):
            for other, factor in self.expand(token):
                idf = math.log(
                    1 + (count - self.frequency[other] + 0.5) / (self.frequency[other] + 0.5)
                )
                for key, counts in self.weights.items():
                    tf = counts.get(other)
                    if not tf:
                        continue
                    norm = tf * (K1 + 1) / (tf + K1 * (1 - B + B * self.lengths[key] / average))
                    scores[key] += factor * idf * norm
                    matched.setdefault(key, set()).add(token)
        for key in scores:
            scores[key] *= self.prior[key]
        return [
            Hit(key=key, score=score, matched=frozenset(matched[key]))
            for key, score in heapq.nlargest(limit, scores.items(), key=lambda pair: pair[1])
        ]


# --- Справочники -----------------------------------------------------------------------


@cache
def synonyms() -> dict[str, Any]:
    """Словарь синонимов поиска (data/reference/search_synonyms.json)."""
    path = settings.REFERENCE_DIR / "search_synonyms.json"
    return json.loads(path.read_bytes().decode("utf-8"))


def series_index() -> Index:
    """Указатель показателей основного набора: оба языка в одном документе."""
    from apps.warehouse.queries import featured_set

    return _series_index(featured_set().digest)


@lru_cache(maxsize=2)
def _series_index(digest: str) -> Index:  # noqa: ARG001 - отпечаток — ключ кэша
    """Построить указатель показателей для отпечатка файла набора ``digest``."""
    from apps.warehouse.queries import featured_set

    current = featured_set()
    words = synonyms()
    themes = {theme.slug: theme for theme in current.themes}
    full = _full_titles([item.key for item in current.series])
    index = Index()
    for item in current.series:
        theme = themes.get(item.theme)
        theme_words = words["themes"].get(item.theme, {})
        series_words = words["series"].get(item.key, {})
        weights = SERIES_FIELDS
        index.add(
            item.key,
            [
                (f"{item.short_title_ru} {item.short_title_en}", weights["title"]),
                (
                    " ".join(series_words.get("ru", []) + series_words.get("en", [])),
                    weights["synonyms"],
                ),
                (f"{item.question_ru} {item.question_en}", weights["question"]),
                (full.get(item.key, ""), weights["full"]),
                (
                    " ".join([theme.title_ru, theme.title_en] if theme else [])
                    + " "
                    + " ".join(theme_words.get("ru", []) + theme_words.get("en", [])),
                    weights["theme"],
                ),
            ],
            prior=float(series_words.get("prior", HEADLINE_PRIOR if item.headline else 1.0)),
        )
    return index


def _full_titles(keys: list[str]) -> dict[str, str]:
    """Полные названия рядов из каталога: показатель и разрез на обоих языках."""
    from apps.catalog.models import Series

    titles: dict[str, str] = {}
    rows = Series.objects.filter(key__in=keys).select_related("indicator")
    for row in rows:
        titles[row.key] = " ".join(
            filter(None, [row.indicator.name_ru, row.indicator.name_en, row.name_ru, row.name_en])
        )
    return titles


def catalog_index() -> Index:
    """Указатель всех рядов каталога по названию показателя, разреза и раздела."""
    from apps.catalog.selectors import series_options_version

    return _catalog_index(series_options_version())


@lru_cache(maxsize=2)
def _catalog_index(version: str) -> Index:  # noqa: ARG001 - отпечаток — ключ кэша
    """Построить указатель рядов каталога для отпечатка ``version``."""
    from apps.catalog.models import Series

    index = Index()
    rows = Series.objects.select_related("indicator", "indicator__section").only(
        "key",
        "name_ru",
        "name_en",
        "indicator__name_ru",
        "indicator__name_en",
        "indicator__section__name_ru",
        "indicator__section__name_en",
    )
    for row in rows:
        section = row.indicator.section
        index.add(
            row.key,
            [
                (f"{row.indicator.name_ru} {row.indicator.name_en or ''}", 2.0),
                (f"{row.name_ru or ''} {row.name_en or ''}", 1.0),
                (f"{section.name_ru} {section.name_en or ''}" if section else "", 0.3),
            ],
        )
    return index


def content_stamp() -> tuple[str, str]:
    """Отпечаток глоссария и методики: указатели строятся заново после правки в панели."""
    from apps.content.models import GlossaryTerm, MethodologySection

    glossary = GlossaryTerm.objects.aggregate(stamp=Max("updated_at"))["stamp"]
    sections = MethodologySection.objects.aggregate(stamp=Max("updated_at"))["stamp"]
    return str(glossary), str(sections)


def glossary_index() -> Index:
    """Указатель терминов глоссария: термин, синонимы, краткое определение."""
    return _glossary_index(content_stamp()[0])


@lru_cache(maxsize=2)
def _glossary_index(stamp: str) -> Index:  # noqa: ARG001 - отпечаток — ключ кэша
    """Построить указатель терминов для отпечатка ``stamp``."""
    from apps.content.models import GlossaryTerm

    index = Index()
    for term in GlossaryTerm.objects.filter(is_published=True).prefetch_related("translations"):
        texts = {
            field_name: " ".join(
                str(term.safe_translation_getter(field_name, language_code=code) or "")
                for code in ("ru", "en")
            )
            for field_name in ("term", "synonyms", "short_definition")
        }
        index.add(
            term.slug,
            [(texts["term"], 3.0), (texts["synonyms"], 2.0), (texts["short_definition"], 0.5)],
        )
    return index


def methodology_index() -> Index:
    """Указатель разделов методики: заголовок, краткое содержание."""
    return _methodology_index(content_stamp()[1])


@lru_cache(maxsize=2)
def _methodology_index(stamp: str) -> Index:  # noqa: ARG001 - отпечаток — ключ кэша
    """Построить указатель разделов методики для отпечатка ``stamp``."""
    from apps.content.models import MethodologySection

    index = Index()
    for section in MethodologySection.objects.filter(is_published=True).prefetch_related(
        "translations"
    ):
        texts = {
            field_name: " ".join(
                str(section.safe_translation_getter(field_name, language_code=code) or "")
                for code in ("ru", "en")
            )
            for field_name in ("title", "summary")
        }
        index.add(section.code, [(texts["title"], 3.0), (texts["summary"], 1.0)])
    return index
