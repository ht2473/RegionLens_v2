"""
Сборка тестового склада — один раз на прогон.

Синхронизация справочников выполняется в транзакции теста, который её запросил,
и откатывается: остальные проверки опираются на пустую базу.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

import duckdb
from django.conf import settings
from django.db import transaction

from .synthetic import VERSION_CODE, SyntheticDataset


@contextmanager
def accept_small_source() -> Iterator[None]:
    """Снять пороги объёма источника: синтетический набор в сотни раз меньше настоящего."""
    from apps.warehouse.etl import source

    with patch.multiple(source, MIN_OBSERVATIONS=0, MIN_TERRITORIES=0, MIN_INDICATORS=0):
        yield


def build_warehouse(dataset: SyntheticDataset, target_path: Path) -> None:
    """Собрать склад из синтетического набора; записи конвейера в PostgreSQL откатываются."""
    from apps.warehouse.etl.pipeline import Pipeline
    from apps.warehouse.models import EtlRun

    with transaction.atomic():
        # Без запуска конвейер пропускает проверки качества.
        run = EtlRun.objects.create(mode=EtlRun.Mode.FULL, source_path=str(dataset.path))
        pipeline = Pipeline(
            source_path=dataset.path,
            target_path=target_path,
            run=run,
            progress=lambda message: None,
        )
        with accept_small_source():
            pipeline.build()
        transaction.set_rollback(True)


def sync_catalog(target_path: Path) -> dict[str, int]:
    """
    Перенести измерения склада в справочники PostgreSQL текущего теста.

    :param target_path: путь файла склада.
    :return: статистика синхронизации.
    """
    from apps.warehouse.etl import catalog_sync

    connection = duckdb.connect(str(target_path), read_only=True)
    try:
        statistics = catalog_sync.sync_all(connection)
    finally:
        connection.close()

    _register_dataset_version(target_path)
    return statistics


def _register_dataset_version(target_path: Path) -> Any:
    """Зафиксировать версию набора данных: без неё страница «О данных» пуста."""
    from datetime import date

    from apps.catalog.models import DatasetVersion
    from tests.support import synthetic

    version, _ = DatasetVersion.objects.update_or_create(
        code=VERSION_CODE,
        defaults={
            "published_on": date(2026, 3, 13),
            "title": settings.DATA_SOURCE_TITLE,
            "publisher": settings.DATA_SOURCE_ORIGIN,
            "processor": settings.DATA_SOURCE_PROCESSOR,
            "source_url": settings.DATA_SOURCE_URL,
            "licence": settings.DATA_SOURCE_LICENSE,
            "file_name": target_path.name,
            "file_size_bytes": target_path.stat().st_size,
            "first_year": synthetic.FIRST_YEAR,
            "last_year": synthetic.LAST_YEAR,
            "is_current": True,
        },
    )
    return version
