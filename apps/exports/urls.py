"""Маршруты выгрузки документов."""

from __future__ import annotations

from django.urls import path

from . import views

app_name = "exports"

urlpatterns = [
    path("documents/", views.DocumentView.as_view(), name="document"),
    path("data.csv", views.DataDownloadView.as_view(), name="data-csv"),
]
