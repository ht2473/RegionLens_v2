"""
Приём таблицы: набор и версия в базе, исходный файл на диске, перечень таблиц в нём,
выбор таблицы и дозагрузка второй части вставки; новая версия файла той же таблицы.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
from dataclasses import asdict
from pathlib import Path, PurePosixPath
from typing import Any

from django.conf import settings
from django.core.files import File
from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from django.db.models import Max, Sum
from django.http import HttpRequest
from django.utils.translation import gettext as _

from apps.core.throttle import allow, allow_key
from apps.warehouse.duckdb_client import close_dataset

from . import access, ingest, paste, tables
from .models import Dataset, DatasetVersion

logger = logging.getLogger(__name__)

# Пример: набор «Если быть точным» «Состояние окружающей среды в регионах России с 2014 года».
EXAMPLE_PATH = Path(settings.REFERENCE_DIR) / "userdata_example.zip"
EXAMPLE_NAME = "data_environment_113_v20230123_csv.zip"
EXAMPLE_URL = "https://tochno.st/datasets/environment"

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


def create_version(
    request: HttpRequest,
    dataset: Dataset,
    *,
    uploaded: UploadedFile | None = None,
    text: str = "",
    markup: str = "",
) -> DatasetVersion:
    """
    Принять новый файл той же таблицы: версия-черновик рядом с текущей, которая работает,
    пока новая не собрана. Прежний черновик удаляется; файл, совпадающий с текущим, — отказ.
    """
    from . import renew

    check_limits(request, new_table=False)
    previous = dataset.current_version
    renew.discard_draft(dataset)
    number = (dataset.versions.aggregate(top=Max("number"))["top"] or 0) + 1
    if uploaded is not None:
        limit = settings.USERDATA_UPLOAD_MAX_BYTES
        if uploaded.size is None or uploaded.size > limit:
            raise ingest.IngestError(upload_size_text(), "upload_size")
        name = PurePosixPath((uploaded.name or "").replace("\\", "/")).name or "table"
        suffix = PurePosixPath(name).suffix.lower()
        version = _new_version(dataset, number, previous, name, uploaded.size)
        path = version.directory / (SOURCE_STEM + (suffix if suffix in KNOWN_SUFFIXES else ".bin"))
        try:
            version.sha256 = _store(uploaded, path, limit)
            _check_changed(version, previous)
            inspection = ingest.inspect(path, name)
        except ingest.IngestError:
            _drop(version)
            raise
    else:
        rows = paste.rows_of(text, markup)
        if len(rows) < 2:  # noqa: PLR2004 — шапка и хотя бы одна строка
            raise ingest.IngestError(
                _("Таблица не распознана. Скопируйте её целиком вместе с шапкой."), "paste_empty"
            )
        version = _new_version(dataset, number, previous, _("Вставка из буфера"), 0)
        version.file_kind = ingest.PASTE
        path = version.directory / paste.FILE_NAME
        version.file_size = paste.write(rows, path)
        version.sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        try:
            _check_changed(version, previous)
            inspection = ingest.inspect(path, paste.FILE_NAME)
        except ingest.IngestError:
            _drop(version)
            raise
    if version.file_kind != ingest.PASTE:
        version.file_kind = inspection.kind
    version.file_size = path.stat().st_size
    version.report = {"inspection": inspection.as_dict(), "renewal": {"state": renew.AUTO}}
    version.save()
    update_size(dataset)
    logger.info(
        "userdata: новая версия %s набора %s, вид %s, %s байт",
        version.number,
        dataset.public_id,
        version.file_kind,
        version.file_size,
    )
    return version


def _new_version(
    dataset: Dataset, number: int, previous: DatasetVersion | None, name: str, size: int
) -> DatasetVersion:
    version = DatasetVersion.objects.create(
        dataset=dataset,
        number=number,
        previous=previous,
        file_name=name[:255],
        file_size=size,
        sha256="",
        file_kind="",
    )
    version.directory.mkdir(parents=True, exist_ok=True)
    return version


def _check_changed(version: DatasetVersion, previous: DatasetVersion | None) -> None:
    """Файл, совпадающий с текущей версией, новой версией не становится."""
    if previous is not None and previous.sha256 and previous.sha256 == version.sha256:
        raise ingest.IngestError(
            _("Этот файл совпадает с текущей версией таблицы: менять нечего."), "same_file"
        )


def drop_version(version: DatasetVersion) -> None:
    """Удалить версию вместе с её каталогом; занятый файл сборки удалит очистка."""
    _drop(version)


def _drop(version: DatasetVersion) -> None:
    for path in version.directory.rglob("data-*.duckdb"):
        close_dataset(path)
    shutil.rmtree(version.directory, ignore_errors=True)
    dataset = version.dataset
    version.delete()
    update_size(dataset)


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
    update_size(version.dataset)
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
    update_size(version.dataset)
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


def discard(dataset: Dataset, *, rebuild: bool = True) -> None:
    """
    Удалить набор вместе с файлами. Файл сборки, открытый другим процессом (Windows),
    удалит очистка ``prune_personal_data``: каталог без набора — осиротевший. ``rebuild`` —
    пересобрать таблицы, формулы которых ссылались на эту.
    """
    from .formula_store import dependents

    waiting = dependents(dataset) if rebuild else []
    for path in dataset.directory.rglob("data-*.duckdb"):
        close_dataset(path)
    shutil.rmtree(dataset.directory, ignore_errors=True)
    dataset.delete()
    # Формулы других таблиц, ссылавшиеся на эту, остаются без значений — с причиной.
    from . import jobs

    for other in waiting:
        version = other.current_version
        if version is not None and version.state == version.State.BUILT:
            jobs.start(version, jobs.BUILD)


def check_limits(request: HttpRequest, *, new_table: bool = True) -> None:
    """
    Пределы до приёма таблицы: частота загрузок, число таблиц и место; ``new_table`` ложно —
    новая версия той же таблицы, число таблиц не растёт.
    """
    user = request.user
    if user.is_authenticated:
        if not allow_key(
            "userdata-upload",
            f"user:{user.pk}",
            limit=settings.USERDATA_UPLOADS_PER_HOUR,
            window=3600,
        ):
            raise ingest.IngestError(_rate_text(), "rate")
        mine = Dataset.objects.filter(owner=user)
        if new_table and mine.count() >= settings.USERDATA_MAX_DATASETS:
            raise ingest.IngestError(
                _("Таблиц уже %(count)s — это предел. Удалите ненужные таблицы.")
                % {"count": settings.USERDATA_MAX_DATASETS},
                "quota",
            )
        if used_bytes(user) >= settings.USERDATA_QUOTA_BYTES:
            raise ingest.IngestError(
                _("Место для таблиц закончилось: %(limit)s МБ. Удалите ненужные таблицы.")
                % {"limit": settings.USERDATA_QUOTA_BYTES // (1024 * 1024)},
                "space",
            )
        return
    if not allow(
        request, "userdata-upload", limit=settings.USERDATA_GUEST_UPLOADS_PER_HOUR, window=3600
    ):
        raise ingest.IngestError(_rate_text(), "rate")
    if new_table and access.owned(request).count() >= settings.USERDATA_GUEST_MAX_DATASETS:
        raise ingest.IngestError(
            _(
                "Без входа можно держать не больше %(count)s таблиц одни сутки. Войдите, "
                "чтобы сохранить их и загрузить новые."
            )
            % {"count": settings.USERDATA_GUEST_MAX_DATASETS},
            "guest",
        )


def used_bytes(user: Any) -> int:
    """Место, занятое таблицами учётной записи."""
    total = Dataset.objects.filter(owner=user).aggregate(total=Sum("size_bytes"))["total"]
    return int(total or 0)


def create_example(request: HttpRequest) -> Dataset:
    """
    Таблица-пример: набор «Если быть точным» об окружающей среде (CC BY 4.0) как скачан —
    архивом; разбор и сборка — без вопросов, по распознаванию.
    """
    from . import describe, jobs

    with EXAMPLE_PATH.open("rb") as handle:
        uploaded = File(handle, name=EXAMPLE_NAME)
        uploaded.size = EXAMPLE_PATH.stat().st_size
        dataset = create_from_upload(request, uploaded)  # type: ignore[arg-type]
    dataset.title = _("Состояние окружающей среды в регионах России")
    dataset.source_title = _("«Если быть точным», обработка данных Росгидромета и Росстата")
    dataset.source_url = EXAMPLE_URL
    dataset.description = _(
        "Пример таблицы: набор «Если быть точным» в архиве, как он скачивается с сайта "
        "набора. Условия использования — Creative Commons BY 4.0."
    )
    dataset.save(update_fields=["title", "source_title", "source_url", "description"])
    version = dataset.current_version
    assert version is not None
    best = next(table for table in inspection_of(version).tables if table.best)
    choose_table(version, best.key)
    if jobs.ensure_summary(version) not in {"", jobs.READY}:
        raise ingest.IngestError(_("Пример не разобран. Повторите позже."), "example")
    result = describe.recognition_of(version, request.user)
    describe.save_answers(version, result, {}, request.user)
    for stage in (jobs.EXTRACT, jobs.BUILD):
        version.refresh_from_db()
        if jobs.start(version, stage, inline=True) != jobs.READY:
            raise ingest.IngestError(_("Пример не разобран. Повторите позже."), "example")
    return dataset


def _rate_text() -> str:
    return _("Слишком много загрузок подряд — повторите через несколько минут.")


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
    update_size(dataset)
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


def update_size(dataset: Dataset) -> None:
    """Занятое место — все файлы набора на диске."""
    total = sum(path.stat().st_size for path in dataset.directory.rglob("*") if path.is_file())
    Dataset.objects.filter(pk=dataset.pk).update(size_bytes=total)
    dataset.size_bytes = total


def _title(file_name: str) -> str:
    """Название набора по имени файла: без расширения и подчёркиваний."""
    stem = PurePosixPath(file_name).stem.replace("_", " ").strip()
    return stem or _("Таблица")


def export_boards(user: Any) -> list[dict[str, Any]]:
    """Доски учётной записи для выгрузки «Персональных данных»: блоки и закрытые ссылки."""
    from .boards import blocks_of
    from .models import Board

    return [
        {
            "title": board.title,
            "description": board.description,
            "created_at": board.created_at.isoformat(),
            "blocks": blocks_of(board),
            "year": board.year,
            "territories": board.territories,
            "shares": _export_shares(board.shares.all()),
        }
        for board in Board.objects.filter(owner=user).prefetch_related("shares")
    ]


def _export_shares(shares: Any) -> list[dict[str, Any]]:
    """Закрытые ссылки без токенов: срок, отзыв, разрешение скачивать, открытия."""
    return [
        {
            "created_at": share.created_at.isoformat(),
            "expires_at": share.expires_at.isoformat(),
            "revoked_at": share.revoked_at.isoformat() if share.revoked_at else None,
            "downloads": share.downloads,
            "opened": share.opened_count,
        }
        for share in shares
    ]


def export_datasets(user: Any) -> list[dict[str, Any]]:
    """
    Таблицы учётной записи для выгрузки «Персональных данных»: описание, версии, ряды без
    значений и закрытые ссылки без токенов.
    """
    found = []
    for dataset in (
        Dataset.objects.filter(owner=user)
        .select_related("current_version")
        .prefetch_related("shares", "versions")
    ):
        version = dataset.current_version
        found.append(
            {
                "title": dataset.title,
                "description": dataset.description,
                "source": dataset.source_title,
                "source_url": dataset.source_url,
                "uploaded_at": dataset.created_at.isoformat(),
                "file_name": version.file_name if version else "",
                "file_size": version.file_size if version else 0,
                "size_on_disk": dataset.size_bytes,
                "state": dataset.state,
                "versions": [
                    {
                        "number": item.number,
                        "file_name": item.file_name,
                        "uploaded_at": item.created_at.isoformat(),
                        "state": item.state,
                    }
                    for item in sorted(dataset.versions.all(), key=lambda item: item.number)
                ],
                "shares": _export_shares(dataset.shares.all()),
                "series": [
                    {
                        "title": record.title,
                        "unit": record.unit,
                        "kind": record.kind,
                        "period": record.period,
                        "slices": record.slices,
                        "derived": record.derived,
                    }
                    for record in (version.series.order_by("order") if version else [])
                ],
            }
        )
    return found


def prune() -> tuple[int, int]:
    """
    Удалить истёкшие таблицы гостей и осиротевшие файлы: каталоги без набора, прежние
    файлы сборки. Занятый другим процессом файл (Windows) удалится при следующей очистке.
    Возвращает число удалённых таблиц и каталогов.
    """
    from django.utils import timezone

    expired = list(Dataset.objects.filter(owner__isnull=True, expires_at__lt=timezone.now()))
    for dataset in expired:
        discard(dataset)
    root = Path(settings.USERDATA_DIR)
    if not root.exists():
        return len(expired), 0
    known = {str(value) for value in Dataset.objects.values_list("public_id", flat=True)}
    orphans = [path for path in root.iterdir() if path.is_dir() and path.name not in known]
    for path in orphans:
        shutil.rmtree(path, ignore_errors=True)
    current = {
        version.data_path
        for version in DatasetVersion.objects.exclude(data_file="")
        if version.data_path is not None
    }
    for path in root.glob("*/*/data-*.duckdb"):
        if path not in current:
            close_dataset(path)
            try:
                path.unlink()
            except OSError:
                logger.info("userdata: файл %s занят, удалится при следующей очистке", path.name)
    for path in root.glob("*/*/data-*.duckdb.tmp"):
        if path.with_suffix("") not in current:
            shutil.rmtree(path, ignore_errors=True)
    return len(expired), len(orphans)
