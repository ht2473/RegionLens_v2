"""Конфигурация приложения ``catalog``."""

from __future__ import annotations

from django.apps import AppConfig


class CatalogConfig(AppConfig):
    """Справочники: территории, показатели, ряды, источники, методические примечания."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.catalog"
    verbose_name = "Справочники"
