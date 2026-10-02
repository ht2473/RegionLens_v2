"""Маршруты распределения и таблицы; карта, рейтинг и динамика — в своих приложениях."""

from __future__ import annotations

from django.urls import path

from . import views

app_name = "surface"

urlpatterns = [
    path("distribution/", views.DistributionView.as_view(), name="distribution"),
    path("table/", views.TableView.as_view(), name="table"),
]
