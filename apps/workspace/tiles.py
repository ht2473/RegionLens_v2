"""
Плитки сохранённого: вид экрана, регион, показатель, исследование, своя таблица.

Одна плитка на всё — в «Сохранённом» (с правкой на месте) и в «Продолжить» обзора:
миниатюра, вид, название, показатель, параметры словами.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from django.urls import reverse
from django.utils.translation import gettext, gettext_lazy

from .constants import QUERY_TARGETS, QUERY_TARGETS_BY_CODE
from .describe import parameter_parts
from .models import Favorite, SavedQuery
from .services import mark_name
from .thumbs import Thumb, icon_thumb, series_thumb, territory_thumb, view_thumb

# Отбор «Сохранённого»: виды в порядке перечня — регионы, показатели, затем страницы.
KIND_TERRITORY = "territory"
KIND_INDICATOR = "indicator"
KIND_LABELS: dict[str, Any] = {
    KIND_TERRITORY: gettext_lazy("Регионы"),
    KIND_INDICATOR: gettext_lazy("Показатели"),
    **{target.code: target.title for target in QUERY_TARGETS},
}


@dataclass(slots=True)
class Tile:
    """Плитка: что открыть, как выглядит и что с ней можно сделать."""

    kind: str
    kind_label: str
    icon: str
    title: str
    url: str
    thumb: Thumb
    moment: datetime
    subtitle: str = ""
    chips: list[str] = field(default_factory=list)
    meta: str = ""
    # Правка на месте: адрес и имя поля пометки, адрес названия (у вида).
    note: str = ""
    note_url: str = ""
    note_name: str = ""
    rename_url: str = ""
    # Без сценариев название правится на отдельной странице.
    edit_url: str = ""
    delete_url: str = ""
    dom_id: str = ""
    vanished: tuple[str, ...] = ()


def _date(moment: datetime | None) -> str:
    return moment.strftime("%d.%m.%Y") if moment else ""


def query_tile(query: SavedQuery) -> Tile:
    """Плитка сохранённого вида экрана."""
    info = query.target_info
    icon = info.icon if info else "activity"
    what, rest = parameter_parts(query.target, query.parameters)
    # Название, данное само, уже называет показатель и первые параметры — без повтора.
    what = [phrase for phrase in what if phrase not in query.title]
    rest = [phrase for phrase in rest if phrase not in query.title]
    if query.last_opened_at:
        meta = gettext("открыт %(date)s") % {"date": _date(query.last_opened_at)}
    else:
        meta = gettext("сохранён %(date)s") % {"date": _date(query.created_at)}
    return Tile(
        kind=query.target,
        kind_label=str(info.title) if info else query.target,
        icon=icon,
        title=query.title,
        url=reverse("workspace:query-open", kwargs={"public_id": query.public_id}),
        thumb=view_thumb(query.target, query.parameters, icon),
        moment=query.last_opened_at or query.updated_at,
        subtitle=" · ".join(what),
        chips=rest,
        meta=meta,
        note=query.description,
        note_url=reverse("workspace:query-change", kwargs={"public_id": query.public_id}),
        note_name="description",
        rename_url=reverse("workspace:query-change", kwargs={"public_id": query.public_id}),
        edit_url=reverse("workspace:query-edit", kwargs={"public_id": query.public_id}),
        delete_url=reverse("workspace:query-delete", kwargs={"public_id": query.public_id}),
        dom_id=f"view-{query.public_id}",
        vanished=query.vanished,
    )


def favorite_tile(favorite: Favorite) -> Tile:
    """Плитка отмеченного региона или показателя."""
    common: dict[str, Any] = {
        "title": favorite.title,
        "url": favorite.url,
        "moment": favorite.updated_at,
        "meta": gettext("сохранён %(date)s") % {"date": _date(favorite.created_at)},
        "note": favorite.note,
        "note_url": reverse("workspace:favorite-note", kwargs={"pk": favorite.pk}),
        "note_name": "note",
        "delete_url": reverse("workspace:favorite-delete", kwargs={"pk": favorite.pk}),
        "dom_id": f"mark-{favorite.pk}",
    }
    if favorite.territory is not None:
        parent = favorite.territory.parent
        return Tile(
            kind=KIND_TERRITORY,
            kind_label=gettext("Регион"),
            icon="map-pin",
            thumb=territory_thumb(favorite.territory.code),
            subtitle=parent.name if parent is not None else "",
            **common,
        )
    series = favorite.series or _main_series(favorite)
    full = mark_name(favorite)
    short = _short_title(series) or full
    common["title"] = short
    return Tile(
        kind=KIND_INDICATOR,
        kind_label=gettext("Показатель"),
        icon="activity",
        thumb=series_thumb(series.key) if series is not None else icon_thumb("activity"),
        subtitle=full if full != short else "",
        **common,
    )


def _short_title(series: Any) -> str:
    """Краткое имя ряда — как в основном наборе; без склада — пусто."""
    from apps.catalog.indicator import describe_series
    from apps.warehouse.duckdb_client import WarehouseNotBuiltError

    if series is None:
        return ""
    try:
        return describe_series(series).short_title
    except WarehouseNotBuiltError:
        return ""


def _main_series(favorite: Favorite) -> Any:
    """Ряд, которым открывается показатель: с наибольшим охватом субъектов."""
    if favorite.indicator is None:
        return None
    return favorite.indicator.series.order_by("-region_coverage", "name_ru").first()


def study_tile(study: Any) -> Tile:
    """Плитка исследования: миниатюра — первая карточка вида или ответа."""
    from apps.userdata.studies import ANSWER, VIEW, blocks_of

    blocks = blocks_of(study)
    thumb = icon_thumb("multiples")
    for block in blocks:
        if block["kind"] == VIEW:
            info = QUERY_TARGETS_BY_CODE.get(block["target"])
            thumb = view_thumb(block["target"], block["parameters"], info.icon if info else "map")
            break
        if block["kind"] == ANSWER and block.get("series"):
            thumb = series_thumb(block["series"])
            break
    return Tile(
        kind="study",
        kind_label=gettext("Исследование"),
        icon="multiples",
        title=study.title,
        url=reverse("userdata:study", args=[study.public_id]),
        thumb=thumb,
        moment=study.updated_at,
        subtitle=gettext("карточек и заметок: %(count)s") % {"count": len(blocks)},
        meta=gettext("изменено %(date)s") % {"date": _date(study.updated_at)},
    )


def dataset_tile(dataset: Any) -> Tile:
    """Плитка своей таблицы: миниатюра — картограмма первого ряда."""
    from apps.warehouse.routing import USER_PREFIX

    version = dataset.current_version
    first = version.series.order_by("order").first() if version is not None else None
    built = version is not None and version.state == "built"
    thumb = (
        series_thumb(f"{USER_PREFIX}{dataset.code}:{first.code}", "table")
        if built and first is not None
        else icon_thumb("table")
    )
    subtitle = (
        gettext("рядов: %(count)s") % {"count": version.series_count}
        if built
        else gettext("не собрана")
    )
    return Tile(
        kind="dataset",
        kind_label=gettext("Своя таблица"),
        icon="table",
        title=dataset.title,
        url=reverse("userdata:dataset", args=[dataset.public_id]),
        thumb=thumb,
        moment=dataset.updated_at,
        subtitle=subtitle,
        meta=gettext("изменена %(date)s") % {"date": _date(dataset.updated_at)},
    )
