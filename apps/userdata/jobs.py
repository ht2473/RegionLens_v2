"""
Долгие разборы отдельным процессом: сводка большой таблицы (DuckDB по всему файлу).

Страница опрашивает состояние раз в секунду; перезапуск рабочего процесса сервера разбор
не обрывает. Оборванный разбор считается неудавшимся через ``ABANDONED_AFTER``.
"""

from __future__ import annotations

import logging
import subprocess  # nosec B404 — запускается собственная команда проекта
import sys
from datetime import datetime, timedelta
from typing import Any

from django.conf import settings
from django.utils import timezone

from . import ingest, tables
from .models import DatasetVersion

logger = logging.getLogger(__name__)

# Состояния сводки в отчёте версии.
PENDING = "pending"
RUNNING = "running"
READY = "ready"
FAILED = "failed"
ABANDONED_AFTER = timedelta(minutes=10)
# Сводка таблицы меньше этого считается прямо в запросе.
INLINE_BYTES = 30 * 1024 * 1024


def summary_state(version: DatasetVersion) -> str:
    """Состояние сводки большой таблицы: ``""`` — не нужна, иначе pending/running/ready/failed."""
    table = table_of(version)
    path = version.directory / version.recipe["file"]
    if not tables.is_large(path, table):
        return ""
    if "profile" in version.report:
        return READY
    state = version.report.get("profile_state", PENDING)
    started = version.report.get("profile_started", "")
    if (
        state == RUNNING
        and started
        and timezone.now() - datetime.fromisoformat(started) > ABANDONED_AFTER
    ):
        return FAILED
    return state


def ensure_summary(version: DatasetVersion) -> str:
    """Сводка готова или начата: маленькая считается сразу, большая — отдельным процессом."""
    state = summary_state(version)
    if state in {"", READY, RUNNING, FAILED}:
        return state
    path = version.directory / version.recipe["file"]
    if path.stat().st_size <= INLINE_BYTES:
        summarize(version)
        return summary_state(version)
    _mark(version, profile_state=RUNNING, profile_started=timezone.now().isoformat())
    launch(version)
    return RUNNING


def summarize(version: DatasetVersion) -> None:
    """Посчитать сводку и записать её в отчёт версии; сбой — в отчёт, без содержимого файла."""
    path = version.directory / version.recipe["file"]
    try:
        profile = tables.summarize(path, table_of(version))
    except Exception as error:  # DuckDB сообщает о разборе файла своими исключениями
        logger.warning(
            "userdata: сводка версии %s не посчитана: %s", version.pk, type(error).__name__
        )
        _mark(version, profile_state=FAILED)
        raise
    _mark(version, profile=profile, profile_state=READY)


def launch(version: DatasetVersion) -> None:
    """Запустить ``manage.py userdata_summarize`` отделённым процессом."""
    command = [
        sys.executable,
        str(settings.BASE_DIR / "manage.py"),
        "userdata_summarize",
        str(version.pk),
    ]
    detached: dict[str, Any] = {"start_new_session": True}
    if sys.platform == "win32":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        detached = {"creationflags": flags}
    try:
        subprocess.Popen(  # noqa: S603  # nosec B603 — запускается собственная команда проекта
            command,
            cwd=settings.BASE_DIR,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **detached,
        )
    except OSError:
        logger.exception("userdata: процесс сводки не запустился")
        _mark(version, profile_state=FAILED)


def table_of(version: DatasetVersion) -> ingest.TableInfo:
    """Выбранная таблица версии из рецепта."""
    data = dict(version.recipe["table"])
    data.setdefault("sample", [])
    return ingest.TableInfo(**data)


def _mark(version: DatasetVersion, **fields: Any) -> None:
    version.refresh_from_db(fields=["report"])
    version.report = {**version.report, **fields}
    version.save(update_fields=["report", "updated_at"])
