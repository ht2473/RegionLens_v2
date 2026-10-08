"""Настройки графиков ECharts, собираемые на сервере."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from django.utils.html import escape
from django.utils.translation import gettext_lazy as _

from apps.core import chart_layout as layout
from apps.core.templatetags.formatting import ru_number

# Цвет линии страны — ориентира среди сравниваемых.
COUNTRY_COLOUR = "var(--text-secondary)"

# Цвет отметок разрывов сопоставимости.
BREAK_COLOUR = "var(--attention)"

# Цвет заливки межквартильного коридора.
CORRIDOR_COLOUR = "var(--accent-quiet)"

# Цвет спокойной отметки на оси времени: год, выбранный на рабочей поверхности.
MARK_COLOUR = "var(--text-muted)"

# Облако точек: сколько крайних точек подписывать по каждой оси с каждой стороны.
EXTREME_LABELS = 3

# Полоса точек: размер точки и выбранной, поле сверху под подписи отметок, цвет точки
# без класса шкалы.
STRIP_POINT = 9
STRIP_SELECTED = 13
STRIP_TOP = 36
STRIP_NO_CLASS = "var(--text-muted)"

# Линии окружения на графике мест — серые; линия под указателем — цвета акцента.
CONTEXT_COLOUR = "var(--border-strong)"
HOVER_COLOUR = "var(--text-accent)"

# Длина ряда, начиная с которой точки на линии не показываются: на ширине карточки
# точки в 5 px сливаются в полосу уже на полутора десятках лет.
MAX_POINT_MARKERS = 12

# Толщина выделенной линии.
PRIMARY_WIDTH = 3


# ---------------------------------------------------------------------------------------
# Динамика во времени
# ---------------------------------------------------------------------------------------


def timeline_option(
    years: Sequence[int | str],
    lines: list[dict[str, Any]],
    *,
    unit: str = "",
    breaks: list[dict[str, Any]] | None = None,
    corridor: dict[str, list[Any]] | None = None,
    year_mark: int | None = None,
    end_labels: bool = True,
) -> dict[str, Any]:
    """
    Построить график динамики; деления оси — годы или подписи месяцев.

    Линия — ``{"name", "values"}`` с необязательными ``country``, ``primary``, ``muted``
    и ``colour``; коридор — прозрачная нижняя область и закрашенная над ней.
    """
    series: list[dict[str, Any]] = []

    if corridor:
        series.append(
            {
                "name": str(_("Нижняя граница")),
                "type": "line",
                "data": corridor["lower"],
                "lineStyle": {"opacity": 0},
                "stack": "corridor",
                "symbol": "none",
                "silent": True,
                "tooltip": {"show": False},
                "legendHoverLink": False,
            }
        )
        series.append(
            {
                "name": str(_("Половина субъектов")),
                "type": "line",
                "data": corridor["band"],
                "lineStyle": {"opacity": 0},
                "areaStyle": {"color": CORRIDOR_COLOUR, "opacity": 0.75},
                "stack": "corridor",
                "symbol": "none",
                "silent": True,
                "tooltip": {"show": False},
            }
        )

    # Цвета отсчитываются после рядов коридора — так же, как их назначает библиотека.
    palette_index = len(series)
    for line in lines:
        muted = bool(line.get("muted"))
        primary = bool(line.get("primary"))
        entry: dict[str, Any] = {
            "name": line["name"],
            "type": "line",
            "data": line["values"],
            "symbol": "circle",
            "symbolSize": 5,
            "showSymbol": len(years) <= MAX_POINT_MARKERS and not muted,
            "connectNulls": False,
            "emphasis": {"focus": "series"},
            "blur": layout.blur(),
            # Выделение щелчком по линии, а не только по точке.
            "triggerLineEvent": True,
        }
        if line.get("country"):
            entry["lineStyle"] = {"color": COUNTRY_COLOUR, "width": 2, "type": "dashed"}
            entry["itemStyle"] = {"color": COUNTRY_COLOUR}
            ink = str(line.get("ink") or COUNTRY_COLOUR)
        elif line.get("colour"):
            # Явный цвет: подпись вне холста должна совпасть с линией.
            entry["lineStyle"] = {"color": line["colour"]}
            entry["itemStyle"] = {"color": line["colour"]}
            ink = str(line.get("ink") or line["colour"])
        else:
            # Цвет палитры назначается здесь, чтобы знать цвет подписи у конца линии.
            entry["lineStyle"] = {"color": layout.palette_colour(palette_index)}
            entry["itemStyle"] = {"color": layout.palette_colour(palette_index)}
            ink = layout.palette_ink(palette_index)
            palette_index += 1
        if line.get("width"):
            entry.setdefault("lineStyle", {})["width"] = line["width"]
        if primary:
            entry.setdefault("lineStyle", {})["width"] = line.get("width") or PRIMARY_WIDTH
            # Выделенная линия — поверх окружения.
            entry["z"] = 5
        if muted:
            entry.setdefault("lineStyle", {})["opacity"] = layout.MUTED_OPACITY
        if end_labels:
            entry["endLabel"] = layout.end_label(primary=primary, muted=muted, colour=ink)
            # Сошедшиеся подписи разводятся по вертикали.
            entry["labelLayout"] = {"moveOverlap": "shiftY"}
        if (breaks or year_mark is not None) and line is lines[0]:
            entry["markLine"] = time_marks(breaks or [], year_mark)
        series.append(entry)

    return {
        "grid": layout.grid(axis_name=bool(unit), end_labels=end_labels),
        "legend": layout.legend(show=False),
        "tooltip": {"trigger": "axis", "valueFormatter": None},
        "xAxis": {"type": "category", "data": [str(year) for year in years], "boundaryGap": False},
        "yAxis": layout.value_axis(unit, scale=True),
        "series": series,
        **_territory_links(lines),
    }


def time_marks(breaks: list[dict[str, Any]], year_mark: int | None = None) -> dict[str, Any]:
    """
    Собрать вертикальные отметки на оси времени: разрывы и выбранный год.

    Подписывается только год: название разрыва легло бы поперёк графика, оно названо
    под графиком. Выбранный год отмечается спокойным цветом без подписи.
    """
    data: list[dict[str, Any]] = [
        {
            "xAxis": str(item["year"]),
            "name": str(item["year"]),
            "value": item.get("label", ""),
        }
        for item in breaks
    ]
    if year_mark is not None:
        data.append(
            {
                "xAxis": str(year_mark),
                "name": str(year_mark),
                "value": "",
                "lineStyle": {"color": MARK_COLOUR, "type": "dotted", "width": 1},
                # Год уже написан делением оси под чертой.
                "label": {"show": False},
            }
        )

    return {
        "symbol": "none",
        "silent": False,
        "label": {
            "show": True,
            "formatter": "{b}",
            "position": "end",
            "rotate": 0,
            "distance": 4,
            "color": BREAK_COLOUR,
            "fontSize": 10,
        },
        "lineStyle": {"color": BREAK_COLOUR, "type": "dashed", "width": 1},
        "data": data,
    }


def _territory_links(lines: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Сопоставить названия линий кодам территорий (служебный раздел ``territories``).

    Клиент снимает раздел до построения и по нему связывает линии со строками таблицы.
    """
    links = {line["name"]: line["code"] for line in lines if line.get("code")}
    return {"territories": links} if links else {}


