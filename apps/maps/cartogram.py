"""
Географическая картограмма, собираемая на сервере в SVG.

Контур субъекта описан один раз в определениях; общая карта и врезки ссылаются на него,
врезка — то же изображение с поворотом, масштабом и сдвигом. Врезки — в полосе под картой.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from django.utils.translation import gettext_lazy as _

from .boundaries import Boundary, boundaries_geometry
from .projection import Albers, fit_width

# Ширина чертежа: при ней координаты пишутся целыми числами без видимой потери.
CANVAS_WIDTH = 2000.0

# Поле вокруг карты, чтобы обводка крайних субъектов не обрезалась.
CANVAS_PADDING = 10.0

# Знаков после запятой в координатах контура.
PRECISION = 0

# Наименьшее число точек, при котором кольцо описывает фигуру, а не отрезок.
MIN_RING_POINTS = 3

# Полоса врезок под картой: отступ от карты, высота рамок, строка подписи
# и промежуток между рамками.
INSET_BAND_OFFSET = 20.0
INSET_MAX_HEIGHT = 312.0
INSET_CAPTION = 40.0
INSET_GAP = 28.0
INSET_EDGE = 80.0


@dataclass(frozen=True, slots=True)
class Inset:
    """
    Врезка: окно карты, показанное отдельно и крупнее.

    ``codes`` — субъекты врезки, они подписываются; ``padding`` — доля стороны окна
    вокруг них: чем теснее окно, тем крупнее содержимое.
    """

    key: str
    title: Any
    codes: tuple[str, ...]
    padding: float


# Во врезках — субъекты, у которых на чертеже шириной 1000 сторона квадрата равной
# площади меньше 12 единиц (у Севастополя — 3,1).
INSETS: tuple[Inset, ...] = (
    Inset("moscow", _("Москва"), ("RU-MOW",), 0.55),
    Inset("spb", _("Санкт-Петербург"), ("RU-SPE",), 0.55),
    Inset("sevastopol", _("Севастополь"), ("RU-SEV",), 0.90),
    Inset(
        "caucasus",
        _("Северный Кавказ"),
        ("RU-AD", "RU-KC", "RU-KB", "RU-SE", "RU-IN", "RU-CE", "RU-DA"),
        0.05,
    ),
)


@dataclass(frozen=True, slots=True)
class Shape:
    """Контур субъекта, переведённый в единицы чертежа."""

    code: str
    path: str
    box: tuple[float, float, float, float]
    rings: tuple[tuple[tuple[float, float], ...], ...]


@dataclass(frozen=True, slots=True)
class InsetLayout:
    """Рамка врезки, преобразование к ней и состав попавших в неё субъектов."""

    key: str
    title: Any
    frame: tuple[float, float, float, float]
    transform: str
    caption_x: float
    caption_y: float
    codes: tuple[str, ...]
    focus: tuple[str, ...]
    angle: float
    scale: float
    shift_x: float
    shift_y: float

    def place(self, point: tuple[float, float]) -> tuple[float, float]:
        """Перевести точку общей карты в координаты врезки."""
        x, y = _turn(point, self.angle)
        return x * self.scale + self.shift_x, y * self.scale + self.shift_y


@dataclass(slots=True)
class Placement:
    """
    Подпись до разведения и записи в разметку; изменяемая, её двигают по нескольку раз.

    ``fixed`` — подпись крайнего значения, которая стоит на месте и расталкивает соседей.
    """

    x: float
    y: float
    text: str
    value: str
    light: bool
    selected: bool
    width: float
    height: float
    fixed: bool = False
    origin_x: float = 0.0
    origin_y: float = 0.0

    @property
    def shifted(self) -> bool:
        """Проверить, отодвинута ли подпись от своего субъекта заметно."""
        return abs(self.x - self.origin_x) + abs(self.y - self.origin_y) > LEADER_THRESHOLD


@dataclass(frozen=True, slots=True)
class Layout:
    """Геометрия чертежа: не зависит ни от показателя, ни от года."""

    width: float
    height: float
    map_height: float
    shapes: dict[str, Shape]
    insets: tuple[InsetLayout, ...]
    anchors: dict[str, tuple[float, float]]

    def anchor(self, code: str) -> tuple[float, float]:
        """Получить точку подписи субъекта, вычисляя при первом обращении: поиск дорог."""
        if code not in self.anchors:
            self.anchors[code] = anchor(self.shapes[code].rings)
        return self.anchors[code]


@lru_cache(maxsize=1)
def layout() -> Layout | None:
    """Построить геометрию чертежа — один раз за жизнь процесса."""
    geometry = boundaries_geometry()
    if not geometry:
        return None

    projection = Albers()
    projected = {
        code: tuple(
            tuple(projection.point(longitude, latitude) for longitude, latitude in ring)
            for ring in boundary.rings
        )
        for code, boundary in geometry.items()
    }
    viewport = fit_width(
        [point for rings in projected.values() for ring in rings for point in ring],
        CANVAS_WIDTH,
        CANVAS_PADDING,
    )

    placed = {
        code: tuple(tuple(viewport.place(*point) for point in ring) for ring in rings)
        for code, rings in projected.items()
    }
    shapes = {
        code: Shape(code=code, path=_path(rings), box=_box(rings), rings=rings)
        for code, rings in placed.items()
    }

    insets = _insets(placed, shapes, geometry, viewport.height + INSET_BAND_OFFSET)
    return Layout(
        width=viewport.width,
        height=(
            insets[0].frame[1] + insets[0].frame[3] + INSET_CAPTION if insets else viewport.height
        ),
        map_height=viewport.height,
        shapes=shapes,
        insets=insets,
        anchors={},
    )


# ---------------------------------------------------------------------------------------
# Врезки
# ---------------------------------------------------------------------------------------


def _insets(
    placed: dict[str, tuple[tuple[tuple[float, float], ...], ...]],
    shapes: dict[str, Shape],
    geometry: dict[str, Boundary],
    top: float,
) -> tuple[InsetLayout, ...]:
    """Разложить врезки в полосе под картой: высота рамок общая, ширина — по окну."""
    prepared: list[tuple[Inset, float, tuple[float, float, float, float]]] = []
    for inset in INSETS:
        window = _window(inset, placed, geometry)
        if window is not None:
            prepared.append((inset, window[0], window[1]))
    if not prepared:
        return ()

    # Высота рамок — наибольшая, при которой полоса помещается в ширину чертежа.
    aspects = [(box[2] - box[0]) / (box[3] - box[1]) for _, _, box in prepared]
    gaps = INSET_GAP * (len(prepared) - 1)
    height = min(INSET_MAX_HEIGHT, (CANVAS_WIDTH - 2 * INSET_EDGE - gaps) / sum(aspects))
    widths = [height * aspect for aspect in aspects]
    offset = (CANVAS_WIDTH - sum(widths) - gaps) / 2

    layouts: list[InsetLayout] = []
    for (inset, angle, box), width in zip(prepared, widths, strict=True):
        # Поворот, увеличение и перенос окна в рамку; в записи — в обратном порядке.
        scale = height / (box[3] - box[1])
        shift_x = offset - box[0] * scale
        shift_y = top - box[1] * scale
        layouts.append(
            InsetLayout(
                key=inset.key,
                title=inset.title,
                frame=(offset, top, width, height),
                transform=(
                    f"translate({_number(shift_x)} {_number(shift_y)}) "
                    f"scale({scale:.4f}) rotate({angle:.3f})"
                ),
                caption_x=offset + width / 2,
                caption_y=top + height + INSET_CAPTION - 6,
                codes=_within(box, angle, placed),
                focus=tuple(code for code in inset.codes if code in shapes),
                angle=angle,
                scale=scale,
                shift_x=shift_x,
                shift_y=shift_y,
            )
        )
        offset += width + INSET_GAP
    return tuple(layouts)


def _window(
    inset: Inset,
    placed: dict[str, tuple[tuple[tuple[float, float], ...], ...]],
    geometry: dict[str, Boundary],
) -> tuple[float, tuple[float, float, float, float]] | None:
    """
    Определить поворот врезки и показываемое окно чертежа.

    Содержимое разворачивается севером вверх: иначе окно Кавказа, повёрнутого почти
    на 50°, наполовину пусто. Окно расширяется на долю стороны ради окружения.
    """
    codes = [code for code in inset.codes if code in placed]
    if not codes:
        return None

    angle = _north_angle(codes, geometry)
    points = [_turn(point, angle) for code in codes for ring in placed[code] for point in ring]
    x0 = min(point[0] for point in points)
    y0 = min(point[1] for point in points)
    x1 = max(point[0] for point in points)
    y1 = max(point[1] for point in points)
    grow = max(x1 - x0, y1 - y0) * inset.padding
    return angle, (x0 - grow, y0 - grow, x1 + grow, y1 + grow)


def _north_angle(codes: list[str], geometry: dict[str, Boundary]) -> float:
    """Вычислить поворот, при котором север во врезке смотрит вверх, — по середине долгот."""
    longitudes = [
        longitude
        for code in codes
        for ring in geometry[code].rings
        for longitude, _latitude in ring
    ]
    middle = (min(longitudes) + max(longitudes)) / 2
    return Albers().convergence(middle)


def _turn(point: tuple[float, float], angle: float) -> tuple[float, float]:
    """Повернуть точку чертежа вокруг начала координат на угол в градусах."""
    radians = math.radians(angle)
    cosine = math.cos(radians)
    sine = math.sin(radians)
    return (
        point[0] * cosine - point[1] * sine,
        point[0] * sine + point[1] * cosine,
    )


def _within(
    window: tuple[float, float, float, float],
    angle: float,
    placed: dict[str, tuple[tuple[tuple[float, float], ...], ...]],
) -> tuple[str, ...]:
    """Отобрать субъекты, попадающие в окно врезки, по габаритам; лишнее обрежет рамка."""
    left, top, right, bottom = window
    result: list[str] = []
    for code, rings in placed.items():
        points = [_turn(point, angle) for ring in rings for point in ring]
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        if min(xs) <= right and max(xs) >= left and min(ys) <= bottom and max(ys) >= top:
            result.append(code)
    return tuple(result)


# ---------------------------------------------------------------------------------------
# Описание контура
# ---------------------------------------------------------------------------------------


def _number(value: float) -> str:
    """Записать координату кратчайшим образом, не теряя выбранной точности."""
    text = f"{value:.{PRECISION}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _path(rings: tuple[tuple[tuple[float, float], ...], ...]) -> str:
    """
    Собрать описание контура для атрибута ``d`` приращениями — вдвое короче.

    Приращения — между округлёнными положениями, чтобы кольцо замыкалось; совпавшие
    после округления точки отбрасываются.
    """
    parts: list[str] = []
    for ring in rings:
        drawn: list[tuple[float, float]] = []
        previous: tuple[float, float] | None = None
        for x, y in ring:
            point = (round(x, PRECISION), round(y, PRECISION))
            if point != previous:
                drawn.append(point)
                previous = point
        if len(drawn) < MIN_RING_POINTS:
            continue

        current = drawn[0]
        steps: list[str] = []
        for point in drawn[1:]:
            steps.append(f"{_number(point[0] - current[0])} {_number(point[1] - current[1])}")
            current = point
        parts.append(f"M{_number(drawn[0][0])} {_number(drawn[0][1])}l" + "l".join(steps) + "z")
    return "".join(parts)


def _box(
    rings: tuple[tuple[tuple[float, float], ...], ...],
) -> tuple[float, float, float, float]:
    """Определить габарит контура на чертеже."""
    xs = [point[0] for ring in rings for point in ring]
    ys = [point[1] for ring in rings for point in ring]
    return min(xs), min(ys), max(xs), max(ys)


# ---------------------------------------------------------------------------------------
# Точка подписи — наиболее удалённая от границы: центр тяжести у вогнутых фигур
# оказывается снаружи (у Якутии — в море).
# ---------------------------------------------------------------------------------------

# Число делений грубой сетки поиска и число уточняющих проходов.
ANCHOR_GRID = 12
ANCHOR_REFINEMENTS = 3

# Наибольшее число отрезков границы при поиске точки подписи.
ANCHOR_SEGMENTS = 240


def anchor(
    rings: tuple[tuple[tuple[float, float], ...], ...],
) -> tuple[float, float]:
    """Найти точку контура, наиболее удалённую от его границы."""
    if not rings:
        return 0.0, 0.0

    largest = max(rings, key=_ring_area)
    segments = _segments(largest)
    x0, y0, x1, y1 = _box((largest,))

    best = ((x0 + x1) / 2, (y0 + y1) / 2)
    best_distance = -1.0
    step_x = (x1 - x0) / ANCHOR_GRID
    step_y = (y1 - y0) / ANCHOR_GRID
    centre = best

    for refinement in range(ANCHOR_REFINEMENTS):
        for row in range(ANCHOR_GRID + 1):
            for column in range(ANCHOR_GRID + 1):
                point = (
                    centre[0] + (column - ANCHOR_GRID / 2) * step_x,
                    centre[1] + (row - ANCHOR_GRID / 2) * step_y,
                )
                if not _inside(point, rings):
                    continue
                distance = _distance_to_border(point, segments)
                if distance > best_distance:
                    best_distance = distance
                    best = point
        if refinement < ANCHOR_REFINEMENTS - 1:
            centre = best
            step_x /= ANCHOR_GRID / 2
            step_y /= ANCHOR_GRID / 2

    return best


def _ring_area(ring: tuple[tuple[float, float], ...]) -> float:
    """Вычислить площадь кольца независимо от направления обхода."""
    total = 0.0
    for index in range(len(ring) - 1):
        total += ring[index][0] * ring[index + 1][1] - ring[index + 1][0] * ring[index][1]
    return abs(total) / 2


def _segments(
    ring: tuple[tuple[float, float], ...],
) -> list[tuple[float, float, float, float]]:
    """Разложить кольцо на отрезки, проредив слишком подробный контур."""
    step = max(1, len(ring) // ANCHOR_SEGMENTS)
    points = list(ring[::step])
    if points[0] != points[-1]:
        points.append(points[0])
    return [
        (points[index][0], points[index][1], points[index + 1][0], points[index + 1][1])
        for index in range(len(points) - 1)
    ]


def _inside(point: tuple[float, float], rings: tuple[tuple[tuple[float, float], ...], ...]) -> bool:
    """Проверить, лежит ли точка внутри контура по правилу «чёт — нечет»."""
    x, y = point
    crossings = 0
    for ring in rings:
        for index in range(len(ring) - 1):
            ax, ay = ring[index]
            bx, by = ring[index + 1]
            if (ay > y) != (by > y) and x < ax + (y - ay) / (by - ay) * (bx - ax):
                crossings += 1
    return crossings % 2 == 1


def _distance_to_border(
    point: tuple[float, float], segments: list[tuple[float, float, float, float]]
) -> float:
    """Определить расстояние от точки до ближайшего отрезка границы."""
    x, y = point
    best = float("inf")
    for ax, ay, bx, by in segments:
        dx = bx - ax
        dy = by - ay
        length = dx * dx + dy * dy
        share = 0.0 if length == 0 else ((x - ax) * dx + (y - ay) * dy) / length
        share = max(0.0, min(1.0, share))
        gap_x = x - (ax + share * dx)
        gap_y = y - (ay + share * dy)
        best = min(best, gap_x * gap_x + gap_y * gap_y)
    return best**0.5


# ---------------------------------------------------------------------------------------
# Сборка чертежа под данные
# ---------------------------------------------------------------------------------------

# Приставка к именам определений: имена общие для всех SVG страницы.
DEFINITION_PREFIX = "rl-geo"

# Доля ширины у краёв чертежа, где подпись прижимается к своему краю.
LABEL_EDGE = 0.12

# Размеры подписей во врезке и оценка ширины знака в долях кегля для узкого начертания
# (замер — 0,486; оценка с запасом).
CODE_SIZE = 19.0
VALUE_SIZE = 17.0
GLYPH_WIDTH = 0.52

# Предел сдвига подписи от своего субъекта и число проходов разведения.
INSET_LABEL_SHIFT = 40.0
LABEL_SHIFT = 110.0
SPREAD_ROUNDS = 30

# Кегль подписи на общей карте и порог, начиная с которого отодвинутая подпись
# соединяется со своим субъектом чертой.
LABEL_SIZE = 28.0
LEADER_THRESHOLD = 26.0

# Отступ подписи от кромки чертежа — для краевых субъектов.
LABEL_INSET = 46.0


def build_map(
    rows: list[dict[str, Any]],
    *,
    selected: Sequence[str] = (),
    prefix: str = DEFINITION_PREFIX,
) -> dict[str, Any] | None:
    """
    Собрать чертёж картограммы: к готовой геометрии — заливка, подсказки и подписи.

    ``prefix`` — приставка опознавателей контуров: у второй карты на странице своя.
    """
    plan = layout()
    if plan is None:
        return None

    known = {row["code"]: row for row in rows if row["code"] in plan.shapes}
    highlighted = _highlighted(known, selected)
    regions = [_region(code, row, prefix) for code, row in known.items()]
    labels = _labels(plan, known, highlighted)

    return {
        "width": _number(plan.width),
        "height": _number(plan.height),
        "map_height": _number(plan.map_height),
        "view_box": f"0 0 {_number(plan.width)} {_number(plan.height)}",
        "definitions": [
            {"id": f"{prefix}-{code}", "path": plan.shapes[code].path} for code in known
        ],
        "regions": regions,
        "labels": labels,
        "insets": [
            _inset(plan, inset, known, highlighted, selected=selected, prefix=prefix)
            for inset in plan.insets
        ],
        "no_data_pattern": f"{prefix}-no-data",
    }


def _region(code: str, row: dict[str, Any], prefix: str = DEFINITION_PREFIX) -> dict[str, Any]:
    """Описать один субъект на общей карте; признак выбора — из строки наблюдения."""
    return {
        "code": code,
        "reference": f"#{prefix}-{code}",
        "href": row.get("href", ""),
        "title": row.get("title", row.get("name", code)),
        "fill": (f"var({row['colour']})" if row.get("colour") else f"url(#{prefix}-no-data)"),
        "class_index": "" if row.get("class_index") is None else str(row["class_index"]),
        "selected": bool(row.get("selected")),
    }


def _labels(
    plan: Layout, known: dict[str, dict[str, Any]], highlighted: list[str]
) -> list[dict[str, Any]]:
    """Подписать на карте выбранные субъекты; у кого есть врезка — во врезке."""
    inset_codes = {code for inset in plan.insets for code in inset.focus}
    placements: list[Placement] = []
    for code in highlighted:
        if code in inset_codes:
            continue
        row = known[code]
        x, y = plan.anchor(code)
        name = str(row.get("name", code))
        value = str(row.get("label", ""))
        placements.append(
            Placement(
                x=_clamp(x, plan.width),
                y=_clamp(y, plan.map_height),
                text=name,
                value=value,
                light=False,
                selected=True,
                width=max(len(name), len(value)) * GLYPH_WIDTH * LABEL_SIZE,
                height=LABEL_SIZE * 2.6,
            )
        )
    _spread(placements, LABEL_SHIFT)

    return [
        {
            "x": _number(item.x),
            "y": _number(item.y),
            "name": item.text,
            "value": item.value,
            "place": _place(item.x, plan.width),
            "selected": item.selected,
            "leader": item.shifted,
            "origin_x": _number(item.origin_x),
            "origin_y": _number(item.origin_y),
        }
        for item in placements
    ]


def _highlighted(known: dict[str, dict[str, Any]], selected: Sequence[str]) -> list[str]:
    """Определить подписываемые субъекты — выбранные на рабочей поверхности."""
    codes: list[str] = []
    for code in selected:
        if code in known and code not in codes:
            codes.append(code)
    return codes


def _spread(placements: list[Placement], shift: float) -> None:
    """
    Развести подписи врезки по оси наименьшего перекрытия, не дальше предела.

    Подпись с величиной стоит на месте; далеко отодвинутая соединяется чертой.
    """
    for item in placements:
        item.origin_x, item.origin_y = item.x, item.y
    for _round in range(SPREAD_ROUNDS):
        for first in range(len(placements)):
            for second in range(first + 1, len(placements)):
                one, two = placements[first], placements[second]
                gap_x = (one.width + two.width) / 2 - abs(one.x - two.x)
                gap_y = (one.height + two.height) / 2 - abs(one.y - two.y)
                if gap_x <= 0 or gap_y <= 0:
                    continue

                # Двигается тот, у кого нет величины; если величины нет у обоих,
                # расходятся оба навстречу.
                movers = [item for item in (one, two) if not item.fixed] or [one, two]
                vertical = gap_y <= gap_x
                step = (gap_y if vertical else gap_x) / len(movers) + 0.5
                for item in movers:
                    other = two if item is one else one
                    if vertical:
                        item.y += step if item.y >= other.y else -step
                    else:
                        item.x += step if item.x >= other.x else -step

    for item in placements:
        item.x = item.origin_x + max(-shift, min(shift, item.x - item.origin_x))
        item.y = item.origin_y + max(-shift, min(shift, item.y - item.origin_y))


def _clamp(value: float, limit: float) -> float:
    """Удержать точку подписи внутри чертежа."""
    return max(LABEL_INSET, min(limit - LABEL_INSET, value))


def _place(x: float, width: float) -> str:
    """Выбрать выключку подписи так, чтобы она не вышла за кромку чертежа."""
    if x < width * LABEL_EDGE:
        return "start"
    if x > width * (1 - LABEL_EDGE):
        return "end"
    return "middle"


def _inset(
    plan: Layout,
    inset: InsetLayout,
    known: dict[str, dict[str, Any]],
    highlighted: list[str],
    *,
    selected: Sequence[str],
    prefix: str = DEFINITION_PREFIX,
) -> dict[str, Any]:
    """Описать одну врезку: рамку, преобразование, состав и подписи сокращениями."""
    left, top, width, height = inset.frame
    codes = [code for code in inset.codes if code in known]

    # Подпись с величиной — последней, поверх соседних.
    placements: list[Placement] = []
    for code in sorted(inset.focus, key=lambda item: item in highlighted):
        if code not in known:
            continue
        row = known[code]
        x, y = inset.place(plan.anchor(code))
        text = row.get("abbreviation") or code
        value = row.get("label", "") if code in highlighted else ""
        placements.append(
            Placement(
                x=x,
                y=y,
                text=text,
                value=value,
                light=bool(row.get("light_label")),
                selected=code in selected,
                width=max(
                    len(text) * GLYPH_WIDTH * CODE_SIZE,
                    len(value) * GLYPH_WIDTH * VALUE_SIZE,
                ),
                height=CODE_SIZE * (2.7 if value else 1.45),
                fixed=bool(value),
            )
        )
    _spread(placements, INSET_LABEL_SHIFT)
    labels = [
        {
            "x": _number(item.x),
            "y": _number(item.y),
            "text": item.text,
            "value": item.value,
            "light": item.light,
            "selected": item.selected,
        }
        for item in placements
    ]

    return {
        "key": inset.key,
        "title": inset.title,
        "x": _number(left),
        "y": _number(top),
        "width": _number(width),
        "height": _number(height),
        "clip": f"{prefix}-clip-{inset.key}",
        "transform": inset.transform,
        "caption_x": _number(inset.caption_x),
        "caption_y": _number(inset.caption_y),
        "regions": [_region(code, known[code], prefix) for code in codes],
        "labels": labels,
    }
