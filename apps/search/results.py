"""Группы страницы результатов: показатели, регионы, термины, страницы."""

from __future__ import annotations

from typing import Any

from django.urls import reverse

from apps.search.index import (
    catalog_index,
    glossary_index,
    methodology_index,
    series_index,
)
from apps.search.parse import MIN_COVERAGE, MIN_SCORE, Reading

# Сколько записей в группе.
FEATURED_LIMIT = 6
CATALOG_LIMIT = 8
TERMS_LIMIT = 4
PAGES_LIMIT = 4


def collect(reading: Reading) -> dict[str, list[dict[str, Any]]]:
    """Собрать группы результатов по разобранному запросу."""
    content = list(reading.content)
    return {
        "series": _series(content),
        "regions": _regions(reading),
        "terms": _terms(content),
        "pages": _pages(reading.query, content),
    }


def _links(keys: list[str]) -> dict[str, dict[str, Any]]:
    """Адреса страниц рядов и их названия из каталога."""
    from apps.catalog.models import Series

    rows = Series.objects.filter(key__in=keys).select_related("indicator")
    return {
        row.key: {
            "href": reverse("catalog:series-detail", kwargs={"slug": row.indicator.slug})
            + f"?series={row.key}",
            "title": row.indicator.name,
            "subtitle": row.name if row.name else "",
            "years": f"{row.first_year}–{row.last_year}" if row.first_year else "",
        }
        for row in rows
    }


def _series(content: list[str]) -> list[dict[str, Any]]:
    """Показатели: сначала основной набор, затем остальные ряды каталога."""
    from apps.warehouse.queries import featured_set

    if not content:
        return []
    by_key = featured_set().by_key()
    featured = [
        hit.key for hit in series_index().search(content, FEATURED_LIMIT) if hit.score >= MIN_SCORE
    ]
    rest = [
        hit.key
        for hit in catalog_index().search(content, CATALOG_LIMIT + len(featured))
        if hit.key not in featured and hit.score >= MIN_SCORE
    ][:CATALOG_LIMIT]
    links = _links(featured + rest)
    rows = []
    for key in featured + rest:
        link = links.get(key)
        if link is None:
            continue
        item = by_key.get(key)
        rows.append(
            {
                **link,
                "title": item.short_title if item else link["title"],
                "subtitle": "" if item else link["subtitle"],
                "unit": item.unit_label if item else "",
                "featured": item is not None,
            }
        )
    return rows


def _regions(reading: Reading) -> list[dict[str, Any]]:
    """Места запроса — ссылками на паспорт."""
    from apps.catalog.models import Territory

    codes = [code for place in reading.places for code in place.codes]
    territories = {
        row.code: row for row in Territory.objects.filter(code__in=codes).select_related("parent")
    }
    found = []
    for code in dict.fromkeys(codes):
        territory = territories.get(code)
        if territory is None:
            continue
        parent = territory.parent
        found.append(
            {
                "name": territory.name,
                "href": reverse("catalog:territory-detail", kwargs={"slug": territory.slug}),
                "parent": parent.name if parent else "",
            }
        )
    return found


def _terms(content: list[str]) -> list[dict[str, Any]]:
    """Термины глоссария, покрывающие запрос хотя бы наполовину: одно общее слово — не повод."""
    from apps.content.models import GlossaryTerm

    if not content:
        return []
    unique = len(set(content))
    slugs = [
        hit.key
        for hit in glossary_index().search(content, TERMS_LIMIT)
        if hit.score >= MIN_SCORE and len(hit.matched) / unique >= MIN_COVERAGE
    ]
    terms = {term.slug: term for term in GlossaryTerm.objects.filter(slug__in=slugs)}
    url = reverse("content:glossary")
    return [
        {
            "title": terms[slug].term,
            "text": terms[slug].short_definition,
            "href": f"{url}#{terms[slug].anchor}",
        }
        for slug in slugs
        if slug in terms
    ]


def _pages(query: str, content: list[str]) -> list[dict[str, Any]]:
    """Разделы сайта по названию и разделы методики по содержанию."""
    from apps.content.models import MethodologySection
    from apps.core.navigation import matching_items

    pages = [
        {"title": item["title"], "text": item.get("hint", ""), "href": item["url"]}
        for item in matching_items(query)[:PAGES_LIMIT]
    ]
    if content:
        codes = [
            hit.key
            for hit in methodology_index().search(content, PAGES_LIMIT)
            if hit.score >= MIN_SCORE
        ]
        sections = {row.code: row for row in MethodologySection.objects.filter(code__in=codes)}
        url = reverse("content:methodology")
        pages += [
            {
                "title": sections[code].title,
                "text": sections[code].summary,
                "href": f"{url}#{sections[code].anchor}",
            }
            for code in codes
            if code in sections
        ]
    return pages[: PAGES_LIMIT * 2]
