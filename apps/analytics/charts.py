"""
Графики аналитического раздела: кривая Лоренца, матрица связей, рассеяние, диаграмма Морана.

Общие построения — в ``apps.core.charts``.
"""

from __future__ import annotations

from html import escape
from typing import Any

from django.utils.translation import gettext_lazy as _

from apps.core import chart_layout as layout
from apps.core.charts import extreme_codes, time_marks
from apps.core.templatetags.formatting import ru_number

# Цвет вспомогательных линий: равенства на кривой Лоренца и осей диаграммы Морана.
GUIDE_COLOUR = "var(--text-muted)"

# Цвет линии подобранной зависимости.
FIT_COLOUR = "var(--attention)"

# Цвет подписи точки: мелкий текст — текстовой ступенью, а не цветом точки.
LABEL_COLOUR = "var(--text-secondary)"

# Цвет заливки области между кривой Лоренца и линией равенства.
AREA_COLOUR = "var(--accent-quiet)"

# Ширина подписи показателя на оси матрицы связей, точек.
MATRIX_LABEL = 150

# Облако точек: перекос — во сколько раз наибольшее значение больше медианы и от скольких
# точек о нём судить.
SKEW_RATIO = 4.0
SKEW_MIN_POINTS = 10

# Цвета расходящейся шкалы для матрицы связей: от обратной связи через отсутствие
# связи к прямой.
DIVERGING_SCALE = (
    "var(--scale-div-neg-3)",
    "var(--scale-div-neg-2)",
    "var(--scale-div-neg-1)",
    "var(--scale-div-zero)",
    "var(--scale-div-pos-1)",
    "var(--scale-div-pos-2)",
    "var(--scale-div-pos-3)",
)

# ---------------------------------------------------------------------------------------
# Неравенство
# ---------------------------------------------------------------------------------------


def lorenz_option(points: list[dict[str, float]], *, label: str = "") -> dict[str, Any]:
    """Построить кривую Лоренца с диагональю равенства."""
    curve = [
        [round(point["population"] * 100, 3), round(point["value"] * 100, 3)] for point in points
    ]

    return {
        "grid": layout.grid(legend=True, axis_name=True, bottom_name=True),
        "tooltip": {"trigger": "axis"},
        "legend": layout.legend(scroll=False),
        "xAxis": {
            "type": "value",
            "min": 0,
            "max": 100,
            **layout.bottom_axis_name(str(_("накопленная доля территорий, %"))),
        },
        "yAxis": layout.value_axis(str(_("накопленная доля величины, %")), min=0, max=100),
        "series": [
            {
                "name": str(_("равное распределение")),
                "type": "line",
                "data": [[0, 0], [100, 100]],
                "symbol": "none",
                # Цвет значка легенды — из itemStyle, иначе он берёт первый цвет палитры.
                "itemStyle": {"color": GUIDE_COLOUR},
                "lineStyle": {"color": GUIDE_COLOUR, "type": "dashed", "width": 1},
                "silent": True,
            },
            {
                "name": label or str(_("фактическое распределение")),
                "type": "line",
                "data": curve,
                "symbol": "none",
                "smooth": False,
                "areaStyle": {"color": AREA_COLOUR, "opacity": 0.5},
                "lineStyle": {"width": 2},
            },
        ],
    }


