"""
Сохранённые виды экрана и избранное личного кабинета.

Хранится способ получить результат, а не сам результат: склад пересобирается.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from django.db import models
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.core.models import PublicIdentifierModel, TimeStampedModel

from .constants import (
    QUERY_TARGET_CHOICES,
    QUERY_TARGETS_BY_CODE,
    QueryTarget,
)


class SavedQuery(PublicIdentifierModel, TimeStampedModel):
    """Сохранённые параметры страницы; при открытии расчёт выполняется заново."""

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="saved_queries",
        verbose_name="владелец",
    )
    title = models.CharField("название", max_length=160)
    description = models.TextField(
        "пояснение",
        blank=True,
        help_text="Зачем сохранена выборка и на что обратить внимание при открытии",
    )
    target = models.CharField(
        "страница",
        max_length=32,
        choices=QUERY_TARGET_CHOICES,
        db_index=True,
    )
    parameters = models.JSONField(
        "параметры",
        default=dict,
        blank=True,
        help_text="Строка запроса страницы в разобранном виде",
    )

    # Не поле: ряды, которых больше нет в складе, — проставляет перечень «Сохранённого».
    vanished: tuple[str, ...] = ()

    opened_count = models.PositiveIntegerField("число открытий", default=0)
    last_opened_at = models.DateTimeField("последнее открытие", null=True, blank=True)

    class Meta:
        verbose_name = "сохранённая выборка"
        verbose_name_plural = "сохранённые выборки"
        ordering = ["-updated_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "title"],
                name="savedquery_unique_title_per_user",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "-updated_at"], name="savedquery_user_time_idx"),
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def target_info(self) -> QueryTarget | None:
        """Описание страницы, к которой относится выборка."""
        return QUERY_TARGETS_BY_CODE.get(self.target)

    @property
    def url(self) -> str:
        """Адрес страницы с восстановленными параметрами; список — повторяющимися парами."""
        info = self.target_info
        if info is None:
            return ""
        try:
            base = reverse(info.url_name)
        except NoReverseMatch:  # pragma: no cover - возможен лишь при удалении маршрута
            return ""

        pairs: list[tuple[str, str]] = []
        for key, value in (self.parameters or {}).items():
            if isinstance(value, list):
                pairs.extend((key, str(item)) for item in value)
            elif value is not None:
                pairs.append((key, str(value)))

        return f"{base}?{urlencode(pairs)}" if pairs else base

    @property
    def series_keys(self) -> list[str]:
        """Ключи рядов в параметрах: ``series``, оси ``x`` и ``y``, веса ``weight:<ключ>``."""
        values = self.parameters or {}
        found: list[str] = []
        listed = values.get("series")
        for key in listed if isinstance(listed, list) else [listed]:
            if key:
                found.append(str(key))
        found.extend(str(values[axis]) for axis in ("x", "y") if values.get(axis))
        found.extend(name.removeprefix("weight:") for name in values if name.startswith("weight:"))
        return list(dict.fromkeys(found))

    @property
    def phrases(self) -> list[str]:
        """Параметры словами: «Уровень бедности», «2024 год», «шкала — квантили, классов: 5»."""
        from .describe import describe_parameters

        return describe_parameters(self.target, self.parameters)

    def register_open(self) -> None:
        """Отметить открытие выборки."""
        SavedQuery.objects.filter(pk=self.pk).update(
            opened_count=models.F("opened_count") + 1,
            last_opened_at=timezone.now(),
        )


class Favorite(TimeStampedModel):
    """
    Отметка «слежу за этим» на показателе, ряде или территории.

    Ровно одна из трёх связей заполнена — это проверяет ограничение базы.
    """

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="favorites",
        verbose_name="владелец",
    )
    indicator = models.ForeignKey(
        "catalog.Indicator",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="favorites",
        verbose_name="показатель",
    )
    series = models.ForeignKey(
        "catalog.Series",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="favorites",
        verbose_name="ряд",
    )
    territory = models.ForeignKey(
        "catalog.Territory",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="favorites",
        verbose_name="территория",
    )
    note = models.CharField("пометка", max_length=200, blank=True)

    class Meta:
        verbose_name = "избранное"
        verbose_name_plural = "избранное"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(indicator__isnull=False, series__isnull=True, territory__isnull=True)
                    | models.Q(indicator__isnull=True, series__isnull=False, territory__isnull=True)
                    | models.Q(indicator__isnull=True, series__isnull=True, territory__isnull=False)
                ),
                name="favorite_exactly_one_object",
            ),
            models.UniqueConstraint(
                fields=["user", "indicator"],
                condition=models.Q(indicator__isnull=False),
                name="favorite_unique_indicator",
            ),
            models.UniqueConstraint(
                fields=["user", "series"],
                condition=models.Q(series__isnull=False),
                name="favorite_unique_series",
            ),
            models.UniqueConstraint(
                fields=["user", "territory"],
                condition=models.Q(territory__isnull=False),
                name="favorite_unique_territory",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.kind}: {self.title}"

    @property
    def target(self) -> Any:
        """Объект, на который поставлена отметка."""
        return self.indicator or self.series or self.territory

    @property
    def kind(self) -> str:
        """Вид объекта: показатель, ряд или территория."""
        if self.indicator_id:
            return "indicator"
        if self.series_id:
            return "series"
        return "territory"

    @property
    def title(self) -> str:
        """Название объекта для показа в перечне."""
        target = self.target
        return str(target) if target is not None else ""

    @property
    def url(self) -> str:
        """Адрес страницы объекта."""
        if self.indicator is not None:
            return reverse("catalog:series-detail", kwargs={"slug": self.indicator.slug})
        if self.series is not None:
            # Разрез страница показателя читает параметром series.
            base = reverse("catalog:series-detail", kwargs={"slug": self.series.indicator.slug})
            return f"{base}?{urlencode({'series': self.series.key})}"
        if self.territory is not None:
            return reverse("catalog:territory-detail", kwargs={"slug": self.territory.slug})
        # Недостижимо при ограничении базы; на случай записи в обход него.
        raise ValueError("Запись избранного не ссылается ни на один объект")
