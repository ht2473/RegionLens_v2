"""Маршруты поиска."""

from __future__ import annotations

from django.urls import path

from apps.search import views

app_name = "search"

urlpatterns = [
    path("search/", views.SearchView.as_view(), name="results"),
    path("search/suggest/", views.suggest, name="suggest"),
]
