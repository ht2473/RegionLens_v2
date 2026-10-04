"""
Долгие разборы отдельным процессом: сводка большой таблицы (DuckDB по всему файлу),
извлечение длинной таблицы по рецепту и сборка файла набора.

Маленькая таблица разбирается прямо в запросе. Страница опрашивает состояние раз в секунду;
перезапуск рабочего процесса сервера разбор не обрывает. Оборванный разбор считается
неудавшимся через ``ABANDONED_AFTER``; одновременно идёт не больше
``USERDATA_PARALLEL_JOBS`` разборов, остальные ждут очереди.
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
# Этапы после описания таблицы: извлечение по рецепту и сборка файла набора.
EXTRACT = "extract"
BUILD = "build"
STAGES = (EXTRACT, BUILD)
# Извлекается в запросе таблица не больше этого (книги — сжатые, поэтому меньше).
INLINE_TABLE_BYTES = 8 * 1024 * 1024
INLINE_BOOK_BYTES = 3 * 1024 * 1024
# Собирается в запросе набор не больше этого числа наблюдений.
INLINE_OBSERVATIONS = 200_000


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
    mark(version, profile_state=RUNNING, profile_started=timezone.now().isoformat())
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
        mark(version, profile_state=FAILED)
        raise
    mark(version, profile=profile, profile_state=READY)


def launch(version: DatasetVersion) -> None:
    """Запустить ``manage.py userdata_summarize`` отделённым процессом."""
    if not _spawn(["userdata_summarize", str(version.pk)]):
        mark(version, profile_state=FAILED)


# --- Извлечение и сборка ------------------------------------------------------------------------


def stage_state(version: DatasetVersion, stage: str) -> str:
    """Состояние этапа: ``""`` — не начат, иначе pending/running/ready/failed."""
    state = str(version.report.get(f"{stage}_state", ""))
    started = version.report.get(f"{stage}_started", "")
    if (
        state == RUNNING
        and started
        and timezone.now() - datetime.fromisoformat(started) > ABANDONED_AFTER
    ):
        return FAILED
    return state


def stage_error(version: DatasetVersion, stage: str) -> str:
    """Почему этап не удался — текстом для человека."""
    return str(version.report.get(f"{stage}_error", ""))


def start(version: DatasetVersion, stage: str, *, inline: bool | None = None) -> str:
    """
    Начать этап: маленькая таблица разбирается сразу, большая — отдельным процессом,
    если есть свободное место в очереди; иначе этап ждёт следующего опроса.
    ``inline`` — выполнить в запросе независимо от размера (пример).
    """
    if inline or (inline is None and _inline(version, stage)):
        run(version, stage)
        version.refresh_from_db(fields=["report", "state"])
        return stage_state(version, stage)
    if running_count() >= settings.USERDATA_PARALLEL_JOBS:
        mark(version, **{f"{stage}_state": PENDING, f"{stage}_error": ""})
        return PENDING
    mark(
        version,
        **{
            f"{stage}_state": RUNNING,
            f"{stage}_started": timezone.now().isoformat(),
            f"{stage}_error": "",
        },
    )
    if not _spawn(["userdata_build", str(version.pk), stage]):
        mark(version, **{f"{stage}_state": FAILED})
    return RUNNING


def resume(version: DatasetVersion, stage: str) -> str:
    """Опрос хода: этап, ждавший очереди, начинается, когда место освободилось."""
    state = stage_state(version, stage)
    if state == PENDING:
        return start(version, stage)
    return state


def run(version: DatasetVersion, stage: str) -> None:
    """Выполнить этап; сбой — в отчёт текстом для человека и письмом без содержимого файла."""
    from . import build, extract

    mark(version, **{f"{stage}_state": RUNNING, f"{stage}_started": timezone.now().isoformat()})
    if stage == BUILD:
        DatasetVersion.objects.filter(pk=version.pk).update(state=DatasetVersion.State.BUILDING)
    try:
        if stage == EXTRACT:
            report = extract.extract(version)
            mark(version, extract=report, extract_state=READY, extract_error="")
            return
        build.build(version)
    except Exception as error:  # разбор чужого файла может упасть где угодно
        expected = isinstance(error, extract.ExtractError)
        if not expected:
            logger.exception("userdata: этап %s версии %s не удался", stage, version.pk)
            _alert(version, stage, error)
        mark(version, **{f"{stage}_state": FAILED, f"{stage}_error": extract.failure_text(error)})
        if stage == BUILD:
            DatasetVersion.objects.filter(pk=version.pk).update(state=DatasetVersion.State.FAILED)
        return
    mark(version, **{f"{stage}_state": READY, f"{stage}_error": ""})


def running_count() -> int:
    """Сколько разборов идёт сейчас отдельными процессами."""
    since = (timezone.now() - ABANDONED_AFTER).isoformat()
    count = 0
    for stage in STAGES:
        count += DatasetVersion.objects.filter(
            **{f"report__{stage}_state": RUNNING, f"report__{stage}_started__gte": since}
        ).count()
    return count


def _inline(version: DatasetVersion, stage: str) -> bool:
    """Этап выполняется в запросе: таблица или набор невелики."""
    if stage == BUILD:
        report = version.report.get("extract") or {}
        return bool(report) and int(report.get("observations", 0)) <= INLINE_OBSERVATIONS
    table = table_of(version)
    path = version.directory / version.recipe["file"]
    if tables.is_large(path, table):
        return False
    limit = INLINE_BOOK_BYTES if table.kind in ingest.WORKBOOK_KINDS else INLINE_TABLE_BYTES
    return path.stat().st_size <= limit


def _alert(version: DatasetVersion, stage: str, error: Exception) -> None:
    """Письмо администратору о сбое разбора: опознаватели и вид ошибки, без файла."""
    from apps.core.alerts import notify

    notify(
        f"userdata:{stage}:{type(error).__name__}",
        "Свои данные: таблица не разобрана",
        f"Этап «{stage}» версии {version.pk} (набор {version.dataset.public_id}, вид файла "
        f"{version.file_kind}, {version.file_size} байт) завершился ошибкой "
        f"{type(error).__name__}. Подробности — в журнале сервера.",
        repeat_after=timedelta(days=1),
    )


def _spawn(arguments: list[str]) -> bool:
    """Запустить команду проекта отделённым процессом; ``False`` — не запустилась."""
    command = [sys.executable, str(settings.BASE_DIR / "manage.py"), *arguments]
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
        logger.exception("userdata: процесс разбора не запустился")
        return False
    return True


def table_of(version: DatasetVersion) -> ingest.TableInfo:
    """Выбранная таблица версии из рецепта."""
    data = dict(version.recipe["table"])
    data.setdefault("sample", [])
    return ingest.TableInfo(**data)


def mark(version: DatasetVersion, **fields: Any) -> None:
    """Дописать поля в отчёт версии, перечитав его: этап мог записать своё."""
    version.refresh_from_db(fields=["report"])
    version.report = {**version.report, **fields}
    version.save(update_fields=["report", "updated_at"])
