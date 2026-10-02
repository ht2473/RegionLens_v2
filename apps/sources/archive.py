"""
Неизменяемый архив полученных файлов: ``<источник>/<дата>_<имя>`` и опись ``MANIFEST.csv``.

Файл кладётся один раз и больше не меняется; повторно полученный файл с тем же хэшем
не записывается. Всё разобранное и склад воспроизводимы из архива.
"""

from __future__ import annotations

import csv
import hashlib
import io
import os
import re
import stat
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.utils import timezone

MANIFEST_NAME = "MANIFEST.csv"
MANIFEST_FIELDS = ("sha256", "size", "source", "original_name", "url", "captured_at", "path")


@dataclass(frozen=True, slots=True)
class ArchivedFile:
    """Запись описи: файл архива и откуда он получен."""

    sha256: str
    size: int
    source: str
    original_name: str
    url: str
    captured_at: str
    path: str  # относительно корня архива

    @property
    def absolute_path(self) -> Path:
        """Путь к файлу на диске."""
        return archive_root() / self.path

    @property
    def captured(self) -> datetime:
        """Момент получения."""
        return datetime.fromisoformat(self.captured_at)


def archive_root() -> Path:
    """Корень архива."""
    return Path(settings.SOURCE_ARCHIVE_DIR)


def sha256_of(content: bytes) -> str:
    """Хэш содержимого."""
    return hashlib.sha256(content).hexdigest()


def entries(source: str | None = None) -> list[ArchivedFile]:
    """Записи описи в порядке получения, по одному источнику или все."""
    manifest = archive_root() / MANIFEST_NAME
    if not manifest.exists():
        return []
    reader = csv.DictReader(io.StringIO(manifest.read_bytes().decode("utf-8")))
    found = [
        ArchivedFile(
            sha256=row["sha256"],
            size=int(row["size"]),
            source=row["source"],
            original_name=row["original_name"],
            url=row["url"],
            captured_at=row["captured_at"],
            path=row["path"],
        )
        for row in reader
    ]
    return [item for item in found if source is None or item.source == source]


def find(sha256: str) -> ArchivedFile | None:
    """Запись описи по хэшу содержимого."""
    return next((item for item in entries() if item.sha256 == sha256), None)


def store(
    source: str,
    original_name: str,
    content: bytes,
    *,
    url: str = "",
    captured_at: datetime | None = None,
) -> tuple[ArchivedFile, bool]:
    """
    Положить файл в архив; вернуть запись и признак того, что файл новый.

    Файл с тем же содержимым уже в архиве — возвращается прежняя запись.
    """
    digest = sha256_of(content)
    existing = find(digest)
    if existing is not None:
        return existing, False

    moment = captured_at or timezone.now()
    safe_name = re.sub(r"[^\w.\-]+", "_", Path(original_name).name) or "file"
    directory = archive_root() / source
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{moment:%Y-%m-%d}_{safe_name}"
    counter = 2
    while target.exists():
        target = directory / f"{moment:%Y-%m-%d}_{counter}_{safe_name}"
        counter += 1

    temporary = target.with_name(target.name + ".part")
    temporary.write_bytes(content)
    temporary.replace(target)
    # Файл архива только для чтения: его не должен переписать никто, включая сборщик.
    target.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)

    record = ArchivedFile(
        sha256=digest,
        size=len(content),
        source=source,
        original_name=original_name,
        url=url,
        captured_at=moment.isoformat(timespec="seconds"),
        path=target.relative_to(archive_root()).as_posix(),
    )
    _append(record)
    return record, True


def verify() -> list[str]:
    """Сверить архив с описью: пропавшие файлы и несовпадение хэша."""
    problems = []
    for item in entries():
        path = item.absolute_path
        if not path.exists():
            problems.append(f"{item.path}: файла нет")
        elif sha256_of(path.read_bytes()) != item.sha256:
            problems.append(f"{item.path}: хэш не совпадает с описью")
    return problems


def _append(record: ArchivedFile) -> None:
    """Дописать строку в опись; заголовок — при первой записи."""
    manifest = archive_root() / MANIFEST_NAME
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=MANIFEST_FIELDS, lineterminator="\n")
    if not manifest.exists():
        writer.writeheader()
    writer.writerow(asdict(record))
    with manifest.open("ab") as handle:
        handle.write(buffer.getvalue().encode("utf-8"))
        handle.flush()
        os.fsync(handle.fileno())
