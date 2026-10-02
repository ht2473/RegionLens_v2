"""Конфигурация приложения ``exports``."""

from __future__ import annotations

from django.apps import AppConfig


class ExportsConfig(AppConfig):
    """Подготовка и выдача отчётов пользователям."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.exports"
    verbose_name = "Отчёты и выгрузки"
