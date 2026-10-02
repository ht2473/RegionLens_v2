"""Маршруты картографического раздела."""

from __future__ import annotations

from django.urls import path

from . import views

app_name = "maps"

urlpatterns = [
    path("map/", views.ChoroplethView.as_view(), name="choropleth"),
]
