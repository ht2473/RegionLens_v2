"""Условия использования, политика обработки и согласие на обработку персональных данных."""

from __future__ import annotations

import re
from datetime import timedelta
from io import StringIO
from pathlib import Path

import pytest
from axes.models import AccessAttempt
from django.conf import settings
from django.core import mail
from django.core.management import call_command
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.core.documents import (
    ACCESS_LOG_DAYS,
    CONSENT_REVISION,
    TICKET_CONTACT_DAYS,
    TICKET_TRACE_DAYS,
)
from apps.core.logging import FormFreeReporterFilter
from apps.feedback.constants import TicketStatus
from apps.feedback.models import Ticket
from apps.sources.collect import SOURCES
from tests.conftest import TEST_PASSWORD

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

REGISTRATION = {
    "email": "reader@example.org",
    "full_name": "Читатель",
    "password1": "длинный-пароль-читателя",
    "password2": "длинный-пароль-читателя",
}


class TestTermsPage:
    """Содержание страницы условий."""

    def test_sections_and_sources(self, client: Client) -> None:
        """Все разделы на месте; условия — у набора и у каждого собираемого источника."""
        response = client.get(reverse("core:terms"))
        assert response.status_code == 200
        content = response.content.decode("utf-8")

        for anchor in (
            "general",
            "data",
            "calculations",
            "api",
            "account",
            "prohibited",
            "warranty",
            "code",
            "changes",
            "law",
            "contacts",
        ):
            assert f'id="{anchor}"' in content
            assert f'href="#{anchor}"' in content

        assert len(response.context["source_terms"]) == len(SOURCES)
        for module in SOURCES.values():
            assert module.licence_ru in content
        assert settings.DATA_SOURCE_LICENSE in content
        assert settings.DATA_SOURCE_URL in content
        assert f'href="{reverse("core:privacy")}"' in content
        assert f'href="{reverse("core:consent")}"' in content

    def test_russian_names_are_marked_on_english_page(self, client: Client) -> None:
        """Набор назван по-английски с русским названием рядом; «Росстат» переведён."""
        content = client.get("/en/terms/").content.decode("utf-8")
        assert (
            f'{settings.DATA_SOURCE_TITLE_EN} (<span lang="ru">{settings.DATA_SOURCE_TITLE}</span>)'
            in content
        )
        assert settings.PROJECT_AUTHOR_EN in content
        assert f'<span lang="ru">{settings.DATA_SOURCE_PROCESSOR}</span>' in content
        assert settings.DATA_SOURCE_ORIGIN not in content

    def test_api_rate_comes_from_settings(self, client: Client) -> None:
        """Предел API называется по настройкам."""
        content = client.get(reverse("core:terms")).content.decode("utf-8")
        assert "10 000 обращений в час" in content.replace(chr(0xA0), " ")

    def test_english_version(self, client: Client) -> None:
        """Английская версия открывается с английскими условиями источников."""
        with override_settings(LANGUAGE_CODE="en"):
            response = client.get("/en/terms/")
        assert response.status_code == 200
        content = response.content.decode("utf-8")
        for module in SOURCES.values():
            assert module.licence_en in content

    def test_documents_in_sitemap(self, client: Client) -> None:
        """Условия, политика и согласие входят в карту сайта."""
        sitemap = client.get("/sitemap.xml").content.decode("utf-8")
        for name in ("core:terms", "core:privacy", "core:consent"):
            assert reverse(name) in sitemap


