"""
Ключ и сертификаты подписи документов — из каталога ``ATTEST_DIR``.

В каталоге лежат ключ службы подписи и её сертификат, корневой сертификат и список отзыва.
Корневого закрытого ключа на сервере нет: сертификат службы выпускают им в стороне
(команда ``provenance ca issue``) и приносят сюда. Украденный с сервера ключ службы отзывается
списком отзыва, корневой сертификат у проверяющих остаётся прежним.

Без ключа подпись выключена: документы выдаются как прежде, страница проверки говорит,
что подпись не настроена.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from cryptography import x509
from django.conf import settings
from provenance import ca
from provenance.errors import ProvenanceError

SIGNER_KEY = "signer.key"
SIGNER_CERTIFICATE = "signer.crt"
ROOT_CERTIFICATE = "root.crt"
CRL = "crl.pem"
# Файлы каталога, которые отдаются всем: по ним документ проверяют без сайта.
PUBLIC_FILES = {
    ROOT_CERTIFICATE: "application/x-pem-file",
    SIGNER_CERTIFICATE: "application/x-pem-file",
    CRL: "application/x-pem-file",
}


def directory() -> Path:
    """Каталог ключей и сертификатов."""
    return Path(settings.ATTEST_DIR)


def _stamp(name: str) -> tuple[str, int]:
    """Путь файла и время его изменения: смена файла сбрасывает прочитанное."""
    path = directory() / name
    try:
        return str(path), path.stat().st_mtime_ns
    except OSError:
        return str(path), 0


@lru_cache(maxsize=4)
def _signer(key: tuple[str, int], certificate: tuple[str, int]) -> ca.Identity | None:
    if not key[1] or not certificate[1]:
        return None
    return ca.Identity(ca.load_key(Path(key[0])), ca.load_certificate(Path(certificate[0])))


@lru_cache(maxsize=4)
def _certificate(stamp: tuple[str, int]) -> x509.Certificate | None:
    return ca.load_certificate(Path(stamp[0])) if stamp[1] else None


@lru_cache(maxsize=4)
def _crl(stamp: tuple[str, int]) -> x509.CertificateRevocationList | None:
    return ca.load_crl(Path(stamp[0])) if stamp[1] else None


def signer() -> ca.Identity | None:
    """Ключ и сертификат службы подписи; ``None`` — подпись не настроена."""
    return _signer(_stamp(SIGNER_KEY), _stamp(SIGNER_CERTIFICATE))


def roots() -> list[x509.Certificate]:
    """Корневые сертификаты, которым верит страница проверки."""
    root = _certificate(_stamp(ROOT_CERTIFICATE))
    return [root] if root is not None else []


def crl() -> x509.CertificateRevocationList | None:
    """Список отзыва, если он положен в каталог."""
    return _crl(_stamp(CRL))


def configured() -> bool:
    """Подпись настроена: есть ключ службы, её сертификат и корневой сертификат."""
    try:
        return signer() is not None and bool(roots())
    except ProvenanceError:
        return False


@dataclass(frozen=True, slots=True)
class Trust:
    """Что сайт публикует о своей подписи: по этим сведениям документ проверяют без него."""

    root_name: str
    root_fingerprint: str
    signer_name: str
    signer_serial: str
    signer_until: datetime
    crl_updated: datetime | None


def trust() -> Trust | None:
    """Сведения о корневом сертификате и сертификате службы; без настройки — ``None``."""
    if not configured():
        return None
    identity, root, revoked = signer(), roots()[0], crl()
    assert identity is not None
    return Trust(
        root_name=ca.common_name(root.subject),
        root_fingerprint=ca.fingerprint(root),
        signer_name=ca.common_name(identity.certificate.subject),
        signer_serial=ca.serial_text(identity.certificate),
        signer_until=identity.certificate.not_valid_after_utc,
        crl_updated=revoked.last_update_utc if revoked is not None else None,
    )