# Размер миниатюрного графика в единицах чертежа и сколько лет он показывает.
SPARK_WIDTH = 120
SPARK_HEIGHT = 32
SPARK_YEARS = 12


def sparkline_path(values: list[float | None]) -> dict[str, Any] | None:
    """Миниатюрный график хода величины — путь SVG без осей; пропуск разрывает линию."""
    points = values[-SPARK_YEARS:]
    known = [value for value in points if value is not None]
    if len(known) < 2:  # noqa: PLR2004 - по одной точке линии нет
        return None
    low, high = min(known), max(known)
    spread = (high - low) or 1.0
    step = SPARK_WIDTH / (len(points) - 1)
    pad = 3.0

    segments: list[str] = []
    pen_down = False
    last: tuple[float, float] = (0.0, 0.0)
    for index, value in enumerate(points):
        if value is None:
            pen_down = False
            continue
        x = round(index * step, 1)
        y = round(pad + (1 - (value - low) / spread) * (SPARK_HEIGHT - 2 * pad), 1)
        segments.append(f"{'L' if pen_down else 'M'}{x} {y}")
        pen_down = True
        last = (x, y)
    return {
        "path": " ".join(segments),
        "width": SPARK_WIDTH,
        "height": SPARK_HEIGHT,
        # Координаты — строками: дробь шаблон записал бы с запятой.
        "end_x": f"{last[0]:g}",
        "end_y": f"{last[1]:g}",
    }


# ---------------------------------------------------------------------------------------
# Распределения и сопоставления
# ---------------------------------------------------------------------------------------


