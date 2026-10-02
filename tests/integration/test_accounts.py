"""Проверки аутентификации, ролей и разделов личного кабинета."""

from __future__ import annotations

from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.constants import ROLE_USER
from apps.accounts.models import User
from tests.conftest import TEST_PASSWORD

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

# Разделы кабинета, требующие входа. Каждый должен отвечать перенаправлением
# неаутентифицированному посетителю, а не показывать пустую страницу.
CABINET_ROUTES = [
    "accounts:dashboard",
    "accounts:profile",
    "accounts:security",
    "accounts:password-change",
    "accounts:two-factor",
    "accounts:data",
    "accounts:data-export",
    "workspace:saved",
    "feedback:mine",
]


class TestRegistration:
    """Создание учётной записи."""

    def test_registration_creates_member(self, client: Client) -> None:
        """Новая учётная запись получает базовую роль и настройки интерфейса."""
        response = client.post(
            reverse("accounts:register"),
            {
                "email": "New.User@Example.COM",
                "full_name": "Петров Пётр Петрович",
                "password1": TEST_PASSWORD,
                "password2": TEST_PASSWORD,
                "accepted_terms": "on",
                "accepted_consent": "on",
            },
        )

        assert response.status_code == 302
        user = User.objects.get(email="new.user@example.com")
        assert user.role_names == [ROLE_USER]
        assert not user.has_panel_access

    def test_duplicate_email_is_rejected(self, client: Client, member: User) -> None:
        """
        Адрес, отличающийся только регистром, считается занятым.

        Регистр в адресе почты значения не имеет, и две учётные записи, различающиеся
        только им, привели бы к путанице при входе.
        """
        response = client.post(
            reverse("accounts:register"),
            {
                "email": member.email.upper(),
                "full_name": "Двойников Иван Иванович",
                "password1": TEST_PASSWORD,
                "password2": TEST_PASSWORD,
                "accepted_terms": "on",
                "accepted_consent": "on",
            },
        )

        assert response.status_code == 200
        assert User.objects.count() == 1

    def test_password_mismatch_is_rejected(self, client: Client) -> None:
        """Несовпадающие пароли не создают учётную запись."""
        response = client.post(
            reverse("accounts:register"),
            {
                "email": "mismatch@example.com",
                "full_name": "Иванов Иван Иванович",
                "password1": TEST_PASSWORD,
                "password2": TEST_PASSWORD + "-другой",
                "accepted_terms": "on",
                "accepted_consent": "on",
            },
        )

        assert response.status_code == 200
        assert not User.objects.filter(email="mismatch@example.com").exists()


class TestAuthentication:
    """Вход и выход."""

    def test_login_by_email(self, client: Client, member: User) -> None:
        """Вход по адресу электронной почты."""
        response = client.post(
            reverse("accounts:login"),
            {"username": member.email, "password": TEST_PASSWORD},
        )

        assert response.status_code == 302

    @pytest.mark.parametrize("route", CABINET_ROUTES)
    def test_cabinet_requires_login(self, client: Client, route: str) -> None:
        """Раздел кабинета отправляет гостя на форму входа."""
        response = client.get(reverse(route))
        assert response.status_code == 302
        assert reverse("accounts:login") in response.url


class TestBruteForceProtection:
    """Защита формы входа от подбора пароля."""

    def test_repeated_failures_lock_the_form(
        self, client: Client, member: User, settings: Any
    ) -> None:
        """
        После нескольких неудачных попыток вход блокируется даже с верным паролем.

        Учёт попыток включается здесь явно, предел снижен.
        """
        settings.AXES_ENABLED = True
        settings.AXES_FAILURE_LIMIT = 2

        for _ in range(2):
            client.post(
                reverse("accounts:login"),
                {"username": member.email, "password": "неверный-пароль"},
            )

        response = client.post(
            reverse("accounts:login"),
            {"username": member.email, "password": TEST_PASSWORD},
        )

        # 429 «слишком много запросов» — ответ, предусмотренный django-axes
        # для исчерпанного предела попыток.
        assert response.status_code == 429
        assert not response.wsgi_request.user.is_authenticated
        # Отказ объясняется страницей в оформлении ресурса, а не пустым ответом.
        assert "Вход временно заблокирован" in response.content.decode("utf-8")

    def test_attempts_are_counted_per_account(
        self, client: Client, member: User, settings: Any
    ) -> None:
        """
        Неудачные попытки учитываются вместе с введённой учётной записью.

        Без этого блокировка свелась бы к блокировке по адресу отправителя и отрезала
        бы всех, кто работает за общим адресом организации.
        """
        from axes.models import AccessAttempt

        settings.AXES_ENABLED = True
        client.post(
            reverse("accounts:login"),
            {"username": member.email, "password": "неверный-пароль"},
        )

        attempt = AccessAttempt.objects.get()
        assert attempt.username == member.email
        assert attempt.ip_address


class TestCabinetSections:
    """Разделы кабинета, относящиеся к учётной записи."""

    def test_cabinet_header_names_the_role(self, member_client: Client) -> None:
        """Шапка кабинета называет роль вошедшего."""
        response = member_client.get(reverse("workspace:saved"))

        assert response.status_code == 200
        assert response.context["cabinet_user"]["role"]
        assert response.context["cabinet_user"]["manages"] is False

    def test_profile_update_is_saved(self, member_client: Client, member: User) -> None:
        """Правка профиля сохраняется."""
        response = member_client.post(
            reverse("accounts:profile"),
            {"full_name": "Новиков Новик Новикович"},
        )

        member.refresh_from_db()
        assert response.status_code == 302
        assert member.full_name == "Новиков Новик Новикович"

    def test_password_change(self, member_client: Client, member: User) -> None:
        """Пароль меняется из кабинета."""
        response = member_client.post(
            reverse("accounts:password-change"),
            {
                "old_password": TEST_PASSWORD,
                "new_password1": "другой-пароль-98765",
                "new_password2": "другой-пароль-98765",
            },
        )

        member.refresh_from_db()
        assert response.status_code == 302
        assert member.check_password("другой-пароль-98765")
