"""Сеансы, двухфакторный вход и смена адреса почты."""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.contrib.auth import update_session_auth_hash
from django.core import signing
from django.db import transaction
from django.db.models import F
from django.http import HttpRequest
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext

from apps.core.mail import deliver

from . import totp
from .models import User

# Сеанс прошёл второй фактор: без отметки панель управления закрыта.
SECOND_FACTOR_SESSION_KEY = "accounts:second-factor"

# Ключ для подтверждения, настраиваемый при включении входа с кодом.
SETUP_SESSION_KEY = "accounts:pending-totp"

# Ссылка подтверждения нового адреса живёт столько же, сколько ссылка восстановления пароля.
EMAIL_CHANGE_SALT = "apps.accounts.email-change"


# ---------------------------------------------------------------------------------------
# Сеансы
# ---------------------------------------------------------------------------------------


def end_other_sessions(request: HttpRequest | None, user: User) -> None:
    """Завершить все сеансы учётной записи, кроме текущего (если он её)."""
    User.objects.filter(pk=user.pk).update(session_version=F("session_version") + 1)
    user.refresh_from_db(fields=["session_version"])
    if request is not None and request.user.is_authenticated and request.user.pk == user.pk:
        update_session_auth_hash(request, user)


def mark_second_factor(request: HttpRequest) -> None:
    """Отметить, что сеанс прошёл проверку кодом."""
    request.session[SECOND_FACTOR_SESSION_KEY] = True


def passed_second_factor(request: HttpRequest) -> bool:
    """Сеанс прошёл проверку кодом."""
    return bool(request.session.get(SECOND_FACTOR_SESSION_KEY))


# ---------------------------------------------------------------------------------------
# Двухфакторный вход
# ---------------------------------------------------------------------------------------


def pending_secret(request: HttpRequest) -> str:
    """Ключ, который пользователь сейчас добавляет в приложение; создаётся при первом показе."""
    secret = request.session.get(SETUP_SESSION_KEY)
    if not secret:
        secret = totp.new_secret()
        request.session[SETUP_SESSION_KEY] = secret
    return str(secret)


@transaction.atomic
def enable_two_factor(request: HttpRequest, user: User, secret: str, step: int) -> list[str]:
    """Включить вход с кодом; вернуть резервные коды для показа один раз."""
    codes = totp.new_recovery_codes()
    user.totp_secret = secret
    user.totp_enabled_at = timezone.now()
    user.totp_last_step = step
    user.recovery_codes = [totp.recovery_hash(code) for code in codes]
    user.save(
        update_fields=[
            "totp_secret",
            "totp_enabled_at",
            "totp_last_step",
            "recovery_codes",
            "updated_at",
        ]
    )
    request.session.pop(SETUP_SESSION_KEY, None)
    end_other_sessions(request, user)
    mark_second_factor(request)
    return codes


def disable_two_factor(request: HttpRequest | None, user: User) -> None:
    """Выключить вход с кодом и завершить прочие сеансы."""
    user.totp_secret = ""
    user.totp_enabled_at = None
    user.totp_last_step = 0
    user.recovery_codes = []
    user.save(
        update_fields=[
            "totp_secret",
            "totp_enabled_at",
            "totp_last_step",
            "recovery_codes",
            "updated_at",
        ]
    )
    end_other_sessions(request, user)
    if request is not None:
        request.session.pop(SECOND_FACTOR_SESSION_KEY, None)


def renew_recovery_codes(user: User) -> list[str]:
    """Выдать новый набор резервных кодов вместо прежнего."""
    codes = totp.new_recovery_codes()
    user.recovery_codes = [totp.recovery_hash(code) for code in codes]
    user.save(update_fields=["recovery_codes", "updated_at"])
    return codes


