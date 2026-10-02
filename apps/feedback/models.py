"""
Обращения посетителей; ответ — одно письмо, хранится полем обращения.

Обратиться можно без входа: для гостя сохраняются имя и адрес почты.
"""

from __future__ import annotations

from django.db import models
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.core.models import PublicIdentifierModel, TimeStampedModel

from .constants import (
    CLOSED_STATUSES,
    DELIVERY_BADGE,
    OPEN_STATUSES,
    STATUS_BADGE,
    DeliveryStatus,
    TicketStatus,
    TicketTopic,
)

# Длина краткого представления обращения в перечнях.
SUBJECT_PREVIEW_LENGTH = 60


class TicketQuerySet(models.QuerySet):
    """Типовые выборки обращений."""

    def open(self) -> TicketQuerySet:
        """Обращения, ждущие ответа."""
        return self.filter(status__in=OPEN_STATUSES)

    def closed(self) -> TicketQuerySet:
        """Обращения, работа по которым завершена."""
        return self.filter(status__in=CLOSED_STATUSES)

    def answered(self) -> TicketQuerySet:
        """Обращения, на которые отправлен ответ."""
        return self.filter(status=TicketStatus.ANSWERED)

    def for_user(self, user: User) -> TicketQuerySet:
        """
        Обращения, отправленные из учётной записи.

        Обращения гостя с тем же адресом сюда не входят: адрес при регистрации
        не подтверждается, и по нему открывалась бы чужая переписка.
        """
        return self.filter(author=user)

    def with_related(self) -> TicketQuerySet:
        """Выборка со связанными записями для перечней панели управления."""
        return self.select_related("author", "answered_by")


class Ticket(PublicIdentifierModel, TimeStampedModel):
    """Обращение пользователя."""

    author = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tickets",
        verbose_name="автор",
        help_text="Пусто, если обращение оставлено без входа в систему",
    )
    contact_name = models.CharField("имя отправителя", max_length=180)
    contact_email = models.EmailField("адрес для ответа", max_length=254, db_index=True)

    topic = models.CharField(
        "тема",
        max_length=20,
        choices=TicketTopic.choices,
        default=TicketTopic.OTHER,
        db_index=True,
    )
    subject = models.CharField("заголовок", max_length=200)
    body = models.TextField("текст обращения")

    status = models.CharField(
        "состояние",
        max_length=16,
        choices=TicketStatus.choices,
        default=TicketStatus.NEW,
        db_index=True,
    )

    page_url = models.CharField(
        "страница обращения",
        max_length=500,
        blank=True,
        help_text="Адрес страницы, с которой отправлено обращение; заполняется автоматически",
    )

    ip_address = models.GenericIPAddressField("адрес отправителя", null=True, blank=True)
    user_agent = models.CharField("клиент", max_length=255, blank=True)
    consent_at = models.DateTimeField("согласие на обработку дано", null=True, blank=True)
    consent_revision = models.DateField("редакция согласия", null=True, blank=True)

    answer = models.TextField(
        "ответ",
        blank=True,
        help_text="Уходит письмом на адрес для ответа",
    )
    answered_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="answered_tickets",
        verbose_name="ответил",
    )
    answered_at = models.DateTimeField("ответ написан", null=True, blank=True)
    answer_delivery = models.CharField(
        "доставка ответа",
        max_length=8,
        choices=DeliveryStatus.choices,
        default=DeliveryStatus.NONE,
        blank=True,
    )
    answer_sent_at = models.DateTimeField("письмо принято почтовым узлом", null=True, blank=True)
    answer_attempts = models.PositiveSmallIntegerField("попыток отправки", default=0)
    answer_error = models.CharField("причина отказа", max_length=500, blank=True)

    objects = TicketQuerySet.as_manager()

    class Meta:
        verbose_name = "обращение"
        verbose_name_plural = "обращения"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "-created_at"], name="ticket_status_time_idx"),
        ]

    def __str__(self) -> str:
        return f"#{self.pk} {self.subject[:SUBJECT_PREVIEW_LENGTH]}"

    @property
    def cabinet_url(self) -> str:
        """Адрес обращения в кабинете автора."""
        return reverse("feedback:mine-detail", kwargs={"public_id": self.public_id})

    @property
    def manage_url(self) -> str:
        """Адрес карточки обращения в панели управления."""
        return reverse("dashboard:ticket-detail", kwargs={"public_id": self.public_id})

    @property
    def is_open(self) -> bool:
        """Признак того, что обращение ждёт ответа."""
        return self.status in OPEN_STATUSES

    @property
    def is_answered(self) -> bool:
        """Признак отправленного ответа."""
        return self.status == TicketStatus.ANSWERED

    @property
    def status_badge(self) -> str:
        """Класс оформления метки состояния."""
        return STATUS_BADGE.get(self.status, "badge--outline")

    @property
    def delivery_badge(self) -> str:
        """Класс оформления метки доставки ответа."""
        return DELIVERY_BADGE.get(self.answer_delivery, "badge--outline")

    @property
    def can_reply(self) -> bool:
        """Есть адрес для ответа: у обращения удалённой учётной записи его нет."""
        return bool(self.contact_email)

    @property
    def can_resend(self) -> bool:
        """Ответ есть, а письмо не ушло — его можно отправить ещё раз."""
        return (
            self.can_reply and bool(self.answer) and self.answer_delivery == DeliveryStatus.FAILED
        )

    @property
    def response_hours(self) -> float | None:
        """Время до ответа в часах."""
        if self.answered_at is None:
            return None
        return (self.answered_at - self.created_at).total_seconds() / 3600

    @property
    def waiting_hours(self) -> float:
        """Сколько часов обращение ждёт ответа."""
        return (timezone.now() - self.created_at).total_seconds() / 3600
