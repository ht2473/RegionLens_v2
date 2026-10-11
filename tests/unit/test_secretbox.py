"""Шифрование секретов в базе: AES-256-GCM, назначение записи и смена ключа."""

from __future__ import annotations

from typing import Any

import pytest

from apps.core import secretbox

CONTEXT = "accounts.User.totp_secret:7"
SECRET = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"


def test_round_trip_and_fresh_nonce(settings: Any) -> None:
    """Расшифровывается то, что зашифровано; одно значение дважды — разные записи."""
    settings.FIELD_ENCRYPTION_KEYS = []
    first, second = secretbox.seal(SECRET, CONTEXT), secretbox.seal(SECRET, CONTEXT)
    assert first != second
    assert SECRET not in first
    assert secretbox.unseal(first, CONTEXT) == SECRET
    assert secretbox.key_id_of(first) == secretbox.DERIVED_KEY_ID


def test_other_context_does_not_open(settings: Any) -> None:
    """Запись другой строки (другой пользователь) не расшифровывается: AAD не совпадает."""
    settings.FIELD_ENCRYPTION_KEYS = []
    sealed = secretbox.seal(SECRET, CONTEXT)
    with pytest.raises(secretbox.SecretBoxError):
        secretbox.unseal(sealed, "accounts.User.totp_secret:8")


def test_tampered_record_is_rejected(settings: Any) -> None:
    """Изменённый знак шифротекста — отказ, а не другой ключ кодов."""
    settings.FIELD_ENCRYPTION_KEYS = []
    sealed = secretbox.seal(SECRET, CONTEXT)
    flipped = sealed[:-3] + ("A" if sealed[-3] != "A" else "B") + sealed[-2:]
    with pytest.raises(secretbox.SecretBoxError):
        secretbox.unseal(flipped, CONTEXT)
    with pytest.raises(secretbox.SecretBoxError):
        secretbox.unseal("открытый текст", CONTEXT)


def test_rotation_reads_old_writes_new(settings: Any) -> None:
    """Новый ключ шифрует, прежний читает; без прежнего ключа старая запись не читается."""
    old, new = secretbox.new_key(), secretbox.new_key()
    settings.FIELD_ENCRYPTION_KEYS = [f"a:{old}"]
    settings.FIELD_ENCRYPTION_KEY_ID = ""
    legacy = secretbox.seal(SECRET, CONTEXT)
    settings.FIELD_ENCRYPTION_KEYS = [f"b:{new}", f"a:{old}"]
    settings.FIELD_ENCRYPTION_KEY_ID = "b"
    assert secretbox.unseal(legacy, CONTEXT) == SECRET
    assert secretbox.key_id_of(secretbox.seal(SECRET, CONTEXT)) == "b"
    settings.FIELD_ENCRYPTION_KEYS = [f"b:{new}"]
    with pytest.raises(secretbox.SecretBoxError):
        secretbox.unseal(legacy, CONTEXT)


def test_bad_key_settings(settings: Any) -> None:
    """Ключ не той длины и неизвестный номер текущего ключа — понятная ошибка."""
    settings.FIELD_ENCRYPTION_KEYS = ["a:c2hvcnQ"]
    with pytest.raises(secretbox.SecretBoxError, match="32 байта"):
        secretbox.keys()
    settings.FIELD_ENCRYPTION_KEYS = [f"a:{secretbox.new_key()}"]
    settings.FIELD_ENCRYPTION_KEY_ID = "нет-такого"
    with pytest.raises(secretbox.SecretBoxError):
        secretbox.current_key_id()


def test_derived_key_follows_secret_key(settings: Any) -> None:
    """Без своих ключей ключ выводится из SECRET_KEY: с другим SECRET_KEY запись не читается."""
    settings.FIELD_ENCRYPTION_KEYS = []
    sealed = secretbox.seal(SECRET, CONTEXT)
    settings.SECRET_KEY = settings.SECRET_KEY + "-другой"
    with pytest.raises(secretbox.SecretBoxError):
        secretbox.unseal(sealed, CONTEXT)
