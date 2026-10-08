"""
Представления «Распределение» и «Таблица» рабочей поверхности.

Распределение — полоса точек в цветах шкалы карты; таблица строится от справочника, поэтому
субъект без значения занимает в ней строку.
"""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest
from django.utils.html import escape
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from apps.catalog.constants import ValueQuality
from apps.catalog.indicator import describe_series
from apps.catalog.models import Territory
from apps.core.charts import strip_option
from apps.core.templatetags.formatting import position_share, ru_number
from apps.maps.panel import build_values
from apps.surface.findings import distribution_finding
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
    """Собрать полосу точек, сводку и положение выбранных территорий."""
    context = build_values(request, state)
    context["view_summary"] = context["scale_summary"]
    if state.series is None or state.year is None:
        return context

    rows: list[dict[str, Any]] = context["rows"]
    statistics = series_statistics(state.series.key, state.year)
    item = describe_series(state.series)
    context.update(
        {
            "statistics": statistics,
            "strip_option": _strip_chart(
                rows, statistics, item, compared=bool(context["compare_year"])
            ),
            "positions": _positions(rows, state.codes),
            "finding": distribution_finding(rows, statistics, item),
            "valued_count": sum(1 for row in rows if row["mapped"] is not None),
        }
    )
    return context


def _strip_chart(
    rows: list[dict[str, Any]],
    statistics: dict[str, Any] | None,
    item: Any,
    *,
    compared: bool,
) -> dict[str, Any] | None:
    """
    Полоса точек года: цвет точки — класс шкалы карты, выбранные крупнее и подписаны;
    отметки — квартили, медиана и Россия (у складываемой величины Россия — сумма, её нет).
    """
    valued = [row for row in rows if row["mapped"] is not None]
    if len(valued) < 2:  # noqa: PLR2004 — разброса по одной точке нет
        return None
    precision = item.precision

    def tip(row: dict[str, Any]) -> str:
        parts = [f"<strong>{escape(row['name'])}</strong>", ru_number(row["mapped"], precision)]
        if row["percentile"] is not None and not compared:
            parts.append(escape(position_share(row["percentile"])))
        return "<br>".join(parts)

    points = [
        {
            "name": row["name"],
            "label": row["abbreviation"] or row["name"],
            "value": row["mapped"],
            "colour": f"var({row['colour']})" if row["colour"] else "",
            "selected": row["selected"],
            "tooltip": tip(row),
        }
        for row in valued
    ]
    marks: list[dict[str, Any]] = []
    if statistics and not compared:
        # Квартили — без подписи (о них — в «?»).
        for key, label in (
            ("p25_value", ""),
            ("median_value", gettext("медиана")),
            ("p75_value", ""),
        ):
            if statistics.get(key) is not None:
                marks.append({"value": statistics[key], "label": label})
        country = statistics.get("country_value")
        low = min(row["mapped"] for row in valued)
        high = max(row["mapped"] for row in valued)
        if country is not None and not item.absolute and low <= country <= high:
            marks.append({"value": country, "label": gettext("Россия"), "strong": True})
    return strip_option(points, marks=marks, unit=item.unit_label)


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

    context.update(
        {"sorts": SORTS, "sort": sort, "district": district, "view_summary": [SORTS[sort]]}
    )
    if state.series is None or state.year is None:
        context["table_rows"] = []
        return context

    from apps.accounts.region import my_region

    region = my_region(request)
    rows = _full_table(context["rows"], state.codes, mine=region.code if region is not None else "")
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


def _full_table(
    rows: list[dict[str, Any]], codes: list[str], *, mine: str = ""
) -> list[dict[str, Any]]:
    """
    Дополнить наблюдения субъектами, которых в складе за этот год нет, — строками с прочерком.

    Столбик у значения — доля наибольшего (строкой: дробь шаблон записал бы с запятой); только
    у величин одного знака.
    """
    known = {row["code"]: row for row in rows}
    selected = set(codes)
    values = [row["value"] for row in rows if row["value"] is not None]
    highest = max(values) if values and min(values) >= 0 and max(values) > 0 else None
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
                "mine": territory.code == mine,
                "bar": (
                    f"{row['value'] / highest:.3f}"
                    if highest and row and row["value"] is not None
                    else ""
                ),
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
