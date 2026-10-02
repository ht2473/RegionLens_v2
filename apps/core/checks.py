"""Проверки развёртывания: кто узнает об ошибке 500."""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.checks import CheckMessage, Tags, Warning, register
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
