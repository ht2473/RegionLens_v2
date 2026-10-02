"""Документы сайта о пользователях: редакции и сроки хранения, которые в них названы."""

from __future__ import annotations

from datetime import date

from django.conf import settings
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import SafeString
from django.utils.translation import gettext

# Даты редакций: меняются вместе с содержанием документа.
TERMS_REVISION = date(2026, 9, 26)
PRIVACY_REVISION = date(2026, 9, 26)
CONSENT_REVISION = date(2026, 9, 26)

# Сроки хранения из политики; очистку выполняет команда prune_personal_data.
# Журнал веб-сервера — как roll_keep_for в docker/Caddyfile.
ACCESS_LOG_DAYS = 30
# Адрес IP и браузер отправителя обращения нужны только против рассылок.
TICKET_TRACE_DAYS = 30
# Имя и адрес обращения без учётной записи — после ответа или закрытия.
TICKET_CONTACT_DAYS = 365
# Уничтожение после отзыва согласия (ч. 5 ст. 21 152-ФЗ).
DESTRUCTION_DAYS = 30


def backup_retention_days() -> int | None:
    """Через сколько суток удалённое исчезает из всех копий; ``None`` — внешние копии бессрочны."""
    days = int(settings.BACKUP_RETENTION_DAYS)
    if settings.BACKUP_REMOTE:
        if not settings.BACKUP_REMOTE_RETENTION_DAYS:
            return None
        days = max(days, int(settings.BACKUP_REMOTE_RETENTION_DAYS))
    return days


def consent_label() -> SafeString:
    """Подпись флажка согласия со ссылкой на его текст; ссылка — в новой вкладке."""
    consent = format_html(
        '<a href="{}" target="_blank" rel="noopener">{}</a>',
        reverse("core:consent"),
        gettext("согласие на обработку персональных данных"),
    )
    return format_html(gettext("Даю {consent}"), consent=consent)
