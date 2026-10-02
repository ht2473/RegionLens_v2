"""
Методические примечания источника и разрывы сопоставимости рядов.

Примечания разбираются при сборке: годы и вид изменения привязываются к точкам ряда.
"""

from __future__ import annotations

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import get_language

from apps.core.models import TimeStampedModel

from ..constants import DATASET_FIRST_YEAR, BreakKind
from .indicator import Series
from .territory import Territory

# Длина краткого представления примечания в строковом виде.
NOTE_PREVIEW_LENGTH = 80

# Допустимые годы записи.
YEAR_VALIDATORS = [
    MinValueValidator(DATASET_FIRST_YEAR - 20),  # комментарии ссылаются и на более ранние годы
    MaxValueValidator(2100),
]


class MethodologyNote(TimeStampedModel):
    """Методическое примечание источника; хранится один раз и связано со всеми своими рядами."""

    checksum = models.CharField(
        "контрольная сумма текста",
        max_length=40,
        unique=True,
        db_index=True,
        help_text="SHA-1 нормализованного текста: ключ дедупликации примечаний",
    )
    text_ru = models.TextField("текст примечания (рус.)")
    text_en = models.TextField("текст примечания (англ.)", blank=True)

    kind = models.CharField(
        "тип изменения",
        max_length=20,
        choices=BreakKind.choices,
        default=BreakKind.METHODOLOGY,
        db_index=True,
        help_text="Определяется автоматически по языковым признакам текста",
    )
    mentioned_years = models.JSONField(
        "упомянутые годы",
        default=list,
        blank=True,
        help_text="Годы, извлечённые из текста примечания",
    )
    affects_comparability = models.BooleanField(
        "влияет на сопоставимость",
        default=False,
        db_index=True,
        help_text="Признак того, что примечание описывает разрыв ряда, "
        "а не общее пояснение к методике расчёта",
    )

    occurrence_count = models.PositiveIntegerField(
        "число наблюдений с примечанием",
        default=0,
        help_text="Позволяет оценить значимость примечания",
    )

    series = models.ManyToManyField(
        Series,
        blank=True,
        related_name="methodology_notes",
        verbose_name="ряды",
    )

    class Meta:
        verbose_name = "методическое примечание"
        verbose_name_plural = "методические примечания"
        ordering = ["-occurrence_count"]
        indexes = [
            models.Index(
                fields=["affects_comparability", "-occurrence_count"],
                name="note_comparability_idx",
            ),
        ]

    def __str__(self) -> str:
        preview = self.text_ru[:NOTE_PREVIEW_LENGTH]
        return f"{preview}…" if len(self.text_ru) > NOTE_PREVIEW_LENGTH else preview

    @property
    def text(self) -> str:
        """Текст примечания на языке текущего запроса."""
        if get_language() == "en" and self.text_en:
            return self.text_en
        return self.text_ru

    @property
    def text_lang(self) -> str:
        """Язык текста, который вернёт ``text``: без перевода — русский и на английской странице."""
        return "en" if get_language() == "en" and self.text_en else "ru"


class SeriesBreak(TimeStampedModel):
    """Разрыв сопоставимости ряда в конкретном году."""

    series = models.ForeignKey(
        Series,
        on_delete=models.CASCADE,
        related_name="breaks",
        verbose_name="ряд",
    )
    territory = models.ForeignKey(
        Territory,
        on_delete=models.CASCADE,
        related_name="series_breaks",
        null=True,
        blank=True,
        verbose_name="территория",
        help_text="Заполняется, если разрыв затрагивает только отдельную территорию "
        "(например, изменение состава федерального округа)",
    )
    note = models.ForeignKey(
        MethodologyNote,
        on_delete=models.SET_NULL,
        related_name="breaks",
        null=True,
        blank=True,
        verbose_name="источник сведений",
    )

    year = models.PositiveSmallIntegerField(
        "год разрыва",
        validators=YEAR_VALIDATORS,
        db_index=True,
        help_text="Первый год, начиная с которого данные несопоставимы с предыдущими",
    )
    kind = models.CharField("тип", max_length=20, choices=BreakKind.choices, db_index=True)

    description_ru = models.TextField("пояснение (рус.)", blank=True)
    description_en = models.TextField("пояснение (англ.)", blank=True)

    class Meta:
        verbose_name = "разрыв сопоставимости"
        verbose_name_plural = "разрывы сопоставимости"
        ordering = ["series_id", "year"]
        constraints = [
            models.UniqueConstraint(
                fields=["series", "territory", "year", "kind"],
                name="series_break_unique",
                nulls_distinct=False,
            ),
        ]
        indexes = [
            models.Index(fields=["series", "year"], name="series_break_series_year_idx"),
        ]

    def __str__(self) -> str:
        scope = self.territory.name_ru if self.territory else "все территории"
        return f"{self.series_id}: разрыв {self.year} ({scope})"

    @property
    def description(self) -> str:
        """Пояснение на языке текущего запроса; у отметки из примечания — его перевод."""
        english = self._english_description()
        if english:
            return english
        if self.description_ru:
            return self.description_ru
        return self.note.text if self.note else ""

    @property
    def description_lang(self) -> str:
        """Язык пояснения, которое вернёт ``description``."""
        return "en" if self._english_description() else "ru"

    def _english_description(self) -> str:
        """Английское пояснение на английской странице: своё или перевод примечания."""
        if get_language() != "en":
            return ""
        if self.description_en:
            return self.description_en
        return self.note.text_en if self.note is not None else ""
