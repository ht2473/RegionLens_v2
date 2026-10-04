"""
Ряды наборов для представлений: ``UserSeries`` со свойствами ``Series``, которые читают
холст, документы и «Сохранённое», и перечни рядов для выбора.

Доступ проверяет слой рядов (``apps.warehouse.routing``): чужой ряд здесь не находится.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.db.models import F
from django.urls import reverse
from django.utils import translation
from django.utils.translation import gettext

from apps.warehouse import routing
from apps.warehouse.queries import FeaturedSeries

from . import naming
from .models import Dataset, DatasetSeries, DatasetVersion

ABSOLUTE_KIND = "absolute"
RELATIVE_KIND = "rate"


@dataclass(frozen=True, slots=True)
class _Section:
    name: str


@dataclass(frozen=True, slots=True)
class _Indicator:
    name: str
    name_ru: str
    name_en: str
    section: _Section
    slug: str = ""


@dataclass(frozen=True, slots=True)
class _Unit:
    short_name: str
    name: str
    kind: str


class UserSeries:
    """Ряд набора пользователя в роли ``Series``: ключ, названия, единица, направленность."""

    is_user = True
    pk = None

    def __init__(self, record: DatasetSeries, dataset: Dataset) -> None:
        self.record = record
        self.dataset = dataset
        self.key = f"{routing.USER_PREFIX}{dataset.code}:{record.code}"
        self.polarity = record.polarity
        self.has_subsection = bool(naming.subsection(record.slices, record.period))
        self.indicator = _Indicator(
            name=record.title,
            name_ru=record.title,
            name_en=record.title,
            section=_Section(name=gettext("Свои данные · %(title)s") % {"title": dataset.title}),
        )
        self.unit = _Unit(
            short_name=record.unit,
            name=record.unit,
            kind=ABSOLUTE_KIND if self.is_sum else RELATIVE_KIND,
        )

    def __eq__(self, other: object) -> bool:
        return isinstance(other, UserSeries) and other.key == self.key

    def __hash__(self) -> int:
        return hash(self.key)

    def __str__(self) -> str:
        return self.full_title

    @property
    def is_sum(self) -> bool:
        return self.record.kind == DatasetSeries.Kind.SUM

    @property
    def name(self) -> str:
        """Уточнение ряда: разрезы и период года на языке страницы."""
        return naming.subsection(self.record.slices, self.record.period)

    @property
    def name_ru(self) -> str:
        with translation.override("ru"):
            return self.name

    @property
    def full_title(self) -> str:
        """Полное название ряда на языке страницы."""
        return naming.full_title(self.record.title, self.record.slices, self.record.period)

    @property
    def full_title_ru(self) -> str:
        with translation.override("ru"):
            return self.full_title

    @property
    def is_unnormalised(self) -> bool:
        """Сумма по региону: на карте крупные регионы выделяются размером."""
        return self.is_sum

    @property
    def detail_url(self) -> str:
        """Страница набора с этим рядом."""
        url = reverse("userdata:dataset", kwargs={"public_id": self.dataset.public_id})
        return f"{url}#series-{self.record.code}"

    def publisher(self) -> str:
        """Чьи данные: источник, названный при загрузке, или «таблица пользователя»."""
        return self.dataset.source_title or gettext("таблица пользователя")

    def source_line(self) -> str:
        """Реквизит источника: «Данные: …; загружены пользователем; обработка RegionLens»."""
        return source_line(self.dataset)

    def describe(self) -> FeaturedSeries:
        """Описание ряда для выводов, как у рядов склада вне основного набора."""
        with translation.override("ru"):
            title_ru = self.full_title
        with translation.override("en"):
            title_en = self.full_title
        return FeaturedSeries(
            key=self.key,
            short_title_ru=title_ru,
            short_title_en=title_en,
            unit_label_ru=self.record.unit,
            unit_label_en=self.record.unit,
            polarity=self.record.polarity,
            theme="",
            precision=self.record.precision,
            order=self.record.order,
            absolute=self.is_sum,
        )


def user_series(key: str) -> UserSeries | None:
    """Ряд набора по ключу, если набор доступен тому, кто спрашивает."""
    if not routing.is_user_key(key) or routing.source_of(key) is None:
        return None
    code = routing.dataset_code(key)
    series_code = key.rsplit(":", 1)[-1]
    record = (
        DatasetSeries.objects.select_related("version__dataset")
        .filter(version__dataset__code=code, code=series_code)
        .filter(version__dataset__current_version=F("version"))
        .first()
    )
    if record is None:
        return None
    return UserSeries(record, record.version.dataset)


def series_of(version: DatasetVersion) -> list[UserSeries]:
    """Ряды собранной версии в порядке набора."""
    dataset = version.dataset
    return [UserSeries(record, dataset) for record in version.series.order_by("order")]


def option_groups(datasets: list[Dataset]) -> list[dict[str, Any]]:
    """Группы перечня выбора ряда: по набору на группу, ряды — в порядке набора."""
    groups = []
    for dataset in datasets:
        version = dataset.current_version
        if version is None or version.state != DatasetVersion.State.BUILT:
            continue
        items = [
            {
                "key": item.key,
                "title": item.full_title,
                "subsection": "",
                "unnormalised": item.is_unnormalised,
                "search": dataset.title,
            }
            for item in series_of(version)
        ]
        if items:
            groups.append(
                {
                    "title": gettext("Мои таблицы: %(title)s") % {"title": dataset.title},
                    "items": items,
                }
            )
    return groups


def source_line(dataset: Dataset) -> str:
    """Реквизит источника набора для документов и ссылок на расчёт."""
    source = dataset.source_title or gettext("таблица пользователя")
    if dataset.source_url:
        source = f"{source} ({dataset.source_url})"
    return gettext("Данные: %(source)s; загружены пользователем; обработка RegionLens.") % {
        "source": source
    }


def detail_url(series: Any) -> str:
    """Страница о ряде: показателя склада или набора пользователя."""
    if getattr(series, "is_user", False):
        return str(series.detail_url)
    url = reverse("catalog:series-detail", kwargs={"slug": series.indicator.slug})
    return f"{url}?series={series.key}"
