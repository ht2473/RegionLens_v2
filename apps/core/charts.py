"""Настройки графиков ECharts, собираемые на сервере."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from django.utils.translation import gettext_lazy as _

from apps.core import chart_layout as layout

# Цвет линии страны — ориентира среди сравниваемых.
COUNTRY_COLOUR = "var(--text-secondary)"

# Цвет отметок разрывов сопоставимости.
BREAK_COLOUR = "var(--attention)"

# Цвет заливки межквартильного коридора.
CORRIDOR_COLOUR = "var(--accent-quiet)"

# Цвет спокойной отметки на оси времени: год, выбранный на рабочей поверхности.
MARK_COLOUR = "var(--text-muted)"

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


def histogram_option(
    categories: list[str],
    layers: list[dict[str, Any]],
    *,
    unit: str = "",
) -> dict[str, Any]:
    """
    Построить гистограмму распределения, разобранную по классам шкалы.

    Столбец складывается из долей классов: интервал гистограммы бывает шире класса.
    """
    return {
        "grid": layout.grid(axis_name=bool(unit)),
        "legend": layout.legend(show=False),
        # Подсказка по доле: перечень по всему столбцу состоял бы из нулей.
        "tooltip": {"trigger": "item", "formatter": "{a}<br>{c}"},
        "xAxis": {
            "type": "category",
            "data": categories,
            "axisLabel": {"fontSize": 11, "hideOverlap": True},
        },
        "yAxis": layout.value_axis(unit, scale=False, minInterval=1),
        "series": [
            {
                "name": layer["name"],
                "type": "bar",
                "stack": "bins",
                "data": layer["values"],
                "itemStyle": {"color": layer["colour"]},
                "barCategoryGap": "12%",
            }
            for layer in layers
        ],
    }


def bump_option(
    years: list[int],
    lines: list[dict[str, Any]],
    *,
    max_rank: int,
) -> dict[str, Any]:
    """Построить график движения позиций в рейтинге: первое место вверху."""
    series: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        primary = bool(line.get("primary"))
        muted = bool(line.get("muted"))
        entry: dict[str, Any] = {
            "name": line["name"],
            "type": "line",
            "data": line["values"],
            "symbol": "circle",
            "symbolSize": 6,
            "connectNulls": False,
            "itemStyle": {"color": layout.palette_colour(index)},
            "endLabel": layout.end_label(
                primary=primary, muted=muted, colour=layout.palette_ink(index)
            ),
            "labelLayout": {"moveOverlap": "shiftY"},
            "emphasis": {"focus": "series"},
            "blur": layout.blur(),
            "triggerLineEvent": True,
        }
        entry["lineStyle"] = {"color": layout.palette_colour(index)}
        if primary:
            entry["lineStyle"]["width"] = PRIMARY_WIDTH
            # Выделенная линия — поверх окружения.
            entry["z"] = 5
        if muted:
            entry["lineStyle"]["opacity"] = layout.MUTED_OPACITY
            entry["itemStyle"] = {"opacity": layout.MUTED_OPACITY}
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
        **_territory_links(lines),
    }


def radar_option(
    indicators: list[dict[str, Any]],
    entries: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Построить лепестковую диаграмму профиля территорий.

    Оси нормированы долей от размаха по сравниваемым: 100 — лучшее значение среди них.
    """
    return {
        "tooltip": {"trigger": "item"},
        "legend": layout.legend(),
        "radar": {
            "indicator": indicators,
            "radius": "62%",
            "center": ["50%", "58%"],
            "axisName": {"fontSize": 10, "color": "var(--text-secondary)"},
            "splitLine": {"lineStyle": {"color": "var(--border-subtle)"}},
            "splitArea": {"show": False},
            "axisLine": {"lineStyle": {"color": "var(--border-subtle)"}},
        },
        "series": [
            {
                "type": "radar",
                "data": entries,
                "symbolSize": 4,
                "areaStyle": {"opacity": 0.08},
                "lineStyle": {"width": 2},
            }
        ],
    }


def scatter_option(
    points: list[dict[str, Any]],
    *,
    x_name: str = "",
    y_name: str = "",
) -> dict[str, Any]:
    """Построить диаграмму рассеяния сопоставления двух рядов."""
    horizontal_axis: dict[str, Any] = {"type": "value", "scale": True}
    if x_name:
        horizontal_axis.update(layout.bottom_axis_name(x_name))

    return {
        "grid": layout.grid(axis_name=bool(y_name), bottom_name=bool(x_name), right=24),
        "tooltip": {"trigger": "item"},
        "xAxis": horizontal_axis,
        "yAxis": layout.value_axis(y_name, scale=True),
        "series": [
            {
                "type": "scatter",
                "symbolSize": 8,
                "data": [
                    {"name": point["name"], "value": [point["x"], point["y"]]} for point in points
                ],
            }
        ],
    }
