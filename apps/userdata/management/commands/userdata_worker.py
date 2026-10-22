"""
Служба разборов своих данных: ``userdata_worker`` выполняет заявки сайта из очереди.

На стенде большие таблицы разбираются не в контейнере сайта, а в отдельном
(``userdata-worker``) со своим пределом памяти: тяжёлая таблица не теснит сайт и сборку
склада. Сайт кладёт заявку в каталог очереди в томе таблиц (``jobs._enqueue``), служба
берёт её переименованием и запускает ту же команду отдельным процессом — не больше
``USERDATA_PARALLEL_JOBS`` сразу. Процесс, оборвавшийся без итога, отмечается неудачей
сразу. По сигналу остановки новые заявки не берутся, начатые доводятся до конца.
"""

from __future__ import annotations

import logging
import signal
import subprocess  # nosec B404 — запускается собственная команда проекта
import sys
import threading
from argparse import ArgumentParser
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections, connection

from apps.userdata import jobs

logger = logging.getLogger(__name__)

# Как часто служба заглядывает в очередь, секунд.
POLL_SECONDS = 1.0


class Command(BaseCommand):
    """Выполнять заявки на разбор из очереди, пока служба не остановлена."""

    help = "Служба разборов своих данных: выполняет заявки сайта из очереди"

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument(
            "--once", action="store_true", help="выполнить заявки, что есть, и завершиться"
        )

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ARG002
        folder = jobs.queue_dir()
        folder.mkdir(parents=True, exist_ok=True)
        # Заявки, взятые прежним запуском и не доведённые: их процессы остановку не пережили.
        for claimed in sorted(folder.glob(f"*{jobs.CLAIMED}")):
            _finish(claimed, jobs.request_arguments(claimed), failed=True)
        stop = threading.Event()
        previous = {}
        if threading.current_thread() is threading.main_thread():
            for number in (signal.SIGTERM, signal.SIGINT):
                previous[number] = signal.signal(number, lambda *_: stop.set())
        logger.info("userdata: служба разборов запущена, очередь %s", folder)
        try:
            self._serve(folder, stop, once=options["once"])
        finally:
            for number, handler in previous.items():
                signal.signal(number, handler)

    def _serve(self, folder: Path, stop: threading.Event, *, once: bool) -> None:
        running: dict[Any, tuple[Path, list[str]]] = {}
        while True:
            for process, (claimed, arguments) in list(running.items()):
                code = process.poll()
                if code is not None:
                    del running[process]
                    if code:
                        logger.warning(
                            "userdata: разбор %s завершился с кодом %s", " ".join(arguments), code
                        )
                    _finish(claimed, arguments, failed=bool(code))
            if not stop.is_set():
                _take(folder, running)
            (folder / jobs.HEARTBEAT).touch()
            if not running and (stop.is_set() or (once and not _queued(folder))):
                return
            stop.wait(POLL_SECONDS)


def _queued(folder: Path) -> list[Path]:
    """Заявки в порядке поступления: имя начинается со времени."""
    return sorted(folder.glob(f"*{jobs.REQUEST}"))


def _take(folder: Path, running: dict[Any, tuple[Path, list[str]]]) -> None:
    """Взять заявки на свободные места; чужая или испорченная заявка удаляется."""
    for request in _queued(folder):
        if len(running) >= max(settings.USERDATA_PARALLEL_JOBS, 1):
            return
        claimed = request.with_suffix(jobs.CLAIMED)
        try:
            request.rename(claimed)
        except OSError:
            continue
        arguments = jobs.request_arguments(claimed)
        if arguments is None:
            logger.warning("userdata: заявка %s отклонена", request.name)
            claimed.unlink(missing_ok=True)
            continue
        try:
            running[_launch(arguments)] = (claimed, arguments)
        except OSError:
            logger.exception("userdata: процесс разбора не запустился")
            _finish(claimed, arguments, failed=True)


def _launch(arguments: list[str]) -> subprocess.Popen[bytes]:
    """Запустить команду заявки; вывод — в журнал службы."""
    return subprocess.Popen(  # noqa: S603  # nosec B603 — команда проверена по списку
        [sys.executable, str(settings.BASE_DIR / "manage.py"), *arguments],
        cwd=settings.BASE_DIR,
        stdin=subprocess.DEVNULL,
    )


def _finish(claimed: Path, arguments: list[str] | None, *, failed: bool) -> None:
    """Убрать взятую заявку; оборвавшийся разбор — отметить неудачей."""
    if failed and arguments is not None:
        # Служба живёт долго: устаревшее соединение с базой заменяется (вне транзакции).
        if not connection.in_atomic_block:
            close_old_connections()
        try:
            jobs.interrupted(arguments)
        except Exception:  # служба не должна падать из-за одной записи
            logger.exception("userdata: неудача разбора %s не отмечена", " ".join(arguments))
    claimed.unlink(missing_ok=True)
