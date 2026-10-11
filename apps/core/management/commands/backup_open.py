"""
Проверить и расшифровать резервную копию для восстановления (вызывает ``scripts/restore.sh``).

Сначала проверяются подпись описи и суммы файлов; подменённая копия не расшифровывается.
Закрытый ключ расшифровки приносит администратор: на сервере его нет.
"""

from __future__ import annotations

from argparse import ArgumentParser
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.core import backup_crypto


class Command(BaseCommand):
    """Расшифровать копию."""

    help = "Проверяет и расшифровывает резервную копию"

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Описать параметры командной строки."""
        parser.add_argument("manifest", type=Path, help="опись копии manifest-ОТМЕТКА.json")
        parser.add_argument(
            "--key-file", type=Path, required=True, help="закрытый ключ расшифровки"
        )
        parser.add_argument("--out", type=Path, required=True, help="куда положить расшифрованное")

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ARG002 — контракт команды
        """Проверить и расшифровать."""
        try:
            private = backup_crypto.recipient_private_key(options["key_file"])
            opened = backup_crypto.open_copy(options["manifest"], private, options["out"])
        except (backup_crypto.BackupCryptoError, OSError) as error:
            raise CommandError(f"Копия не открыта: {error}") from error
        for path in opened:
            self.stdout.write(str(path))
        self.stdout.write(self.style.SUCCESS("Подпись и суммы верны, копия расшифрована"))
