"""
Перешифровать ключи кодов входа текущим ключом (смена ключа шифрования).

Порядок смены: новый ключ (``manage.py crypto_keys --field``) дописывается в
FIELD_ENCRYPTION_KEYS первым или называется в FIELD_ENCRYPTION_KEY_ID, приложение
перезапускается, затем эта команда; после неё прежний ключ можно убрать из настроек.
"""

from __future__ import annotations

from argparse import ArgumentParser
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.models import User
from apps.core import secretbox


class Command(BaseCommand):
    """Перешифровать записи, зашифрованные прежними ключами."""

    help = "Перешифровывает ключи кодов входа текущим ключом FIELD_ENCRYPTION_KEY_ID"

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Описать параметры командной строки."""
        parser.add_argument(
            "--dry-run", action="store_true", help="только посчитать, ничего не меняя"
        )

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ARG002 — контракт команды
        """Пройти учётные записи с ключом кодов входа."""
        current = secretbox.current_key_id()
        changed = kept = 0
        with transaction.atomic():
            # Пустая строка в условии отбора, а не пароль.
            users = User.objects.exclude(totp_secret="")  # nosec B106
            users = users.select_for_update().only("totp_secret")
            for user in users:
                if secretbox.key_id_of(user.totp_secret) == current:
                    kept += 1
                    continue
                try:
                    secret = user.totp_key
                except secretbox.SecretBoxError as error:
                    raise CommandError(f"пользователь {user.pk}: {error}") from error
                changed += 1
                if not options["dry_run"]:
                    user.set_totp_key(secret)
                    user.save(update_fields=["totp_secret"])
        verb = "перешифровать" if options["dry_run"] else "перешифровано"
        self.stdout.write(
            self.style.SUCCESS(f"Ключ {current!r}: {verb} {changed}, уже им зашифровано {kept}")
        )
