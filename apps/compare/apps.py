"""Настройка приложения ``compare``."""

from __future__ import annotations

from django.apps import AppConfig


class CompareConfig(AppConfig):
    """Приложение сравнения территорий."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.compare"
    verbose_name = "Сравнение"
