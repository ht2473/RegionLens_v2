"""
Удаление персональных данных по истечении сроков, названных в политике обработки.

На стенде запускается таймером хоста (deploy/systemd/regionlens-prune.timer) раз в сутки.
"""

from __future__ import annotations

from datetime import timedelta
from importlib import import_module
from typing import Any

from axes.models import AccessAttempt
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.core.documents import TICKET_CONTACT_DAYS, TICKET_TRACE_DAYS
from apps.feedback.constants import TicketStatus
from apps.feedback.models import Ticket
from apps.userdata.services import prune as prune_tables

# Имя отправителя обращения, у которого истёк срок хранения контактов.
EXPIRED_CONTACT = "удалено по сроку хранения"


class Command(BaseCommand):
    """
    Удалить сеансы, попытки входа, сведения об отправителях обращений и таблицы гостей,
    чей срок истёк, и осиротевшие файлы таблиц.
    """

    help = "Удаляет персональные данные, срок хранения которых истёк"

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ARG002 — контракт команды
        """Выполнить очистку и напечатать, сколько записей затронуто."""
        now = timezone.now()

        import_module(settings.SESSION_ENGINE).SessionStore.clear_expired()

        attempts, _kinds = AccessAttempt.objects.filter(
            attempt_time__lt=now - timedelta(hours=settings.AXES_COOLOFF_TIME)
        ).delete()

        traces = (
            Ticket.objects.filter(created_at__lt=now - timedelta(days=TICKET_TRACE_DAYS))
            .exclude(ip_address=None, user_agent="")
            .update(ip_address=None, user_agent="")
        )

        # Обращения из учётной записи обезличиваются при её удалении.
        contacts = (
            Ticket.objects.filter(
                author__isnull=True,
                status__in=[TicketStatus.ANSWERED, TicketStatus.CLOSED],
                updated_at__lt=now - timedelta(days=TICKET_CONTACT_DAYS),
            )
            .exclude(contact_email="")
            .update(contact_name=EXPIRED_CONTACT, contact_email="")
        )

        tables, orphans = prune_tables()

        self.stdout.write(
            self.style.SUCCESS(
                f"Истёкшие сеансы удалены; попыток входа: {attempts}; обращений без адреса IP: "
                f"{traces}; обращений без имени и адреса: {contacts}; таблиц гостей: {tables}; "
                f"осиротевших каталогов таблиц: {orphans}"
            )
        )
