"""
Рейтинг рабочей поверхности: место, значение, отношение к стране, доля и движение.

Каждый блок вкладки отвечает на свой вопрос: кто сдвинулся и как к этому шёл (списки
с линией мест за последние годы у каждого региона), где каждый и куда шёл (таблица с той же
линией). График мест по годам — только для отмеченных регионов: 85 линий разом не читаются.
Ряды, не пригодные для сравнительного анализа, не ранжируются.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from django.http import HttpRequest
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from apps.catalog.constants import BreakKind, Polarity, territory_short_code
from apps.catalog.indicator import describe_series
from apps.catalog.models import SeriesBreak, Territory
from apps.catalog.selectors import resolve_year
from apps.core.charts import bump_option
from apps.core.templatetags.formatting import ru_number
from apps.surface.findings import ranking_finding
from apps.surface.state import SurfaceState
from apps.warehouse.queries import (
    rank_history,
    rank_movers,
    ranked_years,
    ranking_table,
    series_statistics,
)

# Число территорий в перечнях наибольшего движения.
MOVERS_LIMIT = 5

# Наименьшее число лет для графика движения позиций.
MIN_BUMP_YEARS = 2

# История мест — столько последних лет до года рейтинга: график и столбец таблицы.
HISTORY_YEARS = 10

# Мини-график мест в строке таблицы: размер и наименьший размах шкалы в местах — иначе
# сдвиг на одно место выглядел бы обвалом.
SPARK_WIDTH = 84
SPARK_HEIGHT = 22
SPARK_PAD = 3
SPARK_MIN_SPAN = 10


def first_at_smallest(series: Any, order: str | None) -> bool:
    """
    Первое место у наименьшего значения: явный выбор в адресе, иначе — по смыслу показателя
    (рост — к худшему: безработица, бедность, смертность).
    """
    if order in ("asc", "desc"):
        return order == "asc"
    return describe_series(series).polarity == Polarity.NEGATIVE


def build(request: HttpRequest, state: SurfaceState) -> dict[str, Any]:
    """Собрать списки движения, места по годам и таблицу рейтинга."""
    from apps.accounts.region import my_region

    districts = _districts()
    context: dict[str, Any] = {"districts": districts, "rows": [], "view_summary": []}
    if state.series is None or state.year is None:
        return context

    series = state.series
    item = describe_series(series)
    years = ranked_years(series.key)
    # Рейтинг рассчитан не за все годы ряда: берётся ближайший, и об этом сказано на холсте.
    year = resolve_year(str(state.year), years)
    if year is None:
        return context

    previous_year = _resolve_previous_year(request.GET.get("base"), years, year)
    ascending = first_at_smallest(series, request.GET.get("order"))
    district = request.GET.get("district", "")

    # Период — от раннего года к позднему, какой бы из них ни был годом рейтинга.
    forward = previous_year is None or previous_year < year
    period = sorted((year, previous_year)) if previous_year else None

    rows = ranking_table(series.key, year, previous_year=previous_year, ascending=ascending)
    rank_key = "rank_asc" if ascending else "rank_desc"
    shared = Counter(row[rank_key] for row in rows)
    slugs = _territory_slugs()
    selected = set(state.codes)
    region = my_region(request)
    mine = region.code if region is not None else ""
    for row in rows:
        movement = _movement(row, ascending, forward=forward)
        row["place"] = row[rank_key]
        # Равные значения делят место — у него отметка «=».
        row["tied"] = shared[row[rank_key]] > 1
        row["movement"] = movement
        # Направление показывает значок, в подписи — модуль.
        row["movement_abs"] = abs(movement) if movement is not None else None
        row["slug"] = slugs.get(row["territory_code"], "")
        row["selected"] = row["territory_code"] in selected
        row["mine"] = row["territory_code"] == mine
        row["district_short"] = territory_short_code(
            row["district_code"] or "", row.get("district_abbreviation") or ""
        )
    _value_bars(rows)

    if district:
        rows = [row for row in rows if row["district_code"] == district]
        _district_places(rows, rank_key)

    movers: dict[str, Any] = (
        rank_movers(
            series.key,
            period[1],
            period[0],
            limit=MOVERS_LIMIT,
            ascending=ascending,
            district=district,
        )
        if period
        else {"risen": [], "fallen": [], "risen_more": 0, "fallen_more": 0}
    )
    window = [entry for entry in years if year - HISTORY_YEARS < entry <= year]
    places = _places_by_year(series.key, rows, window, rank_key)
    for row in rows:
        row["spark"] = _spark(window, places.get(row["territory_code"], {}))
    # Линия в списках — до конца сравниваемого периода: год сравнения бывает позже года рейтинга.
    mover_window, mover_places = window, places
    if period and period[1] != year:
        mover_window = [entry for entry in years if period[1] - HISTORY_YEARS < entry <= period[1]]
        mover_places = _places_by_year(series.key, rows, mover_window, rank_key)
    for mover in (*movers["risen"], *movers["fallen"]):
        mover["slug"] = slugs.get(mover["territory_code"], "")
        mover["change"] = _value_change(item, mover)
        mover["spark"] = _spark(mover_window, mover_places.get(mover["territory_code"], {}))

    marked = selected | ({mine} - {""})
    return {
        "ranking_year": year,
        "ranking_year_shifted": year != state.year,
        "previous_year": previous_year,
        "period_start": period[0] if period else None,
        "period_end": period[1] if period else None,
        "ranking_years": years,
        "history_years": window,
        "mover_years": mover_window,
        "ascending": ascending,
        "district": district,
        "districts": districts,
        "rows": rows,
        "missing": _missing(rows, district),
        "movers": movers,
        "finding": ranking_finding(movers, period),
        "breaks": _breaks(series.key, period),
        # График — по отметке: отмеченные регионы цветом, мой регион — тоже.
        "bump_option": _bump(rows, window, places, marked) if selected else None,
        "statistics": series_statistics(series.key, year),
        "ranked_total": len(rows),
        "view_summary": [
            _("первое место у наименьшего") if ascending else _("первое место у наибольшего"),
            *(
                [_("места сравниваются с %(year)s годом") % {"year": previous_year}]
                if previous_year
                else []
            ),
        ],
    }


def _value_bars(rows: list[dict[str, Any]]) -> None:
    """
    Столбик у значения — доля наибольшего значения (строкой: дробь шаблон записал бы
    с запятой); только у величин одного знака.
    """
    values = [row["value"] for row in rows if row["value"] is not None]
    if not values or min(values) < 0 or max(values) <= 0:
        return
    highest = max(values)
    for row in rows:
        if row["value"] is not None:
            row["bar"] = f"{row['value'] / highest:.3f}"


def _district_places(rows: list[dict[str, Any]], rank_key: str) -> None:
    """Место внутри выбранного округа: равные делят место, как и в России."""
    for row in rows:
        row["district_place"] = 1 + sum(1 for other in rows if other[rank_key] < row[rank_key])


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


def _value_change(item: Any, mover: dict[str, Any]) -> str:
    """
    Изменение значения за период рядом со сдвигом места: «+9,5 %», у процентов —
    «+1,2 п. п.». Место может упасть и при росте значения — соседи выросли сильнее.
    Значение списка — позднего года, прежнее — раннего.
    """
    after, before = mover["value"], mover["previous_value"]
    if after is None or before is None:
        return ""
    if item.is_percentage:
        delta = after - before
        unit = gettext("п. п.")
        digits = max(item.precision, 1)
    elif before > 0 and after > 0:
        delta = (after / before - 1) * 100
        unit = "%"
        digits = 1
    else:
        return ""
    sign = "+" if delta > 0 else "−" if delta < 0 else ""
    return f"{sign}{ru_number(abs(delta), digits)} {unit}"


def _districts() -> list[Territory]:
    """Перечень федеральных округов для фильтра."""
    return list(Territory.objects.federal_districts().order_by("display_order"))


def _territory_slugs() -> dict[str, str]:
    """Сопоставить коды территорий их слагам одной выборкой."""
    return dict(Territory.objects.values_list("code", "slug"))


def _missing(rows: list[dict[str, Any]], district: str) -> list[str]:
    """Субъекты без места в рейтинге года — названиями: молча они бы пропали из таблицы."""
    ranked = {row["territory_code"] for row in rows}
    territories = Territory.objects.comparable().with_district()
    return sorted(
        territory.name
        for territory in territories
        if territory.code not in ranked
        and (not district or (territory.parent and territory.parent.code == district))
    )


def _breaks(series_key: str, period: list[int] | None) -> list[dict[str, Any]]:
    """
    Разрывы сопоставимости между годом сравнения и годом рейтинга: сдвиг мест может
    быть следствием смены методики. Смена состава территорий меняет итог по стране,
    а не места субъектов, если не отнесена к субъекту явно.
    """
    if not period:
        return []
    start, end = period
    found = (
        SeriesBreak.objects.filter(series__key=series_key, year__gt=start, year__lte=end)
        .select_related("territory")
        .order_by("year")
    )
    return [
        {
            "year": entry.year,
            "kind": entry.get_kind_display(),
            "territory": entry.territory.name if entry.territory else "",
        }
        for entry in found
        if entry.territory is not None or entry.kind != BreakKind.TERRITORY
    ]


def _places_by_year(
    series_key: str, rows: list[dict[str, Any]], window: list[int], rank_key: str
) -> dict[str, dict[int, int]]:
    """Места территорий таблицы по годам окна истории."""
    if not rows or len(window) < MIN_BUMP_YEARS:
        return {}
    codes = [row["territory_code"] for row in rows]
    history = rank_history(series_key, codes, first_year=window[0], last_year=window[-1])
    return {
        code: {point["year"]: point[rank_key] for point in points if point[rank_key]}
        for code, points in history.items()
    }


def _spark(window: list[int], places: dict[int, int]) -> dict[str, Any] | None:
    """
    Мини-график мест в строке: первое место вверху, своя шкала строки не уже
    ``SPARK_MIN_SPAN`` мест; сдвиг за окно числом — форма линии его не заменяет.
    """
    known = [(index, places[year]) for index, year in enumerate(window) if year in places]
    if len(known) < MIN_BUMP_YEARS:
        return None
    values = [place for _index, place in known]
    span = max(SPARK_MIN_SPAN, max(values) - min(values))
    top = (max(values) + min(values)) / 2 - span / 2
    inner_width = SPARK_WIDTH - 2 * SPARK_PAD
    inner_height = SPARK_HEIGHT - 2 * SPARK_PAD

    def point(index: int, place: int) -> tuple[float, float]:
        x = SPARK_PAD + index * inner_width / max(len(window) - 1, 1)
        y = SPARK_PAD + (place - top) / span * inner_height
        return round(x, 1), round(y, 1)

    parts: list[str] = []
    previous_index = None
    for index, place in known:
        x, y = point(index, place)
        # Пропуск года — разрыв линии, а не прямая через него.
        command = "L" if previous_index is not None and index == previous_index + 1 else "M"
        parts.append(f"{command}{x} {y}")
        previous_index = index
    last_index, last_place = known[-1]
    dot_x, dot_y = point(last_index, last_place)
    first_year, first_place = window[known[0][0]], known[0][1]
    last_year = window[last_index]
    return {
        "path": " ".join(parts),
        "dot_x": dot_x,
        "dot_y": dot_y,
        "change": first_place - last_place,
        "change_abs": abs(first_place - last_place),
        "first_year": first_year,
        "last_year": last_year,
        "text": ", ".join(f"{window[index]} — {place}" for index, place in known),
    }


def _bump(
    rows: list[dict[str, Any]],
    window: list[int],
    places: dict[str, dict[int, int]],
    marked: set[str],
) -> dict[str, Any] | None:
    """Места по годам одним графиком: цветом — отмеченные регионы и мой, остальные — серым."""
    if not places or len(window) < MIN_BUMP_YEARS:
        return None
    lines = []
    for row in rows:
        code = row["territory_code"]
        points = places.get(code, {})
        short = territory_short_code(code, row["abbreviation"]) or row["name"]
        lines.append(
            {
                "name": row["name"],
                # У конца линии — сокращение и место года рейтинга: полное название — в подсказке.
                "label": f"{short} · {row['place']}",
                "values": [points.get(year) for year in window],
                "code": code,
                "primary": code in marked,
            }
        )
    max_rank = max(
        (place for points in places.values() for place in points.values()),
        default=len(rows),
    )
    return bump_option(window, lines, max_rank=max_rank)
