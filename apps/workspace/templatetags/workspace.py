"""Тег меню «Сохранить» для страниц каталога, рабочей поверхности и аналитики."""

from __future__ import annotations

from typing import Any

from django import template

from ..menu import save_menu_context

register = template.Library()


@register.inclusion_tag("workspace/partials/_save_menu.html", takes_context=True)
def save_menu(context: Any, kind: str, identifier: str, series: str = "") -> dict[str, Any]:
    """
    Меню «Сохранить»: ``view`` и код страницы из ``QUERY_TARGETS`` — вид со строкой запроса,
    ``territory`` и код субъекта, ``indicator`` и адрес показателя с открытым рядом.
    """
    request = context["request"]
    return {
        **save_menu_context(
            request,
            kind,
            identifier,
            series=series or "",
            query_string=request.META.get("QUERY_STRING", ""),
            back=request.get_full_path(),
        ),
        "csrf_token": context.get("csrf_token", ""),
    }
