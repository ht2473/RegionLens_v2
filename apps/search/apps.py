"""Конфигурация приложения ``search``."""

from __future__ import annotations

from django.apps import AppConfig


class SearchConfig(AppConfig):
    """Поиск вопросом: разбор запроса по правилам, ответы-шаблоны, журнал запросов без ответа."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.search"
    verbose_name = "Поиск"
