"""
Похожие регионы по стандартизованной матрице ключевых показателей.

Признаки взяты за последние доступные годы; субъекты с неполными данными не участвуют.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from apps.catalog.models import Territory
from apps.catalog.selectors import resolve_year
from apps.warehouse.queries import featured_set, series_years

from .core import similarity
from .selectors import (
    DEFAULT_SERIES_COUNT,
    cached_result,
    default_series_keys,
    region_rows,
    resolve_many,
    series_columns,
)

# Сколько похожих субъектов показывать.
SIMILAR_LIMIT = 6

# Редакция сохранённого результата: меняется вместе с его составом.
RESULT_REVISION = 2


@dataclass(frozen=True, slots=True)
class SimilarResult:
    """Ближайшие территории по сочетанию ключевых показателей."""

    year: int
    columns: list[dict[str, Any]]
    items: list[dict[str, Any]] = field(default_factory=list)
    excluded: bool = False
    excluded_count: int = 0

    @property
    def is_available(self) -> bool:
        """Признак того, что похожие территории найдены."""
        return bool(self.items)


def similar_territories(code: str, *, limit: int = SIMILAR_LIMIT) -> SimilarResult | None:
    """
    Найти территории, ближайшие к указанной по набору ключевых показателей.

    ``None`` — расчёт невозможен; это не то же, что пустой перечень.
    """
    selected = resolve_many(default_series_keys(DEFAULT_SERIES_COUNT))
    if len(selected) < similarity.MIN_FEATURES:
        return None

    keys = [series.key for series in selected]
    year = resolve_year(None, _years(keys))
    if year is None:
        return None

    return cached_result(
        "similar-territories",
        {
            "series": ",".join(keys),
            "year": year,
            "code": code,
            "limit": limit,
            "revision": RESULT_REVISION,
        },
        lambda: _build(code, selected, year, limit),
    )


def _years(keys: list[str]) -> list[int]:
    """Годы, за которые есть значения хотя бы по одному из рядов."""
    bounds = series_years(keys)
    if not bounds:
        return []
    first = min(low for low, _ in bounds.values())
    last = max(high for _, high in bounds.values())
    return list(range(first, last + 1))


def _feature_label(column: dict[str, Any]) -> str:
    """Короткое название признака из основного набора, где оно есть."""
    featured = featured_set().by_key().get(column["key"])
    return featured.short_title if featured is not None else str(column["short"])


def _build(
    code: str,
    selected: list[Any],
    year: int,
    limit: int,
) -> SimilarResult | None:
    """Собрать расстояния по стандартизованной матрице признаков."""
    columns = series_columns(selected, year)
    rows = region_rows()
    territories = [{"code": row["code"], "name": row["name"]} for row in rows]

    matrix, kept, excluded = similarity.prepare(columns, territories)
    if matrix.shape[0] < similarity.MIN_TERRITORIES:
        return None

    if all(item["code"] != code for item in kept):
        # У субъекта нет какого-либо признака — результат, а не отказ расчёта.
        return SimilarResult(
            year=year,
            columns=columns,
            excluded=True,
            excluded_count=len(excluded),
        )

    found = similarity.neighbours(matrix, kept, code, limit=limit)
    slugs = dict(
        Territory.objects.filter(code__in=[item["code"] for item in found]).values_list(
            "code", "slug"
        )
    )
    items = [
        {
            **item,
            "slug": slugs.get(item["code"], ""),
            "apart_label": _feature_label(columns[item["apart"]]) if columns else "",
        }
        for item in found
    ]
    return SimilarResult(
        year=year,
        columns=columns,
        items=items,
        excluded_count=len(excluded),
    )
