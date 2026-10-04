"""Конфигурация приложения ``userdata``."""

from __future__ import annotations

from django.apps import AppConfig


class UserdataConfig(AppConfig):
    """Свои данные: таблицы пользователей, их разбор и ряды."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.userdata"
    verbose_name = "Свои данные"

    def ready(self) -> None:
        """Ключи «u:» слоя рядов ведут в файлы наборов с проверкой доступа."""
        from apps.warehouse import routing

        from . import scope, signals  # noqa: F401 — перенос наборов гостя при входе

        routing.register(scope.resolve)
