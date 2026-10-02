"""Письма администраторам: адресаты, повтор, отказ почты, неудачная сборка без панели."""

from __future__ import annotations

import smtplib
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from django.core import mail
from django.test import override_settings

from apps.core import alerts
from apps.warehouse import builds
from apps.warehouse.etl.pipeline import EtlError
from apps.warehouse.models import EtlRun

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

ADMIN = "admin@example.org"


@override_settings(ADMINS=[ADMIN])
def test_letter_and_repeat() -> None:
    """Событие — письмо с приставкой темы; то же событие раньше срока — без письма."""
    assert alerts.notify("event", "склад не собран", "Причина")
    assert not alerts.notify("event", "склад не собран", "Причина")
    assert alerts.notify("other", "выпуск не разобран", "Причина")
    assert [letter.subject for letter in mail.outbox] == [
        "[RegionLens] склад не собран",
        "[RegionLens] выпуск не разобран",
    ]
    assert mail.outbox[0].to == [ADMIN]


@override_settings(ADMINS=[ADMIN])
def test_repeat_after_period() -> None:
    """Без срока повтора письмо уходит снова."""
    assert alerts.notify("event", "тема", "текст", repeat_after=timedelta(seconds=0))
    assert alerts.notify("event", "тема", "текст", repeat_after=timedelta(seconds=0))
    assert len(mail.outbox) == 2


def test_no_recipients() -> None:
    """Без DJANGO_ADMINS письмо не собирается."""
    with override_settings(ADMINS=[]):
        assert not alerts.notify("event", "тема", "текст")
    assert mail.outbox == []


@override_settings(ADMINS=[ADMIN])
def test_failed_letter_is_sent_next_time() -> None:
    """Не ушедшее письмо не считается отправленным."""
    refusal = smtplib.SMTPServerDisconnected("почтовый узел недоступен")
    with mock.patch("apps.core.mail.send_mail", side_effect=refusal):
        assert not alerts.notify("event", "тема", "текст")
    assert alerts.notify("event", "тема", "текст")
    assert len(mail.outbox) == 1


def test_panel_address_is_russian() -> None:
    """Адрес панели в письме — на языке сайта по умолчанию, без домена."""
    assert alerts.panel_address("dashboard:etl-run", 7) == "/ru/manage/data/runs/7/"


class TestBuildLetters:
    """Неудачная сборка по таймеру или команде — письмо; из панели — нет."""

    @pytest.fixture(autouse=True)
    def admins(self, settings: Any) -> None:
        settings.ADMINS = [ADMIN]

    @staticmethod
    def failing_build(monkeypatch: pytest.MonkeyPatch) -> None:
        def build(self: Any) -> None:
            raise EtlError("Исходный набор не прошёл проверку")

        monkeypatch.setattr("apps.warehouse.etl.pipeline.Pipeline.build", build)

    def test_automatic_build(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        self.failing_build(monkeypatch)
        run = EtlRun.objects.create(mode=EtlRun.Mode.FULL, source_path=str(tmp_path / "a"))
        with pytest.raises(EtlError):
            builds.execute(run)
        assert len(mail.outbox) == 1
        assert "Исходный набор не прошёл проверку" in mail.outbox[0].body
        assert f"/ru/manage/data/runs/{run.pk}/" in mail.outbox[0].body

    def test_panel_build(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, administrator: Any
    ) -> None:
        self.failing_build(monkeypatch)
        run = EtlRun.objects.create(
            mode=EtlRun.Mode.FULL, source_path=str(tmp_path / "a"), started_by=administrator
        )
        with pytest.raises(EtlError):
            builds.execute(run)
        assert mail.outbox == []
