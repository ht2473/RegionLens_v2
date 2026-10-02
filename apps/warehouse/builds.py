"""
Сборки склада: приём нового набора, запуск отдельным процессом и порядок выполнения.

Порядок: блокировка, отметка запуска, сборка, подмена файла, прогрев кэша, чистка наборов.
"""

from __future__ import annotations

import logging
import subprocess  # nosec B404 — запускается собственная команда проекта
import sys
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import duckdb
from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from django.db import connection
from django.utils import timezone, translation

from apps.core import alerts

from .duckdb_client import writable_connection
from .etl import compare, integrity, source
from .etl.pipeline import EtlError, Pipeline, PipelineResult
from .models import EtlRun

if TYPE_CHECKING:
    from apps.accounts.models import User

logger = logging.getLogger(__name__)

# Номер рекомендательной блокировки PostgreSQL для сборки.
BUILD_LOCK_ID = 7_310_001

# Сколько последних удачно собранных наборов хранить в каталоге загрузок.
SOURCES_KEPT = 3

# Через сколько времени сборка без исхода считается оборванной.
ABANDONED_AFTER = timedelta(minutes=30)

# Письмо о проверенной версии не повторяется: она ждёт решения в панели.
CANDIDATE_LETTER_REPEAT = timedelta(days=365)


class BuildBusyError(EtlError):
    """Другая сборка уже идёт."""


