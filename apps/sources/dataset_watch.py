"""
Проверка выхода новой версии набора «Если быть точным» по его странице.

Версия читается из адресов файлов для скачивания (``…_v20260313…``): дата «обновления»
на странице меняется и при правке описания. Новая версия скачивается и проверяется
сборкой-кандидатом; рабочий склад заменяет администратор, приняв отчёт о различиях.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import timedelta
from typing import TYPE_CHECKING

from django.conf import settings
from django.utils import timezone

from apps.catalog.versions import current_version_code
from apps.core import alerts
from apps.core.models import ServiceBeat

from . import network

if TYPE_CHECKING:
    from apps.warehouse.models import EtlRun

# Имя папки набора в хранилище: data_regions_collection_102_v20260313.
VERSION_PATTERN = re.compile(r"data_regions_collection_\d+_(v\d{8})")
# Файл набора целиком: …/data_regions_collection_102_v20260313.parquet.
FILE_PATTERN = re.compile(r"https://[^\s\"'<>\\]+/data_regions_collection_\d+_(v\d{8})\.parquet")
CHECK_EVERY = timedelta(days=1)


def versions_on_page(html: str) -> list[str]:
    """Коды версий в адресах файлов страницы, по возрастанию."""
    return sorted(set(VERSION_PATTERN.findall(html)))


def file_urls(html: str) -> dict[str, str]:
    """Адреса файлов parquet на странице по версиям."""
    return {match.group(1): match.group(0) for match in FILE_PATTERN.finditer(html)}


def is_due() -> bool:
    """Пора ли проверять: прошлой проверки нет или она старше суток."""
    beat = ServiceBeat.objects.filter(service=ServiceBeat.Service.DATASET).first()
    return beat is None or timezone.now() - beat.seen_at >= CHECK_EVERY


def check() -> ServiceBeat:
    """Сравнить версию на странице набора с загруженной и записать отметку."""
    current = current_version_code()
    try:
        html = network.page(settings.DATA_SOURCE_URL)
    except (network.FetchError, OSError) as error:
        return ServiceBeat.record(
            ServiceBeat.Service.DATASET, ok=False, current=current, error=str(error)
        )
    found = versions_on_page(html)
    if not found:
        return ServiceBeat.record(
            ServiceBeat.Service.DATASET,
            ok=False,
            current=current,
            error="на странице набора не найдено адресов файлов с версией",
        )
    return ServiceBeat.record(
        ServiceBeat.Service.DATASET,
        ok=True,
        current=current,
        found=found[-1],
        url=file_urls(html).get(found[-1], ""),
    )


def newer_version() -> str:
    """Версия на сайте новее загруженной — её код, иначе пустая строка."""
    beat = ServiceBeat.objects.filter(service=ServiceBeat.Service.DATASET).first()
    if beat is None or not beat.ok:
        return ""
    found = str((beat.detail or {}).get("found", ""))
    return found if found > current_version_code() else ""


def offer_newer(report: Callable[[str], None]) -> EtlRun | None:
    """
    Скачать новую версию набора и проверить её сборкой-кандидатом; один раз на версию.

    Возвращает запуск проверки или ``None``, если проверять нечего или файл не получен.
    """
    from apps.warehouse import builds
    from apps.warehouse.models import EtlRun

    version = newer_version()
    if not version:
        return None
    if EtlRun.objects.filter(
        mode=EtlRun.Mode.CANDIDATE, statistics__offered__version=version
    ).exists():
        return None
    beat = ServiceBeat.objects.get(service=ServiceBeat.Service.DATASET)
    url = str((beat.detail or {}).get("url", ""))
    if not url:
        report(f"набор: на странице нет файла parquet версии {version}")
        return None
    try:
        content, _remote = network.download(url)
    except (network.FetchError, OSError) as error:
        report(f"набор: версия {version} не скачана — {error}")
        return None
    path = builds.store_download(content, url.rsplit("/", 1)[-1])
    problems = builds.check_structure(path)
    if problems:
        path.unlink(missing_ok=True)
        report(f"набор: у версии {version} другой состав атрибутов")
        alerts.notify(
            f"dataset-structure:{version}",
            f"у новой версии набора {version} другой состав атрибутов",
            f"Версия: {version}\nФайл: {url}\n\n"
            + "\n".join(problems)
            + "\n\nСклад не тронут. Разбор набора нужно поправить под новый состав "
            "(apps/warehouse/etl/source.py) и загрузить версию в панели.",
        )
        return None
    report(f"набор: версия {version} скачана, проверка сборкой")
    return builds.start_candidate(
        source_path=path,
        started_by=None,
        offered={"version": version, "url": url},
        inline=True,
        progress=report,
    )
