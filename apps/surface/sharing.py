"""
Ссылка на текущий вид, запрос к программному интерфейсу и выгрузка с холста.

Интерфейс отдаёт наблюдения, а не готовый вид, поэтому рядом названо, чем ответ отличается.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from django.http import HttpRequest
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from apps.api.docs import anonymous_rate
from apps.core.citation import series_source, view_citation
from apps.surface.panels import (
    PANEL_COMPARE,
    PANEL_RANKINGS,
    PANEL_TABLE,
    Panel,
    view_query,
)
from apps.surface.state import SurfaceState, build_query

if TYPE_CHECKING:  # pragma: no cover - только для проверки типов
    from django.utils.functional import _StrOrPromise as Translatable
else:
    Translatable = str


@dataclass(frozen=True, slots=True)
class ApiRequest:
    """Запрос к программному интерфейсу, отвечающий текущему виду."""

    url: str
    title: Translatable
    note: Translatable


def share_context(
    panel: Panel,
    state: SurfaceState,
    request: HttpRequest,
    context: dict[str, Any],
) -> dict[str, Any]:
    """
    Собрать ссылку на текущий вид, запрос к интерфейсу и ссылку для списка литературы.

    Адрес собирается заново: у запроса HTMX тот же путь отдаёт фрагмент без страницы.
    """
    query = view_query(panel, state, request)
    path = f"{panel.url}?{query}" if query else panel.url
    call = api_request(panel, state, request, context)
    view_url = request.build_absolute_uri(path)
    return {
        "view_url": view_url,
        # Адрес запроса — полный, для командной строки и чужого кода.
        "api_request": replace(call, url=request.build_absolute_uri(call.url)) if call else None,
        "citation": view_citation(
            title=state.series.full_title if state.series else str(panel.tab),
            url=view_url,
            year=state.year or "",
            series_key=state.series.key if state.series else "",
        ),
        "api_rate": anonymous_rate(),
        # Строка источника под картинкой карты и графика.
        "image_source": series_source(state.series.key, state.year or "") if state.series else "",
    }


def api_request(
    panel: Panel,
    state: SurfaceState,
    request: HttpRequest,
    context: dict[str, Any],
) -> ApiRequest | None:
    """Составить запрос к интерфейсу, отвечающий показанному на холсте; без ряда — ничего."""
    # Интерфейс отдаёт только открытые данные: у рядов наборов пользователей запроса нет.
    if state.series is None or state.year is None or getattr(state.series, "is_user", False):
        return None

    if panel.code == PANEL_RANKINGS:
        return _ranking_request(state, request, context)
    if panel.code == PANEL_COMPARE:
        return _timeline_request(state)
    return _year_request(panel, state)


def _ranking_request(
    state: SurfaceState, request: HttpRequest, context: dict[str, Any]
) -> ApiRequest:
    """Запрос рейтинга за год, показанный на холсте, а не выбранный в рейле."""
    series = state.series
    year = context.get("ranking_year") or state.year
    pairs = [("series", series.key if series else ""), ("year", str(year))]
    if request.GET.get("order") == "asc":
        pairs.append(("order", "asc"))

    return ApiRequest(
        url=f"{reverse('api:v1:rankings')}?{build_query(pairs)}",
        title=_("Рейтинг за год"),
        note=_(
            "Возвращаются только субъекты, участвующие в ранжировании: те, по которым "
            "значения нет, в ответ не попадают — как и в таблицу рейтинга на экране."
        ),
    )


def _timeline_request(state: SurfaceState) -> ApiRequest:
    """Запрос ряда по годам; территория — только если выбрана ровно одна."""
    series = state.series
    pairs = [("series", series.key if series else "")]
    note: Translatable = _(
        "Возвращаются наблюдения по всем субъектам за все годы порциями по пятьдесят "
        "строк. Линии на экране построены по выбранным территориям, а приведение "
        "к базовому году выполняется страницей, а не интерфейсом."
    )
    if len(state.codes) == 1:
        pairs.append(("territory", state.codes[0]))
        note = _(
            "Возвращается ряд по выбранной территории за все годы. Приведение "
            "к базовому году выполняется страницей, а не интерфейсом."
        )

    return ApiRequest(
        url=f"{reverse('api:v1:observations')}?{build_query(pairs)}",
        title=_("Наблюдения ряда"),
        note=note,
    )


def _year_request(panel: Panel, state: SurfaceState) -> ApiRequest:
    """Запрос среза по субъектам за год — один для карты, распределения и таблицы."""
    series = state.series
    pairs = [
        ("series", series.key if series else ""),
        ("year_from", str(state.year)),
        ("year_to", str(state.year)),
    ]
    note: Translatable = _(
        "Возвращаются значения по субъектам за выбранный год. Разбиение на классы "
        "шкалы выполняется на сервере страницы: интерфейс отдаёт числа, а не раскраску."
    )
    if panel.code == PANEL_TABLE:
        note = _(
            "Возвращаются только субъекты, которые есть в источнике за этот год; остальные "
            "видны в таблице на экране, но в ответе интерфейса не появляются."
        )

    return ApiRequest(
        url=f"{reverse('api:v1:observations')}?{build_query(pairs)}",
        title=_("Наблюдения за год"),
        note=note,
    )