class TestPrivacyPage:
    """Политика обработки: оператор, цели, сроки из настроек."""

    def test_sections_and_operator(self, client: Client) -> None:
        """Разделы политики на месте, оператор и адрес для обращений названы."""
        content = client.get(reverse("core:privacy")).content.decode("utf-8")
        for anchor in ("operator", "subjects", "purposes", "cookies", "transfer", "rights"):
            assert f'id="{anchor}"' in content
        assert settings.PROJECT_AUTHOR in content
        assert settings.PROJECT_AUTHOR_EMAIL in content
        assert "152-ФЗ" in content

    def test_numbers_come_from_settings(self, client: Client) -> None:
        """Срок сеанса, журнала, копий и обращений называются по настройкам."""
        with override_settings(BACKUP_RETENTION_DAYS=7):
            content = client.get(reverse("core:privacy")).content.decode("utf-8")
        content = content.replace(chr(0xA0), " ")
        assert f"сеанс после входа ({settings.SESSION_COOKIE_AGE // 86400} суток)" in content
        assert "исчезает из них через 7 суток" in content
        assert f"{ACCESS_LOG_DAYS} суток; статистика посещений" in content
        assert f"{TICKET_TRACE_DAYS} суток" in content
        assert f"через {TICKET_CONTACT_DAYS} суток" in content

    def test_backup_term_follows_external_storage(self, client: Client) -> None:
        """С внешним хранилищем удалённое живёт в копиях до его срока; без срока — так и сказано."""
        url = reverse("core:privacy")
        with override_settings(
            BACKUP_RETENTION_DAYS=14, BACKUP_REMOTE="s3:copies", BACKUP_REMOTE_RETENTION_DAYS=30
        ):
            assert "исчезает из них через 30 суток" in client.get(url).content.decode("utf-8")
        with override_settings(BACKUP_REMOTE="s3:copies", BACKUP_REMOTE_RETENTION_DAYS=0):
            content = client.get(url).content.decode("utf-8")
        assert "хранятся без срока" in content
        assert "исчезает из них через" not in content

    def test_error_tracking_is_mentioned_only_when_enabled(self, client: Client) -> None:
        """О службе учёта ошибок сказано, только если она подключена."""
        phrase = "службу учёта ошибок"
        assert phrase not in client.get(reverse("core:privacy")).content.decode("utf-8")
        with override_settings(SENTRY_DSN="https://key@errors.example/1"):
            assert phrase in client.get(reverse("core:privacy")).content.decode("utf-8")

    def test_english_version_opens(self, client: Client) -> None:
        """Английские политика и согласие открываются."""
        for url in ("/en/privacy/", "/en/consent/"):
            assert client.get(url).status_code == 200


class TestConsent:
    """Флажок согласия, запись даты и редакции, повторное согласие."""

    def test_registration_has_separate_consent_checkbox(self, client: Client) -> None:
        """Условия и согласие — два флажка; подпись согласия ведёт на его текст."""
        content = client.get(reverse("accounts:register")).content.decode("utf-8")
        labels = re.findall(r'<label class="checkbox">.*?</label>', content, re.DOTALL)
        assert len(labels) == 2
        assert f'href="{reverse("core:terms")}"' in labels[0]
        assert f'href="{reverse("core:consent")}"' in labels[1]
        assert all('target="_blank"' in label for label in labels)
        assert 'name="accepted_consent"' in content
        assert f'href="{reverse("core:privacy")}"' in content

    def test_registration_requires_both(self, client: Client) -> None:
        """Без флажков учётная запись не заводится."""
        response = client.post(reverse("accounts:register"), REGISTRATION)
        assert response.status_code == 200
        errors = response.context["form"].errors
        assert "accepted_terms" in errors
        assert "accepted_consent" in errors
        assert not User.objects.filter(email=REGISTRATION["email"]).exists()

    def test_registration_records_consent(self, client: Client) -> None:
        """Дата и редакция согласия записываются в учётную запись."""
        client.post(
            reverse("accounts:register"),
            {**REGISTRATION, "accepted_terms": "on", "accepted_consent": "on"},
        )
        user = User.objects.get(email=REGISTRATION["email"])
        assert user.consent_at is not None
        assert user.consent_revision == CONSENT_REVISION
        assert user.has_current_consent

    def test_feedback_records_consent(self, client: Client) -> None:
        """Обращение хранит дату и редакцию согласия; подпись флажка ведёт на его текст."""
        page = client.get(reverse("feedback:create")).content.decode("utf-8")
        assert f'href="{reverse("core:consent")}"' in page
        assert f'href="{reverse("core:privacy")}"' in page
        client.post(
            reverse("feedback:create"),
            {
                "contact_name": "Читатель",
                "contact_email": "reader@example.org",
                "topic": "other",
                "subject": "Вопрос о данных",
                "body": "Подробное описание вопроса о данных региона.",
                "consent": "on",
            },
        )
        ticket = Ticket.objects.get()
        assert ticket.consent_at is not None
        assert ticket.consent_revision == CONSENT_REVISION

    def test_missing_consent_can_be_given_in_cabinet(
        self, member_client: Client, member: User
    ) -> None:
        """Учётная запись без согласия видит предложение дать его и даёт одним флажком."""
        page = member_client.get(reverse("accounts:settings")).content.decode("utf-8")
        assert reverse("accounts:consent") in page
        member_client.post(reverse("accounts:consent"), {"consent": "1"})
        member.refresh_from_db()
        assert member.has_current_consent
        page = member_client.get(reverse("accounts:settings")).content.decode("utf-8")
        assert reverse("accounts:consent") not in page
        assert f'href="{reverse("core:privacy")}"' in page

    def test_export_includes_consent(self, member_client: Client, member: User) -> None:
        """Выгрузка своих данных называет согласие."""
        member.record_consent()
        payload = member_client.get(reverse("accounts:data-export")).json()
        assert payload["profile"]["consent"]["revision"] == CONSENT_REVISION.isoformat()


