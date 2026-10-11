"""
Шифрование ключа кодов входа в базе и резервных копий: запись, вход, смена ключа, миграция,
команды и проверки развёртывания.
"""

from __future__ import annotations

from importlib import import_module
from io import StringIO
from pathlib import Path
from typing import Any

import pytest
from django.apps import apps as django_apps
from django.core.checks import run_checks
from django.core.management import CommandError, call_command
from django.test import override_settings

from apps.accounts import totp
from apps.accounts.models import User
from apps.accounts.security import verify_code
from apps.core import backup_crypto, secretbox
from tests.conftest import TEST_TOTP_SECRET

pytestmark = [pytest.mark.integration, pytest.mark.django_db]


def _raw(user: User) -> str:
    """Значение поля так, как оно лежит в базе."""
    return User.objects.filter(pk=user.pk).values_list("totp_secret", flat=True).get()


class TestTotpSecret:
    """Ключ кодов входа в базе — только зашифрованным."""

    def test_database_holds_ciphertext(self, make_user: Any) -> None:
        """В базе — запись secretbox без ключа в открытом виде; ключ читается свойством."""
        user = make_user(two_factor=True)
        raw = _raw(user)
        assert raw.startswith(f"{secretbox.VERSION}:")
        assert TEST_TOTP_SECRET not in raw
        assert User.objects.get(pk=user.pk).totp_key == TEST_TOTP_SECRET

    def test_code_is_accepted_after_encryption(self, make_user: Any) -> None:
        """Код из приложения-генератора по тому же ключу принимается."""
        user = make_user(two_factor=True)
        code = totp.code_at(TEST_TOTP_SECRET, totp.current_step())
        assert verify_code(User.objects.get(pk=user.pk), code)

    def test_record_moved_to_another_user_is_useless(self, make_user: Any) -> None:
        """Запись одного пользователя, перенесённая другому, не расшифровывается."""
        first = make_user("first@example.com", two_factor=True)
        second = make_user("second@example.com")
        User.objects.filter(pk=second.pk).update(totp_secret=_raw(first))
        with pytest.raises(secretbox.SecretBoxError):
            _ = User.objects.get(pk=second.pk).totp_key

    def test_reencrypt_with_new_key(self, make_user: Any, settings: Any) -> None:
        """Смена ключа: команда перешифровывает записи, после неё прежний ключ не нужен."""
        old, new = secretbox.new_key(), secretbox.new_key()
        settings.FIELD_ENCRYPTION_KEYS = [f"old:{old}"]
        user = make_user(two_factor=True)
        assert secretbox.key_id_of(_raw(user)) == "old"

        settings.FIELD_ENCRYPTION_KEYS = [f"new:{new}", f"old:{old}"]
        settings.FIELD_ENCRYPTION_KEY_ID = "new"
        out = StringIO()
        call_command("reencrypt_secrets", stdout=out)
        assert "перешифровано 1" in out.getvalue()
        assert secretbox.key_id_of(_raw(user)) == "new"

        settings.FIELD_ENCRYPTION_KEYS = [f"new:{new}"]
        assert User.objects.get(pk=user.pk).totp_key == TEST_TOTP_SECRET

    def test_migration_encrypts_plain_secrets(self, make_user: Any) -> None:
        """Миграция 0004 шифрует ключи, записанные открытым текстом, тем же значением."""
        user = make_user()
        User.objects.filter(pk=user.pk).update(totp_secret=TEST_TOTP_SECRET)
        migration = import_module("apps.accounts.migrations.0004_encrypt_totp_secret")
        migration.encrypt_secrets(django_apps, None)
        assert _raw(user).startswith(f"{secretbox.VERSION}:")
        assert User.objects.get(pk=user.pk).totp_key == TEST_TOTP_SECRET
        migration.decrypt_secrets(django_apps, None)
        assert _raw(user) == TEST_TOTP_SECRET


