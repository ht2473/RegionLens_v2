"""
Подпись выдаваемых документов и их проверка.

Документ с официальными данными при выдаче подписывается ключом службы (подпись CMS,
ECDSA P-256), а его SHA-256 и подпись записываются в реестр выданных. Проверить документ
можно двумя путями: по одному файлу — сайт находит его в реестре и заново проверяет подпись;
или с файлом подписи — тогда реестр не нужен, а проверку повторит и сторонняя программа
(``provenance verify``, ``openssl cms -verify``).

Таблицы пользователей не подписываются: сайт ручается только за свои данные.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.functional import Promise
from django.utils.translation import gettext_lazy as _
from provenance import cms
from provenance.errors import Code, ProvenanceError, VerifyError

from apps.catalog.versions import current_version_code

from . import keys
from .models import IssuedDocument

logger = logging.getLogger(__name__)


# Причина отказа проверки — словами сайта, на языке читателя.
REASONS: dict[Code, Promise] = {
    Code.MALFORMED: _("Файл подписи повреждён или это не подпись."),
    Code.UNSUPPORTED: _("Подпись другого вида: сайт выдаёт отсоединённую подпись ECDSA с SHA-256."),
    Code.DOCUMENT_CHANGED: _(
        "Документ изменён после подписи, или подпись сделана для другого файла."
    ),
    Code.BAD_SIGNATURE: _("Подпись не сходится с ключом из сертификата."),
    Code.UNKNOWN_SIGNER: _("В подписи нет сертификата подписавшего."),
    Code.UNTRUSTED: _("Подпись сделана не этим сайтом: сертификат выпущен другим центром."),
    Code.WRONG_PURPOSE: _("Сертификат подписавшего не предназначен для подписи документов."),
    Code.NOT_VALID_THEN: _("В момент подписи сертификат не действовал."),
    Code.FROM_FUTURE: _("Время подписи позже времени проверки."),
    Code.REVOKED: _("Сертификат, которым подписан документ, отозван."),
    Code.BAD_CRL: _("Список отзыва сайта не прошёл проверку."),
}


class Status(StrEnum):
    """Исход проверки документа."""

    AUTHENTIC = "authentic"
    REFUSED = "refused"
    UNKNOWN = "unknown"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class Outcome:
    """Что показать проверяющему: исход, хэш файла, кто подписал, запись реестра."""

    status: Status
    digest: str
    verified: cms.Verified | None = None
    record: IssuedDocument | None = None
    reason: Promise | str = ""
    code: str = ""


def issue(payload: bytes, *, kind: str, export_format: str, filename: str) -> IssuedDocument | None:
    """
    Подписать выдаваемый документ и записать его в реестр; без настроенной подписи — ``None``.

    Тот же документ второй раз не подписывается: запись находится по хэшу.
    """
    try:
        signer = keys.signer()
    except ProvenanceError as error:
        logger.error("Подпись документов не работает: %s", error)
        return None
    if signer is None:
        return None
    digest = hashlib.sha256(payload).hexdigest()
    found = IssuedDocument.objects.filter(sha256=digest).first()
    if found is not None:
        return found
    try:
        with transaction.atomic():
            return IssuedDocument.objects.create(
                sha256=digest,
                size=len(payload),
                kind=kind,
                export_format=export_format,
                filename=filename[:255],
                dataset_version=current_version_code(),
                signature=cms.sign(payload, signer),
            )
    except IntegrityError:
        # Тот же документ в ту же секунду выдал соседний рабочий процесс.
        return IssuedDocument.objects.filter(sha256=digest).first()


def check(content: bytes, signature: bytes | None = None) -> Outcome:
    """
    Проверить документ: с приложенной подписью — по ней, без неё — по реестру выданных.

    Запись реестра сама ничего не доказывает: подпись из неё проверяется заново, так что
    вписанная в базу строка без настоящей подписи проверку не пройдёт.
    """
    digest = hashlib.sha256(content).hexdigest()
    try:
        roots, crl = keys.roots(), keys.crl()
    except ProvenanceError as error:
        logger.error("Сертификаты проверки не читаются: %s", error)
        roots, crl = [], None
    if not roots:
        return Outcome(Status.UNAVAILABLE, digest)

    record = IssuedDocument.objects.filter(sha256=digest).first()
    if signature is None:
        if record is None:
            return Outcome(Status.UNKNOWN, digest)
        signature = bytes(record.signature)
    try:
        verified = cms.verify(content, signature, roots, crl=crl)
    except VerifyError as error:
        return Outcome(
            Status.REFUSED,
            digest,
            record=record,
            reason=REASONS.get(error.code, error.message),
            code=str(error.code),
        )
    return Outcome(Status.AUTHENTIC, digest, verified=verified, record=record)


def prune() -> int:
    """Убрать из реестра записи старше срока хранения; вернуть их число."""
    edge = timezone.now() - timedelta(days=settings.ATTEST_REGISTRY_DAYS)
    removed, _kinds = IssuedDocument.objects.filter(created_at__lt=edge).delete()
    return removed