def strip_option(
    points: list[dict[str, Any]],
    *,
    marks: list[dict[str, Any]],
    unit: str = "",
) -> dict[str, Any]:
    """
    Полоса точек: точка — регион, по горизонтали — значение. Сдвиг по вертикали, чтобы
    точки не перекрывались, считает браузер по ширине холста (служебный раздел ``swarm``).

    Точка — ``{"name", "value", "colour", "tooltip"}`` с необязательными ``selected``
    и ``label`` (подпись выбранной); отметка — ``{"value", "label", "strong"}``: медиана,
    квартили, Россия. Подсказка — готовым текстом с экранированными названиями.
    """
    data: list[dict[str, Any]] = []
    for point in points:
        item: dict[str, Any] = {
            "name": point["name"],
            "value": [point["value"], 0],
            "itemStyle": {"color": point["colour"] or STRIP_NO_CLASS},
            "tooltip": {"formatter": point["tooltip"]},
        }
        if point.get("selected"):
            item["symbolSize"] = STRIP_SELECTED
            item["itemStyle"].update(borderColor="var(--text-primary)", borderWidth=2)
            item["label"] = {
                "show": True,
                "formatter": point.get("label") or point["name"],
                "position": "top",
                "distance": 4,
                "fontSize": 11,
                "fontWeight": "bold",
                "color": "var(--text-primary)",
                "textBorderWidth": 0,
            }
            # Выбранные — поверх соседей.
            item["z"] = 3
        data.append(item)

    return {
        "grid": {**layout.grid(bottom_name=bool(unit), right=24), "top": STRIP_TOP},
        "tooltip": {"trigger": "item"},
        "xAxis": {
            "type": "value",
            "scale": True,
            "splitLine": {"lineStyle": {"type": "dashed"}},
            # На узком холсте подписи делений сходятся: лишние прячутся.
            "axisLabel": {"hideOverlap": True},
            **(layout.bottom_axis_name(unit) if unit else {}),
        },
        "yAxis": {"type": "value", "show": False, "min": -1, "max": 1},
        "series": [
            {
                "type": "scatter",
                "symbolSize": STRIP_POINT,
                "data": data,
                "labelLayout": {"hideOverlap": True},
                "emphasis": {"scale": 1.4},
                "markLine": {
                    "symbol": "none",
                    "silent": True,
                    "data": [
                        {
                            "xAxis": mark["value"],
                            "name": mark["label"],
                            "lineStyle": {
                                "color": BREAK_COLOUR if mark.get("strong") else MARK_COLOUR,
                                "type": "solid" if mark.get("strong") else "dashed",
                                "width": 1,
                            },
                            "label": {
                                "color": BREAK_COLOUR if mark.get("strong") else MARK_COLOUR,
                                # Подпись России — выше подписи медианы: рядом они не сходятся.
                                "distance": 17 if mark.get("strong") else 4,
                            },
                        }
                        for mark in marks
                    ],
                    "label": {
                        "show": True,
                        "formatter": "{b}",
                        "position": "end",
                        "distance": 4,
                        "fontSize": 10,
                    },
                },
            }
        ],
        "swarm": {"series": 0},
    }


def bump_option(
    years: list[int],
    lines: list[dict[str, Any]],
    *,
    max_rank: int,
) -> dict[str, Any]:
    """
    Места в рейтинге по годам: первое место вверху. Все линии серые; цветные и подписанные —
    выделенные (``primary``) и та, что под указателем. Линия — ``{"name", "values"}``
    с необязательными ``label`` (подпись у конца), ``code`` и ``primary``.

    Оформление серых линий одинаково у всех 85 — оно приходит одним образцом
    (``seriesTemplate``), а у ряда — только имя, значения и подпись.
    """

    def styled(colour: str, ink: str, *, primary: bool) -> dict[str, Any]:
        label = layout.end_label(primary=primary, colour=ink)
        label["show"] = primary
        hover = colour if primary else HOVER_COLOUR
        return {
            "type": "line",
            "symbol": "circle",
            "symbolSize": 6 if primary else 3,
            "showSymbol": primary,
            "connectNulls": False,
            "lineStyle": {"color": colour, "width": PRIMARY_WIDTH if primary else 1},
            "itemStyle": {"color": colour},
            "endLabel": label,
            "labelLayout": {"moveOverlap": "shiftY"},
            # Под указателем серая линия получает цвет и подпись, остальные бледнеют.
            "emphasis": {
                "focus": "series",
                "lineStyle": {"color": hover, "width": PRIMARY_WIDTH},
                "itemStyle": {"color": hover},
                "endLabel": {"show": True},
            },
            "blur": layout.blur(),
            "triggerLineEvent": True,
            # Выделенные — поверх серых.
            "z": 5 if primary else 2,
        }

    series: list[dict[str, Any]] = []
    colour_index = 0
    for line in lines:
        entry: dict[str, Any] = {"name": line["name"], "data": line["values"]}
        if line.get("primary"):
            entry.update(
                styled(
                    layout.palette_colour(colour_index),
                    layout.palette_ink(colour_index),
                    primary=True,
                )
            )
            colour_index += 1
        else:
            entry["seriesTemplate"] = True
        entry.setdefault("endLabel", {})["formatter"] = line.get("label") or line["name"]
        series.append(entry)

    return {
        "grid": layout.grid(axis_name=True, end_labels=True),
        "legend": layout.legend(show=False),
        "tooltip": {"trigger": "item"},
        "xAxis": {"type": "category", "data": [str(year) for year in years], "boundaryGap": False},
        "yAxis": layout.value_axis(
            str(_("место")),
            inverse=True,
            min=1,
            max=max_rank,
            minInterval=1,
        ),
        "series": series,
        # Образец оформления серых линий (chart-options.js раскладывает его по рядам).
        "seriesTemplate": styled(CONTEXT_COLOUR, HOVER_COLOUR, primary=False),
        **_territory_links(lines),
    }


