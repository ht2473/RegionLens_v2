"""
Сквозные сценарии кабинета: вход с кодом, восстановление пароля и смена почты по ссылке
из письма, «Мой регион», пометки, обращения и удаление учётной записи.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from django.core.cache import cache
from playwright.sync_api import Page, expect

from apps.accounts import totp

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]

PASSWORD = "e2e-password-12345"


@pytest.fixture(autouse=True)
def fresh_counters() -> None:
    """Пределы частоты считаются в кэше процесса; сценарии не должны их делить."""
    cache.clear()


def sign_in(page: Page, site: Any, email: str, password: str = PASSWORD) -> None:
    """Войти паролем."""
    page.goto(f"{site.url}/ru/login/")
    page.fill("input[name='username']", email)
    page.fill("input[name='password']", password)
    page.locator("main button[type='submit']").click()
    page.wait_for_load_state("networkidle")


def link_from(message: Any, fragment: str) -> str:
    """Адрес из письма, содержащий ``fragment``."""
    found = re.search(rf"https?://\S*{re.escape(fragment)}\S*", message.body)
    assert found, message.body
    return found.group(0)


class TestSignIn:
    """Вход и первая страница кабинета."""

    def test_login_opens_overview(self, page: Page, site: Any, registered_user: Any) -> None:
        """После входа — обзор кабинета с шестью разделами."""
        sign_in(page, site, registered_user.email)
        assert page.url.endswith("/ru/cabinet/")
        expect(page.locator(".cabinet-tabs__tab")).to_have_count(6)
        expect(page.locator(".cabinet-tabs__tab[aria-current='page']")).to_have_text(
            re.compile("Обзор")
        )

    def test_two_factor_is_enabled_and_asked(
        self, page: Page, site: Any, registered_user: Any
    ) -> None:
        """Вход с кодом: включение по QR-ключу, резервные коды, затем код при входе."""
        sign_in(page, site, registered_user.email)
        page.goto(f"{site.url}/ru/cabinet/security/")
        page.get_by_role("link", name="Включить вход с кодом").click()
        expect(page.locator(".qr-code__image")).to_be_visible()

        secret = page.locator(".qr-code__secret").inner_text().replace(" ", "")
        step = totp.current_step()
        page.fill("input[name='code']", totp.code_at(secret, step))
        page.locator("main button[type='submit']").click()
        expect(page.locator(".recovery-codes li")).to_have_count(totp.RECOVERY_COUNT)

        page.locator(".user-menu__trigger").click()
        page.locator(".user-menu__form button").click()
        page.wait_for_load_state("networkidle")

        sign_in(page, site, registered_user.email)
        assert "/login/code/" in page.url
        # Код включения уже принят: следующий шаг — в пределах допуска часов.
        page.fill("input[name='code']", totp.code_at(secret, step + 1))
        page.locator("main button[type='submit']").click()
        page.wait_for_load_state("networkidle")
        assert page.url.endswith("/ru/cabinet/")


class TestLinksFromMail:
    """Сценарии со ссылкой из письма."""

    def test_password_reset(
        self, page: Page, site: Any, registered_user: Any, mailoutbox: list[Any]
    ) -> None:
        """Ссылка восстановления назначает новый пароль, с ним можно войти."""
        page.goto(f"{site.url}/ru/password/reset/")
        page.fill("input[name='email']", registered_user.email)
        page.locator("main button[type='submit']").click()
        page.wait_for_load_state("networkidle")
        assert "/password/reset/sent/" in page.url

        page.goto(link_from(mailoutbox[-1], "/password/reset/"))
        page.wait_for_load_state("networkidle")
        page.fill("input[name='new_password1']", "Новый-Пароль-Для-Входа-2026")
        page.fill("input[name='new_password2']", "Новый-Пароль-Для-Входа-2026")
        page.locator("main button[type='submit']").click()
        page.wait_for_load_state("networkidle")
        assert "/password/reset/done/" in page.url

        sign_in(page, site, registered_user.email, "Новый-Пароль-Для-Входа-2026")
        assert page.url.endswith("/ru/cabinet/")

    def test_email_change(
        self, page: Page, site: Any, registered_user: Any, mailoutbox: list[Any]
    ) -> None:
        """Новый адрес — после кнопки на странице из письма; прежний получает уведомление."""
        sign_in(page, site, registered_user.email)
        page.goto(f"{site.url}/ru/cabinet/security/")
        page.fill("input[name='new_email']", "renamed@example.com")
        page.locator("form[action$='/security/email/'] input[name='password']").fill(PASSWORD)
        page.get_by_role("button", name="Отправить ссылку на новый адрес").click()
        expect(page.get_by_text("Ждёт подтверждения")).to_be_visible()
        assert mailoutbox[-1].to == ["renamed@example.com"]

        page.goto(link_from(mailoutbox[-1], "/email/confirm/"))
        page.get_by_role("button", name="Подтвердить адрес").click()
        page.wait_for_load_state("networkidle")
        expect(page.locator(".cabinet-block__lead").first).to_have_text("renamed@example.com")
        assert mailoutbox[-1].to == [registered_user.email]


class TestMyRegion:
    """«Это мой регион» у гостя."""

    def test_guest_chooses_region(self, page: Page, site: Any, warehouse_committed: Any) -> None:
        """Кнопка в паспорте нажимается без перезагрузки; главная и шапка — с регионом."""
        from apps.catalog.models import Territory

        territory = Territory.objects.comparable().first()
        page.goto(f"{site.url}/ru/regions/{territory.slug}/")
        button = page.locator(".my-region-form button")
        expect(button).to_have_attribute("aria-pressed", "false")
        button.click()
        expect(page.locator(".my-region-form button")).to_have_attribute("aria-pressed", "true")

        page.goto(f"{site.url}/ru/")
        expect(page.locator(".home-region__title")).to_have_text(territory.name)
        expect(page.locator(".app-header__region")).to_be_visible()


class TestCabinetSections:
    """Пометки, обращения, удаление."""

    def test_note_on_saved_region(self, page: Page, site: Any, registered_user: Any) -> None:
        """Пометка к отметке правится во всплывающей панели и видна в перечне."""
        from apps.catalog.models import Territory
        from apps.workspace.models import Favorite

        Favorite.objects.create(
            user=registered_user, territory=Territory.objects.comparable().first()
        )
        sign_in(page, site, registered_user.email)
        page.goto(f"{site.url}/ru/cabinet/saved/")
        page.locator(".saved-mark button[popovertarget^='mark-note-']").click()
        page.locator(".save-query__form input[name='note']").fill("сравнить с соседями")
        page.locator(".save-query__form button[type='submit']").click()
        page.wait_for_load_state("networkidle")
        expect(page.locator(".saved-mark__note")).to_have_text("сравнить с соседями")

    def test_ticket_appears_in_cabinet(self, page: Page, site: Any, registered_user: Any) -> None:
        """Обращение из учётной записи видно в кабинете."""
        sign_in(page, site, registered_user.email)
        page.goto(f"{site.url}/ru/feedback/")
        page.fill("input[name='subject']", "Вопрос о постоянном составе")
        page.fill("textarea[name='body']", "Почему в неравенстве по умолчанию не все субъекты?")
        page.check("input[name='consent']")
        page.get_by_role("button", name="Отправить обращение").click()
        page.get_by_role("link", name="Обращение в кабинете").click()
        expect(page.locator(".cabinet__heading")).to_have_text("Вопрос о постоянном составе")

        page.goto(f"{site.url}/ru/cabinet/tickets/")
        expect(page.locator(".ticket-row")).to_have_count(1)

    def test_account_is_deleted(
        self, page: Page, site: Any, registered_user: Any, mailoutbox: list[Any]
    ) -> None:
        """Удаление учётной записи паролем: выход, письмо, вход больше не работает."""
        sign_in(page, site, registered_user.email)
        page.goto(f"{site.url}/ru/cabinet/data/")
        page.fill("input[name='password']", PASSWORD)
        page.check("input[name='confirm']")
        page.get_by_role("button", name="Удалить учётную запись").click()
        page.wait_for_load_state("networkidle")
        expect(page.locator(".app-header__login")).to_be_visible()
        assert mailoutbox[-1].to == [registered_user.email]

        sign_in(page, site, registered_user.email)
        assert "/login/" in page.url
