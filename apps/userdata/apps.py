"""Конфигурация приложения ``userdata``."""

from __future__ import annotations

from django.apps import AppConfig


class UserdataConfig(AppConfig):
    """Свои данные: таблицы пользователей, их разбор и ряды."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.userdata"
    verbose_name = "Свои данные"
