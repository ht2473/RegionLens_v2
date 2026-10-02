"""Представления каталога: перечни, набор данных, страница показателя, паспорт территории."""

from __future__ import annotations

from .catalog import (
    SeriesListView,
    TerritoryListView,
    quick_search,
    series_options_markup,
    series_picker,
)
from .dataset import DatasetView
from .series import SeriesDetailView
from .sources import DataSourcesView
from .territory import TerritoryCardView, TerritoryDetailView

__all__ = [
    "DataSourcesView",
    "DatasetView",
    "SeriesDetailView",
    "SeriesListView",
    "TerritoryCardView",
    "TerritoryDetailView",
    "TerritoryListView",
    "quick_search",
    "series_options_markup",
    "series_picker",
]
