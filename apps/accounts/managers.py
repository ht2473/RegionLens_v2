"""Менеджеры модели пользователя."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.contrib.auth.models import BaseUserManager, Group

from .constants import ROLE_ADMIN, ROLE_USER

if TYPE_CHECKING:  # pragma: no cover - только для проверки типов
    from .models import User


# Параметр модели — чтобы mypy видел методы пользователя.
class UserManager(BaseUserManager["User"]):
    """Менеджер пользователей, идентифицируемых адресом электронной почты, а не ``username``."""

    use_in_migrations = True

    def _create_user(
        self, email: str, password: str | None, roles: tuple[str, ...], **extra: Any
    ) -> User:
        """Общая часть создания пользователя: адрес, пароль и роли."""
        if not email:
            raise ValueError("Адрес электронной почты обязателен для создания пользователя")

        email = self.normalize_email(email).lower()
        user = self.model(email=email, **extra)
        user.set_password(password)
        user.full_clean(exclude=["password"])
        user.save(using=self._db)
        # Права ролей проставляет accounts.roles.ensure_roles после миграций.
        user.groups.add(*(Group.objects.get_or_create(name=name)[0] for name in roles))
        return user

    def create_user(
        self,
        email: str,
        password: str | None = None,
        *,
        roles: tuple[str, ...] = (),
        **extra: Any,
    ) -> User:
        """Создать пользователя; ``roles`` — имена ролей сверх «Пользователя»."""
        extra.setdefault("is_superuser", False)
        return self._create_user(email, password, (ROLE_USER, *roles), **extra)

    def create_superuser(self, email: str, password: str | None = None, **extra: Any) -> User:
        """Создать администратора системы."""
        extra.setdefault("is_superuser", True)

        if extra["is_superuser"] is not True:
            raise ValueError("Суперпользователь должен иметь признак is_superuser=True")

        return self._create_user(email, password, (ROLE_USER, ROLE_ADMIN), **extra)

    def get_by_natural_key(self, username: str | None) -> User:
        """Поиск пользователя по адресу без учёта регистра."""
        return self.get(email__iexact=username)
