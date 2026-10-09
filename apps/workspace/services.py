"""Действия кабинета: сохранение вида экрана, постановка и снятие отметки избранного."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import parse_qsl
from uuid import UUID

from apps.accounts.models import User
from apps.accounts.permissions import ensure_quota
from apps.catalog.models import Indicator, Series, Territory

from .constants import QUERY_TARGETS_BY_CODE
from .models import Favorite, SavedQuery

# Параметры способа обращения, а не состояния страницы.
IGNORED_PARAMETERS = frozenset(
    {"csrfmiddlewaretoken", "page", "notoolbar", "format", "_", "hx", "next"}
)

# Ограничения размера сохраняемых параметров: строка запроса приходит от клиента.
MAX_PARAMETERS = 40
MAX_VALUES_PER_PARAMETER = 20
MAX_VALUE_LENGTH = 200

# Длина названия и пометки вида; сколько параметров словами идёт в название, данное само.
TITLE_LENGTH = 160
DESCRIPTION_LENGTH = 500
TITLE_PHRASES = 2

# Последнее убранное из «Сохранённого» — в сеансе, для «Вернуть».
REMOVED_SESSION_KEY = "workspace:removed"


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


def auto_title(target: str, parameters: dict[str, Any]) -> str:
    """Название вида, данное само: страница и первые параметры словами через «·»."""
    from .describe import describe_parameters

    info = QUERY_TARGETS_BY_CODE.get(target)
    parts = [str(info.title) if info else target]
    parts.extend(describe_parameters(target, parameters)[:TITLE_PHRASES])
    return " · ".join(parts)[:TITLE_LENGTH]


def free_title(user: User, title: str, *, exclude: int | None = None) -> str:
    """Название, свободное у пользователя: занятое получает номер — «… (2)»."""
    title = title.strip()[:TITLE_LENGTH]
    others = SavedQuery.objects.filter(user=user)
    if exclude is not None:
        others = others.exclude(pk=exclude)
    taken = set(others.values_list("title", flat=True))
    if title not in taken:
        return title
    number = 2
    while True:
        suffix = f" ({number})"
        candidate = title[: TITLE_LENGTH - len(suffix)] + suffix
        if candidate not in taken:
            return candidate
        number += 1


def saved_view(user: Any, target: str, query_string: str) -> SavedQuery | None:
    """Тот же вид в «Сохранённом»: та же страница с теми же параметрами."""
    if not getattr(user, "is_authenticated", False):
        return None
    return SavedQuery.objects.filter(
        user=user, target=target, parameters=parse_query_string(query_string)
    ).first()


def save_view(user: User, *, target: str, query_string: str) -> tuple[SavedQuery, bool]:
    """
    «В Сохранённое» одним нажатием: название — само, переименовать можно в «Сохранённом».

    Тот же вид второй раз не сохраняется; второе значение — сохранён ли он сейчас.
    """
    existing = saved_view(user, target, query_string)
    if existing is not None:
        return existing, False
    title = free_title(user, auto_title(target, parse_query_string(query_string)))
    return create_saved_query(user, title=title, target=target, query_string=query_string), True


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
# Убрать и вернуть
# ---------------------------------------------------------------------------------------


def forget_query(query: SavedQuery) -> dict[str, Any]:
    """Удалить вид; вернуть всё, что нужно, чтобы он вернулся тем же."""
    payload = {
        "kind": "query",
        "title": query.title,
        "description": query.description,
        "target": query.target,
        "parameters": query.parameters,
        "public_id": str(query.public_id),
        "created_at": query.created_at.isoformat(),
        "updated_at": query.updated_at.isoformat(),
        "opened_count": query.opened_count,
        "last_opened_at": query.last_opened_at.isoformat() if query.last_opened_at else None,
    }
    query.delete()
    return payload


def forget_favorite(favorite: Favorite) -> dict[str, Any]:
    """Снять отметку; вернуть всё, что нужно, чтобы она вернулась той же."""
    identifier = (
        favorite.indicator.slug
        if favorite.indicator is not None
        else favorite.series.key
        if favorite.series is not None
        else favorite.territory.code
        if favorite.territory is not None
        else ""
    )
    payload = {
        "kind": "favorite",
        "object": favorite.kind,
        "identifier": identifier,
        "title": mark_name(favorite),
        "note": favorite.note,
        "created_at": favorite.created_at.isoformat(),
        "updated_at": favorite.updated_at.isoformat(),
    }
    favorite.delete()
    return payload


def restore(user: User, payload: dict[str, Any]) -> str:
    """Вернуть убранное; ответ — название для сообщения, пустое — возвращать нечего."""
    if payload.get("kind") == "query":
        return _restore_query(user, payload)
    if payload.get("kind") == "favorite":
        return _restore_favorite(user, payload)
    return ""


def _restore_query(user: User, payload: dict[str, Any]) -> str:
    """Вернуть вид с прежним адресом, счётчиком открытий и датами."""
    public_id = UUID(str(payload["public_id"]))
    found = SavedQuery.objects.filter(public_id=public_id).first()
    if found is not None:
        return found.title if found.user_id == user.pk else ""
    ensure_quota("saved_queries", SavedQuery.objects.filter(user=user).count())
    query = SavedQuery.objects.create(
        user=user,
        public_id=public_id,
        title=free_title(user, str(payload["title"])),
        description=str(payload.get("description", "")),
        target=str(payload["target"]),
        parameters=payload.get("parameters") or {},
        opened_count=int(payload.get("opened_count") or 0),
        last_opened_at=_moment(payload.get("last_opened_at")),
    )
    # Даты — прежние: место в перечне не меняется.
    SavedQuery.objects.filter(pk=query.pk).update(
        created_at=_moment(payload["created_at"]), updated_at=_moment(payload["updated_at"])
    )
    return query.title


def _restore_favorite(user: User, payload: dict[str, Any]) -> str:
    """Вернуть отметку с пометкой и датами; объекта больше нет — возвращать нечего."""
    kind = str(payload.get("object", ""))
    if kind not in ("indicator", "series", "territory"):
        return ""
    target = resolve_favorite_object(kind, str(payload.get("identifier", "")))
    if target is None:
        return ""
    existing = Favorite.objects.filter(user=user, **{kind: target}).first()
    if existing is not None:
        return mark_name(existing)
    ensure_quota("favorites", Favorite.objects.filter(user=user).count())
    favorite = Favorite.objects.create(
        user=user, note=str(payload.get("note", "")), **{kind: target}
    )
    Favorite.objects.filter(pk=favorite.pk).update(
        created_at=_moment(payload["created_at"]), updated_at=_moment(payload["updated_at"])
    )
    return mark_name(favorite)


def mark_name(favorite: Favorite) -> str:
    """Имя отметки для плитки и сообщений: показатель — наименованием без кода."""
    return favorite.indicator.name if favorite.indicator is not None else favorite.title


def _moment(value: Any) -> datetime | None:
    """Момент из строки ISO в сеансе."""
    return datetime.fromisoformat(value) if value else None


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
