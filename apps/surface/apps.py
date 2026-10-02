"""Настройка приложения ``surface``."""

from __future__ import annotations

from django.apps import AppConfig


class SurfaceConfig(AppConfig):
    """Приложение рабочей поверхности."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.surface"
    verbose_name = "Рабочая поверхность"
