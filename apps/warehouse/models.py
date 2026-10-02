"""Запуски сборки склада и замечания проверок качества."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel


class EtlRun(TimeStampedModel):
    """Запуск ETL-конвейера: параметры, длительность, результат и журнал."""

    class Status(models.TextChoices):
        QUEUED = "queued", _("В очереди")
        RUNNING = "running", _("Выполняется")
        SUCCESS = "success", _("Завершён успешно")
        FAILED = "failed", _("Завершён с ошибкой")
        CANCELLED = "cancelled", _("Прерван")

    class Mode(models.TextChoices):
        FULL = "full", _("Полная сборка")
        MARTS = "marts", _("Пересчёт витрин")
        CATALOG = "catalog", _("Синхронизация справочников")

    dataset_version = models.ForeignKey(
        "catalog.DatasetVersion",
        on_delete=models.PROTECT,
        related_name="etl_runs",
        null=True,
        blank=True,
        verbose_name="версия набора данных",
    )
    started_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        related_name="etl_runs",
        null=True,
        blank=True,
        verbose_name="запустил",
        help_text="Пусто, если сборка запущена из командной строки или по расписанию",
    )

    mode = models.CharField("режим", max_length=16, choices=Mode.choices, default=Mode.FULL)
    status = models.CharField(
        "состояние",
        max_length=16,
        choices=Status.choices,
        default=Status.RUNNING,
        db_index=True,
    )

    started_at = models.DateTimeField("начало", default=timezone.now)
    finished_at = models.DateTimeField("окончание", null=True, blank=True)
    duration_seconds = models.FloatField("длительность, с", null=True, blank=True)

    source_path = models.CharField("путь к исходному файлу", max_length=500, blank=True)
    source_checksum = models.CharField("контрольная сумма источника", max_length=64, blank=True)

    observation_count = models.PositiveIntegerField("наблюдений загружено", default=0)
    series_count = models.PositiveIntegerField("рядов", default=0)
    revision_count = models.PositiveIntegerField("пересмотренных наблюдений", default=0)
    break_count = models.PositiveIntegerField("выявлено разрывов", default=0)

    statistics = models.JSONField(
        "показатели сборки",
        default=dict,
        blank=True,
        help_text="Профиль источника, распределение качества значений, длительность этапов",
    )
    log = models.TextField("журнал", blank=True)
    error_message = models.TextField("сообщение об ошибке", blank=True)

    class Meta:
        verbose_name = "запуск ETL"
        verbose_name_plural = "запуски ETL"
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["status", "-started_at"], name="etl_status_started_idx"),
        ]

    def __str__(self) -> str:
        return f"Сборка {self.started_at:%d.%m.%Y %H:%M} — {self.get_status_display()}"

    def finish(self, *, status: str, error: str = "") -> None:
        """Зафиксировать завершение сборки и рассчитать её длительность."""
        self.finished_at = timezone.now()
        self.duration_seconds = (self.finished_at - self.started_at).total_seconds()
        self.status = status
        self.error_message = error
        self.save(
            update_fields=[
                "finished_at",
                "duration_seconds",
                "status",
                "error_message",
                "log",
                "updated_at",
            ]
        )

    def append_log(self, message: str, *, save: bool = False) -> None:
        """Добавить строку в журнал сборки; с ``save=True`` — сразу в базу, для панели."""
        timestamp = timezone.now().strftime("%H:%M:%S")
        self.log = f"{self.log}[{timestamp}] {message}\n"
        if save and self.pk:
            self.save(update_fields=["log", "updated_at"])

    @property
    def is_active(self) -> bool:
        """Сборка ждёт в очереди или идёт."""
        return self.status in {self.Status.QUEUED, self.Status.RUNNING}


class DataQualityCheck(TimeStampedModel):
    """Замечание проверки качества данных, найденное при сборке склада."""

    class Severity(models.TextChoices):
        INFO = "info", _("Информация")
        WARNING = "warning", _("Предупреждение")
        ERROR = "error", _("Ошибка")

    class CheckType(models.TextChoices):
        AGGREGATE_MISMATCH = "aggregate_mismatch", _("Расхождение суммы регионов с итогом")
        SUDDEN_JUMP = "sudden_jump", _("Резкое изменение значения")
        GAP = "gap", _("Разрыв в ряде наблюдений")
        UNIT_INCONSISTENCY = "unit_inconsistency", _("Несогласованность единиц измерения")
        STATUS_OUTDATED = "status_outdated", _("Устаревшее состояние публикации")

    run = models.ForeignKey(
        EtlRun,
        on_delete=models.CASCADE,
        related_name="quality_checks",
        verbose_name="запуск",
    )
    check_type = models.CharField(
        "тип проверки",
        max_length=32,
        choices=CheckType.choices,
        db_index=True,
    )
    severity = models.CharField(
        "уровень",
        max_length=10,
        choices=Severity.choices,
        default=Severity.WARNING,
        db_index=True,
    )

    series_key = models.CharField("ключ ряда", max_length=64, blank=True, db_index=True)
    territory_code = models.CharField("код территории", max_length=16, blank=True)
    year = models.PositiveSmallIntegerField("год", null=True, blank=True)

    message = models.CharField("описание", max_length=500)
    details = models.JSONField("подробности", default=dict, blank=True)

    class Meta:
        verbose_name = "проверка качества данных"
        verbose_name_plural = "проверки качества данных"
        ordering = ["-created_at", "severity"]
        indexes = [
            models.Index(fields=["check_type", "severity"], name="quality_type_severity_idx"),
            models.Index(fields=["series_key", "year"], name="quality_series_year_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.get_check_type_display()}: {self.message[:60]}"

    @property
    def display_message(self) -> str:
        """
        Изложение замечания на языке запроса.

        Поле ``message`` хранит русский текст; для показа замечание собирается из вида
        и ``details``, а неизвестного вида — выводится как есть.
        """
        template = _QUALITY_MESSAGES.get(self.details.get("kind", ""))
        if template is None:
            return self.message
        try:
            return template(self.details)
        except KeyError, TypeError, ValueError:  # pragma: no cover - защита от неполных данных
            return self.message


# Изложение замечаний на языке читателя из вида проверки и подробностей с числами.
_QUALITY_MESSAGES: dict[str, Callable[[dict[str, Any]], str]] = {
    "discontinued_has_values": lambda details: (
        gettext(
            "Ряд отмечен как прекращённый с %(since)s года, "
            "но в складе есть значения за %(year)s год"
        )
        % {"since": details["since"], "year": details["year"]}
    ),
    "compound_unit": lambda _details: gettext(
        "Составное объявление единицы измерения: соотношение между единицей "
        "территорий и единицей для России в целом не определено"
    ),
    "scale_variants": lambda _details: gettext(
        "Выпуски издания объявляют единицу измерения для России в целом "
        "по-разному; значения приведены к единой единице при загрузке"
    ),
    "scale_mismatch": lambda details: (
        gettext(
            "Значения по субъектам и по России в целом приведены в разных единицах "
            "измерения: их отношение устойчиво равно %(ratio).0f"
        )
        % {"ratio": details["median_ratio"]}
    ),
    "not_additive": lambda details: (
        gettext("Сумма по субъектам отличается от значения по России на %(share).1f %%")
        % {"share": abs(details.get("deviation", abs(details["ratio"] - 1))) * 100}
    ),
    "unit_jump": lambda details: (
        gettext(
            "Значение изменилось между выпусками в %(factor).0f раз — "
            "вероятна ошибка единицы измерения в источнике"
        )
        % {"factor": abs(details["factor"])}
    ),
    "gap": lambda details: (
        gettext("В периоде %(first)s–%(last)s отсутствует наблюдений: %(count)s")
        % {
            "first": details["first_year"],
            "last": details["last_year"],
            "count": details["missing_years"],
        }
    ),
}
