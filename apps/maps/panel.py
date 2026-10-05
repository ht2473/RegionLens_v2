"""
Картограмма рабочей поверхности: географическая (:mod:`apps.maps.cartogram`), плиточная
или малые графики по плиточной раскладке (:mod:`apps.maps.multiples`).

Разбиение по классам шкалы (:func:`build_values`) — общее с представлением распределения.
"""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from apps.catalog.indicator import describe_series
from apps.catalog.models import Territory
from apps.core.templatetags.formatting import position_share, ru_number
from apps.surface.state import SurfaceState
from apps.warehouse.queries import region_panel, series_values_by_territory

from .boundaries import boundaries_available, boundaries_meta
from .cartogram import DEFINITION_PREFIX, build_map
from .classification import (
    DEFAULT_CLASSES,
    DEFAULT_METHOD,
    MAX_CLASSES,
    METHODS,
    MIN_CLASSES,
    classify,
    classify_symmetric,
    diverging_palette,
    histogram,
    sequential_palette,
)
from .multiples import build_multiples
from .tiles import tile_grid, tile_position

# Режимы отображения карты: география, плитки и малые графики по плиточной раскладке.
MODE_GEOGRAPHIC = "geo"
MODE_TILES = "tiles"
MODE_SMALL = "small"
MODES = (MODE_GEOGRAPHIC, MODE_TILES, MODE_SMALL)

# Число территорий в списках лидеров и аутсайдеров под картой.
LEADERS_LIMIT = 5


def build(
    request: HttpRequest, state: SurfaceState, *, prefix: str = DEFINITION_PREFIX
) -> dict[str, Any]:
    """
    Подготовить всё, что нужно для отрисовки карты и легенды; ``prefix`` — приставка
    опознавателей контуров, если карт на странице несколько (доска).
    """
    context: dict[str, Any] = {
        "boundaries_available": boundaries_available(),
        "boundaries_meta": boundaries_meta(),
        "mode": _resolve_mode(request.GET.get("mode")),
    }
    context.update(build_values(request, state))
    if state.series is None or state.year is None:
        return context

    rows = context["rows"]
    series = state.series
    mode = context["mode"]
    item = describe_series(series)
    top, bottom = _leaders(
        rows, item.precision, coloured=not context["compare_year"], ratio=not item.absolute
    )
    context.update(
        {
            "leaders_top": top,
            "leaders_bottom": bottom,
            "tile_grid": tile_grid(rows),
            "tile_caption": _("Плиточная картограмма: %(title)s") % {"title": series.full_title},
            "geo_map": (
                build_map(rows, selected=state.codes, prefix=prefix)
                if mode == MODE_GEOGRAPHIC
                else None
            ),
            "geo_caption": _("Картограмма: %(title)s") % {"title": series.full_title},
            "multiples": (
                build_multiples(rows, region_panel(series.key), state.year)
                if mode == MODE_SMALL
                else None
            ),
            "multiples_caption": _("Малые графики: %(title)s") % {"title": series.full_title},
        }
    )
    return context


