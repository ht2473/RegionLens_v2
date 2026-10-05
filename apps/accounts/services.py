"""Действия с учётными записями: регистрация, выгрузка своих данных, удаление."""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.translation import gettext

from apps.core.mail import deliver

from .models import User
from .roles import LastAdministratorError, is_last_administrator

# Имя отправителя в обезличенных обращениях удалённой учётной записи.
REMOVED_AUTHOR = "учётная запись удалена"


def register_user(
    *,
    email: str,
    password: str,
    full_name: str,
) -> User:
    """Создать учётную запись с ролью «Пользователь» и согласием на действующую редакцию."""
    from apps.core.documents import CONSENT_REVISION

    return User.objects.create_user(
        email=email,
        password=password,
        full_name=full_name.strip(),
        consent_at=timezone.now(),
        consent_revision=CONSENT_REVISION,
    )


def _moment(value: Any) -> str | None:
    """Момент времени в ISO 8601 с часовым поясом."""
    return value.isoformat(timespec="seconds") if value else None


def export_account(user: User) -> dict[str, Any]:
    """Всё, что хранится о пользователе: профиль, сохранённое, таблицы и обращения."""
    from apps.feedback.models import Ticket
    from apps.userdata.services import export_datasets
    from apps.workspace.selectors import favorites, saved_queries

    region = user.region
    return {
        "format": "regionlens-account/1",
        "exported_at": _moment(timezone.now()),
        "profile": {
            "email": user.email,
            "full_name": user.full_name,
            "region": {"code": region.code, "name": region.name_ru} if region else None,
            "roles": user.role_names,
            "registered_at": _moment(user.created_at),
            "last_login": _moment(user.last_login),
            "two_factor": user.two_factor_enabled,
            "consent": {
                "given_at": _moment(user.consent_at),
                "revision": user.consent_revision.isoformat() if user.consent_revision else None,
            },
        },
        "favorites": [
            {
                "kind": item.kind,
                "title": item.title,
                "note": item.note,
                "url": item.url,
                "saved_at": _moment(item.created_at),
            }
            for item in favorites(user)
        ],
        "saved_views": [
            {
                "title": query.title,
                "description": query.description,
                "page": query.target,
                "parameters": query.parameters,
                "url": query.url,
                "saved_at": _moment(query.created_at),
                "opened": query.opened_count,
            }
            for query in saved_queries(user)
        ],
        "own_data": export_datasets(user),
        "tickets": [
            {
                "number": ticket.pk,
                "topic": ticket.topic,
                "subject": ticket.subject,
                "body": ticket.body,
                "status": ticket.status,
                "sent_at": _moment(ticket.created_at),
                "answer": ticket.answer,
                "answered_at": _moment(ticket.answered_at),
                "consent_at": _moment(ticket.consent_at),
            }
            for ticket in Ticket.objects.filter(author=user).order_by("created_at")
        ],
    }


def delete_account(user: User) -> None:
    """
    Удалить учётную запись: сохранённое и таблицы уходят вместе с ней, обращения обезличиваются.

    Письмо-подтверждение уходит на адрес удалённой записи после удаления.
    """
    from apps.feedback.models import Ticket
    from apps.userdata.services import discard

    if is_last_administrator(user):
        raise LastAdministratorError
    email, name = user.email, user.full_name
    # Таблицы — с файлами на диске; записи о них ушли бы и каскадом, а файлы — нет.
    for dataset in user.datasets.all():
        discard(dataset, rebuild=False)
    with transaction.atomic():
        Ticket.objects.filter(author=user).update(
            author=None,
            contact_name=REMOVED_AUTHOR,
            contact_email="",
            ip_address=None,
            user_agent="",
        )
        user.delete()

    context = {"name": name, "email": email, "project_name": settings.PROJECT_NAME}
    subject = gettext("Учётная запись удалена")
    body = render_to_string("accounts/mail/account_deleted.txt", context)
    deliver(f"{settings.EMAIL_SUBJECT_PREFIX}{subject}", body, [email])
