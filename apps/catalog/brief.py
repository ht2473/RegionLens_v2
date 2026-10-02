"""Краткая сводка о регионе для главной и кабинета: главное словами, «Что сейчас», плитки."""

from __future__ import annotations

from typing import Any

from django.urls import reverse

from apps.catalog.models import Territory
from apps.catalog.monthly import now_rows
from apps.catalog.passport import build_passport
from apps.core.charts import sparkline_path
from apps.warehouse.duckdb_client import WarehouseNotBuiltError
from apps.warehouse.queries import featured_snapshot


def region_brief(
    territory: Territory, *, metrics: int = 6, now: int | None = None
) -> dict[str, Any]:
    """
    Сводка о регионе; без склада — только название и адрес паспорта.

    ``metrics`` — сколько главных показателей, ``now`` — сколько строк «Что сейчас».
    """
    brief: dict[str, Any] = {
        "territory": territory,
        "href": reverse("catalog:territory-detail", kwargs={"slug": territory.slug}),
        "ready": False,
    }
    try:
        passport = build_passport(territory.code)
        positions = passport.by_key()
        snapshot = featured_snapshot(territory.code)[:metrics] if metrics else []
        rows = now_rows(territory.code)
    except WarehouseNotBuiltError:
        return brief
    brief.update(
        ready=True,
        summary=passport.summary[:3],
        now=rows[:now] if now is not None else rows,
        metrics=[
            {
                **entry,
                "position": positions.get(entry["series"].key),
                "spark": sparkline_path([point["value"] for point in entry["sparkline"]]),
            }
            for entry in snapshot
        ],
    )
    return brief
