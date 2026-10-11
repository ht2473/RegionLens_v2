"""Шифрование и подпись резервных копий: X25519 + AES-256-GCM, опись с подписью Ed25519."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from apps.core import backup_crypto

STAMP = "20261011-030000"


@pytest.fixture
def keys(settings: Any, tmp_path: Path) -> tuple[backup_crypto.KeyPairs, Path]:
    """Ключи копий в настройках и закрытый ключ расшифровки — в файле вне каталога копий."""
    pairs = backup_crypto.new_key_pairs()
    settings.BACKUP_SIGNING_KEY = pairs.signing
    settings.BACKUP_VERIFY_KEY = pairs.verify
    settings.BACKUP_RECIPIENT_KEY = pairs.recipient
    key_file = tmp_path / "recipient.key"
    key_file.write_text(pairs.recipient_private)
    return pairs, key_file


@pytest.fixture
def copy_dir(tmp_path: Path) -> Path:
    """Каталог с копией: выгрузка базы и архив файлов."""
    directory = tmp_path / "backups"
    directory.mkdir()
    (directory / f"db-{STAMP}.dump").write_bytes(b"PGDMP" + bytes(range(256)) * 50)
    (directory / f"files-{STAMP}.tar.gz").write_bytes(b"\x1f\x8b" + b"data" * 3000)
    return directory


def test_file_round_trip_in_chunks(keys: Any, tmp_path: Path, monkeypatch: Any) -> None:
    """Файл больше куска потока расшифровывается в точности."""
    monkeypatch.setattr(backup_crypto, "CHUNK_BYTES", 1000)
    _pairs, key_file = keys
    source = tmp_path / "plain.bin"
    source.write_bytes(bytes(range(256)) * 37)
    sealed, opened = tmp_path / "plain.bin.enc", tmp_path / "opened.bin"
    backup_crypto.encrypt_file(source, sealed, backup_crypto.recipient_key())
    assert source.read_bytes() not in sealed.read_bytes()
    backup_crypto.decrypt_file(sealed, opened, backup_crypto.recipient_private_key(key_file))
    assert opened.read_bytes() == source.read_bytes()


def test_tampered_or_foreign_file_does_not_decrypt(keys: Any, tmp_path: Path) -> None:
    """Изменённый байт или чужой ключ — отказ, и расшифрованного файла не остаётся."""
    _pairs, key_file = keys
    source, sealed, opened = tmp_path / "a", tmp_path / "a.enc", tmp_path / "b"
    source.write_bytes(b"secret" * 1000)
    backup_crypto.encrypt_file(source, sealed, backup_crypto.recipient_key())
    data = bytearray(sealed.read_bytes())
    data[backup_crypto.HEADER_BYTES + 10] ^= 0x01
    sealed.write_bytes(bytes(data))
    with pytest.raises(backup_crypto.BackupCryptoError, match="изменён"):
        backup_crypto.decrypt_file(sealed, opened, backup_crypto.recipient_private_key(key_file))
    assert not opened.exists()
    assert not opened.with_name("b.part").exists()

    backup_crypto.encrypt_file(source, sealed, backup_crypto.recipient_key())
    stranger = tmp_path / "stranger.key"
    stranger.write_text(backup_crypto.new_key_pairs().recipient_private)
    with pytest.raises(backup_crypto.BackupCryptoError):
        backup_crypto.decrypt_file(sealed, opened, backup_crypto.recipient_private_key(stranger))


def test_seal_verify_open(keys: Any, copy_dir: Path, tmp_path: Path) -> None:
    """Копия шифруется, открытые файлы удаляются, опись проверяется, копия открывается."""
    _pairs, key_file = keys
    originals = {path.name: path.read_bytes() for path in copy_dir.iterdir()}
    created = backup_crypto.seal(copy_dir, STAMP)
    names = sorted(path.name for path in copy_dir.iterdir())
    assert names == sorted(
        [
            f"db-{STAMP}.dump.enc",
            f"files-{STAMP}.tar.gz.enc",
            f"manifest-{STAMP}.json",
            f"manifest-{STAMP}.sig",
        ]
    )
    assert len(created) == 4
    manifest, _signature = backup_crypto.manifest_paths(copy_dir, STAMP)
    assert backup_crypto.verify(manifest)["stamp"] == STAMP
    opened = backup_crypto.open_copy(
        manifest, backup_crypto.recipient_private_key(key_file), tmp_path / "opened"
    )
    assert {path.name: path.read_bytes() for path in opened} == originals


def test_swapped_file_fails_verification(keys: Any, copy_dir: Path) -> None:
    """Подменённый файл копии (другое содержимое, тот же ключ получателя) — отказ по SHA-256."""
    backup_crypto.seal(copy_dir, STAMP)
    target = copy_dir / f"db-{STAMP}.dump.enc"
    forged = copy_dir / "forged.dump"
    forged.write_bytes("PGDMP подменённая база".encode())
    backup_crypto.encrypt_file(forged, target, backup_crypto.recipient_key())
    manifest, _signature = backup_crypto.manifest_paths(copy_dir, STAMP)
    with pytest.raises(backup_crypto.BackupCryptoError):
        backup_crypto.verify(manifest)


def test_edited_manifest_fails_signature(keys: Any, copy_dir: Path) -> None:
    """Опись с поправленной суммой не проходит подпись: подменить файл и опись вместе нельзя."""
    backup_crypto.seal(copy_dir, STAMP)
    manifest, _signature = backup_crypto.manifest_paths(copy_dir, STAMP)
    body = json.loads(manifest.read_text())
    body["files"][0]["sha256"] = "0" * 64
    manifest.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n")
    with pytest.raises(backup_crypto.BackupCryptoError, match="подпись"):
        backup_crypto.verify(manifest)


def test_foreign_signer_is_rejected(keys: Any, copy_dir: Path) -> None:
    """Опись, подписанная чужим ключом, не проходит проверку ключом стенда."""
    backup_crypto.seal(copy_dir, STAMP)
    manifest, _signature = backup_crypto.manifest_paths(copy_dir, STAMP)
    other = backup_crypto.verify_key(backup_crypto.new_key_pairs().verify)
    with pytest.raises(backup_crypto.BackupCryptoError, match="подпись"):
        backup_crypto.verify(manifest, other)


def test_manifest_cannot_point_outside(keys: Any, copy_dir: Path, tmp_path: Path) -> None:
    """Имя файла с каталогом в описи — отказ, даже если опись подписана верным ключом."""
    _pairs, key_file = keys
    backup_crypto.seal(copy_dir, STAMP)
    manifest, signature = backup_crypto.manifest_paths(copy_dir, STAMP)
    body = json.loads(manifest.read_text())
    body["files"][0]["plain"] = "../../app/config.py"
    data = (json.dumps(body, ensure_ascii=False, indent=2) + "\n").encode()
    manifest.write_bytes(data)
    signature.write_text(backup_crypto._b64(backup_crypto.signing_key().sign(data)) + "\n")
    with pytest.raises(backup_crypto.BackupCryptoError, match="имя"):
        backup_crypto.open_copy(
            manifest, backup_crypto.recipient_private_key(key_file), tmp_path / "out"
        )


def test_wrong_private_key_is_named(keys: Any, copy_dir: Path, tmp_path: Path) -> None:
    """Ключ не того получателя — отказ с отпечатками обоих ключей до расшифровки."""
    backup_crypto.seal(copy_dir, STAMP)
    manifest, _signature = backup_crypto.manifest_paths(copy_dir, STAMP)
    stranger = tmp_path / "stranger.key"
    stranger.write_text(backup_crypto.new_key_pairs().recipient_private)
    with pytest.raises(backup_crypto.BackupCryptoError, match="зашифрована для ключа"):
        backup_crypto.open_copy(
            manifest, backup_crypto.recipient_private_key(stranger), tmp_path / "out"
        )
    assert not (tmp_path / "out").exists()
