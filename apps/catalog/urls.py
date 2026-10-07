"""Маршруты каталога."""

from __future__ import annotations

from django.urls import path
from django.views.generic import RedirectView

from . import views

app_name = "catalog"

urlpatterns = [
    path("indicators/", views.SeriesListView.as_view(), name="indicator-list"),
    path("indicators/<slug:slug>/", views.SeriesDetailView.as_view(), name="series-detail"),
    path("regions/", views.TerritoryListView.as_view(), name="territory-list"),
    path("regions/<slug:slug>/", views.TerritoryDetailView.as_view(), name="territory-detail"),
    path("regions/<slug:slug>/card/", views.TerritoryCardView.as_view(), name="territory-card"),
    path("datasets/", views.DatasetView.as_view(), name="dataset"),
    path("datasets/sources/", views.DataSourcesView.as_view(), name="sources"),
    # Итог проверок набора — на странице о наборе данных.
    path(
        "datasets/quality/",
        RedirectView.as_view(pattern_name="catalog:dataset", permanent=True),
    ),
    path("picker/series/", views.series_picker, name="series-picker"),
    path("picker/series/options/", views.series_options_markup, name="series-options"),
]
