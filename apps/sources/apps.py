"""Конфигурация приложения ``sources``."""

from __future__ import annotations

from django.apps import AppConfig


class SourcesConfig(AppConfig):
    """Сбор выпусков внешних источников."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.sources"
    verbose_name = "Источники данных"
