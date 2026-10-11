"""Проверить подпись описи резервной копии и суммы её файлов, ничего не расшифровывая."""

from __future__ import annotations

from argparse import ArgumentParser
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.core import backup_crypto


class Command(BaseCommand):
    """Проверить копию."""

    help = "Проверяет подпись описи резервной копии и SHA-256 её файлов"

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Описать параметры командной строки."""
        parser.add_argument("manifest", type=Path, help="опись копии manifest-ОТМЕТКА.json")
        parser.add_argument(
            "--verify-key", default="", help="ключ проверки, если он не задан в BACKUP_VERIFY_KEY"
        )

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ARG002 — контракт команды
        """Проверить и показать итог."""
        try:
            key = backup_crypto.verify_key(options["verify_key"] or None)
            manifest = backup_crypto.verify(options["manifest"], key)
        except backup_crypto.BackupCryptoError as error:
            raise CommandError(f"Копия не прошла проверку: {error}") from error
        for entry in manifest["files"]:
            self.stdout.write(f"  {entry['name']}  {entry['size']} байт  sha256 {entry['sha256']}")
        self.stdout.write(
            self.style.SUCCESS(
                f"Подпись верна (ключ {manifest['signer']}), файлы целы; "
                f"зашифровано для ключа {manifest['recipient']}"
            )
        )
