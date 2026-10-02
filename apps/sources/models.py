"""Источники данных и журнал их выпусков; сами файлы — в архиве на диске."""

from __future__ import annotations

from datetime import timedelta

from django.db import models
from django.utils import timezone
from django.utils.translation import get_language, gettext
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel

# Через сколько времени сбор без исхода считается оборванным.
ABANDONED_AFTER = timedelta(minutes=30)


class Source(TimeStampedModel):
    """
    Внешний источник: сведения о нём — из модуля источника, состояние проверки — здесь.

    Запись заводится сборщиком при первом обращении к источнику.
    """

    code = models.CharField("код", max_length=40, unique=True)
    title_ru = models.CharField("название", max_length=300)
    title_en = models.CharField("название (англ.)", max_length=300, blank=True)
    publisher_ru = models.CharField("издатель", max_length=200)
    publisher_en = models.CharField("издатель (англ.)", max_length=200, blank=True)
    licence_ru = models.CharField("условия использования", max_length=300, blank=True)
    licence_en = models.CharField("условия использования (англ.)", max_length=300, blank=True)
    page_url = models.URLField("страница источника", max_length=500, blank=True)
    check_every = models.DurationField("проверять раз в", default=timedelta(days=1))

    checked_at = models.DateTimeField("последняя проверка", null=True, blank=True)
    check_ok = models.BooleanField("проверка удалась", default=True)
    check_error = models.TextField("ошибка проверки", blank=True)
    running_since = models.DateTimeField("сбор идёт с", null=True, blank=True)

    class Meta:
        verbose_name = "источник данных"
        verbose_name_plural = "источники данных"
        ordering = ["code"]

    def __str__(self) -> str:
        return self.title_ru

    @property
    def title(self) -> str:
        """Название на языке запроса."""
        return self.title_en if get_language() == "en" and self.title_en else self.title_ru

    @property
    def publisher(self) -> str:
        """Издатель на языке запроса."""
        if get_language() == "en" and self.publisher_en:
            return self.publisher_en
        return self.publisher_ru

    @property
    def licence(self) -> str:
        """Условия использования на языке запроса."""
        return self.licence_en if get_language() == "en" and self.licence_en else self.licence_ru

    @property
    def is_running(self) -> bool:
        """Сбор идёт и не оборван."""
        return (
            self.running_since is not None and timezone.now() - self.running_since < ABANDONED_AFTER
        )

    @property
    def is_due(self) -> bool:
        """Пора проверить источник снова."""
        return self.checked_at is None or timezone.now() - self.checked_at >= self.check_every

    def latest_release(self) -> Release | None:
        """Последний разобранный выпуск."""
        return self.releases.filter(status=Release.Status.PARSED).first()


class Release(TimeStampedModel):
    """Полученный файл выпуска: где лежит в архиве и чем закончился разбор."""

    class Status(models.TextChoices):
        FETCHED = "fetched", _("Получен")
        PARSED = "parsed", _("Разобран")
        FAILED = "failed", _("Ошибка разбора")

    source = models.ForeignKey(
        Source, on_delete=models.CASCADE, related_name="releases", verbose_name="источник"
    )
    code = models.CharField("код выпуска", max_length=40, help_text="«07-2026», «2026-03-06»")
    title = models.CharField("выпуск", max_length=200)
    reference_year = models.PositiveSmallIntegerField("год выпуска")
    published_on = models.DateField("опубликован", null=True, blank=True)

    url = models.URLField("адрес", max_length=500, blank=True)
    file_name = models.CharField("имя файла", max_length=255)
    archive_path = models.CharField("путь в архиве", max_length=500)
    sha256 = models.CharField("SHA-256", max_length=64, unique=True)
    size_bytes = models.PositiveBigIntegerField("размер, байт")
    remote_modified = models.DateTimeField("изменён на сайте", null=True, blank=True)
    fetched_at = models.DateTimeField("получен")

    status = models.CharField(
        "состояние", max_length=10, choices=Status.choices, default=Status.FETCHED, db_index=True
    )
    parser_version = models.PositiveSmallIntegerField("версия разбора", default=0)
    parsed_at = models.DateTimeField("разобран", null=True, blank=True)
    row_count = models.PositiveIntegerField("значений", default=0)
    report = models.JSONField("отчёт о разборе", default=dict, blank=True)
    changes = models.JSONField(
        "изменения к прежнему выпуску",
        default=dict,
        blank=True,
        help_text="Последний период по показателям и число изменённых значений",
    )
    error = models.TextField("ошибка разбора", blank=True)

    class Meta:
        verbose_name = "выпуск источника"
        verbose_name_plural = "выпуски источников"
        ordering = ["-reference_year", "-published_on", "-fetched_at"]
        indexes = [models.Index(fields=["source", "status"], name="release_source_status_idx")]

    def __str__(self) -> str:
        return f"{self.source.code}: {self.code}"

    @property
    def display_title(self) -> str:
        """Выпуск и дата публикации; в английской версии вместо русского названия — код."""
        title = (
            self.title
            if get_language() != "en"
            else gettext("выпуск %(release)s") % {"release": self.code}
        )
        if self.published_on is None:
            return title
        day = self.published_on.strftime("%d.%m.%Y")
        if day in title:
            return title
        return gettext("%(title)s, опубликован %(date)s") % {"title": title, "date": day}

    @property
    def parsed_name(self) -> str:
        """Имя файла разобранного выпуска."""
        return f"{self.code}_{self.sha256[:8]}.parquet"
