"""Настройка приложения ``rankings``."""

from __future__ import annotations

from django.apps import AppConfig


class RankingsConfig(AppConfig):
    """Приложение рейтингов."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.rankings"
    verbose_name = "Рейтинги"