class TestBackupCommands:
    """Команды копий: ключи, шифрование, проверка, открытие."""

    def test_keys_file_is_not_overwritten(self, tmp_path: Path) -> None:
        """Закрытый ключ пишется в новый файл; существующий не перезаписывается."""
        target = tmp_path / "backup.key"
        out = StringIO()
        call_command("crypto_keys", "--field", "--backup", str(target), stdout=out)
        assert "BACKUP_RECIPIENT_KEY=" in out.getvalue()
        assert "FIELD_ENCRYPTION_KEYS=" in out.getvalue()
        assert target.read_text().strip() not in out.getvalue()
        with pytest.raises(CommandError, match="не перезаписывается"):
            call_command("crypto_keys", "--backup", str(target), stdout=StringIO())

    def test_without_keys_copy_stays_open(self, tmp_path: Path, settings: Any) -> None:
        """Без ключей копия не трогается, а команда предупреждает."""
        settings.BACKUP_SIGNING_KEY = settings.BACKUP_RECIPIENT_KEY = ""
        dump = tmp_path / "db-1.dump"
        dump.write_bytes(b"PGDMP")
        out = StringIO()
        call_command("backup_seal", str(tmp_path), "--stamp", "1", stdout=out)
        assert "не зашифрована" in out.getvalue()
        assert dump.exists()

    def test_seal_verify_open_commands(self, tmp_path: Path, settings: Any) -> None:
        """Полный путь командами: шифрование, проверка, расшифровка, отказ при подмене."""
        pairs = backup_crypto.new_key_pairs()
        settings.BACKUP_SIGNING_KEY = pairs.signing
        settings.BACKUP_VERIFY_KEY = pairs.verify
        settings.BACKUP_RECIPIENT_KEY = pairs.recipient
        directory = tmp_path / "backups"
        directory.mkdir()
        (directory / "db-5.dump").write_bytes(b"PGDMP" * 100)
        key_file = tmp_path / "recipient.key"
        key_file.write_text(pairs.recipient_private)

        call_command("backup_seal", str(directory), "--stamp", "5", stdout=StringIO())
        manifest = directory / "manifest-5.json"
        out = StringIO()
        call_command("backup_verify", str(manifest), stdout=out)
        assert "Подпись верна" in out.getvalue()
        call_command(
            "backup_open",
            str(manifest),
            "--key-file",
            str(key_file),
            "--out",
            str(tmp_path / "opened"),
            stdout=StringIO(),
        )
        assert (tmp_path / "opened" / "db-5.dump").read_bytes() == b"PGDMP" * 100

        sealed = directory / "db-5.dump.enc"
        sealed.write_bytes(sealed.read_bytes()[:-1] + b"\x00")
        with pytest.raises(CommandError, match="не прошла проверку"):
            call_command("backup_verify", str(manifest), stdout=StringIO())


class TestDeployChecks:
    """Проверка развёртывания: свои ключи шифрования заданы и согласованы."""

    @staticmethod
    def ids(**overrides: Any) -> set[str]:
        with override_settings(**overrides):
            return {message.id for message in run_checks(include_deployment_checks=True)}

    def test_missing_keys_are_reported(self) -> None:
        """Нет своих ключей — два предупреждения."""
        found = self.ids(FIELD_ENCRYPTION_KEYS=[], BACKUP_SIGNING_KEY="", BACKUP_RECIPIENT_KEY="")
        assert {"core.W003", "core.W004"} <= found

    def test_configured_keys_pass(self) -> None:
        """Свои ключи и согласованная пара копий — без замечаний."""
        pairs = backup_crypto.new_key_pairs()
        found = self.ids(
            FIELD_ENCRYPTION_KEYS=[f"k:{secretbox.new_key()}"],
            BACKUP_SIGNING_KEY=pairs.signing,
            BACKUP_VERIFY_KEY=pairs.verify,
            BACKUP_RECIPIENT_KEY=pairs.recipient,
        )
        assert not found & {"core.W003", "core.W004", "core.E003", "core.E004", "core.E005"}

    def test_mismatched_verify_key(self) -> None:
        """Ключ проверки от другой пары — ошибка: копии не прошли бы проверку."""
        pairs, other = backup_crypto.new_key_pairs(), backup_crypto.new_key_pairs()
        found = self.ids(
            BACKUP_SIGNING_KEY=pairs.signing,
            BACKUP_VERIFY_KEY=other.verify,
            BACKUP_RECIPIENT_KEY=pairs.recipient,
        )
        assert "core.E005" in found