def measures_timeline_option(
    years: list[int],
    lines: list[dict[str, Any]],
    *,
    breaks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Построить динамику мер неравенства, каждая — к своему первому году, принятому за сто.

    Разрывы сопоставимости — вертикальными отметками на первой линии.
    """
    option: dict[str, Any] = {
        "grid": layout.grid(axis_name=True, end_labels=True),
        "legend": layout.legend(show=False),
        "tooltip": {"trigger": "axis"},
        "xAxis": {"type": "category", "data": [str(year) for year in years], "boundaryGap": False},
        "yAxis": layout.value_axis(str(_("первый год = 100")), scale=True),
        "series": [
            {
                "name": line["name"],
                "type": "line",
                "data": line["values"],
                "symbol": "circle",
                "symbolSize": 4,
                "connectNulls": False,
                "lineStyle": {"color": layout.palette_colour(index)},
                "itemStyle": {"color": layout.palette_colour(index)},
                "endLabel": layout.end_label(colour=layout.palette_ink(index)),
                "labelLayout": {"moveOverlap": "shiftY"},
                "emphasis": {"focus": "series"},
                "triggerLineEvent": True,
            }
            for index, line in enumerate(lines)
        ],
    }
    if breaks and option["series"]:
        option["series"][0]["markLine"] = time_marks(breaks)
    return option


def decomposition_option(groups: list[dict[str, Any]]) -> dict[str, Any]:
    """Построить вклад федеральных округов во внутригрупповое неравенство."""
    return {
        "grid": layout.grid(bottom_name=True, right=24),
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
        "xAxis": {"type": "value", **layout.bottom_axis_name(str(_("вклад в индекс")))},
        "yAxis": {
            "type": "category",
            "data": [group["name"] for group in groups],
            "axisLabel": {"fontSize": 11},
        },
        "series": [
            {
                "type": "bar",
                "data": [round(group["contribution"], 6) for group in groups],
                "barMaxWidth": 18,
            }
        ],
    }


# ---------------------------------------------------------------------------------------
# Связи между показателями
# ---------------------------------------------------------------------------------------


def matrix_option(
    labels: list[str],
    keys: list[str],
    cells: list[dict[str, Any]],
    *,
    pick: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    Матрица связей — нижний треугольник: по осям краткие названия показателей, в клетке —
    коэффициент. Незначимые с поправкой на множественность — серые без числа. Щелчок по
    клетке открывает разбор пары (``pick`` — селекторы полей пары, см. chart.js).

    Клетка — ``{"x", "y", "value", "significant"}`` в номерах показателей; ``value`` пусто —
    коэффициент не рассчитан.
    """
    significant: list[dict[str, Any]] = []
    quiet: list[dict[str, Any]] = []
    for cell in cells:
        if cell["y"] <= cell["x"] or cell["value"] is None:
            continue
        item = _matrix_cell(labels, cell)
        item["pick"] = [keys[cell["x"]], keys[cell["y"]]]
        # Первая строка и последний столбец треугольника пусты — их на осях нет.
        item["value"] = [cell["x"], cell["y"] - 1, cell["value"]]
        (significant if cell["significant"] else quiet).append(item)
    for item in quiet:
        item["label"] = {"show": False}

    def axis(names: list[str], **extra: Any) -> dict[str, Any]:
        return {
            "type": "category",
            "data": names,
            "splitArea": {"show": False},
            "axisTick": {"show": False},
            "axisLabel": {"fontSize": 11, "width": MATRIX_LABEL, "overflow": "truncate", **extra},
        }

    option: dict[str, Any] = {
        # Поле снизу оставлено под шкалу цвета.
        "grid": {"left": 8, "right": 8, "top": 8, "bottom": 52, "containLabel": True},
        # Подсказка и подпись — готовыми у каждой клетки: шаблон {@[2]} ECharts 6
        # печатает буквально, а подставленное в {b} экранирует.
        "tooltip": {"position": "top"},
        "xAxis": axis(labels[:-1], rotate=30, interval=0),
        "yAxis": {**axis(labels[1:], interval=0), "inverse": True},
        # Без ползунков: их подписи наезжают на названия. Шкала — только у значимых.
        "visualMap": {
            "min": -1,
            "max": 1,
            "calculable": False,
            "seriesIndex": 0,
            "orient": "horizontal",
            "left": "center",
            "bottom": 4,
            "itemHeight": 90,
            "textGap": 8,
            "inRange": {"color": list(DIVERGING_SCALE)},
            "text": [str(_("прямая")), str(_("обратная"))],
        },
        "series": [
            {
                "type": "heatmap",
                "data": significant,
                # Ячейка подписывается самим коэффициентом; текст подписи — у клетки.
                "label": {"show": True, "fontSize": 10},
                "itemStyle": {"borderColor": "var(--surface-base)", "borderWidth": 1},
                "emphasis": {"itemStyle": {"borderColor": "var(--text-primary)", "borderWidth": 2}},
                "cursor": "pointer",
            },
            {
                "type": "heatmap",
                "data": quiet,
                "itemStyle": {
                    "color": "var(--border-subtle)",
                    "borderColor": "var(--surface-base)",
                    "borderWidth": 1,
                },
                "emphasis": {"itemStyle": {"borderColor": "var(--text-primary)", "borderWidth": 2}},
                "cursor": "pointer",
            },
        ],
    }
    if pick:
        option["pick"] = pick
    return option


def _matrix_cell(labels: list[str], cell: dict[str, Any]) -> dict[str, Any]:
    """Клетка матрицы связей: подпись и подсказка с экранированными названиями."""
    value = ru_number(cell["value"], 2)
    pair = f"{escape(labels[cell['y']])} · {escape(labels[cell['x']])}"
    note = "" if cell["significant"] else f"<br>{escape(str(_('связь не подтверждена')))}"
    return {
        "name": f"{labels[cell['y']]} · {labels[cell['x']]}",
        "label": {"formatter": value},
        "tooltip": {"formatter": f"{pair}<br><strong>{value}</strong>{note}"},
    }


def is_skewed(values: list[float]) -> bool:
    """
    Перекос: значения положительны, и несколько крайних далеко от остальных — наибольшее
    больше медианы в разы. На обычной шкале такие точки сжимают облако в угол.
    """
    if len(values) < SKEW_MIN_POINTS or min(values) <= 0:
        return False
    ordered = sorted(values)
    median = ordered[len(ordered) // 2]
    return ordered[-1] / median >= SKEW_RATIO


def scatter_fit_option(
    points: list[dict[str, Any]],
    *,
    x_name: str = "",
    y_name: str = "",
    line: list[list[float]] | None = None,
    line_label: str = "",
    labels: dict[str, str] | None = None,
    log: str = "",
) -> dict[str, Any]:
    """
    Построить диаграмму рассеяния; линия — только при значимой связи. Подписаны крайние
    точки (``labels`` — подписи по коду территории, сокращения). ``log`` — оси
    в логарифмической шкале: «x», «y» или «xy»; точки с неположительным значением на такой
    оси не показываются.
    """
    log_x, log_y = "x" in log, "y" in log
    shown = [
        point for point in points if (not log_x or point["x"] > 0) and (not log_y or point["y"] > 0)
    ]
    extremes = extreme_codes(shown)
    data: list[dict[str, Any]] = []
    for point in shown:
        item: dict[str, Any] = {"name": point["name"], "value": [point["x"], point["y"]]}
        short = (labels or {}).get(point.get("code", ""), "")
        if short and point.get("code") in extremes:
            item["label"] = {"show": True, "formatter": short}
        data.append(item)
    series: list[dict[str, Any]] = [
        {
            "name": str(_("субъекты")),
            "type": "scatter",
            "symbolSize": 9,
            "data": data,
            "emphasis": {"focus": "series"},
            "label": {
                "show": False,
                "position": "right",
                "distance": 3,
                "fontSize": 10,
                "color": LABEL_COLOUR,
                "textBorderWidth": 0,
            },
            "labelLayout": {"hideOverlap": True},
        }
    ]

    if line:
        series.append(
            {
                "name": line_label or str(_("линия связи")),
                "type": "line",
                "data": line,
                "symbol": "none",
                # Цвет значка легенды — тот же, что у линии.
                "itemStyle": {"color": FIT_COLOUR},
                "lineStyle": {"color": FIT_COLOUR, "width": 2},
                "silent": True,
                "tooltip": {"show": False},
            }
        )

    def scale(logarithmic: bool) -> dict[str, Any]:
        return {"type": "log", "logBase": 10} if logarithmic else {"type": "value", "scale": True}

    # Легенда из одного пункта «субъекты» ничего не объясняет — только вместе с линией.
    return {
        "grid": layout.grid(
            legend=bool(line), axis_name=bool(y_name), bottom_name=bool(x_name), right=24
        ),
        "legend": layout.legend(scroll=False) if line else layout.legend(show=False),
        "tooltip": {"trigger": "item"},
        "xAxis": {**scale(log_x), **layout.bottom_axis_name(x_name)},
        "yAxis": {**layout.value_axis(y_name), **scale(log_y)},
        "series": series,
    }


def lag_option(profile: list[dict[str, Any]]) -> dict[str, Any]:
    """Построить профиль связи при разных сдвигах во времени."""
    return {
        "grid": layout.grid(axis_name=True, bottom_name=True),
        "tooltip": {"trigger": "axis"},
        "xAxis": {
            "type": "category",
            "data": [str(item["lag"]) for item in profile],
            **layout.bottom_axis_name(str(_("сдвиг, лет"))),
        },
        "yAxis": layout.value_axis(str(_("коэффициент")), min=-1, max=1),
        "series": [
            {
                "type": "bar",
                "barMaxWidth": 26,
                "data": [
                    round(item["correlation"].coefficient, 3)
                    if item["correlation"].coefficient is not None
                    else None
                    for item in profile
                ],
            }
        ],
    }


# ---------------------------------------------------------------------------------------
# Пространственный анализ
# ---------------------------------------------------------------------------------------


def moran_option(
    quadrants: list[dict[str, Any]],
    *,
    slope: float,
    limit: float,
) -> dict[str, Any]:
    """Построить диаграмму Морана: значение территории и среднее соседей, четверти по типам."""
    return {
        "grid": layout.grid(legend=True, axis_name=True, bottom_name=True, right=24),
        "legend": layout.legend(),
        "tooltip": {"trigger": "item"},
        "xAxis": {
            "type": "value",
            "scale": True,
            **layout.bottom_axis_name(str(_("значение территории, σ"))),
        },
        "yAxis": layout.value_axis(str(_("среднее у соседей, σ")), scale=True),
        "series": [
            *[
                {
                    "name": group["name"],
                    "type": "scatter",
                    "symbolSize": 9,
                    "data": [
                        {"name": point["name"], "value": [point["x"], point["y"]]}
                        for point in group["points"]
                    ],
                    "emphasis": {"focus": "series"},
                }
                for group in quadrants
            ],
            {
                "name": str(_("линия индекса")),
                "type": "line",
                "data": [[-limit, -limit * slope], [limit, limit * slope]],
                "symbol": "none",
                "lineStyle": {"color": FIT_COLOUR, "width": 2},
                "silent": True,
                "markLine": {
                    "symbol": "none",
                    "silent": True,
                    "label": {"show": False},
                    "lineStyle": {"color": GUIDE_COLOUR, "type": "dashed", "width": 1},
                    "data": [{"xAxis": 0}, {"yAxis": 0}],
                },
            },
        ],
    }


# ---------------------------------------------------------------------------------------
# Интегральный индекс
# ---------------------------------------------------------------------------------------


def contribution_option(
    names: list[str],
    components: list[dict[str, Any]],
) -> dict[str, Any]:
    """Построить состав итоговой оценки территорий по вкладам показателей."""
    return {
        "grid": layout.grid(legend=True, axis_name=True),
        "legend": layout.legend(),
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
        "xAxis": {
            "type": "category",
            "data": names,
            "axisLabel": {"rotate": 40, "fontSize": 10, "width": 130, "overflow": "truncate"},
        },
        "yAxis": layout.value_axis(str(_("вклад в оценку"))),
        "series": [
            {
                "name": component["label"],
                "type": "bar",
                "stack": "index",
                "data": component["values"],
                "emphasis": {"focus": "series"},
            }
            for component in components
        ],
    }
