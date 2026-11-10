"""
Подписанная вершина описи архива выпусков.

Опись архива (``MANIFEST.csv``) — перечень хэшей: он ловит случайную порчу файла, но тот,
кто подменит файл, поправит и строку описи. Над строками описи строится дерево хэшей,
а его вершина подписывается ключом службы подписи. Подменить файл вместе со строкой после
этого нельзя: новую вершину подписать нечем. Прежние вершины хранятся рядом — по ним виден
и откат описи к прежнему состоянию.

Вершина обновляется после каждой новой записи архива. Сверка делит расхождения на два вида:
изменённая или убранная запись — подмена, и склад по такому архиву не пересобирается;
новые записи после подписанной вершины — подпись отстала, это предупреждение.
"""

from __future__ import annotations

import logging
from pathlib import Path

from provenance import log
from provenance.errors import ProvenanceError, VerifyError

from apps.sources import archive

from . import keys

logger = logging.getLogger(__name__)

LOG_NAME = "regionlens-archive"


def _manifest() -> Path:
    return archive.archive_root() / archive.MANIFEST_NAME


def seal() -> log.Head | None:
    """Подписать вершину описи; без настроенной подписи или без описи — ``None``."""
    manifest = _manifest()
    signer = keys.signer()
    if signer is None or not manifest.exists():
        return None
    return log.seal_file(manifest, signer, LOG_NAME)


def reseal() -> None:
    """Обновить вершину после новой записи архива; отказ не срывает получение выпуска."""
    try:
        seal()
    except (ProvenanceError, OSError) as error:
        # Сверка архива назовёт неподписанные записи; выпуск при этом уже лежит в архиве.
        logger.error("Вершина описи архива не подписана: %s", error)


def problems() -> list[str]:
    """
    Подмена описи: изменённые, убранные или переставленные записи, удалённая вершина.

    Без подписи и без вершины сверять нечего. Вершина, которую удалили после того, как
    опись уже подписывалась, — подмена: иначе защиту снимало бы удаление одного файла.
    """
    manifest = _manifest()
    if not manifest.exists():
        return []
    head_path, _signature, history_path = log.paths(manifest)
    name = archive.MANIFEST_NAME
    if not head_path.exists():
        deleted = f"{name}: подписанная вершина удалена, а опись уже подписывалась"
        return [deleted] if history_path.exists() else []
    try:
        roots = keys.roots()
        if not roots:
            return [f"{name}: вершину нечем проверить — нет корневого сертификата"]
        log.check_file(manifest, roots, crl=keys.crl())
    except VerifyError as error:
        return [f"{name}: {error.message}"]
    except ProvenanceError as error:
        return [f"{name}: сертификаты подписи не читаются ({error})"]
    return []


def unsealed() -> list[str]:
    """Записи описи, которые подпись ещё не покрывает: опись новая или подпись отстала."""
    manifest = _manifest()
    if not manifest.exists() or not keys.configured():
        return []
    head_path, _signature, history_path = log.paths(manifest)
    name = archive.MANIFEST_NAME
    if not head_path.exists():
        if history_path.exists():
            return []  # это подмена, о ней говорит problems()
        return [f"{name}: опись не подписана (manage.py archive_seal)"]
    try:
        checked = log.check_file(manifest, keys.roots(), crl=keys.crl())
    except ProvenanceError:
        return []  # подмена или сбой проверки — о них говорит problems()
    if checked.unsealed:
        return [f"{name}: после подписанной вершины записей {checked.unsealed} — они не подписаны"]
    return []
