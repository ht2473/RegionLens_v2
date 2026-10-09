"""
«Новое по сохранённому»: выпуски источников и новые версии набора, принёсшие значения
по отмеченным показателям и регионам.

Выпуск попадает в перечень, когда он уже в складе. Новым считается выпуск, полученный
после прошлого визита; показываются и более ранние за ``WINDOW``, без отметки. Первая
загруженная версия набора новостью не считается: новость — версия, сменившая прежнюю.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from django.utils.text import capfirst
from django.utils.translation import gettext

from apps.accounts.models import User
from apps.catalog.models import DatasetVersion, Series, Territory
from apps.catalog.provenance import source_short
from apps.sources.models import Release
from apps.warehouse.duckdb_client import WarehouseNotBuiltError
from apps.warehouse.queries import featured_set
from apps.warehouse.queries.sources import (
    dataset_last_years,
    dataset_territories,
    edition_contents,
    edition_territories,
    release_editions,
)

from .selectors import favorites

# За какой срок выпуски показываются в перечне.
WINDOW = timedelta(days=90)
# Сколько показателей называется у выпуска, остальные — числом.
LISTED_SERIES = 6


@dataclass(slots=True)
class SeriesNews:
    """Отмеченный показатель, по которому пришли значения."""

    title: str
    href: str
    period: str


@dataclass(slots=True)
class RegionNews:
    """Отмеченный регион и число показателей основного набора, пришедших по нему."""

    name: str
    href: str
    count: int


@dataclass(slots=True)
class SourceNews:
    """Выпуски одного источника за срок: последний, сколько их и что они принесли."""

    source: str
    source_title: str
    release: str
    received: datetime
    releases: int
    is_new: bool
    series: list[SeriesNews] = field(default_factory=list)
    more: int = 0
    regions: list[RegionNews] = field(default_factory=list)
    # Подпись к перечню регионов; пустая — «с новыми значениями».
    regions_label: str = ""


@dataclass(slots=True)
class NewsRow:
    """Строка «Нового»: регион или показатель, что пришло, источник и когда."""

    subject: str
    href: str
    what: str
    source: str
    source_title: str
    release: str
    received: datetime
    is_new: bool


def news_rows(items: list[SourceNews]) -> list[NewsRow]:
    """«Новое» по строке на регион и источник и на показатель и источник; новое — первым."""
    rows: list[NewsRow] = []
    for item in items:
        common: dict[str, Any] = {
            "source": item.source,
            "source_title": item.source_title,
            "release": item.release,
            "received": item.received,
            "is_new": item.is_new,
        }
        rows.extend(
            NewsRow(
                subject=region.name,
                href=region.href,
                what=gettext("показателей: %(count)s") % {"count": region.count},
                **common,
            )
            for region in item.regions
        )
        rows.extend(
            NewsRow(subject=entry.title, href=entry.href, what=entry.period, **common)
            for entry in item.series
        )
    rows.sort(key=lambda row: (not row.is_new, -row.received.timestamp()))
    return rows


def edition_code(release: Release) -> str:
    """Код издания выпуска в складе (как ``ReleaseInfo.edition_code``)."""
    return f"rel_{release.source.code}_{release.code}_{release.sha256[:8]}"


def saved_series(user: User) -> dict[str, Series]:
    """Ряды отмеченных показателей: сам ряд или все ряды показателя."""
    marks = list(favorites(user))
    series = {item.series.key: item.series for item in marks if item.series is not None}
    indicators = [item.indicator_id for item in marks if item.indicator_id]
    for row in Series.objects.filter(indicator_id__in=indicators).select_related("indicator"):
        series.setdefault(row.key, row)
    return series


def saved_regions(user: User) -> list[Territory]:
    """Отмеченные регионы и «мой регион»."""
    found = [item.territory for item in favorites(user) if item.territory is not None]
    if user.region is not None and user.region not in found:
        found.insert(0, user.region)
    return found


def period_phrase(year: int, month: int | None) -> str:
    """«июль 2026» или «2025 год»."""
    from apps.catalog.monthly import MONTHS

    if month:
        return f"{MONTHS[month - 1]} {year}"
    return gettext("%(year)s год") % {"year": year}


def saved_updates(user: User, since: datetime) -> list[SourceNews]:
    """Источники, выпуски которых за срок принесли значения по сохранённому; новые — первыми."""
    series = saved_series(user)
    regions = saved_regions(user)
    if not series and not regions:
        return []
    try:
        in_warehouse = {row["edition_code"] for row in release_editions()}
    except WarehouseNotBuiltError:
        return []

    releases = [
        release
        for release in Release.objects.filter(
            status=Release.Status.PARSED, fetched_at__gte=timezone.now() - WINDOW
        )
        .select_related("source")
        .order_by("-fetched_at")
        if edition_code(release) in in_warehouse
    ]
    by_source: dict[str, list[Release]] = {}
    for release in releases:
        by_source.setdefault(release.source.code, []).append(release)

    featured_keys = [item.key for item in featured_set().series]
    news = []
    for group in by_source.values():
        item = _source_news(group, series, regions, featured_keys, since)
        if item.series or item.regions:
            news.append(item)
    dataset = _dataset_news(series, regions, featured_keys, since)
    if dataset is not None:
        news.append(dataset)
    news.sort(key=lambda item: (not item.is_new, -item.received.timestamp()))
    return news


def _dataset_news(
    series: dict[str, Series],
    regions: list[Territory],
    featured_keys: list[str],
    since: datetime,
) -> SourceNews | None:
    """Новая версия набора за срок: последние годы отмеченных рядов и регионы."""
    first = DatasetVersion.objects.order_by("created_at").first()
    versions = [
        version
        for version in DatasetVersion.objects.filter(
            created_at__gte=timezone.now() - WINDOW
        ).order_by("-created_at")
        if first is not None and version.pk != first.pk
    ]
    if not versions:
        return None
    fresh = [version for version in versions if version.created_at > since]
    shown = fresh or versions
    latest = max(shown, key=lambda version: version.published_on)

    last_years = dataset_last_years(sorted(series))
    featured = featured_set().by_key()
    titles = {
        key: featured[key].short_title if key in featured else series[key].full_title
        for key in last_years
    }
    listed = [
        SeriesNews(
            title=titles[key], href=_series_href(series[key]), period=period_phrase(year, None)
        )
        for key, year in sorted(last_years.items(), key=lambda pair: titles[pair[0]])
    ]
    counts = dataset_territories(featured_keys, [region.code for region in regions])
    item = SourceNews(
        source=capfirst(gettext("набор данных")),
        source_title=settings.DATA_SOURCE_TITLE,
        release=gettext("версия %(code)s от %(date)s")
        % {"code": latest.code, "date": latest.published_on.strftime("%d.%m.%Y")},
        received=max(version.created_at for version in shown),
        releases=len(shown),
        is_new=bool(fresh),
        series=listed[:LISTED_SERIES],
        more=max(len(listed) - LISTED_SERIES, 0),
        regions=[
            RegionNews(
                name=region.name,
                href=reverse("catalog:territory-detail", kwargs={"slug": region.slug}),
                count=counts[region.code],
            )
            for region in regions
            if counts.get(region.code)
        ],
        regions_label=gettext("Показателей основного набора со значениями за последний год:"),
    )
    return item if item.series or item.regions else None


def _source_news(
    group: list[Release],
    series: dict[str, Series],
    regions: list[Territory],
    featured_keys: list[str],
    since: datetime,
) -> SourceNews:
    """Сводка выпусков одного источника."""
    fresh = [release for release in group if release.fetched_at > since]
    shown = fresh or group
    latest = max(
        shown,
        key=lambda release: (
            release.reference_year,
            release.published_on or release.fetched_at.date(),
        ),
    )
    codes = sorted(edition_code(release) for release in shown)

    periods: dict[str, tuple[int, int]] = {}
    for row in edition_contents(codes, sorted(series)):
        period = (int(row["last_year"]), int(row["last_month"] or 0))
        periods[row["series_key"]] = max(periods.get(row["series_key"], period), period)

    featured = featured_set().by_key()
    titles = {
        key: featured[key].short_title if key in featured else series[key].full_title
        for key in periods
    }
    listed = [
        SeriesNews(
            title=titles[key],
            href=_series_href(series[key]),
            period=period_phrase(year, month or None),
        )
        for key, (year, month) in sorted(periods.items(), key=lambda pair: titles[pair[0]])
    ]
    counts = edition_territories(codes, featured_keys, [region.code for region in regions])
    return SourceNews(
        source=capfirst(source_short(latest.source.code)),
        source_title=latest.source.title,
        release=latest.display_title,
        received=max(release.fetched_at for release in shown),
        releases=len(shown),
        is_new=bool(fresh),
        series=listed[:LISTED_SERIES],
        more=max(len(listed) - LISTED_SERIES, 0),
        regions=[
            RegionNews(
                name=region.name,
                href=reverse("catalog:territory-detail", kwargs={"slug": region.slug}),
                count=counts[region.code],
            )
            for region in regions
            if counts.get(region.code)
        ],
    )


def _series_href(series: Series) -> str:
    """Страница показателя с разрезом ряда."""
    base = reverse("catalog:series-detail", kwargs={"slug": series.indicator.slug})
    return f"{base}?{urlencode({'series': series.key})}"
