"""
Проверки служебной части: письма, сборка склада из панели, отметки служб, кэш.

Процесс сборки ``etl_build --run`` подменяется выполнением в процессе теста.
"""

from __future__ import annotations

import smtplib
from datetime import timedelta
from pathlib import Path
from typing import Any

import duckdb
import psycopg
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.core.cache import ResilientValkeyCache
from apps.core.models import ServiceBeat
from apps.feedback.constants import DeliveryStatus, TicketStatus, TicketTopic
from apps.feedback.models import Ticket
from apps.warehouse import builds, duckdb_client
from apps.warehouse.models import EtlRun
from tests.support.warehouse import accept_small_source

pytestmark = pytest.mark.integration


@pytest.fixture
def ticket(db: None) -> Ticket:
    """Принятое обращение без ответа."""
    return Ticket.objects.create(
        contact_name="Петров Пётр",
        contact_email="petrov@example.com",
        topic=TicketTopic.METHOD,
        subject="Вопрос по индексу Тейла",
        body="Почему декомпозиция ведётся по федеральным округам?",
    )


def refuse_mail(*args: Any, **kwargs: Any) -> None:
    """Почтовый узел недоступен."""
    raise smtplib.SMTPServerDisconnected("почтовый узел недоступен")


@pytest.fixture(autouse=True)
def inline_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """Сборка, начатая из панели, выполняется в процессе теста, а не отдельным процессом."""

    def run_here(run: EtlRun) -> None:
        builds.run_queued(run.pk)

    monkeypatch.setattr(builds, "launch", run_here)


# ---------------------------------------------------------------------------------------
# Ответ на обращение
# ---------------------------------------------------------------------------------------


def test_answer_is_delivered_and_marked(
    second_admin_client: Client, ticket: Ticket, mailoutbox: list[Any]
) -> None:
    """Ответ сохранён, письмо ушло, в обращении отметка о доставке."""
    second_admin_client.post(ticket.manage_url, {"action": "answer", "answer": "Ответ"})

    ticket.refresh_from_db()
    assert ticket.status == TicketStatus.ANSWERED
    assert ticket.answer_delivery == DeliveryStatus.SENT
    assert ticket.answer_sent_at is not None
    assert ticket.answer_attempts == 1
    assert len(mailoutbox) == 1
    assert ServiceBeat.objects.get(service=ServiceBeat.Service.MAIL).ok


