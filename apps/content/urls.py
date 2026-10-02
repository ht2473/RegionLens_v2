"""Маршруты методики и глоссария; адрес термина — по слагу."""

from __future__ import annotations

from django.urls import path

from . import views

app_name = "content"

urlpatterns = [
    path("methodology/", views.MethodologyView.as_view(), name="methodology"),
    path("glossary/", views.GlossaryView.as_view(), name="glossary"),
    path("glossary/<slug:slug>/", views.TermRedirectView.as_view(), name="term"),
]
