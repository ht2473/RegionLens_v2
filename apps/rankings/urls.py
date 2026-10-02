"""Маршруты раздела рейтингов."""

from __future__ import annotations

from django.urls import path

from . import views

app_name = "rankings"

urlpatterns = [
    path("rankings/", views.RankingView.as_view(), name="index"),
]
