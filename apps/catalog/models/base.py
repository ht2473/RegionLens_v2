"""
Абстрактные двуязычные модели справочников.

Перевод хранится полями ``*_ru`` и ``*_en`` в той же строке.
"""

from __future__ import annotations

from django.db import models
from django.utils.translation import get_language


class BilingualNameModel(models.Model):
    """Справочная сущность с названием на двух языках."""

    # В источнике есть формулировки показателей длиннее 500 знаков.
    name_ru = models.CharField("название (рус.)", max_length=1000, db_index=True)
    name_en = models.CharField(
        "название (англ.)",
        max_length=1000,
        blank=True,
        help_text="Если перевод не заполнен, отображается русское название",
    )

    class Meta:
        abstract = True

    def __str__(self) -> str:
        return self.name

    @property
    def name(self) -> str:
        """Название на языке текущего запроса с откатом на русский оригинал."""
        if get_language() == "en" and self.name_en:
            return self.name_en
        return self.name_ru

    @property
    def has_translation(self) -> bool:
        """Признак наличия английского перевода."""
        return bool(self.name_en)


class BilingualDescriptionModel(models.Model):
    """Справочная сущность с развёрнутым описанием на двух языках."""

    description_ru = models.TextField("описание (рус.)", blank=True)
    description_en = models.TextField("описание (англ.)", blank=True)

    class Meta:
        abstract = True

    @property
    def description(self) -> str:
        """Описание на языке текущего запроса с откатом на русский оригинал."""
        if get_language() == "en" and self.description_en:
            return self.description_en
        return self.description_ru
