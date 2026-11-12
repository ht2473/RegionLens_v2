"""Отправка писем сразу, без очереди, с отметкой исхода для панели управления."""

from __future__ import annotations

import logging
import smtplib
import threading

from django.conf import settings
from django.core.mail import send_mail
from django.db import connections

from .models import ServiceBeat
from .throttle import allow_key

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


def deliver_later(subject: str, body: str, recipients: list[str]) -> None:
    """
    Отправить письмо вне запроса, отдельным потоком; исход отмечается так же.

    Для писем, которые уходят не каждому: время ответа страницы не должно зависеть от того,
    было ли письмо, — иначе по нему видно, зарегистрирован ли адрес. ``MAIL_INLINE``
    (проверки) отправляет сразу.
    """
    if getattr(settings, "MAIL_INLINE", False):
        deliver(subject, body, recipients)
        return

    def send() -> None:
        try:
            deliver(subject, body, recipients)
        finally:
            # Поток заводит своё соединение с базой для отметки исхода.
            connections.close_all()

    threading.Thread(target=send, name="mail", daemon=True).start()


def public_mail_allowed() -> bool:
    """
    Учесть письмо, которое вызвал посетитель без входа (регистрация, восстановление пароля),
    и вернуть, укладывается ли оно в общий предел сайта за час.

    Пределы по адресу и по ящику не спасают от потока со многих адресов на многие ящики,
    а почтовая служба за такой поток закрывает отправителя — вместе с письмами об ошибках
    и восстановлением пароля для всех.
    """
    return allow_key("public-mail", "site", limit=settings.PUBLIC_MAIL_PER_HOUR, window=3600)


def describe(error: BaseException) -> str:
    """Причина отказа для показа администратору."""
    return f"{type(error).__name__}: {error}"[:ERROR_LIMIT]
