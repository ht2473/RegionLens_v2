"""
Устойчивость к потоку обращений и к перегрузке: общий предел по адресу, счёт адресов IPv6,
предел писем по действиям посетителей, настройки прокси и контейнеров.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from django.conf import settings as django_settings
from django.core import mail
from django.test import Client
from django.urls import reverse

from apps.core.client_ip import is_loopback, rate_subject

pytestmark = [pytest.mark.integration, pytest.mark.security, pytest.mark.django_db]

ROOT = Path(django_settings.BASE_DIR)
OUTSIDE = "203.0.113.7"


class TestRateSubject:
    """За кого считаются обращения с адреса."""

    def test_ipv4_is_counted_as_is(self) -> None:
        assert rate_subject("203.0.113.7") == "203.0.113.7"

    def test_ipv6_is_counted_by_subscriber_network(self) -> None:
        """Абонент IPv6 меняет адрес внутри своей сети /64 в каждом запросе."""
        first = rate_subject("2001:db8:1:2:aaaa:bbbb:cccc:dddd")
        second = rate_subject("2001:db8:1:2:1111:2222:3333:4444")
        other = rate_subject("2001:db8:1:3::1")

        assert first == second == "2001:db8:1:2::/64"
        assert other != first

    def test_mapped_ipv4_is_the_same_visitor(self) -> None:
        assert rate_subject("::ffff:203.0.113.7") == "203.0.113.7"

    def test_unknown_and_broken_address(self) -> None:
        assert rate_subject(None) == "unknown"
        assert rate_subject("") == "unknown"
        assert rate_subject("not-an-address") == "not-an-address"

    def test_loopback(self) -> None:
        assert is_loopback("127.0.0.1")
        assert is_loopback("::1")
        assert not is_loopback(OUTSIDE)
        assert not is_loopback(None)


class TestRequestRate:
    """Общий предел обращений с одного адреса."""

    def test_address_over_the_limit_is_refused(self, client: Client, settings: Any) -> None:
        """Сверх предела — короткий отказ 429 с временем повтора, без сборки страницы."""
        settings.REQUESTS_PER_MINUTE = 3
        path = reverse("core:about")

        codes = [client.get(path, REMOTE_ADDR=OUTSIDE).status_code for _ in range(5)]
        refused = client.get(path, REMOTE_ADDR=OUTSIDE)

        assert codes == [200, 200, 200, 429, 429]
        assert refused["Retry-After"] == "60"
        assert refused["Content-Type"].startswith("text/plain")
        assert len(refused.content) < 400

    def test_other_address_is_not_affected(self, client: Client, settings: Any) -> None:
        settings.REQUESTS_PER_MINUTE = 2
        path = reverse("core:about")
        for _ in range(4):
            client.get(path, REMOTE_ADDR=OUTSIDE)

        assert client.get(path, REMOTE_ADDR="198.51.100.20").status_code == 200

    def test_ipv6_addresses_of_one_network_share_the_limit(
        self, client: Client, settings: Any
    ) -> None:
        settings.REQUESTS_PER_MINUTE = 2
        path = reverse("core:about")

        codes = [
            client.get(path, REMOTE_ADDR=f"2001:db8:5:6::{number:x}").status_code
            for number in range(1, 5)
        ]

        assert codes == [200, 200, 429, 429]

    def test_container_itself_is_not_limited(self, client: Client, settings: Any) -> None:
        """Проверка живости и прогрев кэша приходят с петлевого адреса."""
        settings.REQUESTS_PER_MINUTE = 2

        codes = [client.get("/healthz").status_code for _ in range(6)]

        assert set(codes) == {200}

    def test_zero_switches_the_limit_off(self, client: Client, settings: Any) -> None:
        settings.REQUESTS_PER_MINUTE = 0
        path = reverse("core:about")

        codes = [client.get(path, REMOTE_ADDR=OUTSIDE).status_code for _ in range(6)]

        assert set(codes) == {200}

    def test_api_counts_the_address_from_proxy_header(
        self, client: Client, settings: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        Предел API считается по адресу из заголовка прокси, адрес IPv6 — сетью абонента;
        ``X-Forwarded-For``, который клиент может прислать сам, в счёт не идёт.
        """
        from apps.api.throttling import AnonymousHourlyThrottle

        settings.CLIENT_IP_HEADER = "HTTP_X_REAL_IP"
        monkeypatch.setattr(AnonymousHourlyThrottle, "THROTTLE_RATES", {"anon": "2/hour"})

        codes = [
            client.get(
                "/api/v1/territories/",
                HTTP_X_REAL_IP=f"2001:db8:9:9::{number:x}",
                HTTP_X_FORWARDED_FOR=f"198.51.100.{number}",
            ).status_code
            for number in range(1, 5)
        ]

        assert codes == [200, 200, 429, 429]


