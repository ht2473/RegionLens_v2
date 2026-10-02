"""
Проверка выхода новой версии набора «Если быть точным» по его странице.

Версия читается из адресов файлов для скачивания (``…_v20260313…``): дата «обновления»
на странице меняется и при правке описания. Набор не скачивается — загружает его
администратор в панели.
"""

from __future__ import annotations

import re
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from apps.catalog.versions import current_version_code
from apps.core.models import ServiceBeat

from . import network

# Имя папки набора в хранилище: data_regions_collection_102_v20260313.
VERSION_PATTERN = re.compile(r"data_regions_collection_\d+_(v\d{8})")
CHECK_EVERY = timedelta(days=1)


def versions_on_page(html: str) -> list[str]:
    """Коды версий в адресах файлов страницы, по возрастанию."""
    return sorted(set(VERSION_PATTERN.findall(html)))


def is_due() -> bool:
    """Пора ли проверять: прошлой проверки нет или она старше суток."""
    beat = ServiceBeat.objects.filter(service=ServiceBeat.Service.DATASET).first()
    return beat is None or timezone.now() - beat.seen_at >= CHECK_EVERY


def check() -> ServiceBeat:
    """Сравнить версию на странице набора с загруженной и записать отметку."""
    current = current_version_code()
    try:
        found = versions_on_page(network.page(settings.DATA_SOURCE_URL))
    except (network.FetchError, OSError) as error:
        return ServiceBeat.record(
            ServiceBeat.Service.DATASET, ok=False, current=current, error=str(error)
        )
    if not found:
        return ServiceBeat.record(
            ServiceBeat.Service.DATASET,
            ok=False,
            current=current,
            error="на странице набора не найдено адресов файлов с версией",
        )
    return ServiceBeat.record(
        ServiceBeat.Service.DATASET, ok=True, current=current, found=found[-1]
    )


def newer_version() -> str:
    """Версия на сайте новее загруженной — её код, иначе пустая строка."""
    beat = ServiceBeat.objects.filter(service=ServiceBeat.Service.DATASET).first()
    if beat is None or not beat.ok:
        return ""
    found = str((beat.detail or {}).get("found", ""))
    return found if found > current_version_code() else ""
