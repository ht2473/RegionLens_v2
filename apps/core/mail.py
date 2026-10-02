"""Отправка писем сразу, без очереди, с отметкой исхода для панели управления."""

from __future__ import annotations

import logging
import smtplib

from django.core.mail import send_mail

from .models import ServiceBeat

logger = logging.getLogger(__name__)

# Отказы почтового узла: сеть, протокол, таймаут.
MAIL_ERRORS: tuple[type[Exception], ...] = (smtplib.SMTPException, OSError)

# Длина сохраняемой причины отказа.
ERROR_LIMIT = 500


def deliver(subject: str, body: str, recipients: list[str]) -> str:
    """Отправить письмо и отметить исход; вернуть причину отказа или пустую строку."""
    try:
        send_mail(subject, body, None, recipients)
    except MAIL_ERRORS as error:
        reason = describe(error)
        logger.warning("Письмо не ушло: %s", reason)
        ServiceBeat.record(ServiceBeat.Service.MAIL, ok=False, error=reason)
        return reason
    ServiceBeat.record(ServiceBeat.Service.MAIL, ok=True)
    return ""


def describe(error: BaseException) -> str:
    """Причина отказа для показа администратору."""
    return f"{type(error).__name__}: {error}"[:ERROR_LIMIT]
