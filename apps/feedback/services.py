"""Приём обращений с извещением администраторов, ответ письмом, повторная отправка, закрытие."""

from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.http import HttpRequest
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext, override

from apps.core.client_ip import client_ip
from apps.core.documents import CONSENT_REVISION
from apps.core.mail import deliver

from .constants import RATE_LIMIT_PER_HOUR, DeliveryStatus, TicketStatus, TicketTopic
from .models import Ticket

logger = logging.getLogger(__name__)

# Наибольшая длина сохраняемой строки клиента.
USER_AGENT_LIMIT = 255


class RateLimitExceededError(Exception):
    """С одного адреса поступает слишком много обращений; у формы — своё сообщение."""


def check_rate_limit(request: HttpRequest) -> None:
    """Проверить предел обращений с адреса за час; вошедшие пользователи не ограничиваются."""
    if request.user.is_authenticated:
        return

    address = client_ip(request)
    if not address:
        return

    since = timezone.now() - timedelta(hours=1)
    recent = Ticket.objects.filter(ip_address=address, created_at__gte=since).count()
    if recent >= RATE_LIMIT_PER_HOUR:
        raise RateLimitExceededError(
            "С этого адреса за последний час отправлено слишком много обращений. "
            "Попробуйте позже или напишите на адрес поддержки."
        )


@transaction.atomic
def create_ticket(
    request: HttpRequest,
    *,
    contact_name: str,
    contact_email: str,
    topic: str,
    subject: str,
    body: str,
    page_url: str = "",
) -> Ticket:
    """Принять обращение; у вошедшего имя и адрес — из учётной записи, а не из формы."""
    author = request.user if request.user.is_authenticated else None
    if author is not None:
        contact_name = author.full_name
        contact_email = author.email

    ticket = Ticket.objects.create(
        author=author,
        contact_name=contact_name.strip(),
        contact_email=contact_email.strip().lower(),
        topic=topic,
        subject=subject.strip(),
        body=body.strip(),
        page_url=page_url[:500],
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent", "")[:USER_AGENT_LIMIT],
        # Форма не принимается без согласия: оно дано на действующую редакцию.
        consent_at=timezone.now(),
        consent_revision=CONSENT_REVISION,
    )

    notify_administrators(request, ticket, topic=str(TicketTopic(topic).label))
    return ticket


def notify_administrators(request: HttpRequest, ticket: Ticket, *, topic: str) -> None:
    """Сообщить тем, кто разбирает обращения, письмом; обращение уже сохранено."""
    from apps.accounts.constants import MANAGE_TICKETS
    from apps.accounts.roles import with_permission

    recipients = list(with_permission(MANAGE_TICKETS).values_list("email", flat=True))
    if not recipients:
        return
    body = (
        f"Новое обращение: {ticket.subject}\n"
        f"Тема: {topic}\n\n"
        f"{ticket.body}\n\n"
        f"Ответить: {request.build_absolute_uri(ticket.manage_url)}"
    )
    deliver(f"RegionLens: новое обращение — {ticket.subject}", body, recipients)


def answer_ticket(request: HttpRequest, ticket: Ticket, *, answer: str) -> Ticket:
    """Сохранить ответ и отправить его письмом; отказ почты текст не теряет."""
    ticket.answer = answer.strip()
    ticket.answered_by = request.user if request.user.is_authenticated else None
    ticket.answered_at = timezone.now()
    ticket.status = TicketStatus.ANSWERED
    ticket.save(update_fields=["answer", "answered_by", "answered_at", "status", "updated_at"])

    return send_answer(request, ticket)


def send_answer(request: HttpRequest, ticket: Ticket) -> Ticket:
    """Отправить письмо с ответом — впервые или ещё раз — и записать исход в обращение."""
    context = {
        "ticket": ticket,
        "answer": ticket.answer,
        "project_name": settings.PROJECT_NAME,
        "feedback_url": request.build_absolute_uri(reverse("feedback:create")),
    }
    # Язык страницы автора не сохраняется, поэтому письмо — на языке по умолчанию.
    with override(settings.LANGUAGE_CODE):
        subject = gettext("Ответ на обращение № %(number)s: %(subject)s") % {
            "number": ticket.pk,
            "subject": ticket.subject,
        }
        body = render_to_string("feedback/mail/answer.txt", context)

    error = deliver(f"{settings.EMAIL_SUBJECT_PREFIX}{subject}", body, [ticket.contact_email])
    Ticket.objects.filter(pk=ticket.pk).update(
        answer_attempts=F("answer_attempts") + 1,
        answer_delivery=DeliveryStatus.FAILED if error else DeliveryStatus.SENT,
        answer_error=error,
        answer_sent_at=ticket.answer_sent_at if error else timezone.now(),
    )
    ticket.refresh_from_db()
    return ticket


def close_ticket(ticket: Ticket) -> Ticket:
    """
    Закрыть обращение без ответа, не уведомляя автора.

    Ответить можно и позже: форма ответа в карточке остаётся.
    """
    ticket.status = TicketStatus.CLOSED
    ticket.save(update_fields=["status", "updated_at"])
    return ticket