def verify_code(user: User, code: str) -> bool:
    """Проверить код приложения или резервный код; принятый код второй раз не пройдёт."""
    step = totp.matching_step(user.totp_secret, code, after=user.totp_last_step)
    if step is not None:
        # Условие в запросе: из двух одновременных входов с одним кодом пройдёт один.
        updated = User.objects.filter(pk=user.pk, totp_last_step__lt=step).update(
            totp_last_step=step
        )
        user.totp_last_step = step
        return updated == 1

    digest = totp.recovery_hash(code)
    with transaction.atomic():
        fresh = User.objects.select_for_update().get(pk=user.pk)
        if digest not in fresh.recovery_codes:
            return False
        fresh.recovery_codes = [item for item in fresh.recovery_codes if item != digest]
        fresh.save(update_fields=["recovery_codes"])
    user.recovery_codes = fresh.recovery_codes
    return True


# ---------------------------------------------------------------------------------------
# Смена адреса почты
# ---------------------------------------------------------------------------------------


def request_email_change(request: HttpRequest, user: User, new_email: str) -> str:
    """Запомнить новый адрес и отправить на него ссылку подтверждения; вернуть причину отказа."""
    user.pending_email = new_email
    user.save(update_fields=["pending_email", "updated_at"])
    token = signing.dumps(
        {"user": user.pk, "old": user.email, "new": new_email}, salt=EMAIL_CHANGE_SALT
    )
    link = request.build_absolute_uri(reverse("accounts:email-confirm", kwargs={"token": token}))
    context = {
        "user": user,
        "new_email": new_email,
        "link": link,
        "days": settings.PASSWORD_RESET_TIMEOUT // (24 * 3600),
        "project_name": settings.PROJECT_NAME,
    }
    subject = gettext("Подтвердите новый адрес почты")
    body = render_to_string("accounts/mail/email_change_confirm.txt", context)
    return deliver(f"{settings.EMAIL_SUBJECT_PREFIX}{subject}", body, [new_email])


def cancel_email_change(user: User) -> None:
    """Отказаться от ожидающей смены адреса: прежняя ссылка перестаёт действовать."""
    user.pending_email = ""
    user.save(update_fields=["pending_email", "updated_at"])


class EmailChangeError(Exception):
    """Ссылка подтверждения недействительна, устарела или адрес уже занят."""


def confirm_email_change(request: HttpRequest, token: str) -> User:
    """Сменить адрес по ссылке из письма, завершить другие сеансы и известить прежний адрес."""
    try:
        payload: dict[str, Any] = signing.loads(
            token, salt=EMAIL_CHANGE_SALT, max_age=settings.PASSWORD_RESET_TIMEOUT
        )
    except signing.BadSignature as error:
        raise EmailChangeError(gettext("Ссылка недействительна или устарела")) from error

    # Пользователь запроса вычисляется лениво: после смены версии сеансов его сеанс
    # уже не прошёл бы проверку хэша и был бы сброшен.
    _ = request.user.is_authenticated

    with transaction.atomic():
        user = User.objects.select_for_update().filter(pk=int(payload.get("user") or 0)).first()
        new_email = str(payload.get("new", ""))
        if (
            user is None
            or user.email != payload.get("old")
            or user.pending_email.lower() != new_email.lower()
        ):
            raise EmailChangeError(gettext("Ссылка уже использована или отменена"))
        if User.objects.filter(email__iexact=new_email).exclude(pk=user.pk).exists():
            raise EmailChangeError(gettext("Этот адрес уже занят другой учётной записью"))
        old_email = user.email
        user.email = new_email.lower()
        user.pending_email = ""
        user.save(update_fields=["email", "pending_email", "updated_at"])

    end_other_sessions(request, user)
    context = {
        "user": user,
        "old_email": old_email,
        "new_email": user.email,
        "feedback_url": request.build_absolute_uri(reverse("feedback:create")),
        "project_name": settings.PROJECT_NAME,
    }
    subject = gettext("Адрес почты учётной записи изменён")
    body = render_to_string("accounts/mail/email_changed_notice.txt", context)
    deliver(f"{settings.EMAIL_SUBJECT_PREFIX}{subject}", body, [old_email])
    return user
