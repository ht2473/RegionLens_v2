"""
Справочник территорий: страна, федеральные округа, субъекты и соседство субъектов.

Ведётся отдельно: у округов и страны в источнике код ОКТМО ``00000000``.
"""

from __future__ import annotations

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import get_language

from apps.core.models import OrderedModel, TimeStampedModel

from ..constants import TerritoryLevel, TerritoryType, territory_short_code
from .base import BilingualNameModel


class TerritoryQuerySet(models.QuerySet):
    """Типовые выборки территорий."""

    def regions(self) -> TerritoryQuerySet:
        """Только субъекты Российской Федерации, без составных территорий."""
        return self.filter(level=TerritoryLevel.REGION, is_aggregate=False)

    def federal_districts(self) -> TerritoryQuerySet:
        """Только федеральные округа."""
        return self.filter(level=TerritoryLevel.FEDERAL_DISTRICT)

    def country(self) -> TerritoryQuerySet:
        """Российская Федерация в целом."""
        return self.filter(level=TerritoryLevel.COUNTRY)

    def comparable(self) -> TerritoryQuerySet:
        """Территории для рейтингов и карт — без составных, чтобы не учитывать субъекты дважды."""
        return self.filter(level=TerritoryLevel.REGION, is_aggregate=False)

    def with_district(self) -> TerritoryQuerySet:
        """Выборка с предзагруженным федеральным округом."""
        return self.select_related("parent")


