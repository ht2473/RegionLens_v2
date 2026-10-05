"""
Путь таблицы пользователя в проверках: загрузка, «Что в таблице», «Показатели», сборка.

Таблицы — усечённые настоящие файлы ЕБТ (``tests/fixtures/userdata``). Модуль проверки
подключает каталог таблиц импортом ``userdata_dir`` — приспособление само действует
на все проверки модуля.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from apps.userdata import indicators
from apps.userdata.models import Dataset, DatasetSeries, DatasetVersion

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "userdata"


@pytest.fixture(autouse=True)
def userdata_dir(settings: Any, tmp_path: Path) -> Iterator[Path]:
    """Каталог таблиц проверки."""
    settings.USERDATA_DIR = tmp_path / "userdata"
    yield settings.USERDATA_DIR
    from apps.warehouse.duckdb_client import close_connections

    close_connections()
    shutil.rmtree(settings.USERDATA_DIR, ignore_errors=True)


def upload(client: Client, name: str, content: bytes | None = None) -> Dataset:
    """Загрузить файл и выбрать в нём лучшую таблицу."""
    data = content if content is not None else (FIXTURES / name).read_bytes()
    client.post(
        reverse("userdata:upload"),
        {"action": "upload", "file": SimpleUploadedFile(name, data)},
    )
    dataset = Dataset.objects.latest("created_at")
    version = dataset.current_version
    assert version is not None
    best = next(table for table in version.report["inspection"]["tables"] if table["best"])
    client.post(
        reverse("userdata:file", args=[dataset.public_id]),
        {"action": "choose", "table": best["key"], "encoding": ""},
    )
    return dataset


def describe(client: Client, dataset: Dataset, answers: dict[str, Any] | None = None) -> None:
    """Шаг «Что в таблице» с ответами по умолчанию (или заданными)."""
    response = client.post(
        reverse("userdata:table", args=[dataset.public_id]), {"action": "next", **(answers or {})}
    )
    assert response.status_code == 302


def build(
    client: Client,
    dataset: Dataset,
    *,
    kind: str = "",
    per: tuple[str, ...] | None = None,
    title: str = "",
    extra: dict[str, Any] | None = None,
) -> Any:
    """
    Шаг «Показатели» с описанием по умолчанию (или заданным) и сборка.

    ``extra`` — поля показателей по номеру: ``{"breaks-1": "2019"}``.
    """
    page = client.get(reverse("userdata:series", args=[dataset.public_id]))
    assert page.status_code == 200, page.content[:500]
    version = DatasetVersion.objects.get(pk=dataset.current_version_id)
    data: dict[str, Any] = {
        "title": title or dataset.title,
        "source_title": "Если быть точным",
        "source_url": "",
        "description": "",
    }
    for number, item in enumerate(indicators.indicators_of(version), start=1):
        chosen = kind or item.kind
        data.update(
            {
                f"indicator-{number}": item.name,
                f"title-{number}": item.title,
                f"unit-{number}": item.unit,
                f"kind-{number}": chosen,
                f"polarity-{number}": item.polarity,
                f"per-{number}": list(per if per is not None else item.per),
            }
        )
    data.update(extra or {})
    return client.post(reverse("userdata:series", args=[dataset.public_id]), data)


def built(client: Client, name: str = "environment.csv", **options: Any) -> Dataset:
    """Загрузить, описать и собрать таблицу."""
    dataset = upload(client, name)
    describe(client, dataset)
    response = build(client, dataset, **options)
    assert response.status_code == 302, response.content[:500]
    dataset.refresh_from_db()
    return dataset


def version_of(dataset: Dataset) -> DatasetVersion:
    """Текущая версия таблицы из базы."""
    return DatasetVersion.objects.get(pk=dataset.current_version_id)


def key_of(dataset: Dataset, record: DatasetSeries) -> str:
    """Ключ ряда таблицы."""
    return f"u:{dataset.code}:{record.code}"


def first_record(dataset: Dataset) -> DatasetSeries:
    """Первый ряд собранной таблицы."""
    record = version_of(dataset).series.order_by("order").first()
    assert record is not None
    return record
