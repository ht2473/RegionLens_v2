"""Извлечение или сборка набора отдельным процессом: ``userdata_build <версия> <этап>``."""

from __future__ import annotations

import os
import threading
from argparse import ArgumentParser
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils.translation import gettext as _

from apps.userdata import jobs
from apps.userdata.models import DatasetVersion


class Command(BaseCommand):
    """Выполнить этап разбора таблицы и записать итог в отчёт версии."""

    help = "Извлечение по рецепту или сборка набора своих данных (запускается со страницы мастера)"

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("version", type=int, help="номер записи версии набора")
        parser.add_argument("stage", choices=jobs.STAGES, help="этап: extract или build")

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ARG002
        version = DatasetVersion.objects.filter(pk=options["version"]).first()
        if version is None or not version.recipe.get("table"):
            return
        stage = options["stage"]
        # Предел времени разбора: процесс завершается сам, отметив неудачу.
        watchdog = threading.Timer(settings.USERDATA_JOB_SECONDS, _expire, args=(version.pk, stage))
        watchdog.daemon = True
        watchdog.start()
        try:
            jobs.run(version, stage)
        finally:
            watchdog.cancel()


def _expire(pk: int, stage: str) -> None:
    """Разбор дольше предела: отметить неудачу и завершить процесс."""
    version = DatasetVersion.objects.filter(pk=pk).first()
    if version is not None:
        jobs.mark(
            version,
            **{
                f"{stage}_state": jobs.FAILED,
                f"{stage}_error": _(
                    "Разбор занял слишком много времени. Оставьте в таблице только нужные "
                    "показатели или годы и загрузите её снова."
                ),
            },
        )
    os._exit(3)
