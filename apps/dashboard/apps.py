"""Конфигурация приложения ``dashboard``."""

from __future__ import annotations

from django.apps import AppConfig


class DashboardConfig(AppConfig):
    """Панель управления системой."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.dashboard"
    verbose_name = "Панель управления"
