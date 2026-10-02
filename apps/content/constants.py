"""Разделы методики, категории терминов и алфавиты указателя глоссария."""

from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _


class MethodologyBlock(models.TextChoices):
    """Блок методики; порядок объявления — порядок изложения на странице."""

    DATA = "data", _("Подготовка данных")
    READING = "reading", _("Карта, места и оценки")
    NORMALIZATION = "normalization", _("Нормализация")
    WEIGHTS = "weights", _("Взвешивание")
    AGGREGATION = "aggregation", _("Агрегация")
    STATISTICS = "statistics", _("Статистические методы")
    INEQUALITY = "inequality", _("Неравенство и конвергенция")
    SPATIAL = "spatial", _("Пространственный анализ")
    QUALITY = "quality", _("Качество данных и расчётов")


class GlossaryCategory(models.TextChoices):
    """Тематическая принадлежность термина глоссария."""

    STATISTICS = "statistics", _("Статистика")
    ECONOMY = "economy", _("Региональная экономика")
    METHOD = "method", _("Методы анализа")
    DATA = "data", _("Работа с данными")
    SYSTEM = "system", _("Обозначения на сайте")


# Длина краткого представления материала в перечнях панели управления.
TITLE_PREVIEW_LENGTH = 70

# Алфавиты указателя глоссария; показываются и незанятые буквы.
RU_ALPHABET = "АБВГДЕЖЗИКЛМНОПРСТУФХЦЧШЩЭЮЯ"
EN_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
