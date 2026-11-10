"""
Защита учётных записей: подтверждение почты при регистрации, предел кодов в «Настройках»,
ключ настройки в сеансе, знакомый браузер против блокировки чужой записи, пределы частоты
при отказе хранилища счётчиков.
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

import pytest
from django.contrib.auth import SESSION_KEY
from django.contrib.sessions.models import Session
from django.core.management import call_command
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts import security, totp
from apps.accounts.constants import CODE_CHECK_PER_USER
from apps.accounts.models import User
from apps.accounts.services import prune_unconfirmed
from apps.core import throttle
from apps.core.cache import ResilientValkeyCache
from tests.conftest import TEST_PASSWORD, TEST_TOTP_SECRET, login_with_code

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

OTHER_PASSWORD = "совсем-другой-пароль-2026"


def registration(email: str, password: str = TEST_PASSWORD) -> dict[str, str]:
    """Поля формы регистрации."""
    return {
        "email": email,
        "full_name": "Петров Пётр Петрович",
        "password1": password,
        "password2": password,
        "accepted_terms": "on",
        "accepted_consent": "on",
        "website": "",
    }


def link_from(message: Any) -> str:
    """Путь первой ссылки письма."""
    found = re.search(r"https?://[^/\s]+(/\S+)", message.body)
    assert found is not None
    return found.group(1)


def sign_in(client: Client, email: str, password: str = TEST_PASSWORD) -> Any:
    """Отправить форму входа."""
    return client.post(reverse("accounts:login"), {"username": email, "password": password})


class TestRegistrationConfirmation:
    """Запись получает только владелец адреса: вход — после перехода по ссылке из письма."""

    def test_account_opens_only_after_the_link(self, client: Client, mailoutbox: list[Any]) -> None:
        """После формы — письмо и закрытая запись; после ссылки — обычный вход."""
        response = client.post(reverse("accounts:register"), registration("new@example.com"))
        assert response.url == reverse("accounts:register-done")
        assert SESSION_KEY not in client.session
        user = User.objects.get(email="new@example.com")
        assert user.email_confirmed_at is None
        assert [message.to for message in mailoutbox] == [["new@example.com"]]

        refused = sign_in(client, "new@example.com")
        assert refused.status_code == 200
        assert "не подтверждён" in refused.content.decode()
        assert SESSION_KEY not in client.session

        link = link_from(mailoutbox[0])
        # Открытие ссылки ничего не меняет: почтовые службы открывают ссылки сами.
        assert client.get(link).status_code == 200
        user.refresh_from_db()
        assert user.email_confirmed_at is None

        confirmed = client.post(link)
        assert confirmed.url == reverse("accounts:login")
        user.refresh_from_db()
        assert user.email_confirmed_at is not None
        assert SESSION_KEY not in client.session

        assert sign_in(client, "new@example.com").status_code == 302
        assert SESSION_KEY in client.session

    def test_taken_address_answers_the_same(
        self, client: Client, member: User, mailoutbox: list[Any]
    ) -> None:
        """Занятый адрес: тот же ответ страницы, письмо-напоминание, пароль прежний."""
        response = client.post(
            reverse("accounts:register"), registration(member.email, OTHER_PASSWORD)
        )
        assert response.url == reverse("accounts:register-done")
        assert User.objects.count() == 1
        member.refresh_from_db()
        assert member.check_password(TEST_PASSWORD)
        assert len(mailoutbox) == 1
        assert "уже есть" in mailoutbox[0].body
        assert "/register/confirm/" not in mailoutbox[0].body

    def test_unconfirmed_account_is_taken_over_by_a_new_request(
        self, client: Client, mailoutbox: list[Any]
    ) -> None:
        """
        Заявку на чужой адрес переписывает заявка владельца: пароль новый, прежняя ссылка
        не действует.
        """
        client.post(reverse("accounts:register"), registration("owner@example.com", OTHER_PASSWORD))
        client.post(reverse("accounts:register"), registration("owner@example.com"))
        assert User.objects.count() == 1
        stale, fresh = (link_from(message) for message in mailoutbox)

        assert "недействительна" in client.post(stale).content.decode()
        user = User.objects.get()
        assert user.email_confirmed_at is None

        client.post(fresh)
        user.refresh_from_db()
        assert user.email_confirmed_at is not None
        assert user.check_password(TEST_PASSWORD)

    def test_used_link_does_not_sign_in(self, client: Client, mailoutbox: list[Any]) -> None:
        """Повторное открытие ссылки ведёт ко входу и сеанс не открывает."""
        client.post(reverse("accounts:register"), registration("new@example.com"))
        link = link_from(mailoutbox[0])
        client.post(link)
        again = Client().post(link)
        assert again.url == reverse("accounts:login")

    def test_broken_link_is_refused(self, client: Client) -> None:
        """Подделанная ссылка — отказ."""
        url = reverse("accounts:register-confirm", kwargs={"token": "eyJ1c2VyIjoxfQ:1abc:xyz"})
        assert "недействительна" in client.post(url).content.decode()

    def test_password_reset_confirms_the_address(
        self, client: Client, mailoutbox: list[Any]
    ) -> None:
        """Ссылка восстановления пароля приходит на тот же адрес и подтверждает его."""
        client.post(reverse("accounts:register"), registration("new@example.com", OTHER_PASSWORD))
        client.post(reverse("accounts:password-reset"), {"email": "new@example.com"})
        page = client.get(link_from(mailoutbox[-1]), follow=True)
        form_url = page.redirect_chain[-1][0]
        client.post(form_url, {"new_password1": TEST_PASSWORD, "new_password2": TEST_PASSWORD})
        user = User.objects.get(email="new@example.com")
        assert user.email_confirmed_at is not None
        assert sign_in(client, "new@example.com").status_code == 302

    def test_unconfirmed_accounts_are_pruned(
        self, client: Client, member: User, settings: Any
    ) -> None:
        """Запись без подтверждения удаляется, когда ссылка истекла; подтверждённая остаётся."""
        client.post(reverse("accounts:register"), registration("ghost@example.com"))
        assert prune_unconfirmed() == 0
        old = timezone.now() - timedelta(seconds=settings.PASSWORD_RESET_TIMEOUT + 60)
        User.objects.filter(email="ghost@example.com").update(created_at=old)
        User.objects.filter(pk=member.pk).update(created_at=old)
        call_command("prune_personal_data", verbosity=0)
        assert list(User.objects.values_list("email", flat=True)) == [member.email]


class TestCodeLimitInSettings:
    """Выключение входа с кодом: код не перебирается из открытого сеанса."""

    def test_wrong_codes_are_limited(self, client: Client, make_user: Any) -> None:
        """После предела неверных кодов отказ получает и верный."""
        user = make_user(email="coded@example.com", two_factor=True)
        login_with_code(client, user)
        url = reverse("accounts:two-factor-disable")
        centre = totp.current_step()
        valid = {totp.code_at(TEST_TOTP_SECRET, step) for step in range(centre - 2, centre + 3)}
        wrong = [f"{number:06d}" for number in range(50) if f"{number:06d}" not in valid]
        for code in wrong[: CODE_CHECK_PER_USER.limit]:
            page = client.post(url, {"password": TEST_PASSWORD, "code": code})
            assert "Код не подошёл" in page.content.decode()

        good = totp.code_at(TEST_TOTP_SECRET, totp.current_step())
        page = client.post(url, {"password": TEST_PASSWORD, "code": good})
        assert "Слишком много неверных кодов" in page.content.decode()
        user.refresh_from_db()
        assert user.two_factor_enabled


class TestSetupSecretInSession:
    """Ключ, который пользователь настраивает, в сеансе лежит зашифрованным."""

    def test_session_record_holds_no_plain_secret(
        self, member_client: Client, member: User
    ) -> None:
        """В записи сеанса — запись secretbox; ключ читается только с ключом сервера."""
        page = member_client.get(reverse("accounts:two-factor")).content.decode()
        stored = Session.objects.get(pk=member_client.session.session_key).get_decoded()
        sealed = stored[security.SETUP_SESSION_KEY]
        assert sealed.startswith("v1:")
        shown = re.search(r"((?:[A-Z2-7]{4} ){7}[A-Z2-7]{4})", page)
        assert shown is not None
        assert shown.group(1).replace(" ", "") not in str(stored)

    def test_record_of_another_account_is_not_reused(
        self, member_client: Client, member: User, make_user: Any
    ) -> None:
        """Запись привязана к учётной записи: перенесённая в чужой сеанс, она заменяется."""
        member_client.get(reverse("accounts:two-factor"))
        sealed = member_client.session[security.SETUP_SESSION_KEY]
        other = make_user(email="other@example.com")
        client = Client()
        client.force_login(other)
        session = client.session
        session[security.SETUP_SESSION_KEY] = sealed
        session.save()
        client.get(reverse("accounts:two-factor"))
        assert client.session[security.SETUP_SESSION_KEY] != sealed


class TestKnownBrowser:
    """Блокировка по одной почте не закрывает вход владельцу из его браузера."""

    @pytest.fixture(autouse=True)
    def stand_lockout(self, settings: Any) -> None:
        """Учёт попыток — как на стенде: по адресу и по почте в отдельности."""
        settings.AXES_ENABLED = True
        settings.AXES_FAILURE_LIMIT = 5
        settings.LOGIN_LOCKOUT_PARAMETERS = ["ip_address", "username"]

    @staticmethod
    def attack(email: str) -> None:
        """Пять неверных паролей с пяти разных адресов."""
        for index in range(5):
            sign_in(Client(REMOTE_ADDR=f"198.51.100.{index + 1}"), email, "не-тот-пароль")

    def test_stranger_is_locked_out(self, member: User) -> None:
        """Незнакомый браузер после чужих попыток получает отказ и с верным паролем."""
        self.attack(member.email)
        stranger = Client(REMOTE_ADDR="203.0.113.50")
        assert sign_in(stranger, member.email).status_code == 429

    def test_owner_browser_still_signs_in(self, member: User) -> None:
        """Браузер, из которого владелец уже входил, чужие попытки не останавливают."""
        owner = Client(REMOTE_ADDR="203.0.113.50")
        assert sign_in(owner, member.email).status_code == 302
        assert security.DEVICE_COOKIE in owner.cookies
        owner.post(reverse("accounts:logout"))

        self.attack(member.email)
        assert sign_in(owner, member.email).status_code == 302
        assert SESSION_KEY in owner.session

    def test_mark_of_one_account_does_not_help_another(self, member: User, make_user: Any) -> None:
        """Отметка выдана на одну почту и блокировку другой записи не снимает."""
        other = make_user(email="other@example.com")
        browser = Client(REMOTE_ADDR="203.0.113.50")
        sign_in(browser, other.email)
        browser.post(reverse("accounts:logout"))
        self.attack(member.email)
        assert sign_in(browser, member.email).status_code == 429

    def test_owner_own_failures_still_count(self, member: User) -> None:
        """Отметка не отменяет предел: свои неверные пароли блокируют и знакомый браузер."""
        owner = Client(REMOTE_ADDR="203.0.113.50")
        sign_in(owner, member.email)
        owner.post(reverse("accounts:logout"))
        for _ in range(5):
            sign_in(owner, member.email, "не-тот-пароль")
        assert sign_in(owner, member.email).status_code == 429

    def test_forged_mark_is_ignored(self, member: User) -> None:
        """Отметка без подписи сервера ничего не даёт."""
        self.attack(member.email)
        browser = Client(REMOTE_ADDR="203.0.113.50")
        browser.cookies[security.DEVICE_COOKIE] = security.device_mark(member.email)
        assert sign_in(browser, member.email).status_code == 429


class TestLimitsWithoutStorage:
    """Отказ хранилища счётчиков не снимает пределы: считает сам процесс."""

    def test_limit_holds_when_storage_is_down(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Сверх предела обращения отклоняются и без хранилища."""
        monkeypatch.setattr(throttle, "cache", ResilientValkeyCache("redis://127.0.0.1:1", {}))
        monkeypatch.setattr(throttle, "_local", {})
        answers = [throttle.allow_key("probe", "x", limit=3, window=3600) for _ in range(8)]
        assert answers == [True, True, True, False, False, False, False, False]
        assert throttle.used("probe", "x") == 8

    def test_window_ends(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Запасной счётчик живёт одно окно."""
        monkeypatch.setattr(throttle, "cache", ResilientValkeyCache("redis://127.0.0.1:1", {}))
        monkeypatch.setattr(throttle, "_local", {})
        assert throttle.allow_key("probe", "y", limit=1, window=0)
        assert throttle.allow_key("probe", "y", limit=1, window=0)
