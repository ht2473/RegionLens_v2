"""Теги кнопок «Сохранить» для страниц каталога, рабочей поверхности и аналитики."""

from __future__ import annotations

from typing import Any

from django import template

from ..services import favorite_state

register = template.Library()


@register.inclusion_tag("workspace/partials/_favorite_button.html", takes_context=True)
def favorite_button(context: Any, kind: str, identifier: str) -> dict[str, Any]:
    """Кнопка отметки избранного: ``indicator``, ``series`` или ``territory`` и их ключ."""
    request = context["request"]
    return {
        "request": request,
        "favorite_kind": kind,
        "favorite_identifier": identifier,
        "favorite_active": favorite_state(request.user, kind, identifier),
        "favorite_back": request.get_full_path(),
        "csrf_token": context.get("csrf_token", ""),
    }


@register.inclusion_tag("workspace/partials/_save_query.html", takes_context=True)
def save_query_button(context: Any, target: str) -> dict[str, Any]:
    """
    Кнопка сохранения состояния страницы в кабинет или на доску; ``target`` — код
    из ``QUERY_TARGETS``.
    """
    from apps.userdata.boards import owned

    request = context["request"]
    return {
        "request": request,
        "save_query_target": target,
        "boards": list(owned(request.user).values("public_id", "title")[:50]),
        "csrf_token": context.get("csrf_token", ""),
    }
