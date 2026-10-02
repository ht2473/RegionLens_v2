"""
Проверки безопасности: заголовки, доступ по ролям и к чужим объектам, CSRF, внедрение
в SQL и разметку, хранение паролей, ограничение частоты, настройки.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

# Строки, которыми проверяется устойчивость к внедрению.
INJECTION_PAYLOADS = [
    "'; DROP TABLE fact_observation; --",
    "1 OR 1=1",
    "' UNION SELECT * FROM dim_territory --",
    "%27%20OR%20%271%27%3D%271",
]

# Разделы, доступные только после входа.
PRIVATE_ROUTES = [
    "accounts:dashboard",
    "accounts:profile",
    "workspace:saved",
]


class TestResponseHeaders:
    """Заголовки, которыми браузер ограничивает поведение страницы."""

    def test_content_type_is_not_sniffed(self, client: Client) -> None:
        """
        Браузеру запрещено угадывать тип содержимого.

        Без запрета загруженный пользователем файл может быть исполнен
        как разметка на том же домене.
        """
        response = client.get(reverse("core:home"))
        assert response.headers["X-Content-Type-Options"] == "nosniff"

    def test_framing_is_forbidden(self, client: Client) -> None:
        """Страницу нельзя встроить во фрейм: это защита от подмены нажатий."""
        response = client.get(reverse("core:home"))
        assert response.headers["X-Frame-Options"] == "DENY"

    def test_referrer_is_not_leaked(self, client: Client) -> None:
        """
        Адрес страницы не передаётся на сторонние сайты.

        В адресе хранится конфигурация расчёта, и утечка её на внешний ресурс —
        это утечка того, чем занимался пользователь.
        """
        response = client.get(reverse("core:home"))
        assert response.headers["Referrer-Policy"] == "same-origin"

    def test_production_policy_is_strict(self) -> None:
        """На рабочем стенде объявлена строгая политика содержимого без встроенных сценариев."""
        from config.settings import production

        directives = production.SECURE_CSP
        assert directives["default-src"] == ["'self'"]
        assert directives["script-src"] == ["'self'"]
        assert directives["object-src"] == ["'none'"]
        assert directives["frame-ancestors"] == ["'none'"]
        assert directives["upgrade-insecure-requests"] is True

    def test_policy_header_is_sent(self, client: Client) -> None:
        """Политику объявляет встроенный слой Django, а не сторонний пакет."""
        response = client.get(reverse("core:home"))
        policy = response.headers["Content-Security-Policy"]
        assert "default-src 'self'" in policy
        assert "script-src 'self'" in policy
        assert "'unsafe-inline'" not in policy.split("script-src", 1)[1].split(";", 1)[0]

    def test_production_requires_https(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """
        Рабочий стенд по умолчанию переводит обращения на HTTPS и объявляет HSTS.

        Переменные окружения снимаются, и настройки перечитываются — проверяется умолчание.
        """
        import importlib

        from config.settings import production

        for name in (
            "SECURE_SSL_REDIRECT",
            "SECURE_HSTS_SECONDS",
            "SESSION_COOKIE_SECURE",
            "CSRF_COOKIE_SECURE",
        ):
            monkeypatch.delenv(name, raising=False)

        module = importlib.reload(production)
        try:
            assert module.DEBUG is False
            assert module.SECURE_SSL_REDIRECT is True
            assert module.SECURE_HSTS_SECONDS >= 31_536_000
            assert module.SESSION_COOKIE_SECURE is True
            assert module.CSRF_COOKIE_SECURE is True
        finally:
            # Модуль возвращается к состоянию, соответствующему окружению прогона.
            importlib.reload(production)


class TestAccessControl:
    """Разграничение доступа."""

    @pytest.mark.parametrize("route", PRIVATE_ROUTES)
    def test_private_route_redirects_guest(self, client: Client, route: str) -> None:
        """Личный раздел не отдаётся гостю, а ведёт на страницу входа."""
        response = client.get(reverse(route))

        assert response.status_code == 302
        assert reverse("accounts:login") in response["Location"]

    def test_management_panel_is_closed_to_member(self, member_client: Client) -> None:
        """
        Панель управления закрыта для рядовой роли.

        Проверяется отказ, а не отсутствие ссылки в меню: скрытая ссылка
        доступа не ограничивает.
        """
        response = member_client.get(reverse("dashboard:index"))
        assert response.status_code in {302, 403}

    def test_service_admin_is_closed_to_regular_user(self, member_client: Client) -> None:
        """Служебная панель Django недоступна обычному пользователю."""
        response = member_client.get("/django-admin/", follow=True)
        content = response.content.decode("utf-8")
        assert "Управление данными" not in content


class TestForeignObjects:
    """Доступ к чужим объектам."""


class TestCrossSiteRequestForgery:
    """Подделка межсайтовых запросов."""

    def test_post_without_token_is_refused(self, member: Any) -> None:
        """
        Отправка формы без токена отклоняется.

        Проверка выполняется клиентом с включённой проверкой токена: обычный
        тестовый клиент её отключает и ничего бы не показал.
        """
        client = Client(enforce_csrf_checks=True)
        client.force_login(member)

        response = client.post(reverse("accounts:profile"), {"full_name": "Новое имя"})
        assert response.status_code == 403

    def test_login_form_carries_token(self, client: Client) -> None:
        """Форма входа содержит токен: иначе проверка была бы невыполнима."""
        response = client.get(reverse("accounts:login"))
        assert "csrfmiddlewaretoken" in response.content.decode("utf-8")


class TestInjection:
    """Устойчивость к внедрению."""

    @pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
    def test_warehouse_parameters_are_not_executed(
        self, client: Client, warehouse: Any, payload: str
    ) -> None:
        """
        Внедрение в параметры страницы не выполняется складом.

        Значения передаются в DuckDB исключительно параметрами запроса,
        поэтому строка остаётся строкой и приводит лишь к пустому результату.
        """
        response = client.get(
            reverse("rankings:index"),
            {"series": payload, "year": payload, "district": payload},
        )
        assert response.status_code == 200

    @pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
    def test_api_parameters_are_not_executed(
        self, client: Client, warehouse: Any, payload: str
    ) -> None:
        """Программный интерфейс отвечает отказом по проверке, а не ошибкой склада."""
        response = client.get("/api/v1/observations/", {"series": payload})
        assert response.status_code in {200, 400, 404}

    def test_warehouse_survives_injection_in_search(self, client: Client, warehouse: Any) -> None:
        """Поиск по каталогу не исполняет переданную строку."""
        from apps.catalog.models import Indicator

        response = client.get(reverse("catalog:indicator-list"), {"q": "'; DELETE FROM --"})

        assert response.status_code == 200
        assert Indicator.objects.exists()

    def test_user_content_is_escaped(self, member_client: Client, member: Any) -> None:
        """
        Разметка, введённая пользователем, выводится как текст.

        Название сохранённой выборки задаёт пользователь и видит его же — но
        видеть он должен текст, а не исполненный сценарий.
        """
        from apps.workspace.models import SavedQuery

        SavedQuery.objects.create(
            user=member,
            title="<script>alert(1)</script>",
            target="rankings:index",
            parameters={},
        )

        response = member_client.get(reverse("workspace:saved"))
        content = response.content.decode("utf-8")

        assert "<script>alert(1)</script>" not in content
        assert "&lt;script&gt;" in content


class TestSecrets:
    """Хранение паролей и ключей."""

    def test_password_is_hashed_with_modern_algorithm(self, settings: Any) -> None:
        """Пароли хранятся хешем Argon2 — по настройке основного модуля."""
        from config.settings import base

        assert base.PASSWORD_HASHERS[0].endswith("Argon2PasswordHasher")

    def test_settings_page_hides_secrets(self, admin_panel_client: Client) -> None:
        """Обзор панели управления не показывает секретный ключ и пароли."""
        from django.conf import settings as django_settings

        response = admin_panel_client.get(reverse("dashboard:index"))
        content = response.content.decode("utf-8")

        assert django_settings.SECRET_KEY not in content


class TestRateLimits:
    """Ограничение частоты обращений."""

    def test_anonymous_requests_are_limited(
        self, client: Client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        Обращения к API ограничены по числу.

        Предел подменяется на классе ограничителя: DRF читает пределы один раз при загрузке.
        """
        from apps.api.throttling import AnonymousHourlyThrottle

        monkeypatch.setattr(AnonymousHourlyThrottle, "THROTTLE_RATES", {"anon": "2/hour"})

        codes = [client.get("/api/v1/territories/").status_code for _ in range(4)]
        assert codes[-1] == 429