def test_failed_mail_keeps_answer_and_can_be_resent(
    second_admin_client: Client,
    ticket: Ticket,
    mailoutbox: list[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Отказ почтового узла не теряет ответ: письмо помечено недоставленным,
    а кнопка «Отправить ещё раз» доставляет его, когда почта ожила.
    """
    monkeypatch.setattr("apps.core.mail.send_mail", refuse_mail)
    second_admin_client.post(ticket.manage_url, {"action": "answer", "answer": "Сохранённый ответ"})

    ticket.refresh_from_db()
    assert ticket.status == TicketStatus.ANSWERED
    assert ticket.answer == "Сохранённый ответ"
    assert ticket.answer_delivery == DeliveryStatus.FAILED
    assert ticket.answer_attempts == 1
    assert "SMTPServerDisconnected" in ticket.answer_error
    assert not ServiceBeat.objects.get(service=ServiceBeat.Service.MAIL).ok

    page = second_admin_client.get(ticket.manage_url).content.decode()
    assert "Не доставлено" in page
    assert "Отправить ещё раз" in page

    monkeypatch.undo()
    second_admin_client.post(ticket.manage_url, {"action": "resend"})
    ticket.refresh_from_db()
    assert ticket.answer_delivery == DeliveryStatus.SENT
    assert ticket.answer_error == ""
    assert ticket.answer_attempts == 2
    assert len(mailoutbox) == 1


def test_delivered_answer_is_not_resent(
    second_admin_client: Client, ticket: Ticket, mailoutbox: list[Any]
) -> None:
    """Повторная отправка доставленного ответа отклоняется: второго письма нет."""
    second_admin_client.post(ticket.manage_url, {"action": "answer", "answer": "Ответ"})
    second_admin_client.post(ticket.manage_url, {"action": "resend"})

    assert len(mailoutbox) == 1


# ---------------------------------------------------------------------------------------
# Проверка служб и отметки
# ---------------------------------------------------------------------------------------


def test_record_backup_command(db: None) -> None:
    """Сценарий копирования отмечает копию командой."""
    call_command("record_backup", "--file", "db-1.dump", "--size", "2048", "--offsite", stdout=None)

    beat = ServiceBeat.objects.get(service=ServiceBeat.Service.BACKUP)
    assert beat.ok
    assert beat.detail["size_bytes"] == 2048
    assert beat.detail["offsite"] is True


def test_overview_shows_background_services(admin_panel_client: Client) -> None:
    """Обзор показывает сборку, копию и почту, даже если отметок ещё нет."""
    response = admin_panel_client.get(reverse("dashboard:index"))
    checks = {str(check["title"]): check for check in response.context["health"]}

    assert {"Последняя сборка", "Резервная копия", "Почта"} <= set(checks)
    # Отметки нет: копия не подтверждена — это тревога, а не «всё хорошо».
    assert not checks["Резервная копия"]["ok"]


def test_warm_cache_renders_popular_pages(warehouse: Any) -> None:
    """Прогрев собирает частые страницы на обоих языках."""
    from apps.core.warmup import WARM_PAGES, warm_cache

    assert warm_cache() == 2 * len(WARM_PAGES)


def test_abandoned_build_is_closed(db: None) -> None:
    """
    Сборка, процесс которой остановлен, закрывается и не запрещает новую загрузку.

    Процесс сборки может умереть вместе с контейнером, не отметив исход.
    """
    stale = EtlRun.objects.create(
        status=EtlRun.Status.RUNNING, started_at=timezone.now() - timedelta(hours=1)
    )
    fresh = EtlRun.objects.create(status=EtlRun.Status.QUEUED)

    assert builds.active_run() == fresh

    stale.refresh_from_db()
    assert stale.status == EtlRun.Status.FAILED
    assert "процесс остановлен" in stale.error_message


def test_build_command_runs_panel_build(
    synthetic_dataset: Any, settings: Any, tmp_path: Path, db: None
) -> None:
    """Процесс сборки выполняет запуск, начатый панелью, и не повторяет закрытый."""
    settings.DUCKDB_PATH = tmp_path / "panel.duckdb"
    run = EtlRun.objects.create(
        status=EtlRun.Status.QUEUED, source_path=str(synthetic_dataset.path)
    )
    try:
        with accept_small_source():
            call_command("etl_build", "--run", str(run.pk), stdout=None)
        run.refresh_from_db()
        assert run.status == EtlRun.Status.SUCCESS, run.error_message

        call_command("etl_build", "--run", str(run.pk), stdout=None)
        assert EtlRun.objects.count() == 1
    finally:
        duckdb_client.close_connections()


# ---------------------------------------------------------------------------------------
# Загрузка набора и сборка склада
# ---------------------------------------------------------------------------------------


@pytest.fixture
def build_target(settings: Any, tmp_path: Path, warehouse: Any) -> Path:
    """Сборка из панели пишет в свой файл, а не в общий склад прогона."""
    target = tmp_path / "rebuilt.duckdb"
    settings.DUCKDB_PATH = target
    settings.DATASET_UPLOAD_DIR = tmp_path / "sources"
    duckdb_client.close_connections()
    with accept_small_source():
        yield target
    duckdb_client.close_connections()


def upload(client: Client, path: Path) -> Any:
    """Отправить файл набора из панели."""
    payload = {"dataset": SimpleUploadedFile(path.name, path.read_bytes())}
    return client.post(reverse("dashboard:data-upload"), payload)


def test_upload_is_checked_then_built(
    admin_panel_client: Client, administrator: Any, warehouse: Any, build_target: Path
) -> None:
    """Загруженный набор сначала проверяется; склад собирается по нему после решения."""
    response = upload(admin_panel_client, warehouse.path)

    check = EtlRun.objects.latest("started_at")
    assert response.status_code == 302
    assert response["Location"] == reverse("dashboard:etl-run", kwargs={"pk": check.pk})
    assert check.mode == EtlRun.Mode.CANDIDATE
    assert check.status == EtlRun.Status.SUCCESS, check.error_message
    assert check.awaits_decision
    assert not build_target.exists()

    admin_panel_client.post(reverse("dashboard:candidate-accept", kwargs={"pk": check.pk}))

    run = EtlRun.objects.latest("started_at")
    assert run.mode == EtlRun.Mode.FULL
    assert run.status == EtlRun.Status.SUCCESS, run.error_message
    assert run.started_by == administrator
    assert build_target.exists()
    assert Path(run.source_path).parent == Path(build_target.parent / "sources")
    assert "Сборка завершена" in run.log
    # Ручная пересборка возьмёт загруженный файл, а не исходный.
    assert builds.current_source_path() == Path(run.source_path)


def test_finished_build_refreshes_progress_card(
    admin_panel_client: Client, warehouse: Any, build_target: Path
) -> None:
    """Запрос хода по законченной сборке перезагружает карточку целиком."""
    upload(admin_panel_client, warehouse.path)
    run = EtlRun.objects.latest("started_at")

    response = admin_panel_client.get(
        reverse("dashboard:etl-run", kwargs={"pk": run.pk}), headers={"HX-Request": "true"}
    )

    assert response.headers.get("HX-Refresh") == "true"
    assert 'id="run-progress"' in response.content.decode()
    assert "<html" not in response.content.decode()


def test_active_build_card_polls_progress(admin_panel_client: Client, db: None) -> None:
    """Карточка ждущей сборки запрашивает свой ход сама."""
    run = EtlRun.objects.create(status=EtlRun.Status.QUEUED, source_path="x.parquet")

    page = admin_panel_client.get(reverse("dashboard:etl-run", kwargs={"pk": run.pk}))
    body = page.content.decode()

    assert 'hx-trigger="every 2s"' in body
    assert "Сборка запускается" in body


def test_upload_rejects_foreign_file(
    admin_panel_client: Client, tmp_path: Path, build_target: Path
) -> None:
    """Файл другого состава отклоняется сразу и не сохраняется."""
    foreign = tmp_path / "foreign.parquet"
    duckdb.sql(f"COPY (SELECT 1 AS id, 'x' AS name) TO '{foreign.as_posix()}' (FORMAT parquet)")

    response = upload(admin_panel_client, foreign)

    assert response.status_code == 400
    assert "Нет обязательного атрибута" in response.content.decode()
    assert not EtlRun.objects.exists()
    assert not list((build_target.parent / "sources").glob("*"))


def test_upload_rejects_non_parquet(admin_panel_client: Client, build_target: Path) -> None:
    """Файл не того расширения отклоняется формой."""
    response = admin_panel_client.post(
        reverse("dashboard:data-upload"),
        {"dataset": SimpleUploadedFile("data.csv", b"a,b\n1,2\n")},
    )

    assert response.status_code == 400
    assert "parquet" in response.content.decode()


def test_upload_refused_while_build_is_active(
    admin_panel_client: Client, warehouse: Any, build_target: Path
) -> None:
    """Вторая сборка не ставится, пока идёт первая."""
    EtlRun.objects.create(status=EtlRun.Status.RUNNING, source_path="x.parquet")

    response = upload(admin_panel_client, warehouse.path)

    assert response.status_code == 302
    assert response["Location"] == reverse("dashboard:data")
    assert EtlRun.objects.count() == 1


def test_old_sources_are_pruned(settings: Any, tmp_path: Path, db: None) -> None:
    """После сборки остаются только наборы последних удачных и ещё идущих сборок."""
    directory = tmp_path / "sources"
    directory.mkdir()
    settings.DATASET_UPLOAD_DIR = directory
    files = [directory / f"{index}.parquet" for index in range(6)]
    for index, path in enumerate(files):
        path.write_bytes(b"x")
        if index < 4:
            EtlRun.objects.create(
                status=EtlRun.Status.SUCCESS,
                source_path=str(path),
                finished_at=timezone.now() + timedelta(minutes=index),
            )
    EtlRun.objects.create(status=EtlRun.Status.QUEUED, source_path=str(files[5]))

    removed = builds.prune_sources(keep=3)

    assert {path.name for path in removed} == {"0.parquet", "4.parquet"}
    assert sorted(path.name for path in directory.iterdir()) == [
        "1.parquet",
        "2.parquet",
        "3.parquet",
        "5.parquet",
    ]


@pytest.fixture
def foreign_lock(db: None) -> Any:
    """Блокировка сборки, взятая другим соединением — как будто сборка идёт в другом процессе."""
    database = connection.settings_dict
    other = psycopg.connect(
        dbname=database["NAME"],
        user=database["USER"],
        password=database["PASSWORD"],
        host=database["HOST"],
        port=database["PORT"] or 5432,
        autocommit=True,
    )
    other.execute("SELECT pg_advisory_lock(%s)", [builds.BUILD_LOCK_ID])
    yield other
    other.close()


def test_build_waits_for_lock(foreign_lock: Any, synthetic_dataset: Any) -> None:
    """Пока блокировку держит другая сборка, новая не начинается."""
    run = EtlRun.objects.create(
        status=EtlRun.Status.QUEUED, source_path=str(synthetic_dataset.path)
    )

    with pytest.raises(builds.BuildBusyError):
        builds.execute(run)

    run.refresh_from_db()
    assert run.status == EtlRun.Status.QUEUED


def test_command_refuses_concurrent_build(foreign_lock: Any, synthetic_dataset: Any) -> None:
    """Команда на стенде тоже не начинает вторую сборку."""
    with pytest.raises(CommandError, match="другая сборка"):
        call_command("etl_build", "--source", str(synthetic_dataset.path))

    assert EtlRun.objects.get().status == EtlRun.Status.CANCELLED


# ---------------------------------------------------------------------------------------
# Склад: подмена файла и отпечаток
# ---------------------------------------------------------------------------------------


def test_connection_follows_replaced_file(settings: Any, tmp_path: Path) -> None:
    """После подмены файла склада соединение открывается заново и видит новые данные."""
    target = tmp_path / "w.duckdb"
    for path, value in ((target, 1), (tmp_path / "next.duckdb", 2)):
        with duckdb.connect(str(path)) as writer:
            writer.execute(f"CREATE TABLE t AS SELECT {value} AS x")
    settings.DUCKDB_PATH = target
    duckdb_client.close_connections()
    try:
        assert duckdb_client.fetch_scalar("SELECT x FROM t") == 1
        before = duckdb_client.warehouse_generation()
        # Подменить открытый файл Windows не даёт — соединение закрывается,
        # как его закрыл бы завершившийся поток веб-процесса.
        duckdb_client.close_connections()
        (tmp_path / "next.duckdb").replace(target)
        assert duckdb_client.warehouse_generation() != before
        assert duckdb_client.fetch_scalar("SELECT x FROM t") == 2
    finally:
        duckdb_client.close_connections()


def test_summary_is_cached_under_generation(warehouse: Any) -> None:
    """Сводка склада кэшируется под отпечатком файла и после пересборки не находится."""
    from django.core.cache import cache

    from apps.warehouse.queries import warehouse_summary

    summary = warehouse_summary()
    generation = duckdb_client.warehouse_generation()

    assert cache.get(f"warehouse:summary:{generation}") == summary
    assert generation != "absent"


# ---------------------------------------------------------------------------------------
# Кэш, переживающий недоступность Valkey
# ---------------------------------------------------------------------------------------


def test_unreachable_cache_is_a_miss() -> None:
    """Недоступный Valkey — это промах, а не ошибка страницы."""
    # На порту 1 никто не слушает: соединение отклоняется сразу.
    cache = ResilientValkeyCache(
        "redis://127.0.0.1:1/0",
        {"OPTIONS": {"socket_connect_timeout": 0.2, "socket_timeout": 0.2}},
    )

    assert cache.get("key", "default") == "default"
    cache.set("key", "value")
    assert cache.add("key", "value") is False
    with pytest.raises(ValueError, match="недоступен"):
        cache.incr("key")
    assert cache.get_many(["a", "b"]) == {}
