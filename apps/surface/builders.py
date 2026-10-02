"""
Представления «Распределение» и «Таблица» рабочей поверхности.

Распределение берёт шкалу карты; таблица строится от справочника, поэтому субъект
без значения занимает в ней строку.
"""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest
from django.utils.translation import gettext_lazy as _

from apps.catalog.constants import ValueQuality
from apps.catalog.models import Territory
from apps.core.charts import histogram_option
from apps.core.templatetags.formatting import ru_number
from apps.maps.panel import build_values
from apps.surface.state import SurfaceState
from apps.warehouse.queries import series_statistics

# Способы упорядочивания таблицы значений.
SORT_NAME = "name"
SORT_VALUE = "value"
SORTS: dict[str, Any] = {
    SORT_NAME: _("по названию"),
    SORT_VALUE: _("по значению"),
}

# Пояснения к признаку качества — только для необычных наблюдений.
QUALITY_LABELS: dict[int, Any] = {
    ValueQuality.NO_DATA: _("нет данных в источнике"),
    ValueQuality.HIDDEN: _("значение скрыто"),
    ValueQuality.NOT_APPLICABLE: _("неприменимо"),
}


# ---------------------------------------------------------------------------------------
# Распределение
# ---------------------------------------------------------------------------------------


def build_distribution(request: HttpRequest, state: SurfaceState) -> dict[str, Any]:
    """Собрать гистограмму разброса, сводку и положение выбранных территорий."""
    context = build_values(request, state)
    if state.series is None or state.year is None:
        return context

    rows: list[dict[str, Any]] = context["rows"]
    palette: list[str] = context.get("palette", [])

    context.update(
        {
            "statistics": series_statistics(state.series.key, state.year),
            "histogram_option": _histogram_chart(
                context.get("histogram", []), rows, context.get("intervals", []), palette
            ),
            "positions": _positions(rows, state.codes),
            "valued_count": sum(1 for row in rows if row["mapped"] is not None),
        }
    )
    return context


def _histogram_chart(
    bins: list[dict[str, float]],
    rows: list[dict[str, Any]],
    intervals: list[dict[str, Any]],
    palette: list[str],
) -> dict[str, Any] | None:
    """
    Построить гистограмму распределения, разобрав столбцы по классам шкалы карты.

    Интервал гистограммы и класс шкалы разной ширины: в столбец попадают разные классы.
    """
    if not bins or not intervals:
        return None

    counts = _layer_counts(bins, rows, len(intervals))
    layers = [
        {
            "name": _("%(lower)s — %(upper)s")
            % {
                "lower": ru_number(interval["lower"]),
                "upper": ru_number(interval["upper"]),
            },
            "values": counts[index],
            "colour": f"var({palette[index]})" if index < len(palette) else "",
        }
        for index, interval in enumerate(intervals)
    ]

    return histogram_option(
        [ru_number(item["lower"]) for item in bins],
        layers,
        unit=str(_("субъектов")),
    )


def _layer_counts(
    bins: list[dict[str, float]], rows: list[dict[str, Any]], class_count: int
) -> list[list[int]]:
    """Разложить субъекты по интервалам гистограммы и классам шкалы."""
    low = bins[0]["lower"]
    width = bins[0]["upper"] - bins[0]["lower"]
    counts = [[0] * len(bins) for _ in range(class_count)]
    if width <= 0:
        return counts

    for row in rows:
        value, index = row["mapped"], row["class_index"]
        if value is None or index is None or index >= class_count:
            continue
        position = min(int((value - low) / width), len(bins) - 1)
        counts[index][max(position, 0)] += 1
    return counts


def _positions(rows: list[dict[str, Any]], codes: list[str]) -> list[dict[str, Any]]:
    """Описать положение выбранных территорий внутри разброса — долей субъектов ниже."""
    by_code = {row["code"]: row for row in rows}
    positions: list[dict[str, Any]] = []
    for code in codes:
        row = by_code.get(code)
        if row is None:
            continue
        percentile = row.get("percentile")
        positions.append(
            {
                "code": code,
                "name": row["name"],
                "href": row["href"],
                "value": row["mapped"],
                "rank": row.get("rank_desc"),
                "class_index": row.get("class_index"),
                "colour": row.get("colour", ""),
                "share_below": round(percentile * 100) if percentile is not None else None,
            }
        )
    return positions


# ---------------------------------------------------------------------------------------
# Таблица значений
# ---------------------------------------------------------------------------------------


def build_table(request: HttpRequest, state: SurfaceState) -> dict[str, Any]:
    """Собрать полную таблицу значений по всем субъектам."""
    context = build_values(request, state)
    sort = request.GET.get("sort", SORT_NAME)
    if sort not in SORTS:
        sort = SORT_NAME
    district = request.GET.get("district", "")

    context.update({"sorts": SORTS, "sort": sort, "district": district})
    if state.series is None or state.year is None:
        context["table_rows"] = []
        return context

    rows = _full_table(context["rows"], state.codes)
    if district:
        rows = [row for row in rows if row["district_code"] == district]
    if sort == SORT_VALUE:
        # Субъекты без значения — внизу.
        rows.sort(key=lambda row: (row["value"] is None, -(row["value"] or 0.0)))

    context.update(
        {
            "table_rows": rows,
            "districts": list(Territory.objects.federal_districts().order_by("display_order")),
            "statistics": series_statistics(state.series.key, state.year),
            "missing_count": sum(1 for row in rows if row["value"] is None),
        }
    )
    return context


def _full_table(rows: list[dict[str, Any]], codes: list[str]) -> list[dict[str, Any]]:
    """Дополнить наблюдения субъектами, которых в складе за этот год нет, — строками с прочерком."""
    known = {row["code"]: row for row in rows}
    selected = set(codes)
    table: list[dict[str, Any]] = []

    # Порядок — по названию на языке страницы.
    directory = sorted(Territory.objects.comparable().with_district(), key=lambda item: item.name)
    for territory in directory:
        row = known.get(territory.code)
        district = territory.parent
        quality = row.get("quality") if row else None
        table.append(
            {
                "code": territory.code,
                "slug": territory.slug,
                "name": territory.name,
                "district_code": district.code if district else "",
                "district_name": district.name if district else "",
                "value": row["value"] if row else None,
                "colour": row.get("colour", "") if row else "",
                "rank": row.get("rank_desc") if row else None,
                "percentile": row.get("percentile") if row else None,
                "ratio_to_country": row.get("ratio_to_country") if row else None,
                "quality": quality,
                "note": _row_note(row, quality),
                "selected": territory.code in selected,
            }
        )

    return table


def _row_note(row: dict[str, Any] | None, quality: int | None) -> Any:
    """Составить пометку о причине отсутствия значения; обычное наблюдение её не получает."""
    if row is None:
        return _("нет в источнике за этот год")
    if row["value"] is not None and quality == ValueQuality.OBSERVED:
        return ""
    return QUALITY_LABELS.get(quality, _("значение неизвестно")) if quality is not None else ""
