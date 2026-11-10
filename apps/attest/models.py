"""Реестр выданных документов: по нему документ узнают по одному файлу, без подписи рядом."""

from __future__ import annotations

from django.db import models


class IssuedDocument(models.Model):
    """
    Документ, который сайт выдал и подписал.

    Самого документа здесь нет — только его SHA-256, подпись и что это был за документ.
    Сведений о том, кто его скачал, запись не содержит.
    """

    sha256 = models.CharField("SHA-256 документа", max_length=64, unique=True)
    size = models.PositiveBigIntegerField("размер, байт")
    kind = models.CharField("вид выгрузки", max_length=32)
    export_format = models.CharField("формат", max_length=8)
    filename = models.CharField("имя файла", max_length=255)
    dataset_version = models.CharField("версия набора данных", max_length=32, blank=True)
    signature = models.BinaryField("подпись CMS")
    created_at = models.DateTimeField("выдан", auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "выданный документ"
        verbose_name_plural = "выданные документы"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.filename} ({self.sha256[:12]})"
