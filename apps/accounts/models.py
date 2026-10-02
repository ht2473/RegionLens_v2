"""Пользователь со входом по адресу почты; роли — группы с правами на разделы панели."""

from __future__ import annotations

from functools import cached_property

from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.utils import timezone
from django.utils.crypto import salted_hmac
from django.utils.translation import gettext_lazy as _

from apps.core.models import PublicIdentifierModel, TimeStampedModel

from .constants import (
    PANEL_CODES,
    PANEL_PERMISSIONS,
    ROLE_USER,
    ROLES,
    ROLES_BY_NAME,
    StrOrPromise,
    permission,
)
from .managers import UserManager

# Минимальное число частей ФИО, при котором имеет смысл строить инициалы.
MIN_NAME_PARTS_FOR_INITIALS = 2


class User(AbstractBaseUser, PermissionsMixin, PublicIdentifierModel, TimeStampedModel):
    """Пользователь, идентифицируемый адресом почты; права панели — через группы."""

    email = models.EmailField(
        _("адрес электронной почты"),
        unique=True,
        max_length=254,
        help_text=_("Используется для входа на сайт и служебных писем"),
    )
    full_name = models.CharField(
        _("фамилия, имя, отчество"),
        max_length=180,
        help_text=_("Отображается в интерфейсе и в колонтитулах выгружаемых отчётов"),
    )
    region = models.ForeignKey(
        "catalog.Territory",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_("мой регион"),
        help_text=_("С него открываются главная страница и кабинет"),
    )

    is_active = models.BooleanField(
        "учётная запись активна",
        default=True,
        help_text="Снятие признака блокирует вход без удаления данных пользователя",
    )

    last_seen_at = models.DateTimeField("последняя активность", null=True, blank=True)
    # Граница «нового по сохранённому»: выпуски, полученные позже, — новые.
    updates_seen_at = models.DateTimeField("новое просмотрено", null=True, blank=True)

    # Входит в хэш сеанса: увеличение завершает все сеансы, кроме пересчитанного.
    session_version = models.PositiveIntegerField("версия сеансов", default=0)
    pending_email = models.EmailField("новый адрес, ждёт подтверждения", max_length=254, blank=True)

    # Согласие на обработку персональных данных: доказать его получение должен оператор.
    consent_at = models.DateTimeField("согласие на обработку дано", null=True, blank=True)
    consent_revision = models.DateField("редакция согласия", null=True, blank=True)

    # Второй фактор входа — одноразовые коды по времени (RFC 6238).
    totp_secret = models.CharField("ключ кодов входа", max_length=64, blank=True)
    totp_enabled_at = models.DateTimeField("вход с кодом включён", null=True, blank=True)
    # Последний принятый шаг времени: тот же код второй раз не принимается.
    totp_last_step = models.BigIntegerField("последний принятый шаг", default=0)
    recovery_codes = models.JSONField("резервные коды (хэши)", default=list, blank=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["full_name"]

    class Meta:
        verbose_name = "пользователь"
        verbose_name_plural = "пользователи"
        ordering = ["full_name"]

    def __str__(self) -> str:
        return f"{self.full_name} <{self.email}>"

    # --- Представление имени --------------------------------------------------------------

    def get_full_name(self) -> str:
        """Полное имя пользователя."""
        return self.full_name

    def get_short_name(self) -> str:
        """Сокращённое имя вида «Иванов И. И.»."""
        parts = self.full_name.split()
        if len(parts) >= MIN_NAME_PARTS_FOR_INITIALS:
            initials = " ".join(f"{part[0]}." for part in parts[1:3])
            return f"{parts[0]} {initials}"
        return self.full_name

    # --- Сеансы ----------------------------------------------------------------------------

    def _get_session_auth_hash(self, secret: str | None = None) -> str:
        """Хэш сеанса от пароля и версии сеансов: смена любого из них завершает сеансы."""
        key_salt = "apps.accounts.models.User.get_session_auth_hash"
        return salted_hmac(
            key_salt,
            f"{self.password}:{self.session_version}",
            secret=secret,
            algorithm="sha256",
        ).hexdigest()

    # --- Права и роли ----------------------------------------------------------------------

    @cached_property
    def panel_permissions(self) -> frozenset[str]:
        """Коды прав панели, которые есть у пользователя."""
        if not self.is_active:
            return frozenset()
        return frozenset(code for code in PANEL_CODES if self.has_perm(permission(code)))

    @property
    def has_panel_access(self) -> bool:
        """Открыт хотя бы один раздел панели управления."""
        return bool(self.panel_permissions)

    @property
    def role_names(self) -> list[str]:
        """Имена ролей пользователя в порядке ``ROLES``; без групп — «Пользователь»."""
        if self.pk is None:
            return [ROLE_USER]
        # Через all(): перечень панели заранее выбирает группы одним запросом.
        own = {group.name for group in self.groups.all()}
        names = [role.name for role in ROLES if role.name in own and role.permissions]
        return names or [ROLE_USER]

    @property
    def role_labels(self) -> list[StrOrPromise]:
        """Роли словами."""
        return [ROLES_BY_NAME[name].label for name in self.role_names]

    @property
    def two_factor_enabled(self) -> bool:
        """Вход требует одноразового кода."""
        return self.totp_enabled_at is not None and bool(self.totp_secret)

    @property
    def has_current_consent(self) -> bool:
        """Согласие на обработку дано на действующую редакцию документа."""
        from apps.core.documents import CONSENT_REVISION

        return self.consent_at is not None and self.consent_revision == CONSENT_REVISION

    def record_consent(self) -> None:
        """Записать согласие на действующую редакцию документа."""
        from apps.core.documents import CONSENT_REVISION

        self.consent_at = timezone.now()
        self.consent_revision = CONSENT_REVISION
        self.save(update_fields=["consent_at", "consent_revision"])

    def touch(self) -> None:
        """Обновить отметку последней активности без изменения прочих полей."""
        self.last_seen_at = timezone.now()
        self.save(update_fields=["last_seen_at"])


class PanelAccess(models.Model):
    """Права на разделы панели управления; у модели нет таблицы, только права."""

    class Meta:
        managed = False
        default_permissions = ()
        permissions = list(PANEL_PERMISSIONS)
        verbose_name = "доступ к панели"

    def __str__(self) -> str:
        return "доступ к панели"
