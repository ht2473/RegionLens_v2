"""Маршруты раздела сравнения территорий."""

from __future__ import annotations

from django.urls import path

from . import views

app_name = "compare"

urlpatterns = [
    path("compare/", views.CompareView.as_view(), name="index"),
]
