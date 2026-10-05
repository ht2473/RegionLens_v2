"""
Карточка доски: вид холста, построенный заново по параметрам карточки и общему выбору
доски, или ссылка на инструмент анализа с параметрами словами.

Вид холста строится теми же сборщиками, что и рабочая поверхность, — по копии запроса
с параметрами карточки. У карты на странице свои опознаватели контуров: карт на доске
несколько.
"""

from __future__ import annotations

import copy
from typing import Any
from urllib.parse import urlencode

from django.http import Http404, HttpRequest, QueryDict
from django.urls import reverse

from apps.core.citation import series_source
from apps.maps.panel import build as build_map_view
from apps.surface.panels import PANELS_BY_CODE
from apps.surface.state import SurfaceState, resolve_state, state_context
from apps.surface.subject import describe_subject
from apps.warehouse.duckdb_client import WarehouseNotBuiltError
from apps.workspace.constants import QUERY_TARGETS_BY_CODE
from apps.workspace.describe import describe_parameters

from .boards import CANVAS_TARGETS

# Строк рейтинга и таблицы на карточке; остальное — по ссылке «Открыть».
CARD_ROWS = 10


def card_query(
    parameters: dict[str, Any], *, year: int | None, territories: list[str]
) -> QueryDict:
    """Параметры вида карточки с общим годом и территориями доски поверх своих."""
    query = QueryDict(mutable=True)
    for name, value in (parameters or {}).items():
        if isinstance(value, list):
            query.setlist(name, [str(item) for item in value])
        elif value is not None:
            query[name] = str(value)
    if year is not None:
        query["year"] = str(year)
    if territories:
        query.setlist("territory", list(territories))
    return query


def open_url(block: dict[str, Any], query: QueryDict) -> str:
    """Адрес вида карточки целиком — с общим выбором доски."""
    target = QUERY_TARGETS_BY_CODE[block["target"]]
    encoded = query.urlencode()
    return f"{reverse(target.url_name)}?{encoded}" if encoded else reverse(target.url_name)


def render(
    request: HttpRequest,
    block: dict[str, Any],
    *,
    year: int | None,
    territories: list[str],
) -> dict[str, Any]:
    """Контекст карточки: шаблон, заголовок, адрес вида и содержимое."""
    query = card_query(block.get("parameters") or {}, year=year, territories=territories)
    target = block["target"]
    card: dict[str, Any] = {
        "block": block,
        "target": target,
        "target_title": QUERY_TARGETS_BY_CODE[target].title,
        "icon": QUERY_TARGETS_BY_CODE[target].icon,
        "open_url": open_url(block, query),
        "prefix": f"b{block['id']}",
    }
    if target not in CANVAS_TARGETS:
        card.update(
            template="userdata/cards/_tool.html",
            title=block.get("title") or str(QUERY_TARGETS_BY_CODE[target].title),
            phrases=describe_parameters(target, block.get("parameters")),
        )
        return card
    clone = copy.copy(request)
    # Параметры карточки вместо параметров страницы доски; запрос — копия.
    setattr(clone, "GET", query)  # noqa: B010 — у запроса GET объявлен неизменяемым
    try:
        # Ряд удалённой или чужой таблицы холст считает несуществующим (404) — карточка тоже.
        state = resolve_state(clone)
        if state.series is None:
            raise Http404
        card.update(_canvas(clone, state, target, card["prefix"]))
    except Http404, WarehouseNotBuiltError:
        card.update(template="userdata/cards/_missing.html", title=block.get("title", ""))
        return card
    card["title"] = block.get("title") or card["subject_title"]
    return card


def _canvas(request: HttpRequest, state: SurfaceState, target: str, prefix: str) -> dict[str, Any]:
    """Вид холста карточки: те же сборщики, что у рабочей поверхности."""
    assert state.series is not None
    subject = describe_subject(state)
    context: dict[str, Any] = {
        **state_context(state),
        "subject": subject,
        "subject_title": state.series.full_title,
        "unit": subject.unit if subject else "",
        "source": series_source(state.series.key, state.year or ""),
        "template": f"userdata/cards/_{target}.html",
        "rows_limit": CARD_ROWS,
        "precision": subject.item.precision if subject else None,
    }
    if target == "map":
        # Свои опознаватели контуров: карт на доске несколько.
        context.update(build_map_view(request, state, prefix=prefix))
        return context
    context.update(PANELS_BY_CODE[target].builder(request, state))
    if target == "table":
        rows = context.get("table_rows") or []
        if state.codes:
            rows = [row for row in rows if row["selected"]]
        context["card_rows"] = rows
    elif target == "rankings":
        rows = context.get("rows") or []
        chosen = set(state.codes)
        context["card_rows"] = [
            row
            for index, row in enumerate(rows)
            if index < CARD_ROWS or row["territory_code"] in chosen
        ]
    return context


def add_url(target: str, query_string: str) -> str:
    """Адрес формы «На доску» с видом страницы — для ссылок вне формы."""
    return f"{reverse('userdata:board-add')}?{urlencode({'target': target, 'q': query_string})}"
