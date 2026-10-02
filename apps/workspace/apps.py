"""Конфигурация приложения ``workspace``."""

from __future__ import annotations

from django.apps import AppConfig


class WorkspaceConfig(AppConfig):
    """Личный кабинет пользователя: сохранённые регионы, показатели и виды экрана."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.workspace"
    verbose_name = "Личный кабинет"
