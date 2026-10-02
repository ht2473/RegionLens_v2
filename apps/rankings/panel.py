"""
Рейтинг рабочей поверхности: место, значение, отношение к стране, доля и движение.

Ряды, не пригодные для сравнительного анализа, не ранжируются.
"""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest

from apps.catalog.constants import territory_short_code
from apps.catalog.models import Territory
from apps.catalog.selectors import resolve_year
from apps.core.charts import bump_option
from apps.surface.state import SurfaceState
from apps.warehouse.queries import (
    rank_history,
    rank_movers,
    ranked_years,
    ranking_table,
    series_statistics,
)

# Число территорий на графике движения позиций: больше восьми линий неразличимы.
BUMP_LIMIT = 8

# Число территорий в перечнях наибольшего движения.
MOVERS_LIMIT = 5

# Наименьшее число лет для графика движения позиций.
MIN_BUMP_YEARS = 2


def build(request: HttpRequest, state: SurfaceState) -> dict[str, Any]:
    """Собрать таблицу рейтинга и график движения позиций."""
    districts = _districts()
    context: dict[str, Any] = {"districts": districts, "rows": []}
    if state.series is None or state.year is None:
        return context

    series = state.series
    years = ranked_years(series.key)
    # Рейтинг рассчитан не за все годы ряда: берётся ближайший, и об этом сказано на холсте.
    year = resolve_year(str(state.year), years)
    if year is None:
        return context

    previous_year = _resolve_previous_year(request.GET.get("base"), years, year)
    ascending = request.GET.get("order") == "asc"
    district = request.GET.get("district", "")

    # Период — от раннего года к позднему, какой бы из них ни был годом рейтинга.
    forward = previous_year is None or previous_year < year
    period = sorted((year, previous_year)) if previous_year else None

    rows = ranking_table(series.key, year, previous_year=previous_year, ascending=ascending)
    slugs = _territory_slugs()
    selected = set(state.codes)
    for row in rows:
        movement = _movement(row, ascending, forward=forward)
        row["movement"] = movement
        # Направление показывает значок, в подписи — модуль.
        row["movement_abs"] = abs(movement) if movement is not None else None
        row["slug"] = slugs.get(row["territory_code"], "")
        row["selected"] = row["territory_code"] in selected

    if district:
        rows = [row for row in rows if row["district_code"] == district]

    movers = (
        rank_movers(series.key, period[1], period[0], limit=MOVERS_LIMIT)
        if period
        else {"risen": [], "fallen": []}
    )
    for mover in (*movers["risen"], *movers["fallen"]):
        mover["slug"] = slugs.get(mover["territory_code"], "")

    return {
        "ranking_year": year,
        "ranking_year_shifted": year != state.year,
        "previous_year": previous_year,
        "period_start": period[0] if period else None,
        "period_end": period[1] if period else None,
        "ranking_years": years,
        "ascending": ascending,
        "district": district,
        "districts": districts,
        "rows": rows,
        "movers": movers,
        "bump_option": _bump_chart(series.key, rows, years, ascending, selected),
        "statistics": series_statistics(series.key, year),
        "ranked_total": len(rows),
    }


def _resolve_previous_year(value: str | None, years: list[int], year: int) -> int | None:
    """Определить год, с которым сравниваются позиции; по умолчанию — предыдущий рассчитанный."""
    if value:
        try:
            candidate = int(value)
        except TypeError, ValueError:
            candidate = None
        if candidate in years and candidate != year:
            return candidate

    earlier = [item for item in years if item < year]
    return earlier[-1] if earlier else None


def _movement(row: dict[str, Any], ascending: bool, *, forward: bool = True) -> int | None:
    """
    Вычислить изменение позиции за период от раннего года к позднему; плюс — подъём.

    Если год сравнения позже года рейтинга (``forward`` ложно), знак меняется.
    """
    current = row["rank_asc"] if ascending else row["rank_desc"]
    previous = row["previous_rank_asc"] if ascending else row["previous_rank_desc"]
    if current is None or previous is None:
        return None
    return previous - current if forward else current - previous


def _districts() -> list[Territory]:
    """Перечень федеральных округов для фильтра."""
    return list(Territory.objects.federal_districts().order_by("display_order"))


def _territory_slugs() -> dict[str, str]:
    """Сопоставить коды территорий их слагам одной выборкой."""
    return dict(Territory.objects.values_list("code", "slug"))


def _bump_chart(
    series_key: str,
    rows: list[dict[str, Any]],
    years: list[int],
    ascending: bool,
    selected: set[str],
) -> dict[str, Any] | None:
    """Построить график движения позиций: первые места года и выбранные территории."""
    if not rows or len(years) < MIN_BUMP_YEARS:
        return None

    leaders = rows[:BUMP_LIMIT]
    shown = {row["territory_code"] for row in leaders}
    leaders = leaders + [row for row in rows if row["territory_code"] in selected - shown]

    codes = [row["territory_code"] for row in leaders]
    history = rank_history(series_key, codes, first_year=years[0], last_year=years[-1])

    column = "rank_asc" if ascending else "rank_desc"
    lines = []
    for row in leaders:
        points = {item["year"]: item[column] for item in history.get(row["territory_code"], [])}
        chosen = row["territory_code"] in selected
        lines.append(
            {
                "name": territory_short_code(row["territory_code"], row["abbreviation"])
                or row["name"],
                "values": [points.get(year) for year in years],
                # Связь строки таблицы с линией — по коду: линии подписаны сокращениями.
                "code": row["territory_code"],
                "primary": chosen,
                "muted": bool(selected) and not chosen,
            }
        )

    max_rank = max(
        (item[column] for points in history.values() for item in points if item[column]),
        default=len(rows),
    )
    return bump_option(years, lines, max_rank=max_rank)
