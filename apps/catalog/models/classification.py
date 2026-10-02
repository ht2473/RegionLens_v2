"""
Классификаторы: версии набора, издания и их выпуски, разделы, единицы измерения, метки.

Выпуски нужны для пересмотров: одно наблюдение публикуется в нескольких выпусках.
"""

from __future__ import annotations

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import get_language

from apps.core.models import OrderedModel, TimeStampedModel

from ..constants import (
    DATASET_FIRST_YEAR,
    DATASET_LAST_YEAR,
    UNSPECIFIED_UNIT_NAME,
    Periodicity,
    UnitKind,
)
from .base import BilingualDescriptionModel, BilingualNameModel


class DatasetVersion(TimeStampedModel):
    """Версия исходного набора данных — одна запись на загрузку."""

    code = models.CharField(
        "код версии",
        max_length=32,
        unique=True,
        help_text="Значение атрибута version_date, например v20260313",
    )
    published_on = models.DateField("дата публикации версии")
    title = models.CharField("наименование набора данных", max_length=255)
    publisher = models.CharField("правообладатель", max_length=255)
    processor = models.CharField("обработка", max_length=255, blank=True)
    source_url = models.URLField("адрес набора данных", blank=True)
    licence = models.CharField("лицензия", max_length=120, blank=True)

    file_name = models.CharField("имя файла", max_length=255, blank=True)
    file_size_bytes = models.BigIntegerField("размер файла, байт", null=True, blank=True)
    file_checksum = models.CharField(
        "контрольная сумма",
        max_length=64,
        blank=True,
        help_text="SHA-256 исходного файла: подтверждает неизменность данных",
    )

    observation_count = models.PositiveIntegerField("наблюдений в наборе", default=0)
    first_year = models.PositiveSmallIntegerField("первый год", default=DATASET_FIRST_YEAR)
    last_year = models.PositiveSmallIntegerField("последний год", default=DATASET_LAST_YEAR)

    is_current = models.BooleanField(
        "текущая версия",
        default=False,
        help_text="Версия, используемая приложением для расчётов",
    )

    class Meta:
        verbose_name = "версия набора данных"
        verbose_name_plural = "версии набора данных"
        ordering = ["-published_on"]
        constraints = [
            models.UniqueConstraint(
                fields=["is_current"],
                condition=models.Q(is_current=True),
                name="dataset_version_single_current",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.code})"

    @property
    def citation(self) -> str:
        """Ссылка для цитирования набора данных в оформлении по ГОСТ Р 7.0.5."""
        return (
            f"«{self.title}» // {self.publisher}; обработка: «{self.processor}», "
            f"{self.published_on.year}. URL: {self.source_url}"
        )


class Publication(BilingualNameModel, BilingualDescriptionModel, OrderedModel, TimeStampedModel):
    """Статистическое издание Росстата, объединяющее выпуски разных лет."""

    slug = models.SlugField("адресный идентификатор", max_length=120, unique=True)
    periodicity = models.CharField(
        "периодичность",
        max_length=16,
        choices=Periodicity.choices,
        default=Periodicity.ANNUAL,
    )
    source_url = models.URLField("страница издания на сайте Росстата", blank=True)

    class Meta:
        verbose_name = "статистическое издание"
        verbose_name_plural = "статистические издания"
        ordering = ["display_order", "name_ru"]


class SourceEdition(TimeStampedModel):
    """Выпуск издания определённого года — источник отдельного наблюдения."""

    publication = models.ForeignKey(
        Publication,
        on_delete=models.PROTECT,
        related_name="editions",
        verbose_name="издание",
    )
    source_name = models.CharField(
        "название в исходных данных",
        max_length=255,
        unique=True,
        db_index=True,
        help_text="Точное значение атрибута source",
    )
    year = models.PositiveSmallIntegerField(
        "год выпуска",
        validators=[MinValueValidator(2000), MaxValueValidator(2100)],
        db_index=True,
    )

    observation_count = models.PositiveIntegerField("наблюдений из выпуска", default=0)
    first_year = models.PositiveSmallIntegerField("первый год данных", null=True, blank=True)
    last_year = models.PositiveSmallIntegerField("последний год данных", null=True, blank=True)

    class Meta:
        verbose_name = "выпуск издания"
        verbose_name_plural = "выпуски изданий"
        ordering = ["publication__name_ru", "-year"]
        indexes = [
            models.Index(fields=["publication", "-year"], name="edition_publication_year_idx"),
        ]

    def __str__(self) -> str:
        return self.source_name


