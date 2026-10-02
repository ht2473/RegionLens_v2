"""Темы и состояния обращений, состояния доставки ответа."""

from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _


class TicketTopic(models.TextChoices):
    """Тема обращения из закрытого перечня."""

    DATA_ERROR = "data_error", _("Ошибка в данных")
    METHOD = "method", _("Вопрос по методике расчёта")
    FEATURE = "feature", _("Предложение по развитию")
    BUG = "bug", _("Неисправность в работе сайта")
    ACCESS = "access", _("Доступ и учётная запись")
    COOPERATION = "cooperation", _("Сотрудничество и использование данных")
    OTHER = "other", _("Другое")


class TicketStatus(models.TextChoices):
    """Состояние обращения; на обращение отвечают один раз, письмом."""

    NEW = "new", _("Ждёт ответа")
    ANSWERED = "answered", _("Отвечено")
    CLOSED = "closed", _("Закрыто без ответа")


class DeliveryStatus(models.TextChoices):
    """Доставка ответа письмом: принял ли его почтовый узел."""

    NONE = "", _("Ответа нет")
    SENT = "sent", _("Доставлено")
    FAILED = "failed", _("Не доставлено")


# Оформление метки доставки.
DELIVERY_BADGE: dict[str, str] = {
    DeliveryStatus.SENT: "badge--positive",
    DeliveryStatus.FAILED: "badge--negative",
}


# Состояния, при которых обращение требует внимания администратора.
OPEN_STATUSES: tuple[str, ...] = (TicketStatus.NEW,)

# Состояния, при которых работа по обращению завершена.
CLOSED_STATUSES: tuple[str, ...] = (TicketStatus.ANSWERED, TicketStatus.CLOSED)

# Соответствие состояния и оформления метки в интерфейсе.
STATUS_BADGE: dict[str, str] = {
    TicketStatus.NEW: "badge--accent",
    TicketStatus.ANSWERED: "badge--positive",
    TicketStatus.CLOSED: "badge--neutral",
}

# Число обращений, принимаемых с одного адреса за час.
RATE_LIMIT_PER_HOUR = 5
