"""Журнал запросов без ответа: по нему пополняется словарь синонимов поиска."""

from __future__ import annotations

from datetime import timedelta

from django.db import models
from django.utils import timezone

# Срок хранения записи журнала, дней.
RETENTION_DAYS = 90
# Наибольшая длина сохраняемого запроса.
MAX_QUERY_LENGTH = 200


class SearchMiss(models.Model):
    """Запрос, на который не нашлось ответа; без адреса и учётной записи."""

    text = models.CharField("запрос", max_length=MAX_QUERY_LENGTH)
    language = models.CharField("язык", max_length=8)
    created_at = models.DateTimeField("когда", default=timezone.now, db_index=True)

    class Meta:
        verbose_name = "запрос без ответа"
        verbose_name_plural = "запросы без ответа"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.text

    @classmethod
    def record(cls, text: str, language: str) -> None:
        """Записать запрос и убрать записи старше срока хранения."""
        cls.objects.create(text=text.strip()[:MAX_QUERY_LENGTH], language=language[:8])
        cls.objects.filter(created_at__lt=timezone.now() - timedelta(days=RETENTION_DAYS)).delete()
