"""Конфигурация приложения ``api``."""

from __future__ import annotations

from django.apps import AppConfig


class ApiConfig(AppConfig):
    """Программный интерфейс REST."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.api"
    verbose_name = "Программный интерфейс"
