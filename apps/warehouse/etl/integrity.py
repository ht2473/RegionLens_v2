"""
Ряды, на которые ссылаются справочники проекта и сохранённое пользователями.

После сборки сверяется, что все они есть в складе: иначе смена ключей рядов в новой
версии набора прошла бы молча — показатель паспорта остался бы без данных.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.utils.translation import gettext_lazy as _

# Справочники проекта ведутся вручную; сохранённое — пользователями.
PROJECT_ORIGINS = ("featured", "real", "link", "status", "population", "passport")
USER_ORIGINS = ("saved", "favorite")

ORIGIN_TITLES = {
    "featured": _("Основной набор"),
    "real": _("Пересчёт в реальное выражение"),
    "link": _("Связи с выпусками источников"),
    "status": _("Статусы публикации рядов"),
    "population": _("Численность для рядов на жителя"),
    "passport": _("Стоимость набора в паспорте"),
    "saved": _("Сохранённые виды пользователей"),
    "favorite": _("Избранное пользователей"),
}


@dataclass(frozen=True, slots=True)
class MissingSeries:
    """Ссылка на ряд, которого нет в складе; ``detail`` — подпись или число записей."""

    origin: str
    key: str
    detail: str = ""

    def as_dict(self) -> dict[str, str]:
        """Представление для журнала сборки."""
        return {"origin": self.origin, "key": self.key, "detail": self.detail}


def project_references() -> dict[str, dict[str, str]]:
    """Ключи рядов в справочниках проекта: откуда ссылка → ключ → подпись."""
    from apps.catalog.passport import BASKET_COST, BASKET_INCOME, BASKET_WAGE
    from apps.warehouse.queries.common import featured_set

    found: dict[str, dict[str, str]] = {origin: {} for origin in PROJECT_ORIGINS}
    for item in featured_set().series:
        found["featured"][item.key] = item.short_title_ru
        if item.real is not None:
            for key in (item.real.index, item.real.total):
                if key:
                    found["real"][key] = item.short_title_ru

    for link in _read("source_links.json")["links"]:
        found["link"][link["series"]] = link["measure"]
        if link.get("total_series"):
            found["link"][link["total_series"]] = link["measure"]

    for item in _read("series_status.json")["series"]:
        found["status"][item["key"]] = item["status"]

    population = _read("source_series.json")["population"]
    for name in ("total", "per_capita"):
        found["population"][population[name]] = name

    for key in (BASKET_INCOME, BASKET_WAGE, BASKET_COST):
        found["passport"][key] = key
    return found


def user_references() -> dict[str, Counter[str]]:
    """Ключи рядов в сохранённом пользователями: откуда ссылка → ключ → число записей."""
    from apps.workspace.models import Favorite, SavedQuery

    saved: Counter[str] = Counter()
    for query in SavedQuery.objects.only("parameters"):
        saved.update(query.series_keys)
    favorites = Counter(
        Favorite.objects.filter(series__isnull=False).values_list("series__key", flat=True)
    )
    return {"saved": saved, "favorite": favorites}


def missing_series(known: set[str]) -> list[MissingSeries]:
    """Ссылки на ряды, которых нет среди ``known``: сначала справочники проекта."""
    missing = [
        MissingSeries(origin, key, label)
        for origin, keys in project_references().items()
        for key, label in sorted(keys.items())
        if key not in known
    ]
    missing.extend(
        MissingSeries(origin, key, str(count))
        for origin, counts in user_references().items()
        for key, count in sorted(counts.items())
        if key not in known
    )
    return missing


def grouped(items: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Ссылки из журнала сборки по источникам ссылки — для панели и письма."""
    groups: dict[str, list[dict[str, str]]] = {}
    for item in items:
        groups.setdefault(item["origin"], []).append(item)
    return [
        {
            "origin": origin,
            "title": ORIGIN_TITLES.get(origin, origin),
            "is_project": origin in PROJECT_ORIGINS,
            "items": groups[origin],
        }
        for origin in (*PROJECT_ORIGINS, *USER_ORIGINS)
        if origin in groups
    ]


def _read(name: str) -> dict[str, Any]:
    """Прочитать файл справочника."""
    path = settings.REFERENCE_DIR / name
    payload: dict[str, Any] = json.loads(path.read_bytes().decode("utf-8"))
    return payload
