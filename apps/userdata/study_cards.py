"""
Карточка исследования: вид холста, построенный заново по параметрам карточки и общему
выбору исследования, ответ числами (``answers``) или ссылка на инструмент анализа
с параметрами словами.

Вид холста строится теми же сборщиками, что и рабочая поверхность, — по копии запроса
с параметрами карточки. У карты на странице свои опознаватели контуров: карт в исследовании
несколько.
"""

from __future__ import annotations

import copy
from typing import Any

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

from . import answers
from .studies import ANSWER, CANVAS_TARGETS, PAIR_QUESTIONS

# Строк рейтинга и таблицы на карточке; остальное — по ссылке «Открыть».
CARD_ROWS = 10


def card_query(
    parameters: dict[str, Any], *, year: int | None, territories: list[str]
) -> QueryDict:
    """Параметры вида карточки с общим годом и регионами исследования поверх своих."""
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
    """Адрес вида карточки целиком — с общим выбором исследования."""
    target = QUERY_TARGETS_BY_CODE[block["target"]]
    encoded = query.urlencode()
    return f"{reverse(target.url_name)}?{encoded}" if encoded else reverse(target.url_name)


def kind_title(block: dict[str, Any]) -> Any:
    """Вид карточки словами — над её названием."""
    if block["kind"] == ANSWER:
        return answers.TITLES[block["question"]]
    return QUERY_TARGETS_BY_CODE[block["target"]].title


def _state(request: HttpRequest, query: QueryDict) -> tuple[HttpRequest, SurfaceState]:
    """Выбор холста по параметрам карточки; ряд удалённой или чужой таблицы — 404."""
    clone = copy.copy(request)
    # Параметры карточки вместо параметров страницы исследования; запрос — копия.
    setattr(clone, "GET", query)  # noqa: B010 — у запроса GET объявлен неизменяемым
    state = resolve_state(clone)
    if state.series is None:
        raise Http404
    return clone, state


def render(
    request: HttpRequest,
    block: dict[str, Any],
    *,
    year: int | None,
    territories: list[str],
) -> dict[str, Any]:
    """Контекст карточки: шаблон, заголовок, адрес вида и содержимое."""
    if block["kind"] == ANSWER:
        return _answer(request, block, year=year, territories=territories)
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
    try:
        clone, state = _state(request, query)
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
        # Свои опознаватели контуров: карт в исследовании несколько.
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


def _answer(
    request: HttpRequest,
    block: dict[str, Any],
    *,
    year: int | None,
    territories: list[str],
) -> dict[str, Any]:
    """Карточка-ответ числами; ответа нет — причина словами вместо чисел."""
    question = block["question"]
    card: dict[str, Any] = {
        "block": block,
        "target": question,
        "target_title": answers.TITLES[question],
        "icon": answers.ICONS[question],
        "prefix": f"b{block['id']}",
    }
    try:
        _clone, state = _state(
            request, card_query({"series": block["series"]}, year=year, territories=territories)
        )
        subject = describe_subject(state)
        if subject is None or state.year is None:
            raise Http404
        if question in PAIR_QUESTIONS:
            _other_clone, other = _state(
                request, card_query({"series": block["other"]}, year=year, territories=[])
            )
            partner = describe_subject(other)
            if partner is None:
                raise Http404
            built = answers.relation(answers.Asked(request, state, subject), other, partner)
            title = f"{subject.title} — {partner.title}"
        else:
            built = getattr(answers, question)(answers.Asked(request, state, subject))
            title = subject.title
    except Http404, WarehouseNotBuiltError:
        card.update(template="userdata/cards/_missing.html", title=block.get("title", ""))
        return card
    except answers.NoAnswerError as reason:
        card.update(
            template="userdata/cards/_unanswered.html",
            title=block.get("title") or (subject.title if subject is not None else ""),
            reason=str(reason),
        )
        return card
    assert state.series is not None
    card.update(
        built,
        template=f"userdata/cards/_{question}.html",
        title=block.get("title") or title,
        unit=subject.unit,
        source=series_source(state.series.key, built.get("year") or state.year),
    )
    return card
