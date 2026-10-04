"""
Приём таблицы: набор и версия в базе, исходный файл на диске, перечень таблиц в нём,
выбор таблицы и дозагрузка второй части вставки.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
from dataclasses import asdict
from pathlib import Path, PurePosixPath

from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from django.http import HttpRequest
from django.utils.translation import gettext as _

from . import access, ingest, paste, tables
from .models import Dataset, DatasetVersion

logger = logging.getLogger(__name__)

SOURCE_STEM = "source"
TABLE_STEM = "table"
# Расширения, с которыми исходный файл хранится; прочее — «.bin»: вид решают первые байты.
KNOWN_SUFFIXES = frozenset(
    {".csv", ".tsv", ".txt", ".xlsx", ".xlsm", ".xls", ".ods", ".parquet", ".zip", ".htm", ".html"}
)


def create_from_upload(request: HttpRequest, uploaded: UploadedFile) -> Dataset:
    """Принять загруженный файл; непригодный — отказ ``IngestError`` без следов на диске."""
    limit = settings.USERDATA_UPLOAD_MAX_BYTES
    if uploaded.size is None or uploaded.size > limit:
        raise ingest.IngestError(upload_size_text(), "upload_size")
    name = PurePosixPath((uploaded.name or "").replace("\\", "/")).name or "table"
    suffix = PurePosixPath(name).suffix.lower()
    dataset, version = _create(request, title=_title(name), file_name=name, size=uploaded.size)
    path = version.directory / (SOURCE_STEM + (suffix if suffix in KNOWN_SUFFIXES else ".bin"))
    try:
        version.sha256 = _store(uploaded, path, limit)
        inspection = ingest.inspect(path, name)
    except ingest.IngestError:
        discard(dataset)
        raise
    return _accept(dataset, version, inspection, path)


def create_from_paste(request: HttpRequest, text: str, markup: str = "") -> Dataset:
    """Принять вставленную таблицу: она хранится файлом TSV."""
    rows = paste.rows_of(text, markup)
    if len(rows) < 2:  # noqa: PLR2004 — шапка и хотя бы одна строка
        raise ingest.IngestError(
            _("Таблица не распознана. Скопируйте её целиком вместе с шапкой."), "paste_empty"
        )
    dataset, version = _create(
        request, title=_("Вставленная таблица"), file_name=_("Вставка из буфера"), size=0
    )
    version.file_kind = ingest.PASTE
    path = version.directory / paste.FILE_NAME
    version.file_size = paste.write(rows, path)
    version.sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    try:
        inspection = ingest.inspect(path, paste.FILE_NAME)
    except ingest.IngestError:
        discard(dataset)
        raise
    return _accept(dataset, version, inspection, path)


def append_paste(version: DatasetVersion, text: str, markup: str = "") -> int:
    """Дописать вторую часть вставленной таблицы; вернуть число добавленных строк."""
    path = version.directory / paste.FILE_NAME
    existing = paste.read(path)
    combined = paste.append(existing, paste.rows_of(text, markup))
    added = len(combined) - len(existing)
    if not added:
        return 0
    version.file_size = paste.write(combined, path)
    version.sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    inspection = ingest.inspect(path, paste.FILE_NAME)
    version.report = {**version.report, "inspection": inspection.as_dict()}
    version.recipe = {}
    version.save(update_fields=["file_size", "sha256", "report", "recipe", "updated_at"])
    _update_size(version.dataset)
    return added


def inspection_of(version: DatasetVersion, *, encoding: str = "") -> ingest.Inspection:
    """Перечень таблиц версии; с выбранной кодировкой — прочитанный заново."""
    if encoding:
        return ingest.inspect(source_path(version), version.file_name, encoding=encoding)
    return ingest.Inspection.from_dict(version.report["inspection"])


def choose_table(version: DatasetVersion, key: str, *, encoding: str = "") -> ingest.TableInfo:
    """Выбрать таблицу: из архива распаковывается только она; выбор — в рецепте версии."""
    inspection = inspection_of(version, encoding=encoding)
    table = next((item for item in inspection.tables if item.key == key), None)
    if table is None:
        raise ingest.IngestError(_("Такой таблицы в файле нет."), "member")
    source = source_path(version)
    path = source
    if inspection.kind == ingest.ZIP:
        suffix = PurePosixPath(table.member).suffix.lower()
        path = ingest.extract_member(
            source, table.member, version.directory / (TABLE_STEM + suffix)
        )
    chosen = asdict(table)
    chosen.pop("sample")
    if table.kind == ingest.CSV and table.encoding != "utf-8":
        # Разбор и сборка читают текст в UTF-8; исходный файл остаётся как загружен.
        target = version.directory / f"{TABLE_STEM}.utf8.csv"
        path = tables.transcode(path, table.encoding or "utf-8", target)
        chosen["encoding"] = "utf-8"
    version.recipe = {"table": chosen, "file": path.name}
    version.report = {
        key: value for key, value in version.report.items() if not key.startswith("profile")
    }
    version.save(update_fields=["recipe", "report", "updated_at"])
    _update_size(version.dataset)
    return table


def table_path(version: DatasetVersion) -> Path:
    """Файл выбранной таблицы."""
    return version.directory / version.recipe["file"]


def source_path(version: DatasetVersion) -> Path:
    """Исходный файл версии как загружен."""
    found = sorted(version.directory.glob(f"{SOURCE_STEM}.*")) or sorted(
        version.directory.glob(paste.FILE_NAME)
    )
    if not found:
        raise ingest.IngestError(_("Файл таблицы не найден. Загрузите его снова."), "missing")
    return found[0]


def discard(dataset: Dataset) -> None:
    """Удалить набор вместе с файлами."""
    shutil.rmtree(dataset.directory, ignore_errors=True)
    dataset.delete()


def upload_size_text() -> str:
    """Отказ из-за размера: тот же текст и в браузере до отправки."""
    return _(
        "Файл больше %(limit)s МБ. Загрузите архив ZIP или оставьте в таблице только "
        "нужные показатели."
    ) % {"limit": settings.USERDATA_UPLOAD_MAX_BYTES // (1024 * 1024)}


def _create(
    request: HttpRequest, *, title: str, file_name: str, size: int
) -> tuple[Dataset, DatasetVersion]:
    with transaction.atomic():
        dataset = Dataset(title=title[:200])
        access.assign_owner(dataset, request)
        dataset.save()
        version = DatasetVersion.objects.create(
            dataset=dataset,
            number=1,
            file_name=file_name[:255],
            file_size=size,
            sha256="",
            file_kind="",
        )
    version.directory.mkdir(parents=True, exist_ok=True)
    return dataset, version


def _accept(
    dataset: Dataset, version: DatasetVersion, inspection: ingest.Inspection, path: Path
) -> Dataset:
    if version.file_kind != ingest.PASTE:
        version.file_kind = inspection.kind
    version.file_size = path.stat().st_size
    version.report = {"inspection": inspection.as_dict()}
    version.save()
    dataset.current_version = version
    dataset.save(update_fields=["current_version", "updated_at"])
    _update_size(dataset)
    logger.info(
        "userdata: принят набор %s, вид %s, %s байт, таблиц %s",
        dataset.public_id,
        version.file_kind,
        version.file_size,
        len(inspection.tables),
    )
    return dataset


def _store(uploaded: UploadedFile, target: Path, limit: int) -> str:
    """Записать файл по частям с отпечатком SHA-256; размер сверяется по ходу."""
    digest = hashlib.sha256()
    written = 0
    with target.open("wb") as output:
        for chunk in uploaded.chunks():
            written += len(chunk)
            if written > limit:
                break
            digest.update(chunk)
            output.write(chunk)
    if written > limit:
        target.unlink(missing_ok=True)
        raise ingest.IngestError(upload_size_text(), "upload_size")
    return digest.hexdigest()


def _update_size(dataset: Dataset) -> None:
    """Занятое место — все файлы набора на диске."""
    total = sum(path.stat().st_size for path in dataset.directory.rglob("*") if path.is_file())
    Dataset.objects.filter(pk=dataset.pk).update(size_bytes=total)
    dataset.size_bytes = total


def _title(file_name: str) -> str:
    """Название набора по имени файла: без расширения и подчёркиваний."""
    stem = PurePosixPath(file_name).stem.replace("_", " ").strip()
    return stem or _("Таблица")
