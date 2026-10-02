"""Группы ролей с правами панели: заводятся после миграций, назначаются в панели."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django.contrib.auth.models import Group, Permission
from django.db import transaction
from django.db.models import Q, QuerySet

from .constants import MANAGE_USERS, PANEL_CODES, ROLE_ADMIN, ROLE_USER, ROLES
from .models import User


class LastAdministratorError(Exception):
    """Действие оставило бы систему без администратора."""


def ensure_roles(**_kwargs: Any) -> None:
    """Завести группы ролей и выставить им права; повторный вызов ничего не меняет."""
    found = {
        item.codename: item
        for item in Permission.objects.filter(
            content_type__app_label="accounts", codename__in=PANEL_CODES
        )
    }
    if len(found) < len(PANEL_CODES):
        # Права создаёт сигнал приложения auth; без них роли собирать рано.
        return
    for role in ROLES:
        group, _created = Group.objects.get_or_create(name=role.name)
        group.permissions.set([found[code] for code in role.permissions])


def with_permission(code: str) -> QuerySet[User]:
    """Действующие учётные записи с правом панели: через группу, лично, суперпользователь."""
    return (
        User.objects.filter(is_active=True)
        .filter(
            Q(is_superuser=True)
            | Q(
                groups__permissions__codename=code,
                groups__permissions__content_type__app_label="accounts",
            )
            | Q(
                user_permissions__codename=code,
                user_permissions__content_type__app_label="accounts",
            )
        )
        .distinct()
    )


def administrators() -> QuerySet[User]:
    """Учётные записи, способные назначать роли."""
    return with_permission(MANAGE_USERS)


def is_last_administrator(user: User) -> bool:
    """Пользователь — единственный, кто может назначать роли."""
    if MANAGE_USERS not in user.panel_permissions:
        return False
    return not administrators().exclude(pk=user.pk).exists()


@transaction.atomic
def set_roles(user: User, names: Iterable[str]) -> None:
    """Назначить роли из ``ROLES``; последнего администратора роль «Администратор» не покидает."""
    wanted = {name for name in names if name in {role.name for role in ROLES}} | {ROLE_USER}
    if ROLE_ADMIN not in wanted and not user.is_superuser and is_last_administrator(user):
        raise LastAdministratorError
    groups = [Group.objects.get_or_create(name=name)[0] for name in sorted(wanted)]
    user.groups.set(groups)
    # Права пользователя закэшированы на объекте.
    for attribute in ("_perm_cache", "_group_perm_cache", "_user_perm_cache", "panel_permissions"):
        user.__dict__.pop(attribute, None)
