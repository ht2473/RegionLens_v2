"""Конфигурация приложения ``accounts``."""

from __future__ import annotations

from django.apps import AppConfig
from django.db.models.signals import post_migrate


class AccountsConfig(AppConfig):
    """Пользователи, роли и разграничение доступа."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"
    verbose_name = "Пользователи и доступ"

    def ready(self) -> None:
        """Роли — после прав, которые заводит сигнал приложения auth; регион гостя — при входе."""
        from django.contrib.auth.signals import user_logged_in

        from .region import adopt_guest_region
        from .roles import ensure_roles

        post_migrate.connect(ensure_roles, sender=self, dispatch_uid="accounts-ensure-roles")
        user_logged_in.connect(adopt_guest_region, dispatch_uid="accounts-adopt-region")