class Section(BilingualNameModel, BilingualDescriptionModel, OrderedModel, TimeStampedModel):
    """Тематический раздел показателей, приведённый из строк источника."""

    slug = models.SlugField("адресный идентификатор", max_length=140, unique=True)
    source_name = models.CharField(
        "название в исходных данных",
        max_length=255,
        unique=True,
        db_index=True,
    )
    icon = models.CharField(
        "значок",
        max_length=40,
        blank=True,
        help_text="Идентификатор значка в наборе иконок интерфейса",
    )
    indicator_count = models.PositiveIntegerField("показателей в разделе", default=0)
    series_count = models.PositiveIntegerField("рядов в разделе", default=0)

    class Meta:
        verbose_name = "раздел"
        verbose_name_plural = "разделы"
        ordering = ["display_order", "name_ru"]


class Unit(BilingualNameModel, TimeStampedModel):
    """Единица измерения, приведённая из свободной строки источника, с категорией."""

    code = models.CharField(
        "код в складе",
        max_length=40,
        blank=True,
        help_text="Ключ единицы в аналитическом складе. Название из источника ключом быть "
        "не может: у единиц, восстановленных из формулировок, оно одно — заглушка ND",
    )
    source_name = models.CharField("название в исходных данных", max_length=255, db_index=True)
    short_name_ru = models.CharField("сокращение (рус.)", max_length=60, blank=True)
    short_name_en = models.CharField("сокращение (англ.)", max_length=60, blank=True)

    kind = models.CharField(
        "категория",
        max_length=16,
        choices=UnitKind.choices,
        default=UnitKind.UNKNOWN,
        db_index=True,
    )
    multiplier = models.DecimalField(
        "множитель приведения",
        max_digits=20,
        decimal_places=6,
        default=1,
        help_text="Коэффициент перевода в базовую единицу категории "
        "(например, миллионы рублей → рубли)",
    )
    is_derived_from_name = models.BooleanField(
        "восстановлена из названия показателя",
        default=False,
        help_text="Признак того, что единица не была указана явно и выведена по правилам",
    )
    country_scale = models.FloatField(
        "коэффициент приведения значения по России",
        default=1.0,
        help_text="Источник может объявлять для страны в целом укрупнённую единицу "
        "(«Миллионов рублей; для значений в целом по России: млрд руб»). "
        "Коэффициент приводит такое значение к единице измерения территорий",
    )
    country_scale_unknown = models.BooleanField(
        "соотношение единиц не определено",
        default=False,
        help_text="Составное объявление единицы, в котором базовая единица не указана: "
        "значение по России в целом использовать для сравнения нельзя",
    )

    class Meta:
        verbose_name = "единица измерения"
        verbose_name_plural = "единицы измерения"
        ordering = ["name_ru"]
        constraints = [
            models.UniqueConstraint(
                fields=["code"], condition=~models.Q(code=""), name="unit_unique_code"
            ),
        ]

    @property
    def is_unspecified(self) -> bool:
        """Источник единицу не указал, и восстановить её не удалось."""
        return self.name_ru == UNSPECIFIED_UNIT_NAME

    @property
    def short_name(self) -> str:
        """
        Сокращённое обозначение на языке запроса, при его отсутствии — полное название.

        У неизвестной единицы подписи нет: «Не указана» у оси читалась бы как величина.
        """
        if self.is_unspecified:
            return ""
        if get_language() == "en":
            return self.short_name_en or self.name_en or self.short_name_ru or self.name_ru
        return self.short_name_ru or self.name_ru
