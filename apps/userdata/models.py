"""Таблицы пользователей: набор, его версии и запомненные сопоставления территорий."""

from __future__ import annotations

import secrets
import string
from pathlib import Path

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import PublicIdentifierModel, TimeStampedModel

# Короткий код набора в ключах рядов «u:<код набора>:<код ряда>».
CODE_ALPHABET = string.ascii_lowercase + string.digits
CODE_LENGTH = 12


def new_code() -> str:
    """Случайный код набора."""
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


class Dataset(PublicIdentifierModel, TimeStampedModel):
    """Набор: таблица пользователя со всеми её версиями; в интерфейсе — «таблица»."""

    class State(models.TextChoices):
        DRAFT = "draft", _("черновик")
        READY = "ready", _("готов")
        FAILED = "failed", _("не собран")

    code = models.CharField(
        "код в ключах рядов", max_length=CODE_LENGTH, unique=True, default=new_code
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="владелец",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="datasets",
    )
    # Набор гостя привязан к отпечатку случайного ключа из сеанса, а не к ключу сеанса:
    # тот меняется при входе.
    guest_key = models.CharField(
        "отпечаток гостевого ключа", max_length=64, blank=True, db_index=True
    )
    expires_at = models.DateTimeField("хранится до", null=True, blank=True)
    title = models.CharField("название", max_length=200)
    description = models.TextField("описание", blank=True)
    source_title = models.CharField("источник", max_length=300, blank=True)
    source_url = models.URLField("адрес источника", max_length=500, blank=True)
    state = models.CharField("состояние", max_length=10, choices=State.choices, default=State.DRAFT)
    current_version = models.ForeignKey(
        "DatasetVersion",
        verbose_name="текущая версия",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    size_bytes = models.BigIntegerField("занятое место", default=0)

    class Meta:
        verbose_name = "набор пользователя"
        verbose_name_plural = "наборы пользователей"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(owner__isnull=False) | ~models.Q(guest_key=""),
                name="userdata_dataset_has_owner",
            )
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def directory(self) -> Path:
        """Каталог файлов набора."""
        return Path(settings.USERDATA_DIR) / str(self.public_id)


class DatasetVersion(TimeStampedModel):
    """Версия набора: исходный файл, рецепт разбора и итог сборки."""

    class State(models.TextChoices):
        UPLOADED = "uploaded", _("файл принят")
        DESCRIBED = "described", _("таблица описана")
        BUILDING = "building", _("собирается")
        BUILT = "built", _("собран")
        FAILED = "failed", _("не разобран")

    dataset = models.ForeignKey(
        Dataset, verbose_name="набор", on_delete=models.CASCADE, related_name="versions"
    )
    number = models.PositiveIntegerField("номер")
    file_name = models.CharField("имя файла", max_length=255)
    file_size = models.BigIntegerField("размер файла")
    sha256 = models.CharField("отпечаток SHA-256", max_length=64)
    file_kind = models.CharField("вид файла", max_length=16)
    # Как читать таблицу: выбранная таблица, кодировка, разделитель, роли столбцов, периоды,
    # отбор разрезов, решения о вложенных и ручные сопоставления (А3).
    recipe = models.JSONField("рецепт", default=dict, blank=True)
    # Что нашёл приём и разбор: таблицы файла, образцы, пропуски, вопросы.
    report = models.JSONField("отчёт", default=dict, blank=True)
    parser_version = models.PositiveSmallIntegerField("редакция разборщика", default=1)
    state = models.CharField(
        "состояние", max_length=10, choices=State.choices, default=State.UPLOADED
    )
    error = models.TextField("ошибка", blank=True)
    values_count = models.PositiveIntegerField("значений", default=0)
    series_count = models.PositiveIntegerField("рядов", default=0)
    regions_count = models.PositiveSmallIntegerField("субъектов", default=0)
    first_year = models.SmallIntegerField("первый год", null=True, blank=True)
    last_year = models.SmallIntegerField("последний год", null=True, blank=True)
    # Файл DuckDB сборки в каталоге версии; новая сборка пишет новый файл: открытый
    # другим процессом файл в Windows не подменить.
    data_file = models.CharField("файл сборки", max_length=64, blank=True)

    class Meta:
        verbose_name = "версия набора"
        verbose_name_plural = "версии наборов"
        ordering = ["dataset", "-number"]
        constraints = [
            models.UniqueConstraint(fields=["dataset", "number"], name="userdata_version_number")
        ]

    def __str__(self) -> str:
        return f"{self.dataset} — {self.number}"

    @property
    def directory(self) -> Path:
        """Каталог файлов версии."""
        return self.dataset.directory / str(self.number)

    @property
    def data_path(self) -> Path | None:
        """Файл DuckDB сборки; ``None`` — версия не собрана."""
        return self.directory / self.data_file if self.data_file else None