def dumbbell_option(
    rows: list[dict[str, Any]],
    *,
    before: int,
    after: int,
    precision: int | None = None,
    unit: str = "",
) -> dict[str, Any] | None:
    """
    «Было — стало»: значения двух лет у каждого субъекта — точки, соединённые отрезком.

    Строка — ``{"name", "short", "before", "after"}`` в порядке показа (сверху вниз).
    Подсказки — готовым текстом с экранированными названиями.
    """
    shown = [row for row in rows if row["before"] is not None and row["after"] is not None]
    if not shown:
        return None

    def tip(row: dict[str, Any]) -> str:
        return (
            f"{escape(row['name'])}<br>{before}: {ru_number(row['before'], precision)}"
            f"<br>{after}: <strong>{ru_number(row['after'], precision)}</strong>"
        )

    return {
        "grid": layout.grid(legend=True, bottom_name=bool(unit), right=24),
        "legend": layout.legend(scroll=False),
        "tooltip": {"trigger": "item"},
        "xAxis": {
            "type": "value",
            "scale": True,
            "splitLine": {"lineStyle": {"type": "dashed"}},
            **(layout.bottom_axis_name(unit) if unit else {}),
        },
        "yAxis": {
            "type": "category",
            "data": [row["short"] or row["name"] for row in shown],
            "inverse": True,
            "axisTick": {"show": False},
        },
        "series": [
            {
                "name": str(before),
                "type": "scatter",
                "symbolSize": 9,
                "itemStyle": {"color": MARK_COLOUR},
                "data": [
                    {"value": [row["before"], index], "tooltip": {"formatter": tip(row)}}
                    for index, row in enumerate(shown)
                ],
                # Отрезки «было — стало» — парами точек отметок: вида «lines» в сборке нет.
                "markLine": {
                    "symbol": "none",
                    "silent": True,
                    "label": {"show": False},
                    "lineStyle": {"color": MARK_COLOUR, "type": "solid", "width": 2},
                    "data": [
                        [{"coord": [row["before"], index]}, {"coord": [row["after"], index]}]
                        for index, row in enumerate(shown)
                    ],
                },
            },
            {
                "name": str(after),
                "type": "scatter",
                "symbolSize": 11,
                "itemStyle": {"color": layout.palette_colour(0)},
                "data": [
                    {"value": [row["after"], index], "tooltip": {"formatter": tip(row)}}
                    for index, row in enumerate(shown)
                ],
            },
        ],
    }


def extreme_codes(points: list[dict[str, Any]], count: int = EXTREME_LABELS) -> set[str]:
    """Коды крайних точек облака: наибольшие и наименьшие по каждой оси."""
    chosen: set[str] = set()
    for axis in ("x", "y"):
        ordered = sorted(points, key=lambda point: point[axis])
        for point in ordered[:count] + ordered[-count:]:
            if point.get("code"):
                chosen.add(point["code"])
    return chosen


def scatter_option(
    points: list[dict[str, Any]],
    *,
    x_name: str = "",
    y_name: str = "",
    labels: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    Построить диаграмму рассеяния сопоставления двух рядов; ``labels`` — подписи крайних
    точек по коду территории.
    """
    horizontal_axis: dict[str, Any] = {"type": "value", "scale": True}
    if x_name:
        horizontal_axis.update(layout.bottom_axis_name(x_name))
    extremes = extreme_codes(points) if labels else set()

    def item(point: dict[str, Any]) -> dict[str, Any]:
        entry: dict[str, Any] = {"name": point["name"], "value": [point["x"], point["y"]]}
        label = (labels or {}).get(point.get("code", ""), "")
        if label and point.get("code") in extremes:
            entry["label"] = {"show": True, "formatter": label}
        return entry

    return {
        "grid": layout.grid(axis_name=bool(y_name), bottom_name=bool(x_name), right=24),
        "tooltip": {"trigger": "item"},
        "xAxis": horizontal_axis,
        "yAxis": layout.value_axis(y_name, scale=True),
        "series": [
            {
                "type": "scatter",
                "symbolSize": 8,
                "data": [item(point) for point in points],
                "label": {
                    "show": False,
                    "position": "right",
                    "distance": 3,
                    "fontSize": 10,
                    "color": "var(--text-secondary)",
                    "textBorderWidth": 0,
                },
                "labelLayout": {"hideOverlap": True},
            }
        ],
    }
