"""
Перечисления и константы предметной области.

Коды совпадают с кодами аналитического склада.
"""

from __future__ import annotations

import enum
import re

from django.db import models
from django.utils.translation import gettext_lazy as _


class TerritoryLevel(models.TextChoices):
    """Уровень территориальной единицы наблюдения."""

    COUNTRY = "country", "Страна"
    FEDERAL_DISTRICT = "federal_district", "Федеральный округ"
    REGION = "region", "Регион"


class TerritoryType(models.TextChoices):
    """Тип субъекта Российской Федерации согласно Конституции."""

    REPUBLIC = "republic", "Республика"
    KRAI = "krai", "Край"
    OBLAST = "oblast", "Область"
    FEDERAL_CITY = "federal_city", "Город федерального значения"
    AUTONOMOUS_OBLAST = "autonomous_oblast", "Автономная область"
    AUTONOMOUS_OKRUG = "autonomous_okrug", "Автономный округ"
    AGGREGATE = "aggregate", "Составная территория"
    NOT_APPLICABLE = "not_applicable", "Не применимо"


class ValueQuality(models.IntegerChoices):
    """
    Качество отдельного наблюдения, определяемое при сборке по заглушкам источника.

    ``-99999999`` — нет в источнике или ошибочно; ``-77777777`` — скрыто (прочерк,
    многоточие, крест); ``ND`` — единица не найдена; ``CD`` — атрибут неприменим.
    """

    OBSERVED = 0, "Наблюдаемое значение"
    NO_DATA = 1, "Нет данных в источнике"
    HIDDEN = 2, "Значение скрыто"
    NOT_APPLICABLE = 3, "Неприменимо"


class ValueFlag(enum.IntFlag):
    """
    Признаки значения из внешнего источника; у значений набора признаков нет.

    Хранятся битами в ``flags`` склада: значение бывает и предварительным, и рассчитанным.
    """

    PRELIMINARY = 1  # источник отмечает значение как предварительное или оценку
    COMPUTED = 2  # рассчитано проектом из месяцев, кварталов или итога на жителя
    FROZEN_DENOMINATOR = 4  # на жителя по численности последнего известного года


class BreakKind(models.TextChoices):
    """Тип разрыва сопоставимости ряда."""

    METHODOLOGY = "methodology", _("Изменение методологии наблюдения")
    CLASSIFIER = "classifier", _("Смена классификатора")
    TERRITORY = "territory", _("Изменение состава территории")
    COVERAGE = "coverage", _("Изменение круга наблюдаемых объектов")
    OBSERVATION_START = "observation_start", _("Начало статистического наблюдения")
    PRICE_BASE = "price_base", _("Изменение базы сопоставимых цен")
    SOURCE = "source", _("Смена источника данных")


class Periodicity(models.TextChoices):
    """Периодичность обновления статистического издания."""

    ANNUAL = "annual", "Ежегодно"
    BIENNIAL = "biennial", "Раз в два года"
    TRIENNIAL = "triennial", "Раз в три года"
    IRREGULAR = "irregular", "Нерегулярно"


class Polarity(models.TextChoices):
    """Направленность показателя: у дестимулятора лучшим считается меньшее значение."""

    POSITIVE = "positive", "Стимулятор (больше — лучше)"
    NEGATIVE = "negative", "Дестимулятор (больше — хуже)"
    NEUTRAL = "neutral", "Нейтральный"
    UNKNOWN = "unknown", "Не определена"


class UnitKind(models.TextChoices):
    """Категория единицы измерения."""

    # Названия показываются в фильтре каталога и потому переводятся.
    ABSOLUTE = "absolute", _("Абсолютная величина")
    CURRENCY = "currency", _("Денежная величина")
    SHARE = "share", _("Доля или процент")
    INDEX = "index", _("Индекс или темп изменения")
    RATE = "rate", _("Относительный показатель на численность")
    PHYSICAL = "physical", _("Натуральная величина")
    UNKNOWN = "unknown", _("Не определена")


def territory_short_code(code: str, abbreviation: str = "") -> str:
    """
    Компактное обозначение территории: «МСК» по-русски, «MOW» по-английски.

    Принимает строки, а не запись справочника: те же подписи собираются из строк склада.
    """
    from django.utils.translation import get_language

    if get_language() == "en":
        return short_territory_code(code)
    return abbreviation or short_territory_code(code)


def short_territory_code(code: str) -> str:
    """Буквенная часть кода территории: «MOW» из «RU-MOW», «CFO» из «FD-CFO»."""
    return code.rsplit("-", 1)[-1]


# Числовые заглушки исходного набора данных.
SENTINEL_NO_DATA = -99_999_999.0
SENTINEL_HIDDEN = -77_777_777.0

# Строковые заглушки исходного набора данных.
SENTINEL_STRING_NO_DATA = "ND"
SENTINEL_STRING_NOT_APPLICABLE = "CD"

# Единица, которую источник не указал и которую не удалось восстановить из формулировки.
UNSPECIFIED_UNIT_NAME = "Не указана"

# Составные территории источника (субъект с входящими автономными округами): исключаются
# из рейтингов и карт, иначе автономные округа учитываются дважды.
AGGREGATE_TERRITORY_NAMES = frozenset(
    {
        "Архангельская область (с автономным округом)",
        "Тюменская область (с автономными округами)",
    }
)

# Границы периода, покрываемого набором данных.
DATASET_FIRST_YEAR = 2001
DATASET_LAST_YEAR = 2025


# ---------------------------------------------------------------------------------------
# Приведение величин к сопоставимому между субъектами виду
# ---------------------------------------------------------------------------------------

# Категории единиц измерения, которые сами по себе уже нормированы: доля, индекс
# и относительный показатель на численность не зависят от размера территории.
NORMALISED_UNIT_KINDS = frozenset({"share", "index", "rate"})

# Признаки приведения величины к основе в названии показателя: источник
# не сообщает, нормирован ли ряд.
NORMALISATION_MARKERS: tuple[str, ...] = (
    "на душу",
    "в расчёте на",
    "в расчете на",
    "на одного",
    "на одну",
    "на один",
    "на 100",
    "на 1000",
    "на 10 000",
    "на 100 000",
    "на 1 000",
    "на тысячу",
    "на 1 тыс",
    "на 10 тыс",
    "на 1 жител",
    "средне",
    "в среднем",
    "уровень",
    "доля",
    "удельн",
    "коэффициент",
    "обеспеченность",
    "индекс",
    "процент",
)


def is_unnormalised_title(title: str, unit_kind: str) -> bool:
    """
    Определить по категории единицы и названию, что величина не приведена к основе.

    Сравнение субъектов по такой величине упорядочивает их по размеру; это подсказка, не запрет.
    """
    if unit_kind in NORMALISED_UNIT_KINDS:
        return False

    # В названиях источника встречаются неразрывные и узкие пробелы.
    text = re.sub(r"\s+", " ", title).lower()
    return not any(marker in text for marker in NORMALISATION_MARKERS)
