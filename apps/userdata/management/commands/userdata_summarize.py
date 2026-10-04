"""Сводка большой таблицы пользователя отдельным процессом: ``userdata_summarize <версия>``."""

from __future__ import annotations

from argparse import ArgumentParser
from typing import Any

from django.core.management.base import BaseCommand

from apps.userdata import jobs
from apps.userdata.models import DatasetVersion


class Command(BaseCommand):
    """Посчитать сводку столбцов большой таблицы и записать её в отчёт версии."""

    help = "Сводка большой таблицы своих данных (запускается со страницы мастера)"

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("version", type=int, help="номер записи версии набора")

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ARG002
        version = DatasetVersion.objects.filter(pk=options["version"]).first()
        if version is None or not version.recipe.get("table"):
            return
        jobs.summarize(version)
