"""Выборки содержимого для публичных страниц, панели управления и карты сайта."""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

from django.db.models import QuerySet
from django.utils.translation import get_language

from apps.core.search import search_q

from .constants import EN_ALPHABET, RU_ALPHABET, MethodologyBlock
from .models import GlossaryTerm, MethodologySection

# ---------------------------------------------------------------------------------------
# Методология
# ---------------------------------------------------------------------------------------


def methodology_sections() -> QuerySet[MethodologySection]:
    """Опубликованные разделы методологии с переводами на текущий язык."""
    return (
        MethodologySection.objects.filter(is_published=True)
        .prefetch_related("translations")
        .order_by("block", "display_order", "code")
    )


def methodology_blocks() -> list[dict[str, Any]]:
    """Разделы методики по блокам в порядке перечисления; пустые блоки пропускаются."""
    grouped: OrderedDict[str, list[MethodologySection]] = OrderedDict(
        (code, []) for code, _label in MethodologyBlock.choices
    )
    for section in methodology_sections():
        grouped.setdefault(section.block, []).append(section)

    labels = dict(MethodologyBlock.choices)
    return [
        {"code": code, "title": labels.get(code, code), "sections": sections}
        for code, sections in grouped.items()
        if sections
    ]


def methodology_for_tool(url_name: str) -> MethodologySection | None:
    """Раздел методики для страницы анализа или ничего."""
    return (
        MethodologySection.objects.filter(is_published=True, tool_url_name=url_name)
        .prefetch_related("translations")
        .first()
    )


# ---------------------------------------------------------------------------------------
# Глоссарий
# ---------------------------------------------------------------------------------------


def published_terms(query: str = "", category: str = "") -> QuerySet[GlossaryTerm]:
    """Опубликованные термины с фильтрами; поиск — по термину, синонимам и определению."""
    queryset = (
        GlossaryTerm.objects.published()
        .prefetch_related("translations", "related_terms__translations")
        .select_related("indicator", "methodology_section")
    )
    if category:
        queryset = queryset.filter(category=category)
    if query:
        queryset = queryset.filter(
            search_q(
                query,
                "translations__term",
                "translations__synonyms",
                "translations__short_definition",
            )
        ).distinct()
    return queryset


def terms_by_letter(query: str = "", category: str = "") -> list[dict[str, Any]]:
    """
    Термины, сгруппированные по первой букве.

    Сортировка — в Python: перевод лежит в присоединённой таблице.
    """
    terms = sorted(
        published_terms(query=query, category=category),
        key=lambda term: (term.letter, str(term)),
    )

    grouped: OrderedDict[str, list[GlossaryTerm]] = OrderedDict()
    for term in terms:
        grouped.setdefault(term.letter, []).append(term)

    return [{"letter": letter, "terms": items} for letter, items in grouped.items()]


def alphabet_index(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Буквенный указатель глоссария: весь алфавит языка и встреченные буквы вне его.

    Без перевода термин показывается по-русски и попадает под кириллическую букву.
    """
    present = {group["letter"] for group in groups}
    alphabet = EN_ALPHABET if get_language() == "en" else RU_ALPHABET

    index = [{"letter": letter, "available": letter in present} for letter in alphabet]
    extra = sorted(present - set(alphabet))
    index.extend({"letter": letter, "available": True} for letter in extra)
    return index


# ---------------------------------------------------------------------------------------
# Сводка для панели управления
# ---------------------------------------------------------------------------------------


def content_summary() -> dict[str, Any]:
    """Состояние содержимого для обзора панели, включая записи без английского перевода."""
    terms = GlossaryTerm.objects.all()
    sections = MethodologySection.objects.all()

    return {
        "terms_total": terms.count(),
        "terms_published": terms.published().count(),
        "sections_total": sections.count(),
        "untranslated_terms": terms.exclude(translations__language_code="en").count(),
        "untranslated_sections": sections.exclude(translations__language_code="en").count(),
    }
