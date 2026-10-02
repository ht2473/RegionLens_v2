"""Письмо администраторам об ошибке 500: адресаты, предел частоты, отказ почты, проверка стенда."""

from __future__ import annotations

import logging
import smtplib
from collections.abc import Iterator
from typing import Any
from unittest import mock

import pytest
from django.core import mail
from django.core.cache import cache
from django.core.checks import run_checks
from django.test import Client, override_settings

from apps.core.logging import (
    ERROR_MAIL_LIMIT,
    ErrorMailHandler,
    _Limiter,
    error_mail_limiter,
    error_signature,
)
from apps.core.models import ServiceBeat

pytestmark = [
    pytest.mark.integration,
    pytest.mark.django_db,
    pytest.mark.urls("tests.support.error_urls"),
]

ADMIN = "admin@example.org"


@pytest.fixture
def request_log() -> Iterator[logging.Logger]:
    """Журнал django.request с обработчиком письма; настройки проверок его отключают."""
    log = logging.getLogger("django.request")
    saved = (log.disabled, log.level, log.handlers[:], log.propagate)
    handler = ErrorMailHandler()
    log.disabled, log.handlers, log.propagate = False, [handler], False
    log.setLevel(logging.ERROR)
    error_mail_limiter.reset()
    yield log
    log.disabled, log.level, log.handlers, log.propagate = saved
    error_mail_limiter.reset()


@pytest.fixture
def failing_client() -> Client:
    """Клиент, получающий страницу 500 вместо исключения."""
    return Client(raise_request_exception=False)


@override_settings(ADMINS=[ADMIN])
def test_error_page_sends_one_letter(request_log: logging.Logger, failing_client: Client) -> None:
    """Ошибка 500 — письмо администратору с трассировкой; повтор той же ошибки — без письма."""
    response = failing_client.get("/fail/")
    assert response.status_code == 500
    assert len(mail.outbox) == 1
    letter = mail.outbox[0]
    assert letter.to == [ADMIN]
    assert "Internal Server Error: /fail/" in letter.subject
    assert "RuntimeError" in letter.body
    assert ServiceBeat.objects.get(service=ServiceBeat.Service.MAIL).ok

    # Та же ошибка на другом адресе — то же место в коде: письма нет.
    assert failing_client.get("/fail/2/").status_code == 500
    assert len(mail.outbox) == 1

    # Ошибка в другом месте — новое письмо.
    assert failing_client.get("/fail-elsewhere/").status_code == 500
    assert len(mail.outbox) == 2


@override_settings(ADMINS=[ADMIN])
def test_not_ready_is_not_a_failure(request_log: logging.Logger, failing_client: Client) -> None:
    """Ответ 503 без исключения (склад не собран) — не повод для письма."""
    assert failing_client.get("/not-ready/").status_code == 503
    assert mail.outbox == []


def test_no_recipients_no_letter(request_log: logging.Logger, failing_client: Client) -> None:
    """Без DJANGO_ADMINS письмо не собирается."""
    with override_settings(ADMINS=[]):
        assert failing_client.get("/fail/").status_code == 500
    assert mail.outbox == []


@override_settings(ADMINS=[ADMIN])
def test_mail_failure_keeps_error_page(request_log: logging.Logger, failing_client: Client) -> None:
    """Отказ почтового узла не обрывает ответ: страница 500 и отметка отказа почты."""
    refusal = smtplib.SMTPServerDisconnected("соединение закрыто")
    with mock.patch(
        "django.core.mail.backends.locmem.EmailBackend.send_messages", side_effect=refusal
    ):
        response = failing_client.get("/fail/")
    assert response.status_code == 500
    assert "Ошибка на сервере" in response.content.decode("utf-8")
    beat = ServiceBeat.objects.get(service=ServiceBeat.Service.MAIL)
    assert not beat.ok
    assert "SMTPServerDisconnected" in beat.detail["error"]


class TestLimiter:
    """Предел писем в процессе и между процессами."""

    def test_signature_is_place_not_message(self) -> None:
        """Признак — вид исключения и место; текст сообщения и адрес не входят."""

        def record_for(message: str) -> logging.LogRecord:
            try:
                raise ValueError(message)
            except ValueError as error:
                return logging.LogRecord(
                    "django.request",
                    logging.ERROR,
                    __file__,
                    1,
                    "Internal Server Error: %s",
                    ("/a/",),
                    (type(error), error, error.__traceback__),
                )

        assert error_signature(record_for("один")) == error_signature(record_for("другой"))
        assert error_signature(record_for("один")).startswith("ValueError@")
        plain = logging.LogRecord(
            "django.request", logging.ERROR, __file__, 1, "Service Unavailable: %s", ("/b/",), None
        )
        assert error_signature(plain) == "django.request:Service Unavailable: %s"

    def test_repeat_and_cap(self) -> None:
        """Повтор запрещён на час; разных ошибок — не больше предела."""
        limiter = _Limiter()
        assert limiter.allow("A")
        assert not limiter.allow("A")
        for number in range(ERROR_MAIL_LIMIT - 1):
            assert limiter.allow(f"B{number}")
        assert not limiter.allow("C")

    def test_other_process_already_wrote(self) -> None:
        """Отметка в кэше от другого процесса запрещает письмо."""
        assert _Limiter().allow("общая")
        assert not _Limiter().allow("общая")

    def test_cache_down_leaves_decision_to_process(self) -> None:
        """Недоступный кэш не глушит письма: решает предел процесса."""
        with (
            mock.patch.object(cache, "add", return_value=False),
            mock.patch.object(cache, "get", return_value=None),
        ):
            limiter = _Limiter()
            assert limiter.allow("без кэша")
            assert not limiter.allow("без кэша")


class TestDeployCheck:
    """Проверка развёртывания: кто узнает об ошибке 500."""

    @staticmethod
    def ids(**overrides: Any) -> set[str]:
        with override_settings(**overrides):
            return {message.id for message in run_checks(include_deployment_checks=True)}

    def test_nobody_is_told(self) -> None:
        """Ни адресатов, ни Sentry — предупреждение."""
        assert "core.W001" in self.ids(ADMINS=[], SENTRY_DSN="")

    def test_recipients_or_sentry_are_enough(self) -> None:
        """Адресат или Sentry снимают предупреждение."""
        assert "core.W001" not in self.ids(ADMINS=[ADMIN], SENTRY_DSN="")
        assert "core.W001" not in self.ids(ADMINS=[], SENTRY_DSN="https://key@errors.example/1")

    def test_malformed_address(self) -> None:
        """Строка не адрес — предупреждение."""
        assert "core.W002" in self.ids(ADMINS=["Администратор"])


def test_panel_names_recipients(admin_panel_client: Client) -> None:
    """«Состояние служб» называет адресатов письма об ошибке."""
    with override_settings(ADMINS=[ADMIN]):
        health = admin_panel_client.get("/ru/manage/").context["health"]
    entry = next(item for item in health if str(item["title"]) == "Письма об ошибках")
    assert entry["ok"]
    assert entry["detail"] == ADMIN
