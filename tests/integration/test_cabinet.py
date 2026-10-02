"""
Кабинет и безопасность учётной записи: вход с кодом, сеансы, смена почты, роли через
права, пределы частоты, выгрузка и удаление, «Мой регион», пометки и исчезнувшие ряды.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from django.contrib.auth import SESSION_KEY
from django.core.cache import cache
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts import totp
from apps.accounts.constants import (
    CODE_ATTEMPTS_PER_LOGIN,
    PASSWORD_CHECK_PER_USER,
    REGISTER_PER_IP,
    RESET_PER_EMAIL,
    ROLE_ADMIN,
    ROLE_EDITOR,
    ROLE_USER,
)
from apps.accounts.forms import EmailChangeForm
from apps.accounts.models import User
from apps.accounts.region import REGION_COOKIE
from apps.accounts.security import SECOND_FACTOR_SESSION_KEY, SETUP_SESSION_KEY
from apps.feedback.constants import TicketStatus
from apps.feedback.models import Ticket
from apps.workspace.models import Favorite, SavedQuery
from tests.conftest import TEST_PASSWORD, TEST_TOTP_SECRET, login_with_code

pytestmark = [pytest.mark.integration, pytest.mark.django_db]


@pytest.fixture(autouse=True)
def fresh_counters() -> None:
    """Счётчики пределов частоты — в кэше; между проверками он общий."""
    cache.clear()


def current_code(secret: str = TEST_TOTP_SECRET) -> str:
    """Код текущего шага."""
    return totp.code_at(secret, totp.current_step())


def logged_in(client: Client) -> bool:
    """Сеанс клиента принадлежит вошедшему пользователю."""
    return SESSION_KEY in client.session


# ---------------------------------------------------------------------------------------
# Вход с кодом
# ---------------------------------------------------------------------------------------


class TestTwoFactorLogin:
    """Второй шаг входа после верного пароля."""

    def test_password_alone_does_not_log_in(self, client: Client, make_user: Any) -> None:
        """С включённым входом по коду пароль ведёт ко второму шагу, а не в кабинет."""
        user = make_user(email="coded@example.com", two_factor=True)
        response = client.post(
            reverse("accounts:login"), {"username": user.email, "password": TEST_PASSWORD}
        )
        assert response.status_code == 302
        assert response.url == reverse("accounts:login-code")
        assert not logged_in(client)

    def test_code_completes_login_and_marks_session(self, client: Client, make_user: Any) -> None:
        """Верный код завершает вход, сеанс помечен как прошедший второй фактор."""
        user = make_user(email="coded@example.com", two_factor=True)
        client.post(reverse("accounts:login"), {"username": user.email, "password": TEST_PASSWORD})
        response = client.post(reverse("accounts:login-code"), {"code": current_code()})
        assert response.status_code == 302
        assert logged_in(client)
        assert client.session[SECOND_FACTOR_SESSION_KEY] is True

    def test_used_code_is_rejected(self, client: Client, make_user: Any) -> None:
        """Тот же код второй раз не принимается."""
        user = make_user(email="coded@example.com", two_factor=True)
        code = current_code()
        client.post(reverse("accounts:login"), {"username": user.email, "password": TEST_PASSWORD})
        client.post(reverse("accounts:login-code"), {"code": code})
        client.logout()

        client.post(reverse("accounts:login"), {"username": user.email, "password": TEST_PASSWORD})
        response = client.post(reverse("accounts:login-code"), {"code": code})
        assert response.status_code == 200
        assert not logged_in(client)

    def test_wrong_codes_send_back_to_password(self, client: Client, make_user: Any) -> None:
        """После пяти неверных кодов ввод пароля начинается заново."""
        user = make_user(email="coded@example.com", two_factor=True)
        client.post(reverse("accounts:login"), {"username": user.email, "password": TEST_PASSWORD})
        wrong = "000000" if current_code() != "000000" else "111111"
        for _ in range(CODE_ATTEMPTS_PER_LOGIN - 1):
            assert client.post(reverse("accounts:login-code"), {"code": wrong}).status_code == 200
        response = client.post(reverse("accounts:login-code"), {"code": wrong})
        assert response.url == reverse("accounts:login")
        assert client.get(reverse("accounts:login-code")).url == reverse("accounts:login")

    def test_wrong_code_counts_as_failed_login(
        self, client: Client, make_user: Any, settings: Any
    ) -> None:
        """Неверный код учитывает django-axes: подбор кода закрывает вход."""
        from axes.models import AccessAttempt

        settings.AXES_ENABLED = True
        user = make_user(email="coded@example.com", two_factor=True)
        client.post(reverse("accounts:login"), {"username": user.email, "password": TEST_PASSWORD})
        wrong = "000000" if current_code() != "000000" else "111111"
        client.post(reverse("accounts:login-code"), {"code": wrong})
        assert AccessAttempt.objects.get(username=user.email).failures_since_start == 1

    def test_recovery_code_works_once(self, client: Client, make_user: Any) -> None:
        """Резервный код входит один раз и тратится."""
        user = make_user(email="coded@example.com", two_factor=True)
        user.recovery_codes = [totp.recovery_hash("abcd-efgh")]
        user.save()
        client.post(reverse("accounts:login"), {"username": user.email, "password": TEST_PASSWORD})
        client.post(reverse("accounts:login-code"), {"code": "abcd-efgh"})
        assert logged_in(client)
        user.refresh_from_db()
        assert user.recovery_codes == []

    def test_code_step_needs_password_first(self, client: Client, db: None) -> None:
        """Без верного пароля второй шаг недоступен."""
        assert client.get(reverse("accounts:login-code")).url == reverse("accounts:login")

    def test_next_address_survives_the_code_step(self, client: Client, make_user: Any) -> None:
        """Адрес «куда шёл» переживает второй шаг."""
        user = make_user(email="coded@example.com", two_factor=True)
        target = reverse("workspace:saved")
        client.post(
            f"{reverse('accounts:login')}?next={target}",
            {"username": user.email, "password": TEST_PASSWORD, "next": target},
        )
        response = client.post(reverse("accounts:login-code"), {"code": current_code()})
        assert response.url == target


class TestTwoFactorSettings:
    """Включение, резервные коды и выключение в разделе «Безопасность»."""

    def test_setup_shows_qr_and_enables_with_first_code(
        self, member_client: Client, member: User
    ) -> None:
        """QR-код и ключ на странице; первый код включает вход и показывает резервные коды."""
        page = member_client.get(reverse("accounts:two-factor"))
        assert page.status_code == 200
        assert 'class="qr-code__ink"' in page.content.decode()
        secret = member_client.session[SETUP_SESSION_KEY]

        response = member_client.post(
            reverse("accounts:two-factor"), {"code": current_code(secret)}
        )
        assert response.status_code == 200
        codes = re.findall(r"<code>([a-z0-9]{4}-[a-z0-9]{4})</code>", response.content.decode())
        assert len(codes) == totp.RECOVERY_COUNT

        member.refresh_from_db()
        assert member.two_factor_enabled
        assert member.recovery_codes == [totp.recovery_hash(code) for code in codes]
        assert logged_in(member_client)

    def test_wrong_first_code_keeps_it_off(self, member_client: Client, member: User) -> None:
        """Неверный первый код ничего не включает."""
        member_client.get(reverse("accounts:two-factor"))
        secret = member_client.session[SETUP_SESSION_KEY]
        wrong = "000000" if current_code(secret) != "000000" else "111111"
        member_client.post(reverse("accounts:two-factor"), {"code": wrong})
        member.refresh_from_db()
        assert not member.two_factor_enabled

    def test_disable_needs_password_and_code(self, client: Client, make_user: Any) -> None:
        """Выключение — паролем и действующим кодом."""
        user = make_user(email="coded@example.com", two_factor=True)
        login_with_code(client, user)
        client.post(
            reverse("accounts:two-factor-disable"), {"password": "не тот", "code": current_code()}
        )
        user.refresh_from_db()
        assert user.two_factor_enabled

        client.post(
            reverse("accounts:two-factor-disable"),
            {"password": TEST_PASSWORD, "code": current_code()},
        )
        user.refresh_from_db()
        assert not user.two_factor_enabled

    def test_panel_role_cannot_disable(
        self, admin_panel_client: Client, administrator: User
    ) -> None:
        """С правами панели вход с кодом не выключается."""
        admin_panel_client.post(
            reverse("accounts:two-factor-disable"),
            {"password": TEST_PASSWORD, "code": current_code()},
        )
        administrator.refresh_from_db()
        assert administrator.two_factor_enabled

    def test_new_recovery_codes_replace_old(self, client: Client, make_user: Any) -> None:
        """Новый набор резервных кодов отменяет прежний."""
        user = make_user(email="coded@example.com", two_factor=True)
        user.recovery_codes = [totp.recovery_hash("abcd-efgh")]
        user.save()
        login_with_code(client, user)
        client.post(reverse("accounts:recovery-codes"), {"password": TEST_PASSWORD})
        user.refresh_from_db()
        assert len(user.recovery_codes) == totp.RECOVERY_COUNT
        assert totp.recovery_hash("abcd-efgh") not in user.recovery_codes


# ---------------------------------------------------------------------------------------
# Сеансы
# ---------------------------------------------------------------------------------------


class TestSessions:
    """Выход на других устройствах и завершение сеансов при смене пароля."""

    def test_sign_out_others_keeps_current(self, member: User) -> None:
        """Другой сеанс завершается, текущий остаётся."""
        here, there = Client(), Client()
        here.force_login(member)
        there.force_login(member)

        here.post(reverse("accounts:sign-out-others"))

        assert here.get(reverse("accounts:security")).status_code == 200
        assert there.get(reverse("accounts:security")).status_code == 302

    def test_password_change_ends_other_sessions(self, member: User) -> None:
        """Смена пароля завершает сеансы на других устройствах."""
        here, there = Client(), Client()
        here.force_login(member)
        there.force_login(member)
        here.post(
            reverse("accounts:password-change"),
            {
                "old_password": TEST_PASSWORD,
                "new_password1": "другой-пароль-98765",
                "new_password2": "другой-пароль-98765",
            },
        )
        assert here.get(reverse("accounts:security")).status_code == 200
        assert there.get(reverse("accounts:security")).status_code == 302


# ---------------------------------------------------------------------------------------
# Смена почты
# ---------------------------------------------------------------------------------------


def confirmation_link(mailoutbox: list[Any]) -> str:
    """Адрес подтверждения из последнего письма."""
    found = re.search(r"https?://\S+/email/confirm/\S+/", mailoutbox[-1].body)
    assert found, mailoutbox[-1].body
    return found.group(0).split("testserver", 1)[1]


class TestEmailChange:
    """Новый адрес — после ссылки из письма; прежний адрес извещается."""

    def test_link_goes_to_new_address(
        self, member_client: Client, member: User, mailoutbox: list[Any]
    ) -> None:
        """Ссылка уходит на новый адрес, адрес учётной записи пока прежний."""
        member_client.post(
            reverse("accounts:email-change"),
            {"new_email": "New@Example.com", "password": TEST_PASSWORD},
        )
        member.refresh_from_db()
        assert member.email == "member@example.com"
        assert member.pending_email == "new@example.com"
        assert mailoutbox[-1].to == ["new@example.com"]

    def test_wrong_password_is_refused(
        self, member_client: Client, member: User, mailoutbox: list[Any]
    ) -> None:
        """Без верного пароля письмо не уходит."""
        member_client.post(
            reverse("accounts:email-change"),
            {"new_email": "new@example.com", "password": "не тот"},
        )
        member.refresh_from_db()
        assert member.pending_email == ""
        assert mailoutbox == []

    def test_opening_link_does_not_change_address(
        self, member_client: Client, member: User, mailoutbox: list[Any]
    ) -> None:
        """Открытие ссылки только показывает кнопку: почтовые службы переходят по ссылкам сами."""
        member_client.post(
            reverse("accounts:email-change"),
            {"new_email": "new@example.com", "password": TEST_PASSWORD},
        )
        Client().get(confirmation_link(mailoutbox))
        member.refresh_from_db()
        assert member.email == "member@example.com"

    def test_confirmation_changes_address_and_notifies_old(
        self, member_client: Client, member: User, mailoutbox: list[Any]
    ) -> None:
        """Кнопка меняет адрес, прежний адрес получает уведомление, другие сеансы завершены."""
        other = Client()
        other.force_login(member)
        member_client.post(
            reverse("accounts:email-change"),
            {"new_email": "new@example.com", "password": TEST_PASSWORD},
        )
        link = confirmation_link(mailoutbox)
        member_client.post(link)

        member.refresh_from_db()
        assert member.email == "new@example.com"
        assert member.pending_email == ""
        assert mailoutbox[-1].to == ["member@example.com"]
        assert member_client.get(reverse("accounts:security")).status_code == 200
        assert other.get(reverse("accounts:security")).status_code == 302

        # Ссылка одноразовая.
        page = Client().post(link).content.decode()
        assert "Ссылка уже использована" in page

    def test_cancel_invalidates_link(
        self, member_client: Client, member: User, mailoutbox: list[Any]
    ) -> None:
        """Отмена смены — ссылка из письма больше не действует."""
        member_client.post(
            reverse("accounts:email-change"),
            {"new_email": "new@example.com", "password": TEST_PASSWORD},
        )
        link = confirmation_link(mailoutbox)
        member_client.post(reverse("accounts:email-cancel"))
        Client().post(link)
        member.refresh_from_db()
        assert member.email == "member@example.com"

    def test_taken_address_is_refused(
        self, member_client: Client, make_user: Any, mailoutbox: list[Any]
    ) -> None:
        """Занятый адрес не предлагается."""
        make_user(email="taken@example.com")
        response = member_client.post(
            reverse("accounts:email-change"),
            {"new_email": "Taken@example.com", "password": TEST_PASSWORD},
        )
        assert response.status_code == 200
        assert mailoutbox == []

    def test_forged_link_is_refused(self, client: Client, member: User) -> None:
        """Подделанная ссылка ничего не меняет."""
        response = client.post(
            reverse("accounts:email-confirm", kwargs={"token": "поддельная-ссылка"})
        )
        assert "Ссылка недействительна" in response.content.decode()


# ---------------------------------------------------------------------------------------
# Роли через права
# ---------------------------------------------------------------------------------------


class TestPanelPermissions:
    """Разделы панели по правам, второй фактор, назначение ролей."""

    def test_editor_sees_only_own_sections(self, client: Client, editor: User) -> None:
        """Редактор: содержимое, обращения и посещения открыты, пользователи и данные — 403."""
        login_with_code(client, editor)
        for name in (
            "dashboard:index",
            "dashboard:content",
            "dashboard:ticket-list",
            "dashboard:visits",
        ):
            assert client.get(reverse(name)).status_code == 200, name
        for name in ("dashboard:user-list", "dashboard:data", "dashboard:sources"):
            assert client.get(reverse(name)).status_code == 403, name

        tabs = {
            str(item["code"])
            for item in client.get(reverse("dashboard:index")).context["admin_sections"]
        }
        assert tabs == {"overview", "tickets", "content", "visits"}

    def test_panel_needs_second_factor(self, client: Client, make_user: Any) -> None:
        """С правом, но без входа по коду — страница его включения."""
        user = make_user(email="fresh-admin@example.com", roles=(ROLE_ADMIN,))
        client.force_login(user)
        response = client.get(reverse("dashboard:index"))
        assert response.url == reverse("accounts:security")

    def test_session_without_code_is_asked_for_it(
        self, client: Client, administrator: User
    ) -> None:
        """Вход с кодом включён, а сеанс без отметки — подтвердить кодом и вернуться."""
        client.force_login(administrator)
        response = client.get(reverse("dashboard:index"))
        assert response.url.startswith(reverse("accounts:login-code"))
        confirmed = client.post(response.url, {"code": current_code()})
        assert confirmed.url == reverse("dashboard:index")
        assert client.get(reverse("dashboard:index")).status_code == 200

    def test_roles_are_assigned_by_checkboxes(
        self, admin_panel_client: Client, member: User
    ) -> None:
        """Роли назначаются флажками; «Пользователь» остаётся у всех."""
        admin_panel_client.post(
            reverse("dashboard:user-role", kwargs={"public_id": member.public_id}),
            {"roles": [ROLE_EDITOR]},
        )
        member.refresh_from_db()
        assert member.role_names == [ROLE_EDITOR]
        assert member.groups.filter(name=ROLE_USER).exists()
        assert member.has_panel_access

    def test_last_administrator_is_protected(
        self, admin_panel_client: Client, administrator: User, make_user: Any
    ) -> None:
        """Второго администратора блокировать можно; оставшийся становится последним."""
        from apps.accounts.roles import is_last_administrator

        other = make_user(email="other-admin@example.com", roles=(ROLE_ADMIN,), two_factor=True)
        assert not is_last_administrator(administrator)
        admin_panel_client.post(
            reverse("dashboard:user-toggle", kwargs={"public_id": other.public_id})
        )
        other.refresh_from_db()
        assert other.is_active is False
        assert is_last_administrator(User.objects.get(pk=administrator.pk))

    def test_administrator_resets_lost_code(
        self, admin_panel_client: Client, make_user: Any
    ) -> None:
        """Администратор сбрасывает вход с кодом пользователю, потерявшему телефон."""
        user = make_user(email="lost@example.com", two_factor=True)
        admin_panel_client.post(
            reverse("dashboard:user-two-factor-reset", kwargs={"public_id": user.public_id})
        )
        user.refresh_from_db()
        assert not user.two_factor_enabled

    def test_superuser_is_administrator(self, db: None) -> None:
        """Созданный командой суперпользователь получает роль «Администратор»."""
        user = User.objects.create_superuser(
            email="root@example.com", password=TEST_PASSWORD, full_name="Корнев Корень"
        )
        assert user.role_names == [ROLE_ADMIN]
        assert "manage_users" in user.panel_permissions


# ---------------------------------------------------------------------------------------
# Пределы частоты и поле-ловушка
# ---------------------------------------------------------------------------------------


def registration(email: str, **extra: Any) -> dict[str, Any]:
    """Заполненная форма регистрации."""
    return {
        "email": email,
        "full_name": "Петров Пётр Петрович",
        "password1": TEST_PASSWORD,
        "password2": TEST_PASSWORD,
        "accepted_terms": "on",
        "accepted_consent": "on",
        "website": "",
        **extra,
    }


class TestRateLimits:
    """Регистрация и восстановление пароля — с пределами частоты."""

    def test_registration_is_limited_per_address(self, client: Client, db: None) -> None:
        """С одного адреса — не больше пяти регистраций в час."""
        for index in range(REGISTER_PER_IP.limit):
            Client().post(reverse("accounts:register"), registration(f"u{index}@example.com"))
        response = client.post(reverse("accounts:register"), registration("late@example.com"))
        assert response.status_code == 200
        assert "Слишком много регистраций" in response.content.decode()
        assert not User.objects.filter(email="late@example.com").exists()

    def test_honeypot_blocks_registration(self, client: Client, db: None) -> None:
        """Заполненное поле-ловушка — отказ."""
        client.post(
            reverse("accounts:register"),
            registration("bot@example.com", website="http://spam.example"),
        )
        assert not User.objects.filter(email="bot@example.com").exists()

    def test_reset_mails_are_limited_per_mailbox(self, member: User, mailoutbox: list[Any]) -> None:
        """Письма восстановления на один адрес ограничены, ответ страницы тот же."""
        for _ in range(RESET_PER_EMAIL.limit + 2):
            response = Client().post(reverse("accounts:password-reset"), {"email": member.email})
            assert response.url == reverse("accounts:password-reset-done")
        assert len(mailoutbox) == RESET_PER_EMAIL.limit

    def test_current_password_guessing_is_limited(
        self, member_client: Client, member: User, mailoutbox: list[Any]
    ) -> None:
        """После пяти неверных паролей в формах кабинета отказ получает и верный."""
        for index in range(PASSWORD_CHECK_PER_USER.limit):
            member_client.post(
                reverse("accounts:email-change"),
                {"new_email": f"guess{index}@example.com", "password": f"не тот {index}"},
            )
        response = member_client.post(
            reverse("accounts:email-change"),
            {"new_email": "new@example.com", "password": TEST_PASSWORD},
        )
        assert "Слишком много неверных паролей" in response.content.decode()
        member.refresh_from_db()
        assert member.pending_email == ""
        assert mailoutbox == []

    def test_password_forms_share_the_limit(self, member_client: Client, member: User) -> None:
        """Предел общий: неверные пароли в смене почты закрывают и смену пароля."""
        for index in range(PASSWORD_CHECK_PER_USER.limit):
            member_client.post(
                reverse("accounts:email-change"),
                {"new_email": "new@example.com", "password": f"не тот {index}"},
            )
        new_password = TEST_PASSWORD + "-новый"
        response = member_client.post(
            reverse("accounts:password-change"),
            {
                "old_password": TEST_PASSWORD,
                "new_password1": new_password,
                "new_password2": new_password,
            },
        )
        assert "Слишком много неверных паролей" in response.content.decode()
        member.refresh_from_db()
        assert member.check_password(TEST_PASSWORD)

    def test_right_passwords_do_not_count(self, member: User) -> None:
        """Верный пароль в счётчик не входит."""
        for index in range(PASSWORD_CHECK_PER_USER.limit + 1):
            form = EmailChangeForm(
                data={"new_email": f"new{index}@example.com", "password": TEST_PASSWORD},
                user=member,
            )
            assert form.is_valid(), form.errors


# ---------------------------------------------------------------------------------------
# Мои данные
# ---------------------------------------------------------------------------------------


class TestOwnData:
    """Выгрузка всего своего и удаление учётной записи."""

    def test_export_contains_everything(self, member_client: Client, member: User) -> None:
        """В файле — профиль, сохранённое и обращения; хэша пароля и ключей нет."""
        SavedQuery.objects.create(
            user=member, title="Мой вид", target="map", parameters={"year": "2023"}
        )
        Ticket.objects.create(
            author=member,
            contact_name=member.full_name,
            contact_email=member.email,
            subject="Мой вопрос",
            body="Текст обращения достаточной длины.",
        )
        response = member_client.get(reverse("accounts:data-export"))
        assert response["Content-Disposition"].startswith("attachment;")
        payload = json.loads(response.content)
        assert payload["profile"]["email"] == member.email
        assert payload["saved_views"][0]["title"] == "Мой вид"
        assert payload["tickets"][0]["subject"] == "Мой вопрос"
        text = response.content.decode()
        assert member.password not in text
        assert "totp" not in text

    def test_delete_needs_password(self, member_client: Client, member: User) -> None:
        """Без верного пароля учётная запись остаётся."""
        member_client.post(reverse("accounts:data"), {"password": "не тот", "confirm": "on"})
        assert User.objects.filter(pk=member.pk).exists()

    def test_delete_removes_account_and_anonymises_tickets(
        self, member_client: Client, member: User, mailoutbox: list[Any]
    ) -> None:
        """Удаление: сохранённое уходит, обращения обезличены, письмо на адрес записи."""
        SavedQuery.objects.create(user=member, title="Мой вид", target="map", parameters={})
        ticket = Ticket.objects.create(
            author=member,
            contact_name=member.full_name,
            contact_email=member.email,
            subject="Мой вопрос",
            body="Текст обращения достаточной длины.",
            ip_address="10.0.0.1",
        )
        response = member_client.post(
            reverse("accounts:data"), {"password": TEST_PASSWORD, "confirm": "on"}
        )
        assert response.url == reverse("core:home")
        assert not User.objects.filter(pk=member.pk).exists()
        assert not SavedQuery.objects.exists()
        ticket.refresh_from_db()
        assert (ticket.author, ticket.contact_email, ticket.ip_address) == (None, "", None)
        assert ticket.status == TicketStatus.NEW
        assert mailoutbox[-1].to == ["member@example.com"]
        assert not logged_in(member_client)

    def test_last_administrator_cannot_delete_self(
        self, admin_panel_client: Client, administrator: User
    ) -> None:
        """Последний администратор не удаляет себя."""
        admin_panel_client.post(
            reverse("accounts:data"), {"password": TEST_PASSWORD, "confirm": "on"}
        )
        assert User.objects.filter(pk=administrator.pk).exists()

    def test_panel_cannot_answer_deleted_author(
        self, admin_panel_client: Client, mailoutbox: list[Any]
    ) -> None:
        """Обращению удалённой учётной записи ответить некуда: форма не отправляет письмо."""
        ticket = Ticket.objects.create(
            contact_name="учётная запись удалена",
            contact_email="",
            subject="Вопрос",
            body="Текст обращения достаточной длины.",
        )
        page = admin_panel_client.get(ticket.manage_url).content.decode()
        assert "адреса для ответа нет" in page
        admin_panel_client.post(ticket.manage_url, {"action": "answer", "answer": "Ответ"})
        assert mailoutbox == []


# ---------------------------------------------------------------------------------------
# Мой регион
# ---------------------------------------------------------------------------------------


class TestMyRegion:
    """«Это мой регион»: cookie у гостя, поле у вошедшего, главная и шапка."""

    def test_guest_region_lives_in_cookie(self, client: Client, reference_seed: None) -> None:
        """Гость выбирает регион — cookie с кодом, шапка ведёт на паспорт."""
        response = client.post(reverse("accounts:my-region"), {"region": "RU-TOM"})
        assert response.cookies[REGION_COOKIE].value == "RU-TOM"
        page = client.get(reverse("catalog:territory-list")).content.decode()
        assert 'class="icon-button app-header__region' in page

    def test_unknown_code_is_ignored(self, client: Client, reference_seed: None) -> None:
        """Неизвестный код и округ своим регионом не становятся."""
        for code in ("RU-XXX", "RU-CFD", "RU"):
            response = client.post(reverse("accounts:my-region"), {"region": code})
            assert REGION_COOKIE not in response.cookies

    def test_member_region_is_saved_to_account(
        self, member_client: Client, member: User, reference_seed: None
    ) -> None:
        """У вошедшего регион сохраняется в учётной записи и сбрасывается."""
        member_client.post(reverse("accounts:my-region"), {"region": "RU-TOM"})
        member.refresh_from_db()
        assert member.region.code == "RU-TOM"

        member_client.post(reverse("accounts:my-region"), {"region": "RU-TOM", "clear": "1"})
        member.refresh_from_db()
        assert member.region is None

    def test_login_adopts_guest_region(
        self, client: Client, member: User, reference_seed: None
    ) -> None:
        """Регион, выбранный до входа, переходит в пустую учётную запись."""
        client.post(reverse("accounts:my-region"), {"region": "RU-TOM"})
        client.post(
            reverse("accounts:login"), {"username": member.email, "password": TEST_PASSWORD}
        )
        member.refresh_from_db()
        assert member.region.code == "RU-TOM"

    def test_htmx_returns_pressed_button(self, client: Client, reference_seed: None) -> None:
        """Из паспорта и карточки — новая кнопка вместо перехода."""
        response = client.post(
            reverse("accounts:my-region"), {"region": "RU-TOM"}, headers={"HX-Request": "true"}
        )
        assert response.status_code == 200
        assert 'aria-pressed="true"' in response.content.decode()

    def test_home_and_passport_show_region(self, client: Client, warehouse: Any) -> None:
        """Главная начинается с региона, паспорт показывает кнопку нажатой."""
        from apps.catalog.models import Territory

        territory = Territory.objects.comparable().first()
        client.cookies[REGION_COOKIE] = territory.code
        home = client.get(reverse("core:home")).content.decode()
        assert 'class="home-region"' in home
        assert territory.name in home

        passport = client.get(
            reverse("catalog:territory-detail", kwargs={"slug": territory.slug})
        ).content.decode()
        assert 'aria-pressed="true"' in passport

    def test_dynamics_start_with_own_region(self, client: Client, warehouse: Any) -> None:
        """Динамика без выбора территорий ставит свой регион первым."""
        from apps.catalog.models import Territory

        territory = Territory.objects.comparable().order_by("-code").first()
        client.cookies[REGION_COOKIE] = territory.code
        response = client.get(reverse("compare:index"))
        assert response.context["compared"][0].code == territory.code

    def test_profile_sets_region(
        self, member_client: Client, member: User, reference_seed: None
    ) -> None:
        """Регион выбирается и в профиле."""
        from apps.catalog.models import Territory

        territory = Territory.objects.get(code="RU-TOM")
        response = member_client.post(
            reverse("accounts:profile"),
            {
                "full_name": member.full_name,
                "region": territory.pk,
            },
        )
        member.refresh_from_db()
        assert member.region == territory
        assert response.cookies[REGION_COOKIE].value == "RU-TOM"


# ---------------------------------------------------------------------------------------
# Обзор и «Сохранённое»
# ---------------------------------------------------------------------------------------


class TestOverviewAndSaved:
    """Первая страница кабинета, пометки к отметкам и исчезнувшие ряды."""

    def test_overview_shows_region_and_recent_views(
        self, member_client: Client, member: User, warehouse: Any
    ) -> None:
        """Обзор: мой регион с «Главным» и последние открытые виды."""
        from apps.catalog.models import Territory

        member.region = Territory.objects.comparable().first()
        member.save()
        opened = SavedQuery.objects.create(
            user=member,
            title="Открытый вид",
            target="map",
            parameters={},
            last_opened_at=timezone.now(),
        )
        response = member_client.get(reverse("accounts:dashboard"))
        assert response.context["region"]["ready"] is True
        assert response.context["recent_views"] == [opened]
        assert member.region.name in response.content.decode()

    def test_updates_border_holds_for_the_session(
        self, member_client: Client, member: User
    ) -> None:
        """Граница «нового» запоминается на сеанс, отметка визита сдвигается один раз."""
        member_client.get(reverse("accounts:dashboard"))
        member.refresh_from_db()
        first_seen = member.updates_seen_at
        assert first_seen is not None
        since = member_client.get(reverse("accounts:dashboard")).context["updates_since"]
        member.refresh_from_db()
        assert member.updates_seen_at == first_seen
        assert since <= first_seen

    def test_note_on_mark(self, member_client: Client, member: User, reference_seed: None) -> None:
        """Пометка к отметке сохраняется и видна в перечне; чужую не изменить."""
        from apps.catalog.models import Territory

        mark = Favorite.objects.create(user=member, territory=Territory.objects.get(code="RU-TOM"))
        member_client.post(
            reverse("workspace:favorite-note", kwargs={"pk": mark.pk}),
            {"note": "сравнить с соседями"},
        )
        mark.refresh_from_db()
        assert mark.note == "сравнить с соседями"
        assert (
            "сравнить с соседями" in member_client.get(reverse("workspace:saved")).content.decode()
        )

        other = User.objects.create_user(
            email="x@example.com", password=TEST_PASSWORD, full_name="Икс"
        )
        foreign = Favorite.objects.create(user=other, territory=mark.territory)
        response = member_client.post(
            reverse("workspace:favorite-note", kwargs={"pk": foreign.pk}), {"note": "чужое"}
        )
        assert response.status_code == 404

    def test_saved_regions_open_compare_and_map(
        self, member_client: Client, member: User, reference_seed: None
    ) -> None:
        """Сохранённые регионы открывают сравнение и карту с ними; с одним — только карту."""
        from apps.catalog.models import Territory

        Favorite.objects.create(user=member, territory=Territory.objects.get(code="RU-TOM"))
        page = member_client.get(reverse("workspace:saved")).content.decode()
        assert f"{reverse('maps:choropleth')}?territory=RU-TOM" in page
        assert reverse("compare:index") + "?" not in page

        Favorite.objects.create(user=member, territory=Territory.objects.get(code="RU-NVS"))
        page = member_client.get(reverse("workspace:saved")).content.decode()
        assert "territory=RU-TOM" in page
        assert "territory=RU-NVS" in page
        assert reverse("compare:index") + "?territory=" in page

    def test_vanished_series_is_reported(
        self, member_client: Client, member: User, warehouse: Any
    ) -> None:
        """Вид с рядом, которого нет в складе, помечен в перечне и предупреждает при открытии."""
        query = SavedQuery.objects.create(
            user=member,
            title="Со старым рядом",
            target="map",
            parameters={"series": "NO_SUCH_SERIES:00", "year": "2020"},
        )
        page = member_client.get(reverse("workspace:saved")).content.decode()
        assert "Нет в данных: NO_SUCH_SERIES:00" in page

        response = member_client.get(
            reverse("workspace:query-open", kwargs={"public_id": query.public_id}), follow=True
        )
        warnings = [str(message) for message in response.context["messages"]]
        assert any("NO_SUCH_SERIES:00" in text for text in warnings)

    def test_present_series_is_not_reported(
        self, member_client: Client, member: User, warehouse: Any
    ) -> None:
        """Ряд, который есть в складе, исчезнувшим не считается."""
        from apps.catalog.models import Series

        key = Series.objects.first().key
        SavedQuery.objects.create(
            user=member, title="Живой вид", target="map", parameters={"series": key}
        )
        assert "Нет в данных" not in member_client.get(reverse("workspace:saved")).content.decode()


def test_cabinet_pages_open(member_client: Client, reference_seed: None) -> None:
    """Все разделы кабинета открываются."""
    for name in (
        "accounts:dashboard",
        "workspace:saved",
        "feedback:mine",
        "accounts:profile",
        "accounts:security",
        "accounts:two-factor",
        "accounts:data",
        "accounts:password-change",
    ):
        assert member_client.get(reverse(name)).status_code == 200, name


def test_menu_offers_panel_only_with_rights(
    member_client: Client, client: Client, editor: User
) -> None:
    """Пункт «Панель управления» — только при праве панели."""
    assert (
        "Панель управления" not in member_client.get(reverse("accounts:profile")).content.decode()
    )
    login_with_code(client, editor)
    assert "Панель управления" in client.get(reverse("accounts:profile")).content.decode()


def test_session_hash_depends_on_version(member: User) -> None:
    """Смена версии сеансов меняет хэш сеанса."""
    before = member.get_session_auth_hash()
    member.session_version += 1
    assert member.get_session_auth_hash() != before


def test_reset_command_turns_code_off(make_user: Any) -> None:
    """Команда стенда выключает вход с кодом и завершает сеансы учётной записи."""
    from django.core.management import call_command

    user = make_user(email="lost-admin@example.com", roles=(ROLE_ADMIN,), two_factor=True)
    version = user.session_version
    call_command("reset_two_factor", "Lost-Admin@example.com")
    user.refresh_from_db()
    assert not user.two_factor_enabled
    assert user.session_version == version + 1
