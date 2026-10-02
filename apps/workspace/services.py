"""Действия кабинета: сохранение вида экрана, постановка и снятие отметки избранного."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl

from apps.accounts.models import User
from apps.accounts.permissions import ensure_quota
from apps.catalog.models import Indicator, Series, Territory

from .models import Favorite, SavedQuery

# Параметры способа обращения, а не состояния страницы.
IGNORED_PARAMETERS = frozenset(
    {"csrfmiddlewaretoken", "page", "notoolbar", "format", "_", "hx", "next"}
)

# Ограничения размера сохраняемых параметров: строка запроса приходит от клиента.
MAX_PARAMETERS = 40
MAX_VALUES_PER_PARAMETER = 20
MAX_VALUE_LENGTH = 200


def parse_query_string(query_string: str) -> dict[str, Any]:
    """Разобрать строку запроса страницы; повторяющиеся имена — списком в исходном порядке."""
    parameters: dict[str, Any] = {}

    for key, value in parse_qsl(query_string.lstrip("?"), keep_blank_values=False):
        if key in IGNORED_PARAMETERS or len(parameters) >= MAX_PARAMETERS:
            continue

        trimmed = value[:MAX_VALUE_LENGTH]
        if key not in parameters:
            parameters[key] = trimmed
            continue

        current = parameters[key]
        if not isinstance(current, list):
            current = [current]
            parameters[key] = current
        if len(current) < MAX_VALUES_PER_PARAMETER:
            current.append(trimmed)

    return parameters


# ---------------------------------------------------------------------------------------
# Сохранённые выборки
# ---------------------------------------------------------------------------------------


def create_saved_query(
    user: User,
    *,
    title: str,
    target: str,
    query_string: str,
    description: str = "",
) -> SavedQuery:
    """Сохранить состояние страницы расчёта как именованную выборку."""
    ensure_quota("saved_queries", SavedQuery.objects.filter(user=user).count())

    query = SavedQuery.objects.create(
        user=user,
        title=title.strip(),
        description=description.strip(),
        target=target,
        parameters=parse_query_string(query_string),
    )
    return query


# ---------------------------------------------------------------------------------------
# Избранное
# ---------------------------------------------------------------------------------------


def resolve_favorite_object(kind: str, identifier: str) -> Any:
    """Найти объект избранного по виду и адресному идентификатору."""
    if kind == "indicator":
        return Indicator.objects.filter(slug=identifier).first()
    if kind == "series":
        return Series.objects.filter(key=identifier).first()
    return Territory.objects.filter(code=identifier).first()


def toggle_favorite(
    user: User,
    *,
    kind: str,
    identifier: str,
    note: str = "",
) -> tuple[Favorite | None, bool]:
    """Поставить или снять отметку избранного; вернуть запись и признак добавления."""
    target = resolve_favorite_object(kind, identifier)
    if target is None:
        return None, False

    field = {"indicator": "indicator", "series": "series", "territory": "territory"}[kind]
    existing = Favorite.objects.filter(user=user, **{field: target}).first()
    if existing is not None:
        existing.delete()
        return None, False

    ensure_quota("favorites", Favorite.objects.filter(user=user).count())
    favorite = Favorite.objects.create(user=user, note=note.strip(), **{field: target})
    return favorite, True


def favorite_state(user: Any, kind: str, identifier: str) -> bool:
    """Проверить, отмечен ли объект избранным у пользователя."""
    if not getattr(user, "is_authenticated", False):
        return False

    target = resolve_favorite_object(kind, identifier)
    if target is None:
        return False
    return Favorite.objects.filter(user=user, **{kind: target}).exists()