def _leaders(
    rows: list[dict[str, Any]], precision: int, *, coloured: bool, ratio: bool
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Пять регионов с наибольшим и пять с наименьшим значением года — из строк карты.

    Полоска — только у величин одного знака; при сравнении лет — без цвета класса.
    У абсолютной величины отношения к России нет: значение страны — сумма.
    """
    ranked = sorted(
        (row for row in rows if row["rank_desc"] is not None and row["value"] is not None),
        key=lambda row: row["rank_desc"],
    )
    if not ranked:
        return [], []
    high, low = ranked[0]["value"], ranked[-1]["value"]
    scaled = low >= 0 and high > 0

    def entry(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "code": row["code"],
            "name": row["name"],
            "href": row["href"],
            "rank": row["rank_desc"],
            "value": ru_number(row["value"], precision),
            "ratio": row["ratio_to_country"] if ratio else None,
            # Строкой: дробь в атрибуте шаблон записал бы с запятой.
            "share": f"{row['value'] / high:.3f}" if scaled else "",
            "colour": row["colour"] if coloured else "",
        }

    return (
        [entry(row) for row in ranked[:LEADERS_LIMIT]],
        [entry(row) for row in reversed(ranked[-LEADERS_LIMIT:])],
    )


# ---------------------------------------------------------------------------------------
# Разбиение значений по классам шкалы
# ---------------------------------------------------------------------------------------


def build_values(request: HttpRequest, state: SurfaceState) -> dict[str, Any]:
    """Разложить значения ряда за год по классам шкалы — для карты и распределения."""
    method = request.GET.get("method", DEFAULT_METHOD)
    if method not in METHODS:
        method = DEFAULT_METHOD
    class_count = _resolve_class_count(request.GET.get("classes"))
    # Способы и числа классов — для рейля обоих представлений.
    controls: dict[str, Any] = {
        "methods": METHODS,
        "class_range": range(MIN_CLASSES, MAX_CLASSES + 1),
    }

    if state.series is None or state.year is None:
        return {
            **controls,
            "rows": [],
            "method": method,
            "class_count": class_count,
            "compare_year": None,
        }

    compare_year = _resolve_compare_year(request.GET.get("compare"), state.years, state.year)
    observations = series_values_by_territory(state.series.key, state.year)
    baseline: dict[str, float | None] = {}
    if compare_year:
        baseline = {
            row["territory_code"]: row["value"]
            for row in series_values_by_territory(state.series.key, compare_year)
        }

    # Изменение — от раннего года к позднему, какой бы из них ни был годом карты.
    period = sorted((state.year, compare_year)) if compare_year else None
    rows = _merge_with_reference(
        observations,
        baseline,
        compare_year,
        compare_is_later=bool(compare_year and compare_year > state.year),
    )
    values = [row["mapped"] for row in rows if row["mapped"] is not None]

    if compare_year:
        classification = classify_symmetric(values, class_count)
        palette = diverging_palette(classification.class_count) if classification else []
    else:
        classification = classify(values, method=method, class_count=class_count)
        palette = sequential_palette(classification.class_count) if classification else []

    class_count = classification.class_count if classification else 0
    selected = set(state.codes)
    for row in rows:
        index = classification.class_of(row["mapped"]) if classification else None
        row["class_index"] = index
        row["colour"] = palette[index] if index is not None and index < len(palette) else ""
        row["light_label"] = _needs_light_label(index, class_count, diverging=bool(compare_year))
        row["title"] = _tile_title(row, compare_year)
        row["label"] = _map_label(row["mapped"], compare_year)
        row["selected"] = row["code"] in selected

    return {
        **controls,
        "rows": rows,
        "method": method,
        "class_count": class_count,
        "compare_year": compare_year,
        "period_start": period[0] if period else None,
        "period_end": period[1] if period else None,
        "classification": classification,
        "intervals": _intervals_with_colours(classification, palette),
        "palette": palette,
        "histogram": histogram(values),
        "no_data_count": sum(1 for row in rows if row["mapped"] is None),
    }


def _resolve_mode(value: str | None) -> str:
    """Определить режим отображения карты; без файла границ вместо географии — плитки."""
    mode = value if value in MODES else MODE_GEOGRAPHIC
    if mode == MODE_GEOGRAPHIC and not boundaries_available():
        return MODE_TILES
    return mode


def _map_label(value: float | None, compare_year: int | None) -> str:
    """Записать значение для подписи на карте; изменение — со знаком."""
    if value is None:
        return ""
    text = ru_number(value)
    return f"+{text}" if compare_year is not None and value > 0 else text


def _tile_title(row: dict[str, Any], compare_year: int | None) -> str:
    """
    Составить подсказку плитки: значение и положение в распределении.

    При сравнении лет положение не называется: раскрашено изменение, а положение — об уровне.
    """
    if row["mapped"] is None:
        return f"{row['name']} — {_('нет данных')}"

    parts = [f"{row['name']} — {ru_number(row['mapped'])}"]
    if compare_year is not None:
        parts.append(f" ({_('изменение')})")
    elif row["percentile"] is not None:
        parts.append(", " + position_share(row["percentile"]))
    return "".join(parts)


def _needs_light_label(index: int | None, class_count: int, *, diverging: bool) -> bool:
    """
    Определить, нужна ли светлая подпись на плитке.

    Тёмные ступени у последовательной шкалы — верхние, у расходящейся — крайние.
    """
    if index is None or class_count == 0:
        return False
    if diverging:
        return index == 0 or index == class_count - 1
    return index >= class_count - 2


def _resolve_class_count(value: str | None) -> int:
    """Определить число классов шкалы, ограничив его допустимым диапазоном."""
    try:
        count = int(value) if value else DEFAULT_CLASSES
    except TypeError, ValueError:
        return DEFAULT_CLASSES
    return max(MIN_CLASSES, min(MAX_CLASSES, count))


def _resolve_compare_year(value: str | None, years: list[int], year: int) -> int | None:
    """Определить год сравнения для режима изменений; совпадающий с годом карты — отказ."""
    if not value:
        return None
    try:
        candidate = int(value)
    except TypeError, ValueError:
        return None
    if candidate in years and candidate != year:
        return candidate
    return None


def _merge_with_reference(
    observations: list[dict[str, Any]],
    baseline: dict[str, float | None],
    compare_year: int | None,
    *,
    compare_is_later: bool = False,
) -> list[dict[str, Any]]:
    """Соединить значения из склада со справочником территорий одним запросом."""
    territories = {
        territory.code: territory
        for territory in Territory.objects.comparable().only(
            "code", "slug", "name_ru", "name_en", "abbreviation", "tile_x", "tile_y"
        )
    }

    rows: list[dict[str, Any]] = []
    for observation in observations:
        territory = territories.get(observation["territory_code"])
        if territory is None:
            continue

        value = observation["value"]
        mapped = value
        change = None
        if compare_year is not None:
            previous = baseline.get(observation["territory_code"])
            if value is not None and previous is not None:
                # Разность «поздний минус ранний».
                change = previous - value if compare_is_later else value - previous
            mapped = change

        rows.append(
            {
                "code": territory.code,
                "slug": territory.slug,
                "name": territory.name,
                "href": reverse("catalog:territory-detail", kwargs={"slug": territory.slug}),
                "abbreviation": territory.short_code,
                "tile_x": territory.tile_x,
                "tile_y": territory.tile_y,
                # Координаты плитки в единицах чертежа: шаблон не умеет умножать.
                "tile_px": tile_position(territory.tile_x),
                "tile_py": tile_position(territory.tile_y),
                "value": value,
                "mapped": mapped,
                "change": change,
                "quality": observation["quality"],
                "rank_desc": observation["rank_desc"],
                "percentile": observation["percentile"],
                "ratio_to_country": observation["ratio_to_country"],
                "district_code": observation["district_code"],
            }
        )

    return rows


def _intervals_with_colours(classification: Any, palette: list[str]) -> list[dict[str, Any]]:
    """Дополнить описание классов именами переменных оформления."""
    if classification is None:
        return []
    return [
        {
            **interval,
            "colour": palette[interval["index"]] if interval["index"] < len(palette) else "",
        }
        for interval in classification.intervals()
    ]
