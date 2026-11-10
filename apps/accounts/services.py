"""Действия с учётными записями: регистрация, выгрузка своих данных, удаление."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.core import signing
from django.db import transaction
from django.http import HttpRequest
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import salted_hmac
from django.utils.translation import gettext

from apps.core.mail import deliver

from .models import User
from .roles import LastAdministratorError, is_last_administrator

# Подпись ссылки подтверждения регистрации; срок — как у ссылки восстановления пароля.
REGISTRATION_SALT = "apps.accounts.registration"

# Имя отправителя в обезличенных обращениях удалённой учётной записи.
REMOVED_AUTHOR = "учётная запись удалена"


def register_user(
    *,
    email: str,
    password: str,
    full_name: str,
) -> User:
    """
    Создать учётную запись с ролью «Пользователь» и согласием на действующую редакцию.

    Адрес почты ещё не подтверждён: войти в запись можно после перехода по ссылке из письма.
    """
    from apps.core.documents import CONSENT_REVISION

    return User.objects.create_user(
        email=email,
        password=password,
        full_name=full_name.strip(),
        consent_at=timezone.now(),
        consent_revision=CONSENT_REVISION,
        email_confirmed_at=None,
    )


class RegistrationError(Exception):
    """Ссылка подтверждения регистрации недействительна или устарела."""


def _password_mark(user: User) -> str:
    """Отпечаток хеша пароля: ссылка действует, пока пароль записи тот же, что при её выдаче."""
    return salted_hmac(REGISTRATION_SALT, user.password, algorithm="sha256").hexdigest()[:16]


def request_registration(request: HttpRequest, *, email: str, password: str, full_name: str) -> str:
    """
    Принять заявку на регистрацию и отправить письмо на адрес; вернуть причину отказа почты.

    Письмо уходит в любом случае, а страница отвечает одинаково: по ответу нельзя узнать,
    зарегистрирован ли адрес. Свободный адрес получает ссылку подтверждения; запись, которую
    так и не подтвердили, переписывается новой заявкой (её мог оставить не владелец адреса);
    подтверждённой записи приходит напоминание, что она уже есть.
    """
    from apps.core.documents import CONSENT_REVISION

    existing = User.objects.filter(email__iexact=email).first()
    if existing is not None and existing.email_confirmed_at is not None:
        # Хеш считается и здесь: иначе занятый адрес отвечал бы заметно быстрее свободного.
        make_password(password)
        return _send(request, existing, "registration_exists", gettext("Вы уже зарегистрированы"))
    if existing is None:
        user = register_user(email=email, password=password, full_name=full_name)
    else:
        user = existing
        user.set_password(password)
        user.full_name = full_name.strip()
        user.consent_at = timezone.now()
        user.consent_revision = CONSENT_REVISION
        user.save(
            update_fields=["password", "full_name", "consent_at", "consent_revision", "updated_at"]
        )
    token = signing.dumps(
        {"user": user.pk, "email": user.email, "mark": _password_mark(user)},
        salt=REGISTRATION_SALT,
    )
    link = request.build_absolute_uri(reverse("accounts:register-confirm", kwargs={"token": token}))
    return _send(
        request, user, "registration_confirm", gettext("Подтвердите регистрацию"), link=link
    )


def _send(request: HttpRequest, user: User, template: str, subject: str, **extra: Any) -> str:
    """Письмо регистрации на адрес записи; вернуть причину отказа почты или пустую строку."""
    context = {
        "user": user,
        "project_name": settings.PROJECT_NAME,
        "days": settings.PASSWORD_RESET_TIMEOUT // (24 * 3600),
        "login_url": request.build_absolute_uri(reverse("accounts:login")),
        "reset_url": request.build_absolute_uri(reverse("accounts:password-reset")),
        **extra,
    }
    body = render_to_string(f"accounts/mail/{template}.txt", context)
    return deliver(f"{settings.EMAIL_SUBJECT_PREFIX}{subject}", body, [user.email])


def confirm_registration(token: str) -> User:
    """Подтвердить адрес почты по ссылке из письма; после этого в запись можно войти."""
    try:
        payload: dict[str, Any] = signing.loads(
            token, salt=REGISTRATION_SALT, max_age=settings.PASSWORD_RESET_TIMEOUT
        )
    except signing.BadSignature as error:
        raise RegistrationError(gettext("Ссылка недействительна или устарела")) from error
    user = User.objects.filter(
        pk=int(payload.get("user") or 0), email=str(payload.get("email", "")), is_active=True
    ).first()
    if user is None or (
        user.email_confirmed_at is None and payload.get("mark") != _password_mark(user)
    ):
        # Запись удалена по сроку или переписана новой заявкой: прежняя ссылка не действует.
        raise RegistrationError(gettext("Ссылка недействительна или устарела"))
    if user.email_confirmed_at is None:
        user.email_confirmed_at = timezone.now()
        user.save(update_fields=["email_confirmed_at", "updated_at"])
    return user


def prune_unconfirmed() -> int:
    """Удалить записи, адрес которых не подтвердили за срок действия ссылки; вернуть их число."""
    edge = timezone.now() - timedelta(seconds=settings.PASSWORD_RESET_TIMEOUT)
    removed, _kinds = User.objects.filter(
        email_confirmed_at__isnull=True, created_at__lt=edge
    ).delete()
    return removed


def _moment(value: Any) -> str | None:
    """Момент времени в ISO 8601 с часовым поясом."""
    return value.isoformat(timespec="seconds") if value else None


def export_account(user: User) -> dict[str, Any]:
    """Всё, что хранится о пользователе: профиль, сохранённое, таблицы и обращения."""
    from apps.feedback.models import Ticket
    from apps.userdata.services import export_datasets, export_groups, export_studies
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
        "studies": export_studies(user),
        "region_groups": export_groups(user),
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
