"""
Проверки панели управления: доступ по ролям, защита от потери администратора
и отсутствие упоминаний убранных возможностей.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.constants import ROLE_ADMIN

pytestmark = pytest.mark.integration

SECTIONS: tuple[str, ...] = (
    "dashboard:index",
    "dashboard:ticket-list",
    "dashboard:content",
    "dashboard:user-list",
    "dashboard:data",
    "dashboard:quality",
)


# ---------------------------------------------------------------------------------------
# Доступ
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("url_name", SECTIONS)
def test_sections_open_for_administrator(
    admin_panel_client: Client, url_name: str, reference_seed: None
) -> None:
    """Все разделы панели открываются администратором."""
    response = admin_panel_client.get(reverse(url_name))
    assert response.status_code == 200


def test_member_has_no_access(member_client: Client) -> None:
    """Обычному пользователю панель недоступна целиком."""
    assert member_client.get(reverse("dashboard:index")).status_code == 403


def test_guest_is_redirected_to_login(client: Client, db: None) -> None:
    """Гость отправляется на форму входа, а не получает отказ: ему достаточно войти."""
    response = client.get(reverse("dashboard:index"))
    assert response.status_code == 302
    assert reverse("accounts:login") in response["Location"]


# ---------------------------------------------------------------------------------------
# Управление учётными записями
# ---------------------------------------------------------------------------------------


def test_role_change_is_saved(admin_panel_client: Client, member: Any, administrator: Any) -> None:
    """Изменение роли сохраняется."""
    admin_panel_client.post(
        reverse("dashboard:user-role", kwargs={"public_id": member.public_id}),
        {"roles": [ROLE_ADMIN]},
    )

    member.refresh_from_db()
    assert ROLE_ADMIN in member.role_names


def test_last_administrator_cannot_be_demoted(
    admin_panel_client: Client, administrator: Any
) -> None:
    """
    Последнего администратора нельзя понизить.

    Иначе система осталась бы без учётной записи, способной вернуть кому-либо права,
    и восстанавливать доступ пришлось бы из командной строки стенда.
    """
    admin_panel_client.post(
        reverse("dashboard:user-role", kwargs={"public_id": administrator.public_id}),
        {"roles": []},
    )

    administrator.refresh_from_db()
    assert ROLE_ADMIN in administrator.role_names


def test_administrator_cannot_block_self(admin_panel_client: Client, administrator: Any) -> None:
    """Собственную учётную запись заблокировать нельзя."""
    admin_panel_client.post(
        reverse("dashboard:user-toggle", kwargs={"public_id": administrator.public_id})
    )

    administrator.refresh_from_db()
    assert administrator.is_active is True


def test_blocking_user(admin_panel_client: Client, member: Any) -> None:
    """Блокировка снимает признак активности учётной записи."""
    admin_panel_client.post(
        reverse("dashboard:user-toggle", kwargs={"public_id": member.public_id})
    )

    member.refresh_from_db()
    assert member.is_active is False


# ---------------------------------------------------------------------------------------
# Состояние системы
# ---------------------------------------------------------------------------------------


def test_overview_reports_service_state(admin_panel_client: Client) -> None:
    """Обзор показывает администратору проверки служб."""
    response = admin_panel_client.get(reverse("dashboard:index"))
    checks = {str(check["title"]) for check in response.context["health"]}

    assert response.status_code == 200
    assert "База данных" in checks
    assert "Аналитический склад" in checks


def test_overview_hides_secrets(admin_panel_client: Client, settings: Any) -> None:
    """
    Секреты не показываются ни в каком виде.

    Обзор открыт администратору, но и он не должен видеть ключ приложения:
    доступ к панели и знание ключа — разные уровни риска.
    """
    body = admin_panel_client.get(reverse("dashboard:index")).content.decode()

    assert settings.SECRET_KEY not in body


def test_user_panel_is_closed_by_direct_address(member_client: Client) -> None:
    """
    Разделы панели закрыты и по прямому адресу.

    Скрытие пункта меню само по себе доступ не ограничивает.
    """
    for name in ("dashboard:user-list", "dashboard:data", "dashboard:content"):
        assert member_client.get(reverse(name)).status_code == 403


# ---------------------------------------------------------------------------------------
# Содержание страниц
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("url_name", SECTIONS)
def test_panel_promises_nothing_removed(
    admin_panel_client: Client, url_name: str, reference_seed: None
) -> None:
    """Панель не упоминает журнал действий, уведомления и ключи API: их нет."""
    content = admin_panel_client.get(reverse(url_name)).content.decode()
    for phrase in ("журнале действий", "получит уведомление", "Подписчики получат", "ключи"):
        assert phrase not in content, (url_name, phrase)


@pytest.mark.parametrize(
    "url_name", ["dashboard:user-list", "dashboard:ticket-list", "dashboard:quality"]
)
def test_filters_apply_at_once(
    admin_panel_client: Client, url_name: str, reference_seed: None
) -> None:
    """Отборы помечены для применения сразу: без кнопки «Применить» после каждого выбора."""
    content = admin_panel_client.get(reverse(url_name)).content.decode()
    assert re.search(r'<form class="admin-filters"[^>]*data-autosubmit', content)
