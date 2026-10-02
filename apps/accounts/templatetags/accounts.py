"""Кнопка «Это мой регион» для паспорта и карточки региона."""

from __future__ import annotations

from typing import Any

from django import template

from ..region import my_region

register = template.Library()


@register.inclusion_tag("accounts/partials/_my_region_button.html", takes_context=True)
def my_region_button(context: Any, territory: Any) -> dict[str, Any]:
    """Кнопка выбора региона своим; у выбранного — отметка и сброс."""
    request = context["request"]
    current = my_region(request)
    return {
        "request": request,
        "territory": territory,
        "is_mine": current is not None and current.pk == territory.pk,
        # Во фрагменте HTMX (карточка региона) возврат — на страницу, а не на фрагмент.
        "back": request.headers.get("HX-Current-URL") or request.get_full_path(),
        "csrf_token": context.get("csrf_token", ""),
    }
