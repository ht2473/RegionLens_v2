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
import dataclasses
from typing import Any

from django.http import Http404, HttpRequest, QueryDict
from django.urls import reverse
from django.utils.translation import gettext as _

from apps.core.citation import series_source
from apps.core.templatetags.formatting import ru_number
from apps.maps.panel import MARKS_AUTO, MARKS_SYMBOLS
from apps.maps.panel import build as build_map_view
from apps.surface.panels import PANELS_BY_CODE
from apps.surface.state import SurfaceState, resolve_state, state_context
from apps.surface.subject import describe_subject
from apps.warehouse.duckdb_client import WarehouseNotBuiltError
from apps.workspace.constants import QUERY_TARGETS_BY_CODE
from apps.workspace.describe import describe_parameters

from . import answers, operations, semantics, via
from .studies import ANSWER, CANVAS_TARGETS, PAIR_QUESTIONS, VIEW

# Строк рейтинга и таблицы на карточке; остальное — по ссылке «Открыть».
CARD_ROWS = 10
# Карточки без содержимого: ряда нет или ответа нет — подсказкам «Дальше» там не место.
EMPTY_TEMPLATES = ("userdata/cards/_missing.html", "userdata/cards/_unanswered.html")
# Ступеней шкалы у раскрашенной таблицы — как было у тепловой таблицы.
TABLE_CLASSES = 5


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
        card.update(_canvas(clone, state, block, card["prefix"]))
    except Http404, WarehouseNotBuiltError:
        card.update(template="userdata/cards/_missing.html", title=block.get("title", ""))
        return card
    card["title"] = block.get("title") or card["subject_title"]
    if card.get("marks") == MARKS_SYMBOLS and "marks" not in query:
        # Значки выбраны по виду величины: вид целиком открывается таким же.
        query["marks"] = MARKS_SYMBOLS
        card["open_url"] = open_url(block, query)
    return card


def _canvas(
    request: HttpRequest, state: SurfaceState, block: dict[str, Any], prefix: str
) -> dict[str, Any]:
    """Вид холста карточки: те же сборщики, что у рабочей поверхности."""
    assert state.series is not None
    target = block["target"]
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
        # Свои опознаватели контуров: карт в исследовании несколько. Знаки — по виду величины:
        # у суммы значки, у относительной величины заливка; выбор карточки — в её параметрах.
        context.update(build_map_view(request, state, prefix=prefix, marks=MARKS_AUTO))
        # «Без подписей значений» — карта без чисел у отмеченных регионов.
        context["plain"] = block.get("labels") == "off"
        return context
    context.update(PANELS_BY_CODE[target].builder(request, state))
    if target == "table":
        rows = context.get("table_rows") or []
        context["colour"] = block.get("colour", "")
        if context["colour"]:
            context["table_legend"] = _colour_table(rows, context["precision"])
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


def settings_of(
    block: dict[str, Any], card: dict[str, Any], *, common_year: int | None
) -> dict[str, Any]:
    """
    Настройки карточки для её «Вида»: что уместно виду и что выбрано сейчас. Годы — ряда
    карточки; у линий по годам года нет: на них все годы.
    """
    from apps.catalog.selectors import MAX_COMPARE, grouped_territories
    from apps.maps.classification import (
        DEFAULT_CLASSES,
        DEFAULT_METHOD,
        MAX_CLASSES,
        METHODS,
        MIN_CLASSES,
    )
    from apps.warehouse.queries import year_counts

    from .studies import series_keys

    target = card["target"]
    if block["kind"] == VIEW and target not in CANVAS_TARGETS:
        # Ссылка на инструмент анализа: её вид настраивается в самом инструменте.
        return {}
    parameters = block.get("parameters") or {}
    keys = series_keys(block)
    years: list[int] = []
    if keys and target != "compare":
        try:
            years = sorted((int(year) for year in year_counts(keys[0])), reverse=True)
        except Http404, WarehouseNotBuiltError:
            years = []
    own = block.get("territories")
    regions = "common" if own is None else ("own" if own else "none")
    classes = str(parameters.get("classes", DEFAULT_CLASSES))
    return {
        "views": (
            [(code, QUERY_TARGETS_BY_CODE[code].title) for code in CANVAS_TARGETS]
            if block["kind"] == VIEW and target in CANVAS_TARGETS
            else []
        ),
        "years": years,
        "year": block.get("year"),
        "common_year": common_year,
        "regions": regions,
        "territories": block.get("territories") or [],
        "territory_groups": grouped_territories(),
        "max_compare": MAX_COMPARE,
        "marks": parameters.get("marks", "auto") if target == "map" else "",
        "scale": target in {"map", "distribution"},
        "methods": METHODS,
        "method": parameters.get("method", DEFAULT_METHOD),
        "classes": range(MIN_CLASSES, MAX_CLASSES + 1),
        "class_count": int(classes) if classes.isdigit() else DEFAULT_CLASSES,
        "labels": block.get("labels", "") if target == "map" else None,
        "colour": block.get("colour", "") if target == "table" else None,
        "multiples": block.get("scale", "common") if target == "multiples" else "",
    }


