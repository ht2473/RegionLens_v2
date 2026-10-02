"""Настройка приложения ``maps``."""

from __future__ import annotations

from django.apps import AppConfig


class MapsConfig(AppConfig):
    """Приложение картографических представлений."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.maps"
    verbose_name = "Карты"