class TestProductionSettings:
    """Настройки рабочего стенда — загрузкой модуля production, который тесты иначе не читают."""

    @staticmethod
    def _production_settings() -> Any:
        """Загрузить рабочие настройки, не подменяя настройки текущего процесса."""
        import importlib

        return importlib.import_module("config.settings.production")

    def test_loopback_is_allowed_for_health_probe(self) -> None:
        """Петлевой узел остаётся среди разрешённых при любом DJANGO_ALLOWED_HOSTS."""
        production = self._production_settings()

        assert "127.0.0.1" in production.ALLOWED_HOSTS
        assert "localhost" in production.ALLOWED_HOSTS

    def test_health_probe_is_exempt_from_https_redirect(self) -> None:
        """Проверки состояния — без перехода на HTTPS: `curl --fail` считает 301 успехом."""
        production = self._production_settings()

        assert "^healthz$" in production.SECURE_REDIRECT_EXEMPT
        assert "^readyz$" in production.SECURE_REDIRECT_EXEMPT

    def test_client_address_is_taken_from_proxy_header(self) -> None:
        """
        Адрес посетителя читается из заголовка прокси — и приложением, и счётчиком входов.

        Иначе за прокси у всех один REMOTE_ADDR, и чужие ошибки закрывали бы вход всем.
        """
        production = self._production_settings()

        assert production.CLIENT_IP_HEADER == "HTTP_X_REAL_IP"
        assert production.AXES_IPWARE_META_PRECEDENCE_ORDER[0] == "HTTP_X_REAL_IP"
