"""Общие абстрактные модели и отметки о работе служб."""

from __future__ import annotations

import uuid

from django.db import models
from django.utils import timezone


class TimeStampedModel(models.Model):
    """Базовая модель с отметками создания и изменения."""

    created_at = models.DateTimeField(
        "создано",
        auto_now_add=True,
        db_index=True,
        help_text="Момент создания записи",
    )
    updated_at = models.DateTimeField(
        "изменено",
        auto_now=True,
        help_text="Момент последнего изменения записи",
    )

    class Meta:
        abstract = True


class PublicIdentifierModel(models.Model):
    """
    Модель с публичным идентификатором UUID.

    Первичный ключ остаётся числовым; UUID во внешних ссылках не раскрывает число записей.
    """

    public_id = models.UUIDField(
        "публичный идентификатор",
        default=uuid.uuid4,
        editable=False,
        unique=True,
        db_index=True,
    )

    class Meta:
        abstract = True


class OrderedModel(models.Model):
    """Модель с ручной сортировкой для справочников, где важен порядок отображения."""

    display_order = models.PositiveSmallIntegerField(
        "порядок отображения",
        default=100,
        db_index=True,
        help_text="Меньшее значение — выше в списке",
    )

    class Meta:
        abstract = True


class ServiceBeat(models.Model):
    """
    Последняя отметка о работе службы, которую веб-процесс не может проверить сам.

    Резервное копирование, отправка писем, сбор источников, подсчёт посещений и проверка
    версии набора оставляют отметку, панель показывает последнюю.
    """

    class Service(models.TextChoices):
        BACKUP = "backup", "Резервная копия"
        MAIL = "mail", "Почта"
        COLLECT = "collect", "Сбор источников"
        VISITS = "visits", "Статистика посещений"
        DATASET = "dataset", "Проверка версии набора"

    service = models.CharField("служба", max_length=16, choices=Service.choices, unique=True)
    seen_at = models.DateTimeField("отметка")
    ok = models.BooleanField("исправна", default=True)
    detail = models.JSONField("подробности", default=dict, blank=True)

    class Meta:
        verbose_name = "отметка службы"
        verbose_name_plural = "отметки служб"

    def __str__(self) -> str:
        return f"{self.get_service_display()}: {self.seen_at:%d.%m.%Y %H:%M}"

    @classmethod
    def record(cls, service: str, *, ok: bool = True, **detail: object) -> ServiceBeat:
        """Записать отметку службы, заменив прежнюю."""
        beat, _ = cls.objects.update_or_create(
            service=service,
            defaults={"seen_at": timezone.now(), "ok": ok, "detail": detail},
        )
        return beat
