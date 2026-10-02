"""Страницы, состояние которых можно сохранить в кабинете как вид экрана."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.utils.translation import gettext_lazy as _


@dataclass(frozen=True, slots=True)
class QueryTarget:
    """Страница, состояние которой можно сохранить и восстановить."""

    code: str
    url_name: str
    # Ленивый перевод: на английской странице кабинета название страницы по-английски.
    title: Any
    icon: str = "activity"


# Страницы, состояние которых описывается строкой запроса. Коды представлений рабочей
# поверхности совпадают с ``apps.surface.panels``: сохраняется открытая вкладка.
QUERY_TARGETS: tuple[QueryTarget, ...] = (
    QueryTarget("map", "maps:choropleth", _("Карта"), "map"),
    QueryTarget("rankings", "rankings:index", _("Рейтинг"), "ranking"),
    QueryTarget("compare", "compare:index", _("Динамика и сравнение"), "dynamics"),
    QueryTarget("distribution", "surface:distribution", _("Распределение"), "distribution"),
    QueryTarget("table", "surface:table", _("Таблица значений"), "table"),
    QueryTarget("inequality", "analytics:inequality", _("Неравенство"), "inequality"),
    QueryTarget("convergence", "analytics:convergence", _("Конвергенция"), "convergence"),
    QueryTarget("correlation", "analytics:correlation", _("Корреляции"), "correlation"),
    QueryTarget("spatial", "analytics:spatial", _("Пространственный анализ"), "spatial"),
    QueryTarget("index-builder", "analytics:index-builder", _("Интегральные индексы"), "index"),
    QueryTarget("revisions", "analytics:revisions", _("Пересмотры статистики"), "revisions"),
)

QUERY_TARGETS_BY_CODE: dict[str, QueryTarget] = {item.code: item for item in QUERY_TARGETS}

QUERY_TARGET_CHOICES = [(item.code, item.title) for item in QUERY_TARGETS]
