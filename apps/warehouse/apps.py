"""Конфигурация приложения ``warehouse``."""

from __future__ import annotations

from django.apps import AppConfig


class WarehouseConfig(AppConfig):
    """ETL-конвейер и аналитический склад DuckDB."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.warehouse"
    verbose_name = "Аналитический склад"
