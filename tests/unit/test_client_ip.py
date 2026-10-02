"""
Проверки определения адреса посетителя: от него зависит счётчик неудачных входов.

Первый элемент ``X-Forwarded-For`` присылает клиент — доверять ему нельзя.
"""

from __future__ import annotations

import pytest
from django.test import RequestFactory, override_settings

from apps.core.client_ip import client_ip

pytestmark = pytest.mark.unit

# Адрес прокси во внутренней сети compose — то, что видит приложение в REMOTE_ADDR.
PROXY = "172.18.0.4"
VISITOR = "203.0.113.7"


@pytest.fixture
def factory() -> RequestFactory:
    """Построитель запросов."""
    return RequestFactory()


class TestWithoutProxy:
    """Приложение смотрит в сеть напрямую: заголовкам верить нельзя."""

    @override_settings(CLIENT_IP_HEADER="")
    def test_takes_connection_address(self, factory: RequestFactory) -> None:
        """Берётся адрес соединения."""
        request = factory.get("/", REMOTE_ADDR=VISITOR)
        assert client_ip(request) == VISITOR

    @override_settings(CLIENT_IP_HEADER="")
    def test_ignores_forged_headers(self, factory: RequestFactory) -> None:
        """Присланные клиентом заголовки не принимаются во внимание."""
        request = factory.get(
            "/",
            REMOTE_ADDR=VISITOR,
            HTTP_X_REAL_IP="8.8.8.8",
            HTTP_X_FORWARDED_FOR="8.8.8.8",
        )
        assert client_ip(request) == VISITOR


class TestBehindProxy:
    """Перед приложением стоит обратный прокси, перезаписывающий X-Real-IP."""

    @override_settings(CLIENT_IP_HEADER="HTTP_X_REAL_IP")
    def test_takes_visitor_address(self, factory: RequestFactory) -> None:
        """Берётся адрес из заголовка, а не адрес прокси."""
        request = factory.get("/", REMOTE_ADDR=PROXY, HTTP_X_REAL_IP=VISITOR)
        assert client_ip(request) == VISITOR

    @override_settings(CLIENT_IP_HEADER="HTTP_X_REAL_IP")
    def test_visitors_are_distinguished(self, factory: RequestFactory) -> None:
        """Разные посетители различаются, хотя приходят от одного прокси.

        Ради этого всё и делается: при одном адресе на всех блокировка входа
        по признаку адреса закрывает вход не нарушителю, а всем сразу.
        """
        addresses = {
            client_ip(factory.get("/", REMOTE_ADDR=PROXY, HTTP_X_REAL_IP=visitor))
            for visitor in (VISITOR, "198.51.100.42")
        }
        assert addresses == {VISITOR, "198.51.100.42"}

    @override_settings(CLIENT_IP_HEADER="HTTP_X_REAL_IP")
    def test_forwarded_for_is_not_consulted(self, factory: RequestFactory) -> None:
        """Цепочка X-Forwarded-For не используется: её начало сочиняет клиент."""
        request = factory.get(
            "/",
            REMOTE_ADDR=PROXY,
            HTTP_X_REAL_IP=VISITOR,
            HTTP_X_FORWARDED_FOR="8.8.8.8, " + VISITOR,
        )
        assert client_ip(request) == VISITOR

    @override_settings(CLIENT_IP_HEADER="HTTP_X_REAL_IP")
    def test_falls_back_when_header_absent(self, factory: RequestFactory) -> None:
        """Без заголовка остаётся адрес соединения: обращение могло прийти мимо прокси."""
        request = factory.get("/", REMOTE_ADDR=PROXY)
        assert client_ip(request) == PROXY


class TestEdgeCases:
    """Крайние случаи."""

    def test_no_request(self) -> None:
        """Запроса нет — адреса нет. Так вызывают из команд управления."""
        assert client_ip(None) is None

    @override_settings(CLIENT_IP_HEADER="HTTP_X_REAL_IP")
    def test_empty_header_falls_back(self, factory: RequestFactory) -> None:
        """Пустой заголовок равнозначен отсутствующему."""
        request = factory.get("/", REMOTE_ADDR=PROXY, HTTP_X_REAL_IP="   ")
        assert client_ip(request) == PROXY
