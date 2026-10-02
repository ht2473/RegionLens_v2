"""Служебные маршруты без языкового префикса: проверки состояния, карта сайта, robots.txt."""

from __future__ import annotations

from django.contrib.sitemaps.views import sitemap
from django.urls import path
from django.views.decorators.cache import cache_page
from django.views.generic import TemplateView

from . import views
from .sitemaps import SITEMAPS

urlpatterns = [
    path("healthz", views.healthz, name="healthz"),
    path("readyz", views.readyz, name="readyz"),
    # Около трёх тысяч адресов из каталога; робот запрашивает карту раз в сутки.
    path(
        "sitemap.xml",
        cache_page(6 * 3600)(sitemap),
        {"sitemaps": SITEMAPS},
        name="django.contrib.sitemaps.views.sitemap",
    ),
    path(
        "robots.txt",
        TemplateView.as_view(template_name="core/robots.txt", content_type="text/plain"),
        name="robots",
    ),
]
