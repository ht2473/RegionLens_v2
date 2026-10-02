"""
Проверки подготовки границ: сдвиг через 180-й меридиан, мелкие острова, прореживание,
состав территорий.
"""

from __future__ import annotations

import pytest

from apps.maps.management.commands.build_boundaries import (
    _count_points,
    _douglas_peucker,
    _drop_small_parts,
    _polygons,
    _rebuild,
    _ring_area,
    _round_geometry,
    _shift_longitudes,
    _simplify_geometry,
)

pytestmark = pytest.mark.unit


def square(x: float, y: float, size: float) -> list[list[list[float]]]:
    """Построить квадратный контур для проверок."""
    return [
        [
            [x, y],
            [x + size, y],
            [x + size, y + size],
            [x, y + size],
            [x, y],
        ]
    ]


class TestLongitudeShift:
    """Сшивание контуров через 180-й меридиан."""

    def test_negative_longitudes_are_shifted(self) -> None:
        """
        Отрицательные долготы переводятся в непрерывный диапазон.

        Восточная оконечность страны лежит за 180-м меридианом; без сдвига контур
        растянулся бы через всю карту.
        """
        geometry = {"type": "Polygon", "coordinates": square(-179.0, 65.0, 2.0)}
        shifted = _shift_longitudes(geometry)
        longitudes = [point[0] for ring in shifted["coordinates"] for point in ring]
        assert min(longitudes) > 180

    def test_positive_longitudes_are_left_alone(self) -> None:
        """Контуры восточного полушария не смещаются."""
        geometry = {"type": "Polygon", "coordinates": square(37.0, 55.0, 1.0)}
        assert _shift_longitudes(geometry) == geometry


class TestSmallParts:
    """Отбрасывание мелких островов."""

    def test_small_island_is_dropped(self) -> None:
        """Остров меньше порога исключается из геометрии."""
        geometry = {
            "type": "MultiPolygon",
            "coordinates": [square(30.0, 60.0, 5.0), square(50.0, 70.0, 0.01)],
        }
        result = _drop_small_parts(geometry, min_area=0.03)
        assert result["type"] == "Polygon"
        assert len(_polygons(result)) == 1

    def test_largest_part_survives_any_threshold(self) -> None:
        """
        Наибольшая часть сохраняется всегда.

        Иначе субъект с малой площадью остался бы вовсе без геометрии
        и не закрасился бы на карте.
        """
        geometry = {"type": "Polygon", "coordinates": square(30.0, 60.0, 0.001)}
        result = _drop_small_parts(geometry, min_area=100.0)
        assert _count_points(result) > 0

    def test_ring_area_is_orientation_independent(self) -> None:
        """Площадь не зависит от направления обхода контура."""
        ring = square(0.0, 0.0, 2.0)[0]
        assert _ring_area(ring) == pytest.approx(_ring_area(list(reversed(ring))))


class TestSimplification:
    """Прореживание контуров алгоритмом Дугласа — Пекера."""

    def test_collinear_points_are_removed(self) -> None:
        """Точки, лежащие на прямой, не несут сведений о форме."""
        points = [[0.0, 0.0], [1.0, 0.0], [2.0, 0.0], [3.0, 0.0], [4.0, 0.0]]
        assert _douglas_peucker(points, tolerance=0.01, scale=1.0) == [[0.0, 0.0], [4.0, 0.0]]

    def test_significant_deviation_is_kept(self) -> None:
        """Точка, заметно отклоняющаяся от прямой, сохраняется."""
        points = [[0.0, 0.0], [2.0, 1.0], [4.0, 0.0]]
        assert _douglas_peucker(points, tolerance=0.5, scale=1.0) == points

    def test_endpoints_are_always_kept(self) -> None:
        """Концы ломаной не удаляются ни при каком допуске."""
        points = [[0.0, 0.0], [1.0, 0.05], [2.0, 0.0]]
        result = _douglas_peucker(points, tolerance=10.0, scale=1.0)
        assert result[0] == points[0]
        assert result[-1] == points[-1]

    def test_ring_stays_closed(self) -> None:
        """Упрощённое кольцо остаётся замкнутым."""
        geometry = {"type": "Polygon", "coordinates": square(30.0, 60.0, 5.0)}
        result = _simplify_geometry(geometry, tolerance=0.1)
        ring = result["coordinates"][0]
        assert ring[0] == ring[-1]

    def test_degenerate_contour_is_not_emptied(self) -> None:
        """
        Слишком грубый допуск не оставляет территорию без геометрии.

        Пустой контур означал бы незакрашенный субъект, что хуже грубого контура.
        """
        geometry = {"type": "Polygon", "coordinates": square(30.0, 60.0, 0.2)}
        result = _simplify_geometry(geometry, tolerance=100.0)
        assert _count_points(result) >= 4

    def test_reduces_point_count(self) -> None:
        """
        Прореживание сокращает число точек, сохраняя форму.

        Контур квадрата, размеченный сотней промежуточных точек, должен вернуться
        к своим углам: промежуточные точки лежат на прямых и формы не описывают.
        """
        steps = 25
        ring: list[list[float]] = []
        for index in range(steps):
            ring.append([index * 10 / steps, 0.0])
        for index in range(steps):
            ring.append([10.0, index * 10 / steps])
        for index in range(steps):
            ring.append([10 - index * 10 / steps, 10.0])
        for index in range(steps):
            ring.append([0.0, 10 - index * 10 / steps])
        ring.append(ring[0])

        geometry = {"type": "Polygon", "coordinates": [ring]}
        result = _simplify_geometry(geometry, tolerance=0.1)
        assert _count_points(result) < _count_points(geometry)
        assert _count_points(result) <= 6


class TestGeometryShape:
    """Представление геометрии."""

    def test_single_polygon_is_not_wrapped(self) -> None:
        """Одна часть записывается как Polygon, а не как MultiPolygon из одной части."""
        assert _rebuild([square(0.0, 0.0, 1.0)])["type"] == "Polygon"

    def test_several_parts_form_multipolygon(self) -> None:
        """Несколько частей записываются как MultiPolygon."""
        parts = [square(0.0, 0.0, 1.0), square(10.0, 10.0, 1.0)]
        assert _rebuild(parts)["type"] == "MultiPolygon"

    def test_rounding_reduces_precision(self) -> None:
        """Округление координат уменьшает объём файла, не меняя формы."""
        geometry = {"type": "Polygon", "coordinates": [[[37.123456, 55.654321], [38.0, 56.0]]]}
        rounded = _round_geometry(geometry, precision=3)
        assert rounded["coordinates"][0][0] == [37.123, 55.654]
