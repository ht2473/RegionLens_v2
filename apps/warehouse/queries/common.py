"""Общее для запросов к складу: уровни территорий, кэш по отпечатку, основной набор."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import lru_cache, wraps
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.utils.translation import get_language

from ..duckdb_client import source_generation

# Время жизни кэша сводных величин: данные меняются только при пересборке склада.
SUMMARY_CACHE_TTL = 3600

# Время жизни выборок, кэшируемых по отпечатку склада: срок нужен лишь для вытеснения.
GENERATION_CACHE_TTL = 24 * 3600

# Уровни территорий в складе.
LEVEL_COUNTRY = "country"
LEVEL_DISTRICT = "federal_district"
LEVEL_REGION = "region"

# Код Российской Федерации в целом.
COUNTRY_CODE = "RU"

# Доля от наибольшего за все годы числа субъектов ряда, с которой год считается полным:
# год по умолчанию и подставляемый ближайший год выбираются только среди полных.
MIN_YEAR_COVERAGE = 0.8

# Способы пересчёта денежного ряда в реальное выражение (поле ``real`` основного набора).
REAL_BY_INDEX = "index"
REAL_BY_VOLUME = "volume"
REAL_BY_PRICES = "prices"


def _localized(ru: str, en: str) -> str:
    """Выбрать строку на языке текущего запроса; пустой перевод заменяется русской."""
    return en if get_language() == "en" and en else ru


def covered_years(counts: Mapping[int, int], share: float = MIN_YEAR_COVERAGE) -> list[int]:
    """Годы, в которых субъектов со значением не меньше ``share`` от наибольшего их числа."""
    if not counts:
        return []
    fullest = max(counts.values())
    return sorted(year for year, count in counts.items() if count and count >= share * fullest)


@dataclass(slots=True, frozen=True)
class FeaturedTheme:
    """Тема основного набора: «Население», «Доходы и расходы» и другие."""

    slug: str
    title_ru: str
    title_en: str
    lead_ru: str = ""
    lead_en: str = ""

    @property
    def title(self) -> str:
        """Название темы на языке текущего запроса."""
        return _localized(self.title_ru, self.title_en)

    @property
    def lead(self) -> str:
        """Пояснение к теме на языке текущего запроса."""
        return _localized(self.lead_ru, self.lead_en)


@dataclass(slots=True, frozen=True)
class RealBasis:
    """
    Чем денежный ряд пересчитывается в реальное выражение.

    ``index`` — индекс реальной величины к прошлому году (реальная зарплата);
    ``volume`` — индекс физического объёма итога ``total`` (ВРП, инвестиции, розница);
    ``prices`` — индекс потребительских цен.
    """

    method: str
    index: str
    total: str = ""


@dataclass(slots=True, frozen=True)
class FeaturedSeries:
    """Показатель основного набора: короткое название, подпись единицы, направленность."""

    key: str
    short_title_ru: str
    short_title_en: str
    unit_label_ru: str
    unit_label_en: str
    # ``positive``, ``negative`` или ``neutral``; задана вручную и главнее эвристики сборки.
    polarity: str
    theme: str
    precision: int
    order: int
    # Плитка на главной странице и в паспорте региона.
    headline: bool = False
    # Величина не приведена к основе: не оценивается и со страной не сравнивается.
    absolute: bool = False
    # Вопрос-вход, открывающий рейтинг по ряду: «Где самые высокие зарплаты?».
    question_ru: str = ""
    question_en: str = ""
    # Роль: ``population`` — вес для мер неравенства, ``default`` — ряд первого открытия
    # рабочей поверхности и инструментов, ``home`` — ряд живой карты витрины.
    role: str = ""
    # Пересчёт в реальное выражение; у неденежных рядов и цены набора — ничего.
    real: RealBasis | None = None

    @property
    def short_title(self) -> str:
        """Краткое название на языке текущего запроса."""
        return _localized(self.short_title_ru, self.short_title_en)

    @property
    def unit_label(self) -> str:
        """Подпись единицы измерения на языке текущего запроса."""
        return _localized(self.unit_label_ru, self.unit_label_en)

    @property
    def question(self) -> str:
        """Вопрос-вход на языке текущего запроса."""
        return _localized(self.question_ru, self.question_en)

    @property
    def is_growth_index(self) -> bool:
        """Величина — темп к прошлому периоду («% к прошлому году»)."""
        return self.unit_label_ru.startswith("% к")

    @property
    def is_percentage(self) -> bool:
        """Величина выражена в процентах: разница с другими — в процентных пунктах."""
        return self.unit_label_ru.startswith("%")

    @property
    def is_money(self) -> bool:
        """Величина в рублях — в ценах своего года."""
        return "руб" in self.unit_label_ru


@dataclass(slots=True, frozen=True)
class FeaturedSet:
    """Основной набор целиком: темы и показатели."""

    themes: tuple[FeaturedTheme, ...]
    series: tuple[FeaturedSeries, ...]
    # Отпечаток файла набора — в адресе перечня рядов, который браузер хранит год.
    digest: str

    def theme(self, slug: str) -> FeaturedTheme | None:
        """Найти тему по адресному имени."""
        return next((theme for theme in self.themes if theme.slug == slug), None)

    def by_key(self) -> dict[str, FeaturedSeries]:
        """Показатели набора по ключу ряда."""
        return {item.key: item for item in self.series}

    def grouped(self) -> list[tuple[FeaturedTheme, list[FeaturedSeries]]]:
        """Показатели по темам в порядке тем; пустые темы пропускаются."""
        groups = [
            (theme, [item for item in self.series if item.theme == theme.slug])
            for theme in self.themes
        ]
        return [(theme, items) for theme, items in groups if items]


def by_generation[**P, R](prefix: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """
    Кэшировать выборку из склада до его пересборки.

    Ключ — отпечаток источника (склада или файла набора), язык запроса и аргументы вызова
    (строки, числа, перечни строк).
    """

    def decorator(function: Callable[P, R]) -> Callable[P, R]:
        @wraps(function)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            call = repr((args, sorted(kwargs.items())))
            digest = hashlib.sha256(call.encode()).hexdigest()[:20]
            key = f"warehouse:{prefix}:{source_generation()}:{get_language()}:{digest}"
            # В кортеже: пустой ответ выборки нельзя путать с отсутствием ключа.
            stored = cache.get(key)
            if stored is not None:
                return stored[0]
            result = function(*args, **kwargs)
            cache.set(key, (result,), GENERATION_CACHE_TTL)
            return result

        return wrapper

    return decorator


@lru_cache(maxsize=1)
def featured_set() -> FeaturedSet:
    """Прочитать основной набор показателей; кэшируется на время жизни процесса."""
    path = settings.REFERENCE_DIR / "featured_series.json"
    raw = path.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    themes = tuple(FeaturedTheme(**theme) for theme in payload.get("themes", []))
    items = sorted(
        (_featured_item(item) for item in payload["series"]), key=lambda item: item.order
    )
    return FeaturedSet(
        themes=themes,
        series=tuple(items),
        digest=hashlib.sha256(raw).hexdigest()[:10],
    )


def _featured_item(item: dict[str, Any]) -> FeaturedSeries:
    """Показатель набора из записи файла; способ пересчёта — отдельной записью."""
    real = item.get("real")
    return FeaturedSeries(**{**item, "real": RealBasis(**real) if real else None})


def featured_series() -> tuple[FeaturedSeries, ...]:
    """Показатели основного набора в порядке поля ``order``."""
    return featured_set().series