class TestPublicMail:
    """Письма, которые вызывает посетитель без входа."""

    @staticmethod
    def _register(client: Client, number: int) -> Any:
        return client.post(
            reverse("accounts:register"),
            {
                "email": f"visitor-{number}@example.org",
                "full_name": "Посетитель",
                "password1": "correct-horse-battery-9",
                "password2": "correct-horse-battery-9",
                "accepted_terms": "on",
                "accepted_consent": "on",
                "website": "",
            },
            REMOTE_ADDR=f"198.51.100.{number}",
        )

    def test_site_wide_limit_stops_registration_letters(
        self, client: Client, settings: Any
    ) -> None:
        """Поток регистраций со многих адресов на многие ящики упирается в общий предел."""
        settings.PUBLIC_MAIL_PER_HOUR = 2

        answers = [self._register(client, number) for number in range(1, 5)]

        assert [answer.status_code for answer in answers] == [302, 302, 200, 200]
        assert len(mail.outbox) == 2

    def test_site_wide_limit_stops_reset_letters(
        self, client: Client, settings: Any, make_user: Any
    ) -> None:
        """Сверх общего предела письмо восстановления не уходит, а ответ страницы прежний."""
        settings.PUBLIC_MAIL_PER_HOUR = 1
        for number in (1, 2):
            make_user(email=f"owner-{number}@example.org")

        answers = [
            client.post(
                reverse("accounts:password-reset"),
                {"email": f"owner-{number}@example.org"},
                REMOTE_ADDR=f"198.51.100.{number}",
            )
            for number in (1, 2)
        ]

        assert [answer.status_code for answer in answers] == [302, 302]
        assert len(mail.outbox) == 1

    def test_confirmation_letter_carries_no_text_of_the_applicant(self, client: Client) -> None:
        """
        Письмо подтверждения уходит на адрес, владелец которого мог и не регистрироваться:
        имя из формы — текст того, кто её заполнял, — в письмо не попадает.
        """
        bait = "Срочно перейдите на http://attacker.example"
        client.post(
            reverse("accounts:register"),
            {
                "email": "victim@example.org",
                "full_name": bait,
                "password1": "correct-horse-battery-9",
                "password2": "correct-horse-battery-9",
                "accepted_terms": "on",
                "accepted_consent": "on",
                "website": "",
            },
        )

        assert len(mail.outbox) == 1
        assert "attacker.example" not in mail.outbox[0].body
        assert "attacker.example" not in mail.outbox[0].subject


class TestServicePages:
    """Служебные адреса."""

    def test_security_contact_is_published(self, client: Client) -> None:
        """Адрес для сообщений об уязвимостях — по RFC 9116, со сроком действия впереди."""
        response = client.get("/.well-known/security.txt")
        text = response.content.decode()

        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/plain")
        assert f"Contact: mailto:{django_settings.PROJECT_AUTHOR_EMAIL}" in text
        assert "Expires: 20" in text
        assert "Canonical: http://testserver/.well-known/security.txt" in text

    def test_profile_update_has_no_page_of_its_own(
        self, admin_panel_client: Client, member: Any
    ) -> None:
        """Адрес правки сведений принимает только отправку формы: раньше запрос GET давал 500."""
        path = reverse("dashboard:user-profile", kwargs={"public_id": member.public_id})

        assert admin_panel_client.get(path).status_code == 405


