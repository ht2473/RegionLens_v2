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
