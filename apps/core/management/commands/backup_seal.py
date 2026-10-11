"""
Зашифровать и подписать резервную копию (вызывает ``scripts/backup.sh``).

Файлы копии с отметкой времени шифруются для ключа получателя, открытые удаляются, рядом
кладутся опись и её подпись. Без ключей копия остаётся как есть, а команда предупреждает.
"""

from __future__ import annotations

from argparse import ArgumentParser
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.core import backup_crypto


class Command(BaseCommand):
    """Зашифровать файлы копии и подписать опись."""

    help = "Шифрует файлы резервной копии и подписывает их опись"

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Описать параметры командной строки."""
        parser.add_argument("directory", type=Path, help="каталог копий")
        parser.add_argument("--stamp", required=True, help="отметка времени копии")

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ARG002 — контракт команды
        """Зашифровать и подписать."""
        if not backup_crypto.configured():
            self.stdout.write(
                self.style.WARNING(
                    "Копия не зашифрована: не заданы BACKUP_SIGNING_KEY и BACKUP_RECIPIENT_KEY "
                    "(manage.py crypto_keys --backup ФАЙЛ)."
                )
            )
            return
        try:
            created = backup_crypto.seal(options["directory"], options["stamp"])
        except backup_crypto.BackupCryptoError as error:
            raise CommandError(str(error)) from error
        for path in created:
            self.stdout.write(path.name)
