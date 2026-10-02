"""Статистика посещений: команда GoAccess, разбор отчёта, раздел панели и выгрузка по дням."""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from django.conf import settings
from django.core.management import CommandError, call_command
from django.test import Client
from django.urls import reverse

from apps.core import visits
from apps.core.models import ServiceBeat

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

FIXTURE = Path(settings.BASE_DIR) / "tests" / "fixtures" / "visits" / "report.json"


@pytest.fixture
def visit_dirs() -> Iterator[tuple[Path, Path]]:
    """Пустые каталоги журналов и отчёта."""
    logs, target = Path(settings.VISITS_LOG_DIR), Path(settings.VISITS_DIR)
    for directory in (logs, target):
        shutil.rmtree(directory, ignore_errors=True)
        directory.mkdir(parents=True)
    yield logs, target
    for directory in (logs, target):
        shutil.rmtree(directory, ignore_errors=True)


@pytest.fixture
def report(visit_dirs: tuple[Path, Path]) -> dict[str, Any]:
    """Отчёт GoAccess, снятый на пробном стенде, — на месте отчёта стенда."""
    _logs, target = visit_dirs
    shutil.copy(FIXTURE, target / visits.REPORT_NAME)
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class TestSummary:
    """Сводка по настоящему отчёту GoAccess."""

    def test_totals_days_and_pages(self, report: dict[str, Any]) -> None:
        """Итоги, дни по порядку, месяцы и страницы берутся из панелей отчёта."""
        summary = visits.summarize(report)
        assert summary is not None
        general = report["general"]
        assert summary["general"]["visitors"] == general["unique_visitors"]
        assert summary["general"]["valid"] == general["valid_requests"]
        days = summary["days"]
        assert days, "в отчёте есть дни"
        assert [row["day"] for row in days] == sorted(row["day"] for row in days)
        assert sum(row["visitors"] for row in days) == sum(
            month["visitors"] for month in summary["months"]
        )
        assert summary["pages"][0]["hits"] >= summary["pages"][-1]["hits"]
        assert all(not row["label"].startswith("/static/") for row in summary["pages"])
        assert summary["languages"].get("ru", 0) > 0

    def test_shares_add_up(self, report: dict[str, Any]) -> None:
        """Доли систем и браузеров в сумме — единица."""
        summary = visits.summarize(report)
        assert summary is not None
        for name in ("systems", "browsers"):
            assert summary[name]
            assert abs(sum(row["share"] for row in summary[name]) - 1) < 1e-9

    def test_tolerates_odd_input(self) -> None:
        """Пустой отчёт, неизвестные дни и числа без обёртки не роняют разбор."""
        assert visits.summarize(None) is None
        summary = visits.summarize(
            {
                "general": {"unique_visitors": "3"},
                "visitors": {
                    "data": [
                        {"data": "20260925", "hits": 5, "visitors": 2},
                        {"data": "не дата", "hits": {"count": 1}},
                        {"data": "25/Sep/2026", "hits": {"count": 1}, "visitors": {"count": 1}},
                    ]
                },
                "requests": "испорчено",
            }
        )
        assert summary is not None
        assert summary["general"]["visitors"] == 3
        assert summary["days"] == [
            {"day": date(2026, 9, 25), "hits": 5, "visitors": 2},
            {"day": date(2026, 9, 25), "hits": 1, "visitors": 1},
        ]
        assert summary["pages"] == []


def caddy_line(uri: str, *, ip: str = "203.0.113.57", ts: float = 1790355041.48) -> str:
    """Строка журнала Caddy в JSON, как её пишет стенд."""
    return json.dumps(
        {
            "level": "info",
            "ts": ts,
            "logger": "http.log.access.log0",
            "request": {
                "remote_ip": ip,
                "client_ip": ip,
                "proto": "HTTP/2.0",
                "method": "GET",
                "host": "regionlens.ru",
                "uri": uri,
                "headers": {
                    "User-Agent": ['Mozilla/5.0 "Test" Browser'],
                    "Referer": ["https://yandex.ru/search/?text=regionlens"],
                },
            },
            "status": 200,
            "size": 1234,
        }
    )


