"""
Отметка о снятой резервной копии для панели управления.

Копию снимает ``scripts/backup.sh`` по таймеру хоста и в конце вызывает эту команду.
"""

from __future__ import annotations

from argparse import ArgumentParser
from typing import Any

from django.core.management.base import BaseCommand

from apps.core.models import ServiceBeat


class Command(BaseCommand):
    """Записать отметку о резервной копии."""

    help = "Отмечает снятую резервную копию для панели управления"

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Описать параметры командной строки."""
        parser.add_argument("--file", default="", help="имя файла копии базы")
        parser.add_argument("--size", type=int, default=0, help="размер копии в байтах")
        parser.add_argument(
            "--offsite", action="store_true", help="копия вывезена за пределы сервера"
        )
        parser.add_argument("--failed", action="store_true", help="копию снять не удалось")
        parser.add_argument("--error", default="", help="причина отказа")

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ARG002 — контракт команды
        """Сохранить отметку."""
        ServiceBeat.record(
            ServiceBeat.Service.BACKUP,
            ok=not options["failed"],
            file=options["file"],
            size_bytes=options["size"],
            offsite=options["offsite"],
            error=options["error"][:500],
        )
        self.stdout.write(self.style.SUCCESS("Отметка о резервной копии записана"))
