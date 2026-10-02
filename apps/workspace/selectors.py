"""Чтение сохранённых выборок и избранного пользователя."""

from __future__ import annotations

from collections.abc import Iterable

from django.db.models import QuerySet

from apps.accounts.models import User
from apps.catalog.models import Series
from apps.warehouse.duckdb_client import WarehouseNotBuiltError
from apps.warehouse.queries import series_summary_map

from .models import Favorite, SavedQuery


def saved_queries(user: User) -> QuerySet[SavedQuery]:
    """Сохранённые выборки пользователя."""
    return SavedQuery.objects.filter(user=user)


def favorites(user: User) -> QuerySet[Favorite]:
    """Избранное пользователя вместе со связанными объектами."""
    return Favorite.objects.filter(user=user).select_related(
        "indicator",
        "series",
        "series__indicator",
        "territory",
    )


def vanished_series(queries: Iterable[SavedQuery]) -> dict[int, list[str]]:
    """
    Ряды видов, которых больше нет в складе, — по виду: названия из справочника или ключи.

    Без склада ничего не помечается: отсутствие склада — не исчезновение ряда.
    """
    by_query = {query.pk: query.series_keys for query in queries}
    wanted = sorted({key for keys in by_query.values() for key in keys})
    if not wanted:
        return {}
    try:
        present = set(series_summary_map(wanted))
    except WarehouseNotBuiltError:
        return {}
    missing = set(wanted) - present
    if not missing:
        return {}
    titles = {
        series.key: series.full_title
        for series in Series.objects.filter(key__in=missing).select_related("indicator")
    }
    return {
        pk: [titles.get(key) or key for key in keys if key in missing]
        for pk, keys in by_query.items()
        if any(key in missing for key in keys)
    }
