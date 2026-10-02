"""Страницы ошибок: облик сайта, язык адреса и ссылки дальше."""

from __future__ import annotations

from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import translation
from django.views.defaults import server_error

pytestmark = [pytest.mark.integration, pytest.mark.django_db]


def test_missing_page_answers_in_site_frame(client: Client) -> None:
    """Неизвестный адрес — 404 в каркасе сайта со ссылками на регионы и показатели."""
    response = client.get("/ru/net-takoi-stranitsy/")
    content = response.content.decode("utf-8")

    assert response.status_code == 404
    assert "Страница не найдена" in content
    assert 'class="app-shell"' in content
    assert "/regions/" in content
    assert "/indicators/" in content


def test_missing_page_follows_the_language_of_the_address(client: Client) -> None:
    """Английский адрес получает английскую страницу 404."""
    response = client.get("/en/no-such-page/")

    assert response.status_code == 404
    assert "Page not found" in response.content.decode("utf-8")


def test_closed_page_explains_the_refusal(member_client: Client) -> None:
    """Пользователь без роли администратора видит отказ и путь к обратной связи."""
    response = member_client.get(reverse("dashboard:index"))
    content = response.content.decode("utf-8")

    assert response.status_code == 403
    assert "Нет доступа" in content
    assert "/feedback/" in content


def test_expired_form_is_explained(db: None) -> None:
    """Отказ проверки CSRF объясняет, что делать, а не показывает страницу Django."""
    guarded = Client(enforce_csrf_checks=True)
    response = guarded.post(reverse("accounts:login"), {"username": "x", "password": "y"})
    content = response.content.decode("utf-8")

    assert response.status_code == 403
    assert "Форма устарела" in content
    assert "CSRF verification failed" not in content


def test_server_error_page_needs_no_request_context(rf: Any) -> None:
    """Страница 500 собирается без запроса: при сбое процессоры контекста не выполняются."""
    with translation.override("ru"):
        response = server_error(rf.get("/ru/"))
    content = response.content.decode("utf-8")

    assert response.status_code == 500
    assert "Ошибка на сервере" in content
    assert "css/tokens.css" in content


@pytest.mark.parametrize(
    "path",
    ["/ru/glossary/", "/ru/map/", "/ru/indicators/", "/api/v1/series/"],
)
def test_null_character_in_query_is_refused(client: Client, path: str) -> None:
    """Нулевой байт в параметрах — 400 с оформленной страницей: PostgreSQL его не принимает."""
    response = client.get(path, {"q": "a\x00b", "series": "a\x00b"})
    content = response.content.decode("utf-8")

    assert response.status_code == 400
    assert "Неверный запрос" in content
    assert "css/tokens.css" in content


def test_null_character_page_follows_the_language_of_the_address(client: Client) -> None:
    """Английский адрес получает английскую страницу 400."""
    response = client.get("/en/glossary/", {"q": "a\x00b"})

    assert response.status_code == 400
    assert "Bad request" in response.content.decode("utf-8")


def test_null_character_in_form_is_refused(client: Client) -> None:
    """Нулевой байт в поле формы — 400 до обращения к базе."""
    response = client.post(reverse("accounts:my-region"), {"region": "a\x00b"})

    assert response.status_code == 400
