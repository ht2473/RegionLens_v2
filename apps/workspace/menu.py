"""
Меню «Сохранить» — одно на сайте: вид страницы, регион или показатель.

«В Сохранённое» — одним нажатием (название вида — само); «В исследование» — вид карточкой,
показатель — карточкой карты его ряда, регион — к регионам исследования.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from django.http import HttpRequest

from .constants import QUERY_TARGETS_BY_CODE
from .services import favorite_state, saved_view

# Сколько исследований меню называет: последние изменённые.
STUDIES_LISTED = 6

# Страница, которой показатель встаёт в исследование.
INDICATOR_TARGET = "map"


def save_menu_context(
    request: HttpRequest,
    kind: str,
    identifier: str,
    *,
    series: str = "",
    query_string: str = "",
    back: str = "",
) -> dict[str, Any]:
    """
    Состояние меню: ``kind`` — ``view`` (``identifier`` — код страницы из ``QUERY_TARGETS``),
    ``territory`` (код субъекта) или ``indicator`` (адрес показателя; ``series`` — открытый ряд).
    """
    from apps.userdata.studies import owned

    user = request.user
    context: dict[str, Any] = {
        "request": request,
        "save_kind": kind,
        "save_identifier": identifier,
        "save_series": series,
        "save_query": query_string,
        "save_back": back or request.get_full_path(),
        "saved_title": "",
        "study_target": "",
        "study_query": "",
        "study_territory": "",
    }
    if kind == "view":
        found = saved_view(user, identifier, query_string)
        context["saved"] = found is not None
        context["saved_title"] = found.title if found is not None else ""
        if identifier in QUERY_TARGETS_BY_CODE:
            context["study_target"] = identifier
            context["study_query"] = query_string
    else:
        context["saved"] = favorite_state(user, kind, identifier)
        if kind == "territory":
            context["study_territory"] = identifier
        elif series:
            context["study_target"] = INDICATOR_TARGET
            context["study_query"] = urlencode({"series": series})
    context["studies"] = list(
        owned(request).order_by("-updated_at").values("public_id", "title")[:STUDIES_LISTED]
    )
    return context
