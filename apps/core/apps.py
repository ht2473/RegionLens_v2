"""Конфигурация приложения ``core``."""

from __future__ import annotations

from django.apps import AppConfig


class CoreConfig(AppConfig):
    """Общие компоненты, используемые всеми приложениями проекта."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"
    verbose_name = "Ядро системы"

    def ready(self) -> None:
        """Подключить обращение ``__ifind`` к текстовым полям и проверки развёртывания."""
        from apps.core import checks  # noqa: F401 — регистрация проверок
        from apps.core.search import register_lookups

        register_lookups()
