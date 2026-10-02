"""
Предмет рабочей поверхности: короткое название показателя, год и главные числа.

Описание ряда — как на странице показателя (``describe_series``): ``series.unit`` врёт у 642 рядов.
"""

from __future__ import annotations

from dataclasses import dataclass

from apps.catalog.indicator import describe_series, theme_of
from apps.catalog.provenance import Origin, origin_of_year
from apps.core.templatetags.formatting import ru_number
from apps.surface.state import SurfaceState
from apps.warehouse.queries import FeaturedSeries, series_statistics


@dataclass(frozen=True, slots=True)
class Subject:
    """Предмет экрана словами и главные числа выбранного года."""

    item: FeaturedSeries
    # Тема основного набора или раздел сборника — над заголовком.
    kicker: str
    # Значение по России и середина регионов, уже с точностью ряда; пусто — нет данных.
    country: str
    median: str
    # Сколько регионов со значением в выбранном году.
    regions: int | None
    # Откуда значения выбранного года, если не из набора.
    origin: Origin | None = None

    @property
    def title(self) -> str:
        """Короткое название показателя на языке запроса."""
        return self.item.short_title

    @property
    def unit(self) -> str:
        """Подпись единицы измерения на языке запроса."""
        return self.item.unit_label


def describe_subject(state: SurfaceState) -> Subject | None:
    """Собрать предмет экрана по выбранному ряду и году; без ряда — ничего."""
    series = state.series
    if series is None:
        return None

    item = describe_series(series)
    theme = theme_of(item)
    kicker = theme.title if theme is not None else series.indicator.section.name

    statistics = series_statistics(series.key, state.year) if state.year is not None else None
    country = median = ""
    regions = None
    if statistics:
        if statistics.get("country_value") is not None:
            country = ru_number(statistics["country_value"], item.precision)
        if statistics.get("median_value") is not None:
            median = ru_number(statistics["median_value"], item.precision)
        regions = statistics.get("observations")

    return Subject(
        item=item,
        kicker=kicker,
        country=country,
        median=median,
        regions=regions,
        origin=origin_of_year(series.key, state.year),
    )