class Territory(BilingualNameModel, OrderedModel, TimeStampedModel):
    """Территориальная единица наблюдения: страна, федеральный округ или субъект РФ."""

    code = models.CharField(
        "код территории",
        max_length=16,
        unique=True,
        help_text="Внутренний устойчивый код: ISO 3166-2 для субъектов, RU для страны, "
        "FD-* для федеральных округов",
    )
    slug = models.SlugField(
        "адресный идентификатор",
        max_length=100,
        unique=True,
        help_text="Используется в адресах вида /regions/moskovskaia-oblast/",
    )
    source_name = models.CharField(
        "название в исходных данных",
        max_length=255,
        unique=True,
        db_index=True,
        help_text="Точное значение атрибута object_name; ключ сопоставления при загрузке",
    )

    level = models.CharField(
        "уровень",
        max_length=20,
        choices=TerritoryLevel.choices,
        db_index=True,
    )
    territory_type = models.CharField(
        "тип",
        max_length=20,
        choices=TerritoryType.choices,
        default=TerritoryType.NOT_APPLICABLE,
    )
    parent = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="children",
        verbose_name="федеральный округ",
        help_text="Для субъектов — федеральный округ, для округов — страна",
    )

    is_aggregate = models.BooleanField(
        "составная территория",
        default=False,
        db_index=True,
        help_text="Территория, включающая данные входящих в неё субъектов "
        "(например, «Тюменская область с автономными округами»)",
    )

    # --- Официальные коды -----------------------------------------------------------------
    iso_code = models.CharField(
        "код ISO 3166-2",
        max_length=10,
        blank=True,
        help_text="Международный код субъекта, например RU-MOW",
    )
    okato = models.CharField("код ОКАТО", max_length=11, blank=True, db_index=True)
    oktmo = models.CharField("код ОКТМО", max_length=11, blank=True)

    # --- Географические характеристики -------------------------------------------------------
    capital_ru = models.CharField("административный центр (рус.)", max_length=120, blank=True)
    capital_en = models.CharField("административный центр (англ.)", max_length=120, blank=True)
    area_km2 = models.DecimalField(
        "площадь, км²",
        max_digits=12,
        decimal_places=1,
        null=True,
        blank=True,
        help_text="Необходима для расчёта показателей на единицу площади",
    )
    utc_offset = models.SmallIntegerField(
        "смещение от UTC, часов",
        null=True,
        blank=True,
        validators=[MinValueValidator(2), MaxValueValidator(12)],
    )
    latitude = models.DecimalField(
        "широта центроида",
        max_digits=8,
        decimal_places=5,
        null=True,
        blank=True,
    )
    longitude = models.DecimalField(
        "долгота центроида",
        max_digits=8,
        decimal_places=5,
        null=True,
        blank=True,
    )

    # --- Плиточная картограмма ---------------------------------------------------------------
    tile_x = models.SmallIntegerField("столбец плитки", null=True, blank=True)
    tile_y = models.SmallIntegerField("строка плитки", null=True, blank=True)
    abbreviation = models.CharField(
        "аббревиатура",
        max_length=6,
        blank=True,
        help_text="Краткое обозначение на плиточной карте, например МОС",
    )

    # --- Особенности рядов -------------------------------------------------------------------
    data_since_year = models.PositiveSmallIntegerField(
        "данные доступны с года",
        null=True,
        blank=True,
        help_text="Для Республики Крым и Севастополя ряды начинаются с 2014 года",
    )
    note_ru = models.TextField(
        "примечание (рус.)",
        blank=True,
        help_text="Особенности учёта, влияющие на сопоставимость",
    )
    note_en = models.TextField("примечание (англ.)", blank=True)

    objects = TerritoryQuerySet.as_manager()

    class Meta:
        verbose_name = "территория"
        verbose_name_plural = "территории"
        ordering = ["display_order", "name_ru"]
        indexes = [
            models.Index(fields=["level", "is_aggregate"], name="territory_level_agg_idx"),
            models.Index(fields=["parent", "display_order"], name="territory_parent_order_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(area_km2__gt=0) | models.Q(area_km2__isnull=True),
                name="territory_area_positive",
            ),
        ]

    def __str__(self) -> str:
        return self.name

    @property
    def short_code(self) -> str:
        """Компактное обозначение субъекта: «МСК» по-русски, «MOW» по-английски."""
        return territory_short_code(self.code, self.abbreviation)

    @property
    def note(self) -> str:
        """Примечание на языке запроса с откатом на русский оригинал."""
        if get_language() == "en" and self.note_en:
            return self.note_en
        return self.note_ru

    @property
    def capital(self) -> str:
        """Административный центр на языке текущего запроса."""
        if get_language() == "en" and self.capital_en:
            return self.capital_en
        return self.capital_ru

    @property
    def is_region(self) -> bool:
        """Признак субъекта Российской Федерации."""
        return self.level == TerritoryLevel.REGION

    @property
    def federal_district(self) -> Territory | None:
        """Федеральный округ, в который входит субъект."""
        return self.parent if self.is_region else None


class TerritoryAdjacency(TimeStampedModel):
    """
    Отношение соседства между субъектами, хранимое в обе стороны.

    Морские и мостовые связи — отдельным видом, чтобы их можно было исключить из расчёта.
    """

    class Kind(models.TextChoices):
        LAND = "land", "Сухопутная граница"
        SEA = "sea", "Морская связь"
        BRIDGE = "bridge", "Мостовое сообщение"

    territory = models.ForeignKey(
        Territory,
        on_delete=models.CASCADE,
        related_name="adjacencies",
        verbose_name="территория",
    )
    neighbour = models.ForeignKey(
        Territory,
        on_delete=models.CASCADE,
        related_name="reverse_adjacencies",
        verbose_name="сопредельная территория",
    )
    kind = models.CharField("тип связи", max_length=10, choices=Kind.choices, default=Kind.LAND)

    class Meta:
        verbose_name = "соседство территорий"
        verbose_name_plural = "матрица соседства"
        constraints = [
            models.UniqueConstraint(
                fields=["territory", "neighbour"],
                name="territory_adjacency_unique_pair",
            ),
            models.CheckConstraint(
                condition=~models.Q(territory=models.F("neighbour")),
                name="territory_adjacency_no_self_link",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.territory.name_ru} — {self.neighbour.name_ru}"
