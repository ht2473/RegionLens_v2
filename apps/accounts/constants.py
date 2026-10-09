"""Роли (группы с правами на разделы панели) и пределы кабинета."""

from __future__ import annotations

from dataclasses import dataclass

from django.utils.functional import Promise
from django.utils.translation import gettext_lazy as _

# Подпись роли может быть как строкой, так и отложенным переводом.
StrOrPromise = str | Promise

# Права на разделы панели управления; хранятся у служебной модели PanelAccess.
MANAGE_USERS = "manage_users"
MANAGE_DATA = "manage_data"
MANAGE_TICKETS = "manage_tickets"
MANAGE_CONTENT = "manage_content"

PANEL_PERMISSIONS: tuple[tuple[str, str], ...] = (
    (MANAGE_USERS, "Пользователи и роли"),
    (MANAGE_DATA, "Данные и источники"),
    (MANAGE_TICKETS, "Обращения"),
    (MANAGE_CONTENT, "Глоссарий и методика"),
)
PANEL_CODES: tuple[str, ...] = tuple(code for code, _title in PANEL_PERMISSIONS)


def permission(code: str) -> str:
    """Полное имя права для ``has_perm``."""
    return f"accounts.{code}"


@dataclass(frozen=True, slots=True)
class RoleSpec:
    """Роль: группа Django с набором прав панели; имя группы хранится по-русски."""

    name: str
    label: StrOrPromise
    description: StrOrPromise
    permissions: tuple[str, ...]


ROLE_USER = "Пользователь"
ROLE_EDITOR = "Редактор содержимого"
ROLE_ADMIN = "Администратор"

# Порядок — от меньших прав к большим: шапка кабинета называет последнюю из ролей.
ROLES: tuple[RoleSpec, ...] = (
    RoleSpec(
        ROLE_USER,
        _("Пользователь"),
        _("Личный кабинет: мой регион, сохранённое, обращения и настройки."),
        (),
    ),
    RoleSpec(
        ROLE_EDITOR,
        _("Редактор содержимого"),
        _("Панель управления: глоссарий и разделы методики, ответы на обращения."),
        (MANAGE_CONTENT, MANAGE_TICKETS),
    ),
    RoleSpec(
        ROLE_ADMIN,
        _("Администратор"),
        _(
            "Панель управления целиком: пользователи и роли, загрузка данных и источники, "
            "обращения, глоссарий и методика."
        ),
        PANEL_CODES,
    ),
)
ROLES_BY_NAME: dict[str, RoleSpec] = {role.name: role for role in ROLES}

# Роли, назначаемые в панели флажками; «Пользователь» есть у всех.
ASSIGNABLE_ROLES: tuple[RoleSpec, ...] = tuple(role for role in ROLES if role.permissions)


@dataclass(frozen=True, slots=True)
class Rate:
    """Предел частоты: столько раз за окно в секундах."""

    limit: int
    window: int


HOUR = 3600

# Регистрация и восстановление пароля — по адресу отправителя и по адресу почты:
# первый предел сдерживает поток с одного узла, второй — письма на один ящик.
REGISTER_PER_IP = Rate(5, HOUR)
REGISTER_PER_EMAIL = Rate(3, HOUR)
RESET_PER_IP = Rate(10, HOUR)
RESET_PER_EMAIL = Rate(3, HOUR)
# Смена почты — по учётной записи: письмо уходит на адрес, который вводит пользователь.
EMAIL_CHANGE_PER_USER = Rate(3, HOUR)
# Неверный текущий пароль в формах кабинета — по учётной записи: иначе из открытого
# сеанса пароль подбирался бы без предела, django-axes эти формы не видит.
PASSWORD_CHECK_PER_USER = Rate(5, HOUR)
# Код второго шага: попыток на один ввод пароля и всего с адреса.
CODE_ATTEMPTS_PER_LOGIN = 5
CODE_PER_IP = Rate(30, HOUR)
# Сколько ждёт второй шаг после верного пароля.
PENDING_LOGIN_SECONDS = 10 * 60

# Пределы кабинета, одни для всех: защищают общий стенд от исчерпания памяти.
CABINET_LIMITS: dict[str, int] = {
    "saved_queries": 300,
    "favorites": 500,
}
