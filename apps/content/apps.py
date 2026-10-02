"""Конфигурация приложения ``content``."""

from __future__ import annotations

from django.apps import AppConfig


class ContentConfig(AppConfig):
    """Содержимое, редактируемое в панели управления: методика и глоссарий."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.content"
    verbose_name = "Содержимое"
