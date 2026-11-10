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
        """Роли — после прав приложения auth; регион гостя и отметка браузера — при входе."""
        from django.contrib.auth.signals import user_logged_in

        from .region import adopt_guest_region
        from .roles import ensure_roles
        from .security import remember_device

        post_migrate.connect(ensure_roles, sender=self, dispatch_uid="accounts-ensure-roles")
        user_logged_in.connect(adopt_guest_region, dispatch_uid="accounts-adopt-region")
        user_logged_in.connect(remember_device, dispatch_uid="accounts-remember-device")