def _colour_table(rows: list[dict[str, Any]], precision: int | None) -> list[dict[str, str]]:
    """
    Раскрасить клетки таблицы по годам ступенью общей шкалы (квантили по всем субъектам,
    а не только отмеченным); вернуть легенду. Нуль и пропуск — без цвета: иначе таблица
    с нулями заливает ими нижнюю ступень, и раскраска становится шумом.
    """
    from apps.maps.classification import classify, sequential_palette

    values = [float(cell["value"]) for row in rows for cell in row["cells"] if cell["value"]]
    classification = classify(values, class_count=TABLE_CLASSES)
    if classification is None:
        return []
    palette = sequential_palette(classification.class_count)
    for row in rows:
        for cell in row["cells"]:
            index = classification.class_of(float(cell["value"])) if cell["value"] else None
            if index is None:
                continue
            # Ступень шкалы (--scale-seq-N); на двух верхних, тёмных, подпись светлая.
            cell["step"] = palette[index].rsplit("-", 1)[1]
            cell["light"] = index >= classification.class_count - 2
    return [
        {
            "step": palette[index].rsplit("-", 1)[1],
            "lower": ru_number(item["lower"], precision),
            "upper": ru_number(item["upper"], precision),
        }
        for index, item in enumerate(classification.intervals())
    ]


def _refuse(question: str, keys: list[str]) -> None:
    """
    Ответа нет по типу ряда — с той же причиной, что у пункта окна «Что показать». Условия
    данных проверяет сам ответ.
    """
    action = f"answer:{question}"
    if action not in semantics.RULES:
        return
    for key in keys:
        info = operations.info_of(key)
        if info is None:
            continue
        verdict = semantics.verdict(action, info.value, data=False)
        if not verdict:
            raise answers.NoAnswerError(semantics.sentence(verdict.reason))


def _country_year(state: SurfaceState) -> SurfaceState:
    """Год ответа у показателя только по России — последний год ряда; иначе — нет ряда."""
    assert state.series is not None
    info = operations.info_of(state.series.key)
    if info is None or info.last_year is None or info.regions:
        raise Http404
    return dataclasses.replace(state, year=info.last_year)


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
    action = f"answer:{question}"
    # Сумма — по собранному пересчёту на жителя (на км²): ключ ряда и пометка на карточке.
    own, own_via = via.through(action, block["series"])
    other_key, other_via = (
        via.through(action, block["other"]) if question in PAIR_QUESTIONS else ("", "")
    )
    try:
        _clone, state = _state(
            request, card_query({"series": own}, year=year, territories=territories)
        )
        subject = describe_subject(state)
        if subject is None or state.series is None:
            raise Http404
        _refuse(question, [state.series.key, *([other_key] if question in PAIR_QUESTIONS else [])])
        if state.year is None:
            # Лет со значениями регионов нет: ответ «по России» — за последний год ряда.
            state = _country_year(state)
        if question in PAIR_QUESTIONS:
            _other_clone, other = _state(
                request, card_query({"series": other_key}, year=year, territories=[])
            )
            partner = describe_subject(other)
            if partner is None:
                raise Http404
            built = getattr(answers, question)(
                answers.Asked(request, state, subject), other, partner
            )
            title = f"{subject.title} — {partner.title}"
        else:
            built = getattr(answers, question)(answers.Asked(request, state, subject, block))
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
    assert state.series is not None and state.year is not None
    if own != block["series"] or (other_via and other_key != block.get("other")):
        card["via_note"] = (
            _("Посчитано по значению на км².")
            if semantics.PER_KM2 in {own_via, other_via}
            else _("Посчитано по значению на жителя.")
        )
    card.update(
        built,
        template=f"userdata/cards/_{question}.html",
        title=block.get("title") or built.get("title") or title,
        unit=built.get("unit", subject.unit),
        source=series_source(state.series.key, built.get("year") or state.year),
    )
    return card
