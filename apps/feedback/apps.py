"""Конфигурация приложения ``feedback``."""

from __future__ import annotations

from django.apps import AppConfig


class FeedbackConfig(AppConfig):
    """Обращения посетителей и ответы на них."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.feedback"
    verbose_name = "Обратная связь"
