"""Письма администраторам о том, что требует внимания: сбои сбора и сборки, новая версия набора."""

from __future__ import annotations

import hashlib
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.urls import reverse
from django.utils import translation

from .mail import deliver

# Одно и то же письмо повторяется не чаще: пока причина не устранена, это напоминание.
REPEAT_AFTER = timedelta(days=7)

FOOTER = "\n\n— Письмо отправлено сайтом RegionLens автоматически."


def notify(key: str, subject: str, body: str, *, repeat_after: timedelta = REPEAT_AFTER) -> bool:
    """
    Написать адресатам ``DJANGO_ADMINS``; ``True`` — письмо ушло.

    ``key`` — признак события: письмо с тем же признаком не повторяется раньше
    ``repeat_after``. Не ушедшее письмо будет отправлено при следующем таком событии.
    """
    recipients = list(settings.ADMINS)
    if not recipients:
        return False
    marker = "alert:" + hashlib.sha256(key.encode()).hexdigest()[:32]
    timeout = int(repeat_after.total_seconds())
    # Запись есть — письмо уже было; кэш недоступен — лучше лишнее письмо, чем ни одного.
    if not (cache.add(marker, 1, timeout=timeout) or cache.get(marker) is None):
        return False
    if deliver(settings.EMAIL_SUBJECT_PREFIX + subject, body + FOOTER, recipients):
        cache.delete(marker)
        return False
    return True


def panel_address(name: str, *args: object) -> str:
    """Адрес страницы панели без домена: письмо уходит из фонового процесса, без запроса."""
    with translation.override(settings.LANGUAGE_CODE):
        return reverse(name, args=args)