class TestPrune:
    """Удаление персональных данных по срокам политики."""

    def test_expired_data_is_removed(self) -> None:
        """Попытки входа, адрес IP и контакты старых обращений удаляются, свежие — нет."""
        now = timezone.now()
        old = now - timedelta(days=TICKET_CONTACT_DAYS + 1)
        AccessAttempt.objects.create(
            username="x@example.org",
            ip_address="10.0.0.1",
            user_agent="test",
            failures_since_start=1,
        )
        # Время попытки проставляется при записи — сдвигается отдельно.
        AccessAttempt.objects.update(
            attempt_time=now - timedelta(hours=settings.AXES_COOLOFF_TIME + 1)
        )
        fresh = Ticket.objects.create(
            contact_name="Новый",
            contact_email="new@example.org",
            subject="s",
            body="b",
            ip_address="10.0.0.2",
            user_agent="ua",
        )
        stale = Ticket.objects.create(
            contact_name="Старый",
            contact_email="old@example.org",
            subject="s",
            body="b",
            ip_address="10.0.0.3",
            user_agent="ua",
            status=TicketStatus.ANSWERED,
        )
        waiting = Ticket.objects.create(
            contact_name="Ждёт",
            contact_email="wait@example.org",
            subject="s",
            body="b",
            ip_address="10.0.0.4",
            user_agent="ua",
        )
        Ticket.objects.filter(pk__in=[stale.pk, waiting.pk]).update(created_at=old, updated_at=old)

        call_command("prune_personal_data", stdout=StringIO())

        assert not AccessAttempt.objects.exists()
        fresh.refresh_from_db()
        stale.refresh_from_db()
        waiting.refresh_from_db()
        assert fresh.ip_address == "10.0.0.2"
        assert fresh.contact_email == "new@example.org"
        assert stale.ip_address is None
        assert stale.user_agent == ""
        assert stale.contact_email == ""
        # Обращение без ответа сохраняет адрес: цель — ответ — не достигнута.
        assert waiting.ip_address is None
        assert waiting.contact_email == "wait@example.org"


class TestErrorReports:
    """Письма об ошибках — без значений полей форм."""

    def test_post_values_are_hidden(self, rf: object) -> None:
        """Название поля остаётся, значение — нет."""
        request = rf.post("/", {"password": TEST_PASSWORD, "email": "reader@example.org"})  # type: ignore[attr-defined]
        cleaned = FormFreeReporterFilter().get_post_parameters(request)
        assert set(cleaned) == {"password", "email"}
        assert TEST_PASSWORD not in cleaned.values()
        assert "reader@example.org" not in cleaned.values()

    def test_setting_uses_filter(self) -> None:
        """Фильтр подключён настройкой."""
        assert settings.DEFAULT_EXCEPTION_REPORTER_FILTER.endswith("FormFreeReporterFilter")
        assert not mail.outbox


class TestDocumentLinks:
    """Ссылки на документы из подвала, API и «О проекте»."""

    def test_footer_and_api_docs(self, client: Client) -> None:
        """Подвал ведёт на условия и политику, описание API — на раздел условий."""
        about = client.get(reverse("core:about")).content.decode("utf-8")
        assert f'href="{reverse("core:terms")}"' in about
        assert f'href="{reverse("core:privacy")}"' in about
        docs = client.get(reverse("api:docs")).content.decode("utf-8")
        assert f'href="{reverse("core:terms")}#api"' in docs


def test_access_log_retention_matches_caddy() -> None:
    """Срок хранения журнала в политике совпадает с настройкой Caddy."""
    caddyfile = (Path(settings.BASE_DIR) / "docker" / "Caddyfile").read_text(encoding="utf-8")
    match = re.search(r"roll_keep_for\s+(\d+)h", caddyfile)
    assert match is not None
    assert int(match.group(1)) == ACCESS_LOG_DAYS * 24
