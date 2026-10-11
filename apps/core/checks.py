"""Проверки развёртывания: кто узнает об ошибке 500, ключи шифрования секретов и копий."""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.checks import CheckMessage, Error, Tags, Warning, register
from django.core.exceptions import ValidationError
from django.core.validators import validate_email


@register(Tags.security, deploy=True)
def check_error_reports(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:  # noqa: ARG001
    """Адресаты письма об ошибке 500 заданы и похожи на адреса, либо подключён Sentry."""
    messages: list[CheckMessage] = []
    for address in settings.ADMINS:
        try:
            validate_email(address)
        except ValidationError:
            messages.append(
                Warning(
                    f"В DJANGO_ADMINS не адрес почты: {address!r}",
                    hint="Адреса перечисляются через запятую, без имён.",
                    id="core.W002",
                )
            )
    if not settings.ADMINS and not getattr(settings, "SENTRY_DSN", ""):
        messages.append(
            Warning(
                "Об ошибках 500 никто не узнает: не заданы ни DJANGO_ADMINS, ни SENTRY_DSN.",
                hint="Укажите в .env адрес администратора: DJANGO_ADMINS=admin@example.ru",
                id="core.W001",
            )
        )
    return messages


@register(Tags.security, deploy=True)
def check_encryption_keys(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:  # noqa: ARG001
    """Свои ключи для секретов в базе; ключи копий заданы и согласованы между собой."""
    from apps.core import backup_crypto, secretbox

    messages: list[CheckMessage] = []
    if not settings.FIELD_ENCRYPTION_KEYS:
        messages.append(
            Warning(
                "Ключ кодов входа шифруется ключом, выведенным из SECRET_KEY.",
                hint="Задайте FIELD_ENCRYPTION_KEYS (manage.py crypto_keys --field): иначе смена "
                "SECRET_KEY сделает ключи кодов входа нечитаемыми.",
                id="core.W003",
            )
        )
    else:
        try:
            secretbox.current_key_id()
        except secretbox.SecretBoxError as error:
            messages.append(Error(f"FIELD_ENCRYPTION_KEYS: {error}", id="core.E003"))
    if not backup_crypto.configured():
        messages.append(
            Warning(
                "Резервные копии не шифруются и не подписываются.",
                hint="Задайте BACKUP_SIGNING_KEY, BACKUP_VERIFY_KEY и BACKUP_RECIPIENT_KEY "
                "(manage.py crypto_keys --backup ФАЙЛ).",
                id="core.W004",
            )
        )
        return messages
    try:
        signer = backup_crypto.signing_key().public_key()
        backup_crypto.recipient_key()
        verifier = backup_crypto.verify_key()
    except (backup_crypto.BackupCryptoError, ValueError) as error:
        messages.append(Error(f"Ключи резервных копий: {error}", id="core.E004"))
        return messages
    if backup_crypto.fingerprint(signer) != backup_crypto.fingerprint(verifier):
        messages.append(
            Error(
                "BACKUP_VERIFY_KEY не подходит к BACKUP_SIGNING_KEY: копии не пройдут проверку.",
                id="core.E005",
            )
        )
    return messages
