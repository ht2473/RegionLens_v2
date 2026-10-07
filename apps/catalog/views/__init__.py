"""Представления каталога: перечни, набор данных, страница показателя, паспорт территории."""

from __future__ import annotations

from .catalog import (
    SeriesListView,
    TerritoryListView,
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
    "series_options_markup",
    "series_picker",
]
