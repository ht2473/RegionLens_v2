"""Общие размеры полей графика и сборщики легенды, осей и подписей у концов линий."""

from __future__ import annotations

from typing import Any

# Высота строки легенды: значок (8–10 px) и подпись в 12 px с полями.
LEGEND_BAND = 26

# Поле справа под подписи у концов линий и наибольшая ширина подписи; длинная
# подпись переносится по словам: обрезанную «Стандартное отк…» не узнать.
END_LABEL_BAND = 186
END_LABEL_WIDTH = 168
END_LABEL_LINE = 14

# Высота строки подписи оси значений над полем графика.
AXIS_NAME_BAND = 20

# Отступ подписи оси значений от верхнего конца оси.
AXIS_NAME_GAP = 10

# Отступ подписи горизонтальной оси, вынесенной под неё.
BOTTOM_NAME_GAP = 30

# Поле снизу, необходимое для вынесенной под ось подписи.
BOTTOM_NAME_BAND = 26

# Наибольшая ширина подписи оси: название ряда с единицей и годом шире поля графика.
AXIS_NAME_WIDTH = 260

# Непрозрачность приглушённой линии и её подписи.
MUTED_OPACITY = 0.4
MUTED_LABEL_OPACITY = 0.8

# Непрозрачность окружения при наведении на одну линию; своя у библиотеки — 0,1,
# и окружение почти исчезает.
BLUR_OPACITY = 0.25
BLUR_LABEL_OPACITY = 0.35


def blur() -> dict[str, Any]:
    """Состояние линии, на которую не наведён указатель, при выделении соседней."""
    return {
        "lineStyle": {"opacity": BLUR_OPACITY},
        "itemStyle": {"opacity": BLUR_OPACITY},
        "endLabel": {"opacity": BLUR_LABEL_OPACITY},
    }


def grid(
    *,
    legend: bool = False,
    axis_name: bool = False,
    bottom_name: bool = False,
    end_labels: bool = False,
    left: int = 8,
    right: int = 16,
) -> dict[str, Any]:
    """Собрать поле графика с отступами под легенду и подписи осей."""
    top = 8
    if legend:
        top += LEGEND_BAND
    if axis_name:
        top += AXIS_NAME_BAND

    return {
        "left": left,
        "right": END_LABEL_BAND if end_labels else right,
        "top": top,
        "bottom": BOTTOM_NAME_BAND if bottom_name else 8,
        "containLabel": True,
    }


# Число цветов палитры рядов, как в общих настройках графика. Цвет назначается линии
# здесь, чтобы подпись у её конца набиралась тем же цветом.
PALETTE_SIZE = 8


def palette_colour(index: int) -> str:
    """Цвет линии по её месту в наборе."""
    return f"var(--series-{index % PALETTE_SIZE + 1})"


def palette_ink(index: int) -> str:
    """Цвет подписи у конца линии: текстовая ступень того же цвета (контраст 4,5 : 1)."""
    return f"var(--series-{index % PALETTE_SIZE + 1}-ink)"


def end_label(*, primary: bool = False, muted: bool = False, colour: str = "") -> dict[str, Any]:
    """Собрать подпись у правого конца линии: полужирную у выделенной, бледнее у приглушённой."""
    return {
        "show": True,
        "formatter": "{a}",
        "distance": 6,
        "fontSize": 11,
        "fontWeight": "bold" if primary else "normal",
        # Подпись приглушается слабее линии: в тёмной теме бледный текст неразличим.
        "opacity": MUTED_LABEL_OPACITY if muted else 1,
        "width": END_LABEL_WIDTH,
        "overflow": "break",
        "lineHeight": END_LABEL_LINE,
        "align": "left",
        # Цвет линии и без обводки: по умолчанию подпись белая с тёмной обводкой.
        "color": colour or "inherit",
        "textBorderWidth": 0,
    }


def legend(*, scroll: bool = True, **overrides: Any) -> dict[str, Any]:
    """Собрать легенду над полем графика, по умолчанию прокручиваемую."""
    options: dict[str, Any] = {
        "show": True,
        "top": 0,
        "left": "center",
        "icon": "roundRect",
        "itemWidth": 14,
        "itemHeight": 8,
        "itemGap": 14,
        "padding": [4, 0, 4, 0],
        # Полное название — в подсказке.
        "textStyle": {"width": 240, "overflow": "truncate"},
        "tooltip": {"show": True},
    }
    if scroll:
        options["type"] = "scroll"
    options.update(overrides)
    return options


def value_axis(name: str = "", **overrides: Any) -> dict[str, Any]:
    """
    Собрать вертикальную ось значений с подписью по левому краю.

    По центру подпись выходит за холст. У перевёрнутой оси подпись переносится к её
    началу, иначе ложится на подписи годов.
    """
    axis: dict[str, Any] = {"type": "value"}
    if name:
        axis["name"] = name
        axis["nameGap"] = AXIS_NAME_GAP
        axis["nameLocation"] = "start" if overrides.get("inverse") else "end"
        axis["nameTextStyle"] = {
            "align": "left",
            "width": AXIS_NAME_WIDTH,
            "overflow": "truncate",
        }
    axis.update(overrides)
    return axis


def bottom_axis_name(name: str) -> dict[str, Any]:
    """Собрать подпись горизонтальной оси, вынесенную под неё по центру."""
    return {
        "name": name,
        "nameLocation": "middle",
        "nameGap": BOTTOM_NAME_GAP,
        "nameTextStyle": {"width": AXIS_NAME_WIDTH * 2, "overflow": "truncate"},
    }