class DatasetSeries(models.Model):
    """Ряд набора: показатель, значения разрезов и период года; ключ — «u:<набор>:<ряд>»."""

    class Kind(models.TextChoices):
        SUM = "sum", _("сумма")
        RELATIVE = "relative", _("относительная величина")

    class Polarity(models.TextChoices):
        POSITIVE = "positive", _("рост — к лучшему")
        NEGATIVE = "negative", _("рост — к худшему")
        NEUTRAL = "neutral", _("без оценки")

    class Derived(models.TextChoices):
        NONE = "", _("из таблицы")
        PER_1000 = "per1000", _("на 1 000 жителей")
        PER_100000 = "per100000", _("на 100 000 жителей")
        PER_KM2 = "perkm2", _("на км²")
        REAL = "real", _("в ценах последнего года")
        RUSSIA = "russia", _("Россия = 100")
        GROWTH = "growth", _("% к предыдущему году")
        SHARE = "share", _("доля в сумме по субъектам")
        SLICE_SUM = "slicesum", _("сумма по разрезу")
        MONTHS = "months", _("месяцы, свёрнутые в год")
        FORMULA = "formula", _("формула")

    version = models.ForeignKey(
        DatasetVersion, verbose_name="версия", on_delete=models.CASCADE, related_name="series"
    )
    # Отпечаток «показатель + значения разрезов + период года»: новая версия файла даёт
    # те же коды тем же рядам.
    code = models.CharField("код ряда", max_length=24)
    indicator = models.CharField("показатель в таблице", max_length=300)
    title = models.CharField("название", max_length=300)
    unit = models.CharField("единица", max_length=120, blank=True)
    kind = models.CharField("вид величины", max_length=10, choices=Kind.choices, blank=True)
    polarity = models.CharField(
        "направленность", max_length=10, choices=Polarity.choices, default=Polarity.NEUTRAL
    )
    precision = models.PositiveSmallIntegerField("знаков после запятой", default=1)
    # Значения разрезов: [[название разреза, значение], …].
    slices = models.JSONField("значения разрезов", default=list, blank=True)
    period = models.CharField("период года", max_length=16, default="year:12")
    derived = models.CharField(
        "пересчёт", max_length=10, choices=Derived.choices, blank=True, default=Derived.NONE
    )
    base_code = models.CharField("код исходного ряда", max_length=24, blank=True)
    order = models.PositiveIntegerField("порядок", default=0)
    values_count = models.PositiveIntegerField("значений", default=0)
    regions_count = models.PositiveSmallIntegerField("субъектов", default=0)
    first_year = models.SmallIntegerField("первый год", null=True, blank=True)
    last_year = models.SmallIntegerField("последний год", null=True, blank=True)

    class Meta:
        verbose_name = "ряд набора"
        verbose_name_plural = "ряды наборов"
        ordering = ["version", "order"]
        constraints = [
            models.UniqueConstraint(fields=["version", "code"], name="userdata_series_code")
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def key(self) -> str:
        """Ключ ряда для слоя рядов и адресов холста."""
        return f"u:{self.version.dataset.code}:{self.code}"


class TerritoryLabel(TimeStampedModel):
    """Сопоставление подписи, выбранное человеком: применяется к его следующим таблицам."""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="владелец",
        on_delete=models.CASCADE,
        related_name="territory_labels",
    )
    label_key = models.CharField("подпись для сравнения", max_length=300)
    # Код справочника или «outside» — территории нет в справочнике.
    territory_code = models.CharField("территория", max_length=16)

    class Meta:
        verbose_name = "запомненная подпись территории"
        verbose_name_plural = "запомненные подписи территорий"
        constraints = [
            models.UniqueConstraint(fields=["owner", "label_key"], name="userdata_label_per_owner")
        ]

    def __str__(self) -> str:
        return f"{self.label_key} → {self.territory_code}"
