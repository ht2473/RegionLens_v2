"""Конфигурация приложения ``attest``."""

from __future__ import annotations

from django.apps import AppConfig


class AttestConfig(AppConfig):
    """Подпись выдаваемых документов, их проверка и подписанный журнал архива выпусков."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.attest"
    verbose_name = "Подлинность документов"