class TestHistory:
    """Перенос строк журнала в историю: маскировка, отбор, смещения."""

    def test_line_is_masked(self) -> None:
        """Адрес без последней части, путь без строки запроса, кавычки не ломают формат."""
        converted = visits.history_line(caddy_line("/ru/map/?series=M001:00&year=2024").encode())
        assert converted is not None
        moment, line = converted
        assert moment.utcoffset().total_seconds() == 3 * 3600
        assert line.startswith("203.0.113.0 - - [25/Sep/2026:19:50:41 +0300] ")
        assert '"GET /ru/map/ HTTP/2.0" 200 1234' in line
        assert "series=" not in line
        assert "Mozilla/5.0 'Test' Browser" in line
        assert "203.0.113.57" not in line

    @pytest.mark.parametrize(
        ("uri", "expected"),
        [
            ("/ru/password/reset/MTQ/cz4a1b-9f8e7d/", "/ru/password/reset/*/*/"),
            (
                "/ru/cabinet/security/email/confirm/eyJ1Ijo:1tAbCd/",
                "/ru/cabinet/security/email/confirm/*/",
            ),
        ],
    )
    def test_tokens_are_masked(self, uri: str, expected: str) -> None:
        """Служебные метки в адресах не попадают в историю."""
        converted = visits.history_line(caddy_line(uri).encode())
        assert converted is not None
        assert f"GET {expected} " in converted[1]

    @pytest.mark.parametrize("uri", ["/static/css/tokens.3f2a.css", "/healthz", "/readyz"])
    def test_not_visits_are_skipped(self, uri: str) -> None:
        """Статика и проверки состояния — не посещения."""
        assert visits.history_line(caddy_line(uri).encode()) is None

    def test_ipv6_and_garbage(self) -> None:
        """IPv6 усекается до /64; испорченная строка пропускается."""
        assert visits.mask_ip("2001:db8:1:2:3:4:5:6") == "2001:db8:1:2::"
        assert visits.mask_ip("не адрес") == "0.0.0.0"
        assert visits.history_line(b"not json\n") is None

    def test_offsets_survive_rotation(self, visit_dirs: tuple[Path, Path]) -> None:
        """Строки читаются один раз; после деления журнала дочитывается прежняя часть."""
        logs, target = visit_dirs
        current = logs / "access.log"
        current.write_text(caddy_line("/ru/") + "\n" + caddy_line("/ru/regions/") + "\n", "utf-8")
        assert visits.take_new_lines(visits.log_files(), target) == 2
        assert visits.take_new_lines(visits.log_files(), target) == 0

        # Строка дописана и файл переименован делением, затем начат новый.
        with current.open("a", encoding="utf-8") as log:
            log.write(caddy_line("/ru/terms/") + "\n")
        current.rename(logs / "access-2026-09-25T20-00-00.000.log")
        current.write_text(caddy_line("/en/") + "\n" + caddy_line("/en/terms/")[:40], "utf-8")
        assert visits.take_new_lines(visits.log_files(), target) == 2

        history = (target / visits.HISTORY_DIR / "2026-09.log").read_text("utf-8").splitlines()
        assert [line.split('"')[1].split()[1] for line in history] == [
            "/ru/",
            "/ru/regions/",
            "/ru/terms/",
            "/en/",
        ]

    def test_reused_file_number_is_read_from_start(self, visit_dirs: tuple[Path, Path]) -> None:
        """Номер удалённой части у нового файла: первая строка другая — чтение с начала."""
        logs, target = visit_dirs
        current = logs / "access.log"
        current.write_text(caddy_line("/ru/") + "\n" + caddy_line("/ru/about/") + "\n", "utf-8")
        key = str(current.stat().st_ino)
        stale = {key: {"offset": 10**6, "first": "удалённая часть"}}
        (target / visits.STATE_NAME).write_text(json.dumps(stale), encoding="utf-8")
        assert visits.take_new_lines(visits.log_files(), target) == 2


