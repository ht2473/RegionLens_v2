"""
Шифрование и подпись резервных копий (scripts/backup.sh, scripts/restore.sh).

Шифрование гибридное, как в age: для каждого файла — одноразовая пара X25519, общий секрет
с открытым ключом получателя → HKDF-SHA256 → ключ AES-256-GCM. На сервере лежит только
открытый ключ получателя: сервер шифрует копию, но сам расшифровать её не может; закрытый
ключ хранится вне сервера и нужен лишь при восстановлении. Украденная копия — вместе
с сервером или из хранилища — без него не читается.

Подпись — Ed25519 над описью копии: имя, размер и SHA-256 каждого зашифрованного файла.
Восстановление сначала проверяет подпись и суммы и только потом расшифровывает: подменённую
в хранилище копию оно не примет.

Файл копии: MAGIC ‖ открытый ключ одноразовой пары (32 байта) ‖ одноразовое число (12) ‖
шифротекст ‖ тег GCM (16). Заголовок — связанные данные GCM: его подмена тоже видна.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePath
from typing import Any

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings

MAGIC = b"RLBACKUP1\n"
PUBLIC_BYTES = 32
NONCE_BYTES = 12
TAG_BYTES = 16
HEADER_BYTES = len(MAGIC) + PUBLIC_BYTES + NONCE_BYTES
CHUNK_BYTES = 1 << 20
SUFFIX = ".enc"
FORMAT = "regionlens-backup"
HKDF_INFO = b"regionlens backup v1"
# Файлы копии с отметкой времени: база и архив файлов (scripts/backup.sh).
PARTS = ("db-{stamp}.dump", "files-{stamp}.tar.gz")


class BackupCryptoError(Exception):
    """Копия не проходит проверку или не расшифровывается."""


# --- Ключи ---------------------------------------------------------------------------------


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str, size: int, what: str) -> bytes:
    try:
        raw = base64.urlsafe_b64decode(text.strip() + "=" * (-len(text.strip()) % 4))
    except ValueError as error:
        raise BackupCryptoError(f"{what}: не base64") from error
    if len(raw) != size:
        raise BackupCryptoError(f"{what}: {size} байта, а не {len(raw)}")
    return raw


def _raw_public(key: X25519PublicKey | Ed25519PublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _raw_private(key: X25519PrivateKey | Ed25519PrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )


def fingerprint(key: X25519PublicKey | Ed25519PublicKey) -> str:
    """Отпечаток открытого ключа: первые 16 знаков SHA-256 — для описи и сверки глазами."""
    return hashlib.sha256(_raw_public(key)).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class KeyPairs:
    """Ключи копий строками для .env и закрытый ключ расшифровки — для хранения вне сервера."""

    signing: str
    verify: str
    recipient: str
    recipient_private: str


def new_key_pairs() -> KeyPairs:
    """Пара подписи Ed25519 и пара получателя X25519."""
    signing = Ed25519PrivateKey.generate()
    recipient = X25519PrivateKey.generate()
    return KeyPairs(
        signing=_b64(_raw_private(signing)),
        verify=_b64(_raw_public(signing.public_key())),
        recipient=_b64(_raw_public(recipient.public_key())),
        recipient_private=_b64(_raw_private(recipient)),
    )


def signing_key(text: str | None = None) -> Ed25519PrivateKey:
    """Закрытый ключ подписи из BACKUP_SIGNING_KEY."""
    text = settings.BACKUP_SIGNING_KEY if text is None else text
    return Ed25519PrivateKey.from_private_bytes(_unb64(text, 32, "BACKUP_SIGNING_KEY"))


def verify_key(text: str | None = None) -> Ed25519PublicKey:
    """Открытый ключ проверки: BACKUP_VERIFY_KEY, иначе — из ключа подписи."""
    text = settings.BACKUP_VERIFY_KEY if text is None else text
    if text:
        return Ed25519PublicKey.from_public_bytes(_unb64(text, 32, "BACKUP_VERIFY_KEY"))
    if settings.BACKUP_SIGNING_KEY:
        return signing_key().public_key()
    raise BackupCryptoError("не задан ключ проверки подписи (BACKUP_VERIFY_KEY)")


def recipient_key(text: str | None = None) -> X25519PublicKey:
    """Открытый ключ получателя из BACKUP_RECIPIENT_KEY."""
    text = settings.BACKUP_RECIPIENT_KEY if text is None else text
    return X25519PublicKey.from_public_bytes(_unb64(text, 32, "BACKUP_RECIPIENT_KEY"))


def recipient_private_key(path: Path) -> X25519PrivateKey:
    """Закрытый ключ расшифровки из файла, который хранится вне сервера."""
    text = path.read_text(encoding="ascii").strip()
    return X25519PrivateKey.from_private_bytes(_unb64(text, 32, f"ключ {path.name}"))


def configured() -> bool:
    """Заданы ли ключи для шифрования и подписи копий."""
    return bool(settings.BACKUP_SIGNING_KEY and settings.BACKUP_RECIPIENT_KEY)


# --- Шифрование файла ----------------------------------------------------------------------


def _file_key(shared: bytes, ephemeral: bytes, recipient: bytes) -> bytes:
    """Ключ AES-256 файла из общего секрета X25519; соль — оба открытых ключа."""
    return HKDF(
        algorithm=hashes.SHA256(), length=32, salt=ephemeral + recipient, info=HKDF_INFO
    ).derive(shared)


def encrypt_file(source: Path, target: Path, recipient: X25519PublicKey) -> None:
    """Зашифровать файл потоком: большой архив не нужно держать в памяти."""
    ephemeral = X25519PrivateKey.generate()
    ephemeral_public = _raw_public(ephemeral.public_key())
    key = _file_key(ephemeral.exchange(recipient), ephemeral_public, _raw_public(recipient))
    nonce = secrets.token_bytes(NONCE_BYTES)
    header = MAGIC + ephemeral_public + nonce
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(header)
    partial = target.with_name(target.name + ".part")
    with source.open("rb") as reader, partial.open("wb") as writer:
        writer.write(header)
        while chunk := reader.read(CHUNK_BYTES):
            writer.write(encryptor.update(chunk))
        writer.write(encryptor.finalize())
        writer.write(encryptor.tag)
        writer.flush()
        os.fsync(writer.fileno())
    partial.replace(target)


def decrypt_file(source: Path, target: Path, private: X25519PrivateKey) -> None:
    """
    Расшифровать файл. Расшифрованное пишется во временный файл и становится ``target``
    только после проверки тега: недостоверные данные наружу не попадают.
    """
    size = source.stat().st_size
    if size < HEADER_BYTES + TAG_BYTES:
        raise BackupCryptoError(f"{source.name}: файл короче заголовка")
    partial = target.with_name(target.name + ".part")
    with source.open("rb") as reader:
        header = reader.read(HEADER_BYTES)
        if not header.startswith(MAGIC):
            raise BackupCryptoError(f"{source.name}: не зашифрованная копия RegionLens")
        ephemeral_public = header[len(MAGIC) : len(MAGIC) + PUBLIC_BYTES]
        nonce = header[len(MAGIC) + PUBLIC_BYTES :]
        reader.seek(size - TAG_BYTES)
        tag = reader.read(TAG_BYTES)
        reader.seek(HEADER_BYTES)
        shared = private.exchange(X25519PublicKey.from_public_bytes(ephemeral_public))
        key = _file_key(shared, ephemeral_public, _raw_public(private.public_key()))
        decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
        decryptor.authenticate_additional_data(header)
        remaining = size - HEADER_BYTES - TAG_BYTES
        try:
            with partial.open("wb") as writer:
                while remaining:
                    chunk = reader.read(min(CHUNK_BYTES, remaining))
                    remaining -= len(chunk)
                    writer.write(decryptor.update(chunk))
                writer.write(decryptor.finalize())
        except InvalidTag as error:
            partial.unlink(missing_ok=True)
            raise BackupCryptoError(
                f"{source.name}: не расшифровывается — чужой ключ или файл изменён"
            ) from error
    partial.replace(target)


def sha256_file(path: Path) -> str:
    """SHA-256 файла потоком."""
    digest = hashlib.sha256()
    with path.open("rb") as reader:
        while chunk := reader.read(CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


# --- Опись и подпись -----------------------------------------------------------------------


def manifest_paths(directory: Path, stamp: str) -> tuple[Path, Path]:
    """Опись копии и её подпись."""
    return directory / f"manifest-{stamp}.json", directory / f"manifest-{stamp}.sig"


def seal(directory: Path, stamp: str) -> list[Path]:
    """
    Зашифровать файлы копии с отметкой ``stamp``, удалить открытые и подписать опись.
    Вернуть созданные файлы: зашифрованные, опись и подпись.
    """
    recipient = recipient_key()
    signer = signing_key()
    entries: list[dict[str, Any]] = []
    created: list[Path] = []
    for pattern in PARTS:
        plain = directory / pattern.format(stamp=stamp)
        if not plain.exists():
            continue
        target = plain.with_name(plain.name + SUFFIX)
        encrypt_file(plain, target, recipient)
        entries.append(
            {
                "name": target.name,
                "plain": plain.name,
                "size": target.stat().st_size,
                "sha256": sha256_file(target),
            }
        )
        plain.unlink()
        created.append(target)
    if not entries:
        raise BackupCryptoError(f"нет файлов копии {stamp} в {directory}")
    manifest = {
        "format": FORMAT,
        "version": 1,
        "stamp": stamp,
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
        "recipient": fingerprint(recipient),
        "signer": fingerprint(signer.public_key()),
        "files": entries,
    }
    body = json.dumps(manifest, ensure_ascii=False, indent=2).encode() + b"\n"
    manifest_path, signature_path = manifest_paths(directory, stamp)
    manifest_path.write_bytes(body)
    signature_path.write_text(_b64(signer.sign(body)) + "\n", encoding="ascii")
    return [*created, manifest_path, signature_path]


def verify(manifest_path: Path, key: Ed25519PublicKey | None = None) -> dict[str, Any]:
    """
    Проверить подпись описи и каждый файл по размеру и SHA-256; вернуть опись.
    Любое несоответствие — ``BackupCryptoError`` с причиной.
    """
    key = key or verify_key()
    signature_path = manifest_path.with_suffix(".sig")
    if not signature_path.exists():
        raise BackupCryptoError(f"нет подписи {signature_path.name}")
    body = manifest_path.read_bytes()
    signature = _unb64(signature_path.read_text(encoding="ascii"), 64, "подпись")
    try:
        key.verify(signature, body)
    except InvalidSignature as error:
        raise BackupCryptoError(
            "подпись описи не сходится: опись изменена или подписана другим ключом"
        ) from error
    manifest: dict[str, Any] = json.loads(body)
    if manifest.get("format") != FORMAT:
        raise BackupCryptoError("опись не копии RegionLens")
    for entry in manifest["files"]:
        name = entry["name"]
        # Имена из описи становятся путями при расшифровке: только имя файла, без каталогов.
        if PurePath(name).name != name or PurePath(entry["plain"]).name != entry["plain"]:
            raise BackupCryptoError(f"недопустимое имя файла в описи: {name!r}")
        path = manifest_path.parent / name
        if not path.exists():
            raise BackupCryptoError(f"нет файла {name}")
        if path.stat().st_size != entry["size"]:
            raise BackupCryptoError(f"{name}: размер не совпадает с описью")
        if sha256_file(path) != entry["sha256"]:
            raise BackupCryptoError(f"{name}: SHA-256 не совпадает с описью — файл изменён")
    return manifest


def open_copy(manifest_path: Path, private: X25519PrivateKey, target: Path) -> list[Path]:
    """Проверить копию и расшифровать её файлы в ``target``; вернуть расшифрованные файлы."""
    manifest = verify(manifest_path)
    if manifest["recipient"] != fingerprint(private.public_key()):
        raise BackupCryptoError(
            f"копия зашифрована для ключа {manifest['recipient']}, "
            f"а дан ключ {fingerprint(private.public_key())}"
        )
    target.mkdir(parents=True, exist_ok=True)
    opened = []
    for entry in manifest["files"]:
        plain = target / entry["plain"]
        decrypt_file(manifest_path.parent / entry["name"], plain, private)
        opened.append(plain)
    return opened