@contextmanager
def build_lock() -> Iterator[bool]:
    """
    Взять блокировку сборки без ожидания; ``True``, если взята.

    Блокировка держится соединением и снимается сама, если процесс сборки упал.
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [BUILD_LOCK_ID])
        acquired = bool(cursor.fetchone()[0])
    try:
        yield acquired
    finally:
        if acquired:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [BUILD_LOCK_ID])


def active_run() -> EtlRun | None:
    """Сборка, которая запускается или идёт. Оборванные сборки перед этим закрываются."""
    close_abandoned()
    return (
        EtlRun.objects.filter(status__in=[EtlRun.Status.QUEUED, EtlRun.Status.RUNNING])
        .order_by("-started_at")
        .first()
    )


def close_abandoned() -> int:
    """
    Закрыть сборки, процесс которых остановлен, не дойдя до конца; вернуть их число.

    Иначе запуск навсегда остался бы «идущим», а новая загрузка — запрещённой.
    """
    threshold = timezone.now() - ABANDONED_AFTER
    stale = EtlRun.objects.filter(
        status__in=[EtlRun.Status.QUEUED, EtlRun.Status.RUNNING], started_at__lt=threshold
    )
    closed = 0
    for run in stale:
        run.finish(status=EtlRun.Status.FAILED, error="Сборка не завершилась: процесс остановлен")
        closed += 1
    return closed


def current_source_path() -> Path:
    """Файл набора, из которого собран действующий склад: загруженный из панели или исходный."""
    last = (
        EtlRun.objects.filter(status=EtlRun.Status.SUCCESS, mode=EtlRun.Mode.FULL)
        .exclude(source_path="")
        .order_by("-finished_at")
        .first()
    )
    if last is not None:
        path = Path(last.source_path)
        if path.exists():
            return path
    return Path(settings.SOURCE_PARQUET_PATH)


# ---------------------------------------------------------------------------------------
# Приём загруженного набора
# ---------------------------------------------------------------------------------------


def store_upload(upload: UploadedFile) -> Path:
    """Сохранить загруженный набор под уникальным именем, оканчивающимся исходным."""
    target = _upload_path(upload.name or "dataset.parquet")
    with target.open("wb") as handle:
        for chunk in upload.chunks():
            handle.write(chunk)
    return target


def store_download(content: bytes, name: str) -> Path:
    """Сохранить скачанный набор рядом с загруженными из панели."""
    target = _upload_path(name)
    target.write_bytes(content)
    return target


def _upload_path(name: str) -> Path:
    """Уникальный путь в каталоге наборов: время, случайная часть и исходное имя."""
    directory = Path(settings.DATASET_UPLOAD_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    original = Path(name).stem
    stem = "".join(char if char.isalnum() or char in "-_." else "_" for char in original)
    stamp = timezone.now().strftime("%Y%m%d-%H%M%S")
    return directory / f"{stamp}-{uuid.uuid4().hex[:6]}-{stem}.parquet"


def check_structure(path: Path) -> list[str]:
    """
    Быстро проверить состав и типы атрибутов загруженного набора.

    Объёмы, уровни территорий и справочник проверяет сама сборка.
    """
    try:
        with writable_connection(Path(":memory:")) as memory:
            source.register_source(memory, path)
            columns = dict(
                memory.execute(
                    "SELECT column_name, column_type FROM (DESCRIBE SELECT * FROM src)"
                ).fetchall()
            )
    except duckdb.Error as error:
        return [f"Файл не читается как parquet: {error}"]

    problems = []
    for column, expected in source.EXPECTED_SCHEMA.items():
        if column not in columns:
            problems.append(f"Нет обязательного атрибута «{column}»")
        elif not source.types_compatible(columns[column], expected):
            problems.append(f"Атрибут «{column}» имеет тип {columns[column]}, ожидается {expected}")
    return problems


def start_build(*, source_path: Path, started_by: User | None) -> EtlRun:
    """Создать запуск «в очереди» и начать полную сборку отдельным процессом, не дожидаясь её."""
    run = EtlRun.objects.create(
        mode=EtlRun.Mode.FULL,
        status=EtlRun.Status.QUEUED,
        source_path=str(source_path),
        started_by=started_by,
    )
    run.append_log(f"Набор принят: {source_path.name}. Запуск сборки", save=True)
    launch(run)
    run.refresh_from_db()
    return run


def start_candidate(
    *,
    source_path: Path,
    started_by: User | None,
    offered: dict[str, str] | None = None,
    inline: bool = False,
    progress: Callable[[str], None] | None = None,
) -> EtlRun:
    """
    Начать проверку нового набора: сборку рядом с рабочим складом и отчёт о различиях.

    Из панели проверка идёт отдельным процессом; из сбора (``inline``) — в нём самом.
    ``offered`` — версия и адрес, если набор скачан с сайта.
    """
    run = EtlRun.objects.create(
        mode=EtlRun.Mode.CANDIDATE,
        status=EtlRun.Status.QUEUED,
        source_path=str(source_path),
        started_by=started_by,
        statistics={"offered": offered} if offered else {},
    )
    run.append_log(f"Набор принят: {source_path.name}. Проверка новой версии", save=True)
    if inline:
        run_queued(run.pk, progress=progress)
    else:
        launch(run)
    run.refresh_from_db()
    return run


def accept_candidate(run: EtlRun, user: User) -> EtlRun:
    """Принять проверенную версию: собрать рабочий склад из её набора."""
    build = start_build(source_path=Path(run.source_path), started_by=user)
    _decide(run, user, accepted=True, build=build)
    return build


def reject_candidate(run: EtlRun, user: User) -> None:
    """Отклонить проверенную версию; её файл удаляется при чистке наборов."""
    _decide(run, user, accepted=False)
    prune_sources()


def _decide(run: EtlRun, user: User, *, accepted: bool, build: EtlRun | None = None) -> None:
    """Записать решение по проверенной версии."""
    run.statistics = {
        **(run.statistics or {}),
        "decision": {
            "accepted": accepted,
            "user": user.get_short_name(),
            "at": timezone.now().isoformat(timespec="seconds"),
            "build": build.pk if build is not None else None,
        },
    }
    run.save(update_fields=["statistics", "updated_at"])


def launch(run: EtlRun) -> None:
    """
    Запустить отделённый процесс ``etl_build --run``; при отказе закрыть запуск.

    Перезапуск рабочего процесса сервера не должен обрывать сборку.
    """
    command = [sys.executable, str(settings.BASE_DIR / "manage.py"), "etl_build"]
    command += ["--run", str(run.pk)]
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
    except OSError as error:
        run.finish(status=EtlRun.Status.FAILED, error=f"Процесс сборки не запустился: {error}")


def run_queued(run_id: int, *, progress: Callable[[str], None] | None = None) -> str:
    """Выполнить сборку, начатую из панели: ``success``, ``failed``, ``busy`` или ``skipped``."""
    run = EtlRun.objects.select_related("started_by").filter(pk=run_id).first()
    if run is None or run.status != EtlRun.Status.QUEUED:
        # Запуск уже закрыт, например признан оборванным.
        return "skipped"
    try:
        execute(run, progress=progress)
    except BuildBusyError:
        run.finish(status=EtlRun.Status.FAILED, error="Идёт другая сборка склада")
        return "busy"
    except EtlError:
        # Причина уже записана в запуске и видна в панели.
        return "failed"
    return "success"


# ---------------------------------------------------------------------------------------
# Выполнение
# ---------------------------------------------------------------------------------------


def execute(
    run: EtlRun,
    *,
    target_path: Path | None = None,
    progress: Callable[[str], None] | None = None,
) -> PipelineResult:
    """
    Выполнить полную сборку или проверку новой версии по запуску ``run``.

    При занятой блокировке — ``BuildBusyError``; отказ сборки отмечается и пробрасывается.
    """
    candidate = run.mode == EtlRun.Mode.CANDIDATE
    with build_lock() as acquired:
        if not acquired:
            raise BuildBusyError("Идёт другая сборка склада")

        run.status = EtlRun.Status.RUNNING
        run.started_at = timezone.now()
        run.save(update_fields=["status", "started_at", "updated_at"])

        pipeline = Pipeline(
            source_path=Path(run.source_path),
            target_path=target_path,
            run=run,
            progress=progress,
        )
        try:
            if candidate:
                result = pipeline.check_candidate(current_source=current_source_path())
            else:
                result = pipeline.build()
        except EtlError as error:
            run.finish(status=EtlRun.Status.FAILED, error=str(error))
            _alert_failed(run)
            raise
        except Exception as error:
            run.finish(status=EtlRun.Status.FAILED, error=repr(error))
            _alert_failed(run)
            raise

        run.finish(status=EtlRun.Status.SUCCESS)

    if candidate:
        _alert_candidate(run)
        return result
    _alert_missing(run)
    after_build()
    return result


def _alert_failed(run: EtlRun) -> None:
    """
    Письмо о неудачной сборке, начатой не из панели: по таймеру после сбора или командой.

    Сборку из панели администратор видит сам.
    """
    if run.started_by_id is not None:
        return
    alerts.notify(
        f"build:{run.mode}:{run.error_message[:300]}",
        "склад не собран",
        f"Сборка склада № {run.pk} завершилась с ошибкой:\n{run.error_message}\n\n"
        "Действует прежний склад: сайт показывает данные последней удачной сборки.\n\n"
        f"Запуск в панели: {alerts.panel_address('dashboard:etl-run', run.pk)}",
    )


def _alert_missing(run: EtlRun) -> None:
    """Письмо о справочниках проекта, ссылающихся на ряды, которых нет в собранном складе."""
    missing = run.missing_project_series
    if not missing:
        return
    with translation.override(settings.LANGUAGE_CODE):
        lines = [
            f"{group['title']}: " + ", ".join(item["key"] for item in group["items"])
            for group in integrity.grouped(missing)
        ]
    alerts.notify(
        "missing:" + ",".join(sorted(item["key"] for item in missing)),
        "в складе нет рядов из справочников",
        f"Склад собран (запуск № {run.pk}), но в нём нет рядов, на которые ссылаются "
        "справочники проекта. Скорее всего, в новой версии набора у них сменились ключи; "
        "показатели с этими ссылками остались без данных.\n\n"
        + "\n".join(lines)
        + "\n\nСправочники — файлы data/reference/.\n"
        f"Запуск в панели: {alerts.panel_address('dashboard:etl-run', run.pk)}",
    )


def _alert_candidate(run: EtlRun) -> None:
    """Письмо о проверенной версии набора, скачанной при сборе: её нужно принять или отклонить."""
    if run.started_by_id is not None:
        return
    report = run.report
    version = run.offered_version or report.get("candidate", {}).get("version", "")
    alerts.notify(
        f"candidate:{version}:{run.source_checksum}",
        f"новая версия набора {version}: проверка готова",
        "На сайте «Если быть точным» вышла новая версия набора. Она скачана и собрана рядом "
        "с рабочим складом; рабочий склад не менялся.\n\n"
        + "\n".join(compare.summary_lines(report))
        + "\n\nПринять или отклонить — в карточке проверки: "
        + alerts.panel_address("dashboard:etl-run", run.pk),
        repeat_after=CANDIDATE_LETTER_REPEAT,
    )


def after_build() -> None:
    """
    Действия после успешной сборки: прогрев кэша и чистка загруженных наборов.

    Сбрасывать кэш не нужно: ключи несут отпечаток склада.
    """
    from apps.core.warmup import warm_cache

    prune_sources()
    warm_cache()


def prune_sources(keep: int = SOURCES_KEPT) -> list[Path]:
    """
    Удалить загруженные наборы, кроме последних ``keep`` удачных, незаконченных сборок
    и проверенных версий, которые ждут решения.

    Возвращает удалённые файлы.
    """
    directory = Path(settings.DATASET_UPLOAD_DIR)
    if not directory.exists():
        return []
    successful = (
        EtlRun.objects.filter(status=EtlRun.Status.SUCCESS, mode=EtlRun.Mode.FULL)
        .order_by("-finished_at")
        .values_list("source_path", flat=True)[:keep]
    )
    pending = EtlRun.objects.filter(
        status__in=[EtlRun.Status.QUEUED, EtlRun.Status.RUNNING]
    ).values_list("source_path", flat=True)
    awaiting = [
        run.source_path
        for run in EtlRun.objects.filter(mode=EtlRun.Mode.CANDIDATE, status=EtlRun.Status.SUCCESS)
        if run.awaits_decision
    ]
    needed = {Path(path).name for path in [*successful, *pending, *awaiting] if path}

    removed = []
    for path in directory.glob("*.parquet"):
        if path.name not in needed:
            path.unlink(missing_ok=True)
            removed.append(path)
    if removed:
        logger.info("Удалены прежние наборы: %s", ", ".join(item.name for item in removed))
    return removed
