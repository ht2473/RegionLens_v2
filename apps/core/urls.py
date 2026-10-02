"""Пользовательские маршруты приложения ``core`` (обслуживаются с языковым префиксом)."""

from __future__ import annotations

from django.urls import path
from django.views.generic import RedirectView

from . import views

app_name = "core"

urlpatterns = [
    path("", views.HomeView.as_view(), name="home"),
    path("showcase/frames/", views.home_frames, name="home-frames"),
    path("about/", views.AboutView.as_view(), name="about"),
    path("terms/", views.TermsView.as_view(), name="terms"),
    path("privacy/", views.PrivacyView.as_view(), name="privacy"),
    path("consent/", views.ConsentView.as_view(), name="consent"),
    # Справка входит в страницу «О проекте».
    path("help/", RedirectView.as_view(pattern_name="core:about", permanent=True)),
]