class TestRefresh:
    """Запуск GoAccess и отметка для «Состояния служб»."""

    def test_command_line(self, visit_dirs: tuple[Path, Path]) -> None:
        """Журналы — по времени, текущий последним; отчёт по истории без базы GoAccess."""
        logs, target = visit_dirs
        old, current = logs / "access-2026-09-01T00-00-00.000.log", logs / "access.log"
        old.write_text("{}\n", encoding="utf-8")
        current.write_text("{}\n", encoding="utf-8")
        (logs / "other.txt").write_text("", encoding="utf-8")
        files = visits.log_files()
        assert files[-1] == current
        assert set(files) == {old, current}

        history = [target / visits.HISTORY_DIR / "2026-09.log"]
        command = visits.goaccess_command(history, target)
        assert "--log-format=COMBINED" in command
        assert "--ignore-crawlers" in command
        assert f"--output={target / visits.REPORT_NAME}" in command
        assert not any(flag in command for flag in ("--persist", "--restore"))

    def test_no_logs_is_recorded(self, visit_dirs: tuple[Path, Path]) -> None:
        """Без журналов и истории — отказ с причиной, отмеченный в «Состоянии служб»."""
        with pytest.raises(CommandError, match="Журналов нет"):
            call_command("visits")
        assert not ServiceBeat.objects.get(service=ServiceBeat.Service.VISITS).ok

    def test_success_is_recorded(self, visit_dirs: tuple[Path, Path]) -> None:
        """Удачный запуск: история пополнена, отчёт на месте, отметка с числом посетителей."""
        logs, target = visit_dirs
        (logs / "access.log").write_text(caddy_line("/ru/") + "\n", encoding="utf-8")

        def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            assert command[1].endswith("2026-09.log")
            shutil.copy(FIXTURE, target / visits.REPORT_NAME)
            return subprocess.CompletedProcess(command, 0, "", "")

        with mock.patch("apps.core.visits.subprocess.run", side_effect=fake_run):
            call_command("visits")
        beat = ServiceBeat.objects.get(service=ServiceBeat.Service.VISITS)
        assert beat.ok
        assert beat.detail["lines"] == 1
        expected = json.loads(FIXTURE.read_text(encoding="utf-8"))["general"]["unique_visitors"]
        assert beat.detail["visitors"] == expected

    def test_goaccess_failure_is_recorded(self, visit_dirs: tuple[Path, Path]) -> None:
        """Отказ GoAccess — ошибка команды и его сообщение в отметке; история не теряется."""
        logs, target = visit_dirs
        (logs / "access.log").write_text(caddy_line("/ru/") + "\n", encoding="utf-8")
        failed = subprocess.CompletedProcess(["goaccess"], 1, "", "Token 'x' doesn't match")
        with (
            mock.patch("apps.core.visits.subprocess.run", return_value=failed),
            pytest.raises(CommandError, match="doesn't match"),
        ):
            call_command("visits")
        beat = ServiceBeat.objects.get(service=ServiceBeat.Service.VISITS)
        assert "doesn't match" in beat.detail["error"]
        assert visits.history_files(target)


class TestPanel:
    """Раздел «Посещения» панели управления."""

    def test_empty_state(self, admin_panel_client: Client, visit_dirs: tuple[Path, Path]) -> None:
        """Без отчёта — подсказка о таймере и команде."""
        response = admin_panel_client.get(reverse("dashboard:visits"))
        assert response.status_code == 200
        assert "Статистики ещё нет" in response.content.decode("utf-8")

    def test_report_is_shown(self, admin_panel_client: Client, report: dict[str, Any]) -> None:
        """Итоги, график, месяцы и страницы — на странице; вкладка — в меню панели."""
        response = admin_panel_client.get(reverse("dashboard:visits"))
        content = response.content.decode("utf-8")
        assert response.context["chart_option"]["series"][0]["data"]
        assert 'data-options="visits-option"' in content
        assert response.context["summary"]["pages"][0]["label"] in content
        assert f'href="{reverse("dashboard:visits")}"' in content

    def test_open_to_any_panel_role(
        self, client: Client, editor: Any, report: dict[str, Any]
    ) -> None:
        """Раздел открыт с любым правом панели — и редактору содержимого."""
        from tests.conftest import login_with_code

        login_with_code(client, editor)
        assert client.get(reverse("dashboard:visits")).status_code == 200

    def test_closed_to_users(self, member_client: Client) -> None:
        """Без права панели — отказ."""
        assert member_client.get(reverse("dashboard:visits")).status_code == 403

    def test_days_export(self, admin_panel_client: Client, report: dict[str, Any]) -> None:
        """Выгрузка по дням: шапка и строка на каждый день."""
        response = admin_panel_client.get(reverse("dashboard:visits-export"))
        assert response["Content-Type"] == "text/csv"
        lines = response.content.decode("utf-8-sig").strip().splitlines()
        assert lines[0] == "date,visitors,hits"
        assert len(lines) - 1 == len(visits.summarize(report)["days"])

    def test_health_entry(self, admin_panel_client: Client, visit_dirs: tuple[Path, Path]) -> None:
        """«Состояние служб» называет последний пересчёт и ведёт в раздел."""
        ServiceBeat.record(ServiceBeat.Service.VISITS, ok=True, visitors=12)
        health = admin_panel_client.get(reverse("dashboard:index")).context["health"]
        entry = next(item for item in health if str(item["title"]) == "Статистика посещений")
        assert entry["ok"]
        assert entry["url"] == reverse("dashboard:visits")
        assert "12" in entry["extra"]
