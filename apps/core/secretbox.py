"""
Секреты в базе, которые сервер должен уметь прочитать (ключ кодов входа): AES-256-GCM,
ключ — в настройках сервера, а не в базе.

Запись — «v1:<номер ключа>:<base64url(одноразовое число ‖ шифротекст ‖ тег)>». Номер ключа
позволяет сменить ключ: новые записи шифруются текущим, старые читаются прежним, пока их
не перешифрует ``manage.py reencrypt_secrets``. Связанные данные (AAD) — назначение записи,
например «accounts.User.totp_secret:42»: перенесённый в другую строку или поле шифротекст
не расшифруется, подменённый — тоже (проверка тега GCM).
"""

from __future__ import annotations

import base64
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings

VERSION = "v1"
KEY_BYTES = 32
# 96 бит — размер одноразового числа, рекомендованный для GCM (NIST SP 800-38D).
NONCE_BYTES = 12
# Номер ключа, выведенного из SECRET_KEY, когда своих ключей не задано (разработка, тесты).
DERIVED_KEY_ID = "s0"


class SecretBoxError(Exception):
    """Запись не расшифровывается: чужой ключ, подмена или повреждение."""


def encode_key(key: bytes) -> str:
    """Ключ строкой для настроек: base64url без знаков выравнивания."""
    return base64.urlsafe_b64encode(key).decode("ascii").rstrip("=")


def decode_key(text: str) -> bytes:
    """Ключ из строки настроек."""
    raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    if len(raw) != KEY_BYTES:
        raise SecretBoxError(f"ключ должен быть {KEY_BYTES} байта, а не {len(raw)}")
    return raw


def new_key() -> str:
    """Новый случайный ключ строкой для FIELD_ENCRYPTION_KEYS."""
    return encode_key(secrets.token_bytes(KEY_BYTES))


def keys() -> dict[str, bytes]:
    """
    Ключи по номерам из FIELD_ENCRYPTION_KEYS («номер:ключ», через запятую). Без них — один
    ключ, выведенный из SECRET_KEY по HKDF: он тоже вне базы, но меняется вместе с SECRET_KEY,
    поэтому на рабочем стенде нужны свои ключи (проверка развёртывания core.W003).
    """
    found: dict[str, bytes] = {}
    for item in settings.FIELD_ENCRYPTION_KEYS:
        key_id, _sep, text = item.strip().partition(":")
        if not key_id or not text:
            raise SecretBoxError("запись FIELD_ENCRYPTION_KEYS — «номер:ключ»")
        found[key_id] = decode_key(text)
    if found:
        return found
    derived = HKDF(
        algorithm=hashes.SHA256(), length=KEY_BYTES, salt=None, info=b"regionlens secretbox v1"
    ).derive(settings.SECRET_KEY.encode())
    return {DERIVED_KEY_ID: derived}


def current_key_id() -> str:
    """Номер ключа для новых записей."""
    available = keys()
    wanted = settings.FIELD_ENCRYPTION_KEY_ID or next(iter(available))
    if wanted not in available:
        raise SecretBoxError(f"ключа {wanted!r} нет в FIELD_ENCRYPTION_KEYS")
    return wanted


def seal(plaintext: str, context: str) -> str:
    """Зашифровать строку для записи в базу; ``context`` — назначение записи (AAD)."""
    key_id = current_key_id()
    nonce = secrets.token_bytes(NONCE_BYTES)
    sealed = AESGCM(keys()[key_id]).encrypt(nonce, plaintext.encode(), context.encode())
    body = base64.urlsafe_b64encode(nonce + sealed).decode("ascii").rstrip("=")
    return f"{VERSION}:{key_id}:{body}"


def key_id_of(token: str) -> str:
    """Номер ключа записи."""
    return _parts(token)[0]


def unseal(token: str, context: str) -> str:
    """Расшифровать запись; подмена, чужое назначение или неизвестный ключ — ``SecretBoxError``."""
    key_id, raw = _parts(token)
    key = keys().get(key_id)
    if key is None:
        raise SecretBoxError(f"ключа {key_id!r} нет в FIELD_ENCRYPTION_KEYS")
    nonce, sealed = raw[:NONCE_BYTES], raw[NONCE_BYTES:]
    try:
        return AESGCM(key).decrypt(nonce, sealed, context.encode()).decode()
    except InvalidTag as error:
        raise SecretBoxError("запись подменена или зашифрована для другого назначения") from error


def _parts(token: str) -> tuple[str, bytes]:
    version, _sep, rest = token.partition(":")
    key_id, _sep, body = rest.partition(":")
    if version != VERSION or not key_id or not body:
        raise SecretBoxError("не запись secretbox")
    try:
        raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
    except ValueError as error:
        raise SecretBoxError("запись повреждена") from error
    # Одноразовое число и тег GCM (16 байт) — меньше не бывает.
    if len(raw) < NONCE_BYTES + 16:
        raise SecretBoxError("запись повреждена")
    return key_id, raw
