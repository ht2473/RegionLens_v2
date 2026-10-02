"""
Состояние публикации рядов у источника — из справочника ``series_status.json``
и связей с внешними источниками в складе.

Ряда нет в справочнике — сведений о продолжении нет, и статус не показывается.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING

from django.conf import settings
from django.utils.translation import get_language, gettext

if TYPE_CHECKING:
    from apps.catalog.provenance import SourceLink

CONTINUED = "continued"
DISCONTINUED = "discontinued"


@dataclass(frozen=True, slots=True)
class SeriesStatus:
    """Публикация ряда: продолжается (и где) или прекращена (с какого года и почему)."""

    key: str
    status: str
    since: int | None = None
    source_ru: str = ""
    source_en: str = ""
    reason_ru: str = ""
    reason_en: str = ""

    @property
    def is_discontinued(self) -> bool:
        """Публикация прекращена: новых значений не будет."""
        return self.status == DISCONTINUED

    @property
    def link(self) -> SourceLink | None:
        """Связь ряда с источником в складе: есть — значения уже собираются."""
        if self.is_discontinued:
            return None
        from apps.catalog.provenance import series_link

        return series_link(self.key)

    @property
    def is_collected(self) -> bool:
        """Ряд проекта: все его значения собраны из выпусков внешнего источника."""
        from apps.sources.registry import registry

        return self.key in registry().by_key

    @property
    def is_updated(self) -> bool:
        """Ряд продолжается собранными значениями источника."""
        if self.is_collected:
            return True
        link = self.link
        return link is not None and (link.last_year is not None or link.is_revision)

    @property
    def label(self) -> str:
        """Статус коротко: «обновляется», «публикация прекращена в 2025 году»."""
        if self.is_discontinued:
            return gettext("публикация прекращена в %(year)s году") % {"year": self.since}
        if self.is_updated:
            return gettext("обновляется")
        return gettext("публикация продолжается")

    @property
    def detail(self) -> str:
        """Пояснение: откуда собираются значения, где публикуется или что известно о прекращении."""
        english = get_language() == "en"
        if self.is_discontinued:
            return self.reason_en if english and self.reason_en else self.reason_ru
        source = self.source_en if english and self.source_en else self.source_ru
        if self.is_collected:
            return gettext("Значения собираются из выпусков источника: %(source)s.") % {
                "source": source
            }
        link = self.link
        if link is not None and self.is_updated:
            return link.summary
        return gettext("Источник продолжает публикацию: %(source)s.") % {"source": source}


@lru_cache(maxsize=1)
def _statuses() -> dict[str, SeriesStatus]:
    """Прочитать справочник; кэшируется на время жизни процесса."""
    path = settings.REFERENCE_DIR / "series_status.json"
    payload = json.loads(path.read_bytes().decode("utf-8"))
    sources = payload.get("sources", {})
    found: dict[str, SeriesStatus] = {}
    for item in payload.get("series", []):
        source = sources.get(item.get("source", ""), {})
        found[item["key"]] = SeriesStatus(
            key=item["key"],
            status=item["status"],
            since=item.get("since"),
            source_ru=source.get("title_ru", ""),
            source_en=source.get("title_en", ""),
            reason_ru=item.get("reason_ru", ""),
            reason_en=item.get("reason_en", ""),
        )
    return found


def all_statuses() -> list[SeriesStatus]:
    """Все ряды справочника в его порядке."""
    return list(_statuses().values())


def series_status(key: str) -> SeriesStatus | None:
    """Состояние публикации ряда; ``None`` — сведений нет."""
    return _statuses().get(key)
