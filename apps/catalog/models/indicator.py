"""
Показатели и ряды наблюдений.

Единица анализа — ряд, пара «показатель + разрез»: сравнивать регионы можно только в нём.
"""

from __future__ import annotations

from django.db import models

from apps.core.models import TimeStampedModel

from ..constants import Polarity, is_unnormalised_title
from .base import BilingualDescriptionModel, BilingualNameModel
from .classification import Section, SourceEdition, Unit


class IndicatorQuerySet(models.QuerySet):
    """Типовые выборки показателей."""

    def featured(self) -> IndicatorQuerySet:
        """Показатели, вынесенные на витрину и в паспорт региона."""
        return self.filter(is_featured=True)


class Indicator(BilingualNameModel, BilingualDescriptionModel, TimeStampedModel):
    """Статистический показатель из сборников Росстата."""

    code = models.CharField(
        "код показателя",
        max_length=32,
        unique=True,
        db_index=True,
        help_text="Значение атрибута indicator_code, например Y477100001",
    )
    slug = models.SlugField("адресный идентификатор", max_length=140, unique=True)

    section = models.ForeignKey(
        Section,
        on_delete=models.PROTECT,
        related_name="indicators",
        verbose_name="раздел",
    )
    editions = models.ManyToManyField(
        SourceEdition,
        blank=True,
        related_name="indicators",
        verbose_name="выпуски-источники",
        help_text="Выпуски изданий, в которых встречаются наблюдения по показателю",
    )

    polarity = models.CharField(
        "направленность",
        max_length=10,
        choices=Polarity.choices,
        default=Polarity.UNKNOWN,
        db_index=True,
        help_text="Определяет направление шкалы при построении интегральных индексов",
    )

    is_featured = models.BooleanField(
        "ключевой показатель",
        default=False,
        db_index=True,
        help_text="Выводится на главной странице и в паспорте региона",
    )

    # --- Сводные характеристики (пересчитываются при сборке склада) ------------------------
    series_count = models.PositiveSmallIntegerField("число рядов", default=0)
    observation_count = models.PositiveIntegerField("число наблюдений", default=0)
    first_year = models.PositiveSmallIntegerField("первый год", null=True, blank=True)
    last_year = models.PositiveSmallIntegerField("последний год", null=True, blank=True)

    objects = IndicatorQuerySet.as_manager()

    class Meta:
        verbose_name = "показатель"
        verbose_name_plural = "показатели"
        ordering = ["name_ru"]
        indexes = [
            models.Index(fields=["section", "name_ru"], name="indicator_section_name_idx"),
            models.Index(fields=["is_featured"], name="indicator_featured_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.code} — {self.name}"

    @property
    def has_multiple_series(self) -> bool:
        """Признак того, что показатель разбит на несколько разрезов."""
        return self.series_count > 1


class SeriesQuerySet(models.QuerySet):
    """Типовые выборки рядов."""

    def analysis_ready(self) -> SeriesQuerySet:
        """Ряды, пригодные для сравнительного анализа по порогам покрытия при сборке склада."""
        return self.filter(is_analysis_ready=True)

    def with_related(self) -> SeriesQuerySet:
        """Выборка с предзагруженными показателем, разделом и единицей измерения."""
        return self.select_related("indicator", "indicator__section", "unit")


class Series(BilingualNameModel, TimeStampedModel):
    """
    Ряд наблюдений — пара «показатель + разрез».

    Поля покрытия заполняет сборка склада; по ним ряд предлагается инструментам анализа.
    """

    indicator = models.ForeignKey(
        Indicator,
        on_delete=models.CASCADE,
        related_name="series",
        verbose_name="показатель",
    )
    unit = models.ForeignKey(
        Unit,
        on_delete=models.PROTECT,
        related_name="series",
        verbose_name="единица измерения",
        null=True,
        blank=True,
    )

    key = models.CharField(
        "ключ ряда",
        max_length=64,
        unique=True,
        db_index=True,
        help_text="Устойчивый идентификатор вида <код показателя>:<хеш разреза>; "
        "используется для соединения с аналитическим складом",
    )
    slug = models.SlugField("адресный идентификатор", max_length=160, db_index=True)

    subsection_source = models.CharField(
        "разрез в исходных данных",
        max_length=500,
        blank=True,
        help_text="Точное значение атрибута subsection; пусто, если разрез отсутствует",
    )
    has_subsection = models.BooleanField("показатель разбит на разрезы", default=False)

    polarity = models.CharField(
        "направленность",
        max_length=10,
        choices=Polarity.choices,
        default=Polarity.UNKNOWN,
        help_text="Уточняет направленность показателя для конкретного разреза",
    )

    # --- Покрытие и качество ------------------------------------------------------------------
    observation_count = models.PositiveIntegerField("всего наблюдений", default=0)
    value_count = models.PositiveIntegerField("наблюдений со значением", default=0)
    no_data_count = models.PositiveIntegerField("отметок «нет данных»", default=0)
    hidden_count = models.PositiveIntegerField("скрытых значений", default=0)

    region_count = models.PositiveSmallIntegerField("регионов с данными", default=0)
    year_count = models.PositiveSmallIntegerField("лет с данными", default=0)
    first_year = models.PositiveSmallIntegerField("первый год", null=True, blank=True)
    last_year = models.PositiveSmallIntegerField("последний год", null=True, blank=True)
    longest_gap_free_span = models.PositiveSmallIntegerField(
        "длина непрерывного отрезка, лет",
        default=0,
        help_text="Максимальное число подряд идущих лет без пропусков в среднем по регионам",
    )

    region_coverage = models.FloatField(
        "покрытие регионов",
        default=0.0,
        help_text="Доля субъектов РФ, по которым есть хотя бы одно значение",
    )
    completeness = models.FloatField(
        "полнота",
        default=0.0,
        help_text="Доля заполненных ячеек в матрице «регион × год»",
    )

    is_analysis_ready = models.BooleanField(
        "пригоден для сравнительного анализа",
        default=False,
        db_index=True,
    )

    # --- Сопоставимость -------------------------------------------------------------------------
    break_count = models.PositiveSmallIntegerField("разрывов сопоставимости", default=0)
    revision_count = models.PositiveIntegerField(
        "пересмотренных наблюдений",
        default=0,
        help_text="Число наблюдений, значение которых различается между выпусками изданий",
    )

    objects = SeriesQuerySet.as_manager()

    class Meta:
        verbose_name = "ряд наблюдений"
        verbose_name_plural = "ряды наблюдений"
        ordering = ["indicator__name_ru", "name_ru"]
        indexes = [
            models.Index(fields=["indicator", "name_ru"], name="series_indicator_name_idx"),
            models.Index(
                fields=["is_analysis_ready", "-region_coverage"],
                name="series_ready_coverage_idx",
            ),
        ]

    def __str__(self) -> str:
        return self.full_title

    @property
    def full_title(self) -> str:
        """Полное наименование ряда для заголовков графиков и выгрузок."""
        if self.has_subsection and self.name:
            return f"{self.indicator.name} — {self.name}"
        return self.indicator.name

    @property
    def is_unnormalised(self) -> bool:
        """Величина не приведена к численности, площади или иной основе."""
        parts = [self.indicator.name, self.name or ""]
        if self.unit:
            parts.append(self.unit.short_name)
        return is_unnormalised_title(" ".join(parts), self.unit.kind if self.unit else "unknown")
