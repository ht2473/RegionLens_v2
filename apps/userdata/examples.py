"""
Примеры таблиц разных ведомств (data/examples): каждый проходит обычный мастер загрузки,
вопросы мастера — те же, что у своей таблицы.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.http import HttpRequest
from django.utils.translation import gettext

from .models import Dataset

EXAMPLES_DIR = Path(settings.DATA_DIR) / "examples"
MANIFEST = EXAMPLES_DIR / "examples.json"


@dataclass(frozen=True, slots=True)
class Example:
    """Пример: файл в каталоге примеров, источник и что он показывает."""

    slug: str
    file: str
    name: str
    title: str
    source: str
    source_url: str
    shows: str
    asks: str

    @property
    def path(self) -> Path:
        """Файл примера."""
        return EXAMPLES_DIR / self.file


@cache
def examples() -> tuple[Example, ...]:
    """Примеры по порядку описания; без каталога — пусто."""
    if not MANIFEST.exists():
        return ()
    rows = json.loads(MANIFEST.read_text(encoding="utf-8"))["examples"]
    return tuple(Example(**row) for row in rows)


def example(slug: str) -> Example | None:
    """Пример по имени."""
    return next((item for item in examples() if item.slug == slug), None)


def unfinished(request: HttpRequest, item: Example) -> Dataset | None:
    """Свой не собранный набор из этого примера — последний начатый."""
    from . import access
    from .models import DatasetVersion

    return (
        access.owned(request)
        .filter(source_url=item.source_url, versions__file_name=item.name)
        .exclude(current_version__state=DatasetVersion.State.BUILT)
        .select_related("current_version")
        .order_by("-created_at")
        .first()
    )


def create(request: HttpRequest, item: Example) -> Dataset:
    """Принять файл примера как загруженный: название и источник — из описания."""
    from . import services

    with item.path.open("rb") as handle:
        uploaded = File(handle, name=item.name)
        uploaded.size = item.path.stat().st_size
        dataset = services.create_from_upload(request, uploaded)  # type: ignore[arg-type]
    dataset.title = gettext("Пример: %(title)s") % {"title": item.title}
    dataset.source_title = item.source
    dataset.source_url = item.source_url
    dataset.description = item.shows
    dataset.save(update_fields=["title", "source_title", "source_url", "description"])
    return dataset
