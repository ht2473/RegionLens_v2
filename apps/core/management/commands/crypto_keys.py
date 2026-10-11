"""
Новые ключи шифрования: для секретов в базе (``--field``) и для резервных копий (``--backup``).

Ключи печатаются строками для .env стенда. Закрытый ключ расшифровки копий пишется только
в файл: его хранят вне сервера (менеджер паролей, носитель у администратора), на сервере
остаются открытый ключ получателя и ключ подписи.
"""

from __future__ import annotations

import os
from argparse import ArgumentParser
from datetime import date
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.core import backup_crypto, secretbox


class Command(BaseCommand):
    """Выпустить ключи."""

    help = "Выпускает ключи для секретов в базе (--field) и для резервных копий (--backup ФАЙЛ)"

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Описать параметры командной строки."""
        parser.add_argument("--field", action="store_true", help="ключ AES-256 для секретов")
        parser.add_argument(
            "--backup",
            metavar="ФАЙЛ",
            type=Path,
            help="ключи копий; закрытый ключ расшифровки — в этот файл (хранить вне сервера)",
        )

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ARG002 — контракт команды
        """Выпустить и показать ключи."""
        if not options["field"] and not options["backup"]:
            raise CommandError("Укажите --field и/или --backup ФАЙЛ")
        if options["field"]:
            key_id = f"k{date.today():%Y%m%d}"
            self.stdout.write("# Секреты в базе: дописать первым в FIELD_ENCRYPTION_KEYS")
            self.stdout.write(f"FIELD_ENCRYPTION_KEYS={key_id}:{secretbox.new_key()}")
            self.stdout.write(f"FIELD_ENCRYPTION_KEY_ID={key_id}")
        if options["backup"]:
            path: Path = options["backup"]
            if path.exists():
                raise CommandError(f"{path} уже есть: ключ не перезаписывается")
            pairs = backup_crypto.new_key_pairs()
            # Файл — только владельцу: в нём ключ, которым читаются все копии.
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="ascii") as handle:
                handle.write(pairs.recipient_private + "\n")
            recipient = backup_crypto.recipient_key(pairs.recipient)
            self.stdout.write("# Резервные копии: в .env стенда")
            self.stdout.write(f"BACKUP_SIGNING_KEY={pairs.signing}")
            self.stdout.write(f"BACKUP_VERIFY_KEY={pairs.verify}")
            self.stdout.write(f"BACKUP_RECIPIENT_KEY={pairs.recipient}")
            self.stdout.write(
                self.style.WARNING(
                    f"Закрытый ключ расшифровки (отпечаток {backup_crypto.fingerprint(recipient)}) "
                    f"записан в {path}. Унесите его с сервера: без него копии не восстановить, "
                    "а на сервере он не должен лежать."
                )
            )