class TestDeploymentFiles:
    """Настройки прокси и контейнеров, от которых зависит устойчивость стенда."""

    @staticmethod
    def _compose() -> dict[str, Any]:
        return yaml.safe_load((ROOT / "docker" / "compose.prod.yml").read_text(encoding="utf-8"))

    @staticmethod
    def _caddyfile() -> str:
        return (ROOT / "docker" / "Caddyfile").read_text(encoding="utf-8")

    def test_proxy_takes_the_request_body_before_the_application(self) -> None:
        """
        Тело запроса прокси принимает целиком: иначе несколько соединений, шлющих тело
        по байту, занимают все рабочие потоки приложения, и сайт перестаёт отвечать.
        """
        text = self._caddyfile()

        assert "request_buffers 32MB" in text
        assert "read_header 10s" in text
        assert "read_body" in text

    def test_proxy_sheds_the_load_it_cannot_serve(self) -> None:
        assert "unhealthy_request_count" in self._caddyfile()

    def test_readiness_details_stay_inside(self) -> None:
        text = self._caddyfile()

        assert "handle /readyz" in text
        assert text.index("handle /readyz") < text.index("reverse_proxy web:8000")

    def test_static_is_served_precompressed(self) -> None:
        assert "precompressed br gzip" in self._caddyfile()

    def test_containers_drop_privileges(self) -> None:
        services = self._compose()["services"]

        for name, service in services.items():
            assert service.get("cap_drop") == ["ALL"], name
            assert "no-new-privileges:true" in service.get("security_opt", []), name
            assert service.get("pids_limit"), name
        assert services["caddy"]["cap_add"] == ["NET_BIND_SERVICE"]
        assert "cap_add" not in services["web"]
        assert services["web"]["init"] is True

    def test_database_and_cache_are_not_reachable_from_the_proxy(self) -> None:
        compose = self._compose()
        services = compose["services"]

        assert compose["networks"]["backend"]["internal"] is True
        assert services["postgres"]["networks"] == ["backend"]
        assert services["valkey"]["networks"] == ["backend"]
        assert services["caddy"]["networks"] == ["edge"]
        assert "ports" not in services["postgres"]
        assert "ports" not in services["valkey"]

    def test_proxy_memory_holds_the_buffered_bodies(self) -> None:
        """Под нагрузкой 128 МБ не хватало: ядро останавливало прокси."""
        limit = self._compose()["services"]["caddy"]["mem_limit"]

        assert limit.endswith("m")
        assert int(limit[:-1]) >= 384

    def test_keys_never_reach_the_image(self) -> None:
        """Правило без «**/» действует только в корне: ключ в подкаталоге попадал в образ."""
        rules = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()

        assert "**/*.key" in rules
        assert "**/*.pem" in rules

    def test_image_is_built_from_the_commit(self) -> None:
        text = (ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")

        assert "git archive --format=tar HEAD" in text

    def test_application_is_not_the_database_superuser(self) -> None:
        """
        Суперпользователь образа — служебный; роль приложения заводит сценарий при создании
        каталога данных, и выкладка этот сценарий на сервер передаёт.
        """
        postgres = self._compose()["services"]["postgres"]
        script = (ROOT / "docker" / "postgres-init.sh").read_text(encoding="utf-8")
        deploy = (ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")

        assert postgres["environment"]["POSTGRES_USER"] == "postgres"
        assert "APP_DB_USER" in postgres["environment"]
        assert any(
            "postgres-init.sh:/docker-entrypoint-initdb.d/" in v for v in postgres["volumes"]
        )
        assert "SUPERUSER" not in script
        assert 'OWNER TO :"app_user"' in script
        assert "docker/postgres-init.sh" in deploy


class TestProductionSettings:
    """Настройки рабочего стенда."""

    @staticmethod
    def _production_settings() -> Any:
        import importlib

        return importlib.import_module("config.settings.production")

    def test_log_format_reaches_the_handler(self) -> None:
        """Формат стенда проставлен обработчику: раньше он оставался форматом разработки."""
        production = self._production_settings()

        assert production.LOGGING["handlers"]["console"]["formatter"] == production.LOG_FORMAT

    def test_database_statements_are_time_limited(self) -> None:
        production = self._production_settings()
        options = production.DATABASES["default"]["OPTIONS"]["options"]

        assert "statement_timeout=" in options
        assert "idle_in_transaction_session_timeout=" in options
        assert production.DATABASES["default"]["CONN_HEALTH_CHECKS"] is True
