"""
Проверки проекции по свойствам: равновеликость, отсутствие искажения на стандартных
параллелях, симметрия относительно центрального меридиана.
"""

from __future__ import annotations

import math

import pytest

from apps.maps.projection import Albers, Viewport, bounds, fit_width

pytestmark = pytest.mark.unit


def quadrilateral(
    projection: Albers, west: float, south: float, size: float
) -> list[tuple[float, float]]:
    """Спроецировать небольшой участок, заданный углом и стороной в градусах."""
    return [
        projection.point(west, south),
        projection.point(west + size, south),
        projection.point(west + size, south + size),
        projection.point(west, south + size),
    ]


def area(points: list[tuple[float, float]]) -> float:
    """Вычислить площадь многоугольника по формуле площади Гаусса."""
    total = 0.0
    for index in range(len(points)):
        current = points[index]
        following = points[(index + 1) % len(points)]
        total += current[0] * following[1] - following[0] * current[1]
    return abs(total) / 2


class TestEqualArea:
    """Сохранение площадей."""

    def test_same_true_area_gives_same_drawn_area(self) -> None:
        """
        Участки равной действительной площади занимают на карте равную площадь.

        Площадь трапеции пропорциональна разности синусов широт — она и уравнивается.
        """
        projection = Albers()
        southern = quadrilateral(projection, 40.0, 50.0, 4.0)
        # Северный участок вытянут по долготе так, чтобы его действительная площадь
        # совпала с южным: к северу меридианы сходятся.
        span = 4.0 * (math.sin(math.radians(54.0)) - math.sin(math.radians(50.0)))
        span /= math.sin(math.radians(72.0)) - math.sin(math.radians(68.0))
        northern = [
            projection.point(longitude, latitude)
            for longitude, latitude in (
                (40.0, 68.0),
                (40.0 + span, 68.0),
                (40.0 + span, 72.0),
                (40.0, 72.0),
            )
        ]

        assert area(southern) == pytest.approx(area(northern), rel=0.02)

    def test_cylindrical_projection_would_fail_the_same_check(self) -> None:
        """Та же проверка на цилиндрической проекции не проходит — иначе она была бы тавтологией."""
        southern = [(40.0, 50.0), (44.0, 50.0), (44.0, 54.0), (40.0, 54.0)]
        span = 4.0 * (math.sin(math.radians(54.0)) - math.sin(math.radians(50.0)))
        span /= math.sin(math.radians(72.0)) - math.sin(math.radians(68.0))
        northern = [(40.0, 68.0), (40.0 + span, 68.0), (40.0 + span, 72.0), (40.0, 72.0)]

        assert area(northern) > area(southern) * 1.5


class TestGeometry:
    """Расположение точек на чертеже."""

    def test_central_meridian_is_vertical(self) -> None:
        """На центральном меридиане горизонтальная координата обращается в ноль."""
        projection = Albers()
        for latitude in (45.0, 56.0, 70.0):
            assert projection.point(projection.central_meridian, latitude)[0] == pytest.approx(0.0)

    def test_projection_is_symmetric_about_central_meridian(self) -> None:
        """Точки, равноудалённые от центрального меридиана, ложатся зеркально."""
        projection = Albers()
        left = projection.point(projection.central_meridian - 30.0, 55.0)
        right = projection.point(projection.central_meridian + 30.0, 55.0)
        assert left[0] == pytest.approx(-right[0])
        assert left[1] == pytest.approx(right[1])

    def test_north_grows_upwards(self) -> None:
        """Ось Y проекции направлена на север."""
        projection = Albers()
        assert projection.point(100.0, 70.0)[1] > projection.point(100.0, 50.0)[1]

    def test_reference_latitude_is_the_origin(self) -> None:
        """Опорная параллель на центральном меридиане — начало координат."""
        projection = Albers()
        origin = projection.point(projection.central_meridian, projection.reference_latitude)
        assert origin == pytest.approx((0.0, 0.0))


class TestConvergence:
    """Сходимость меридианов — угол между направлением на север и вертикалью."""

    def test_no_convergence_on_central_meridian(self) -> None:
        """На центральном меридиане север смотрит прямо вверх."""
        assert Albers().convergence(100.0) == pytest.approx(0.0)

    def test_convergence_grows_with_distance(self) -> None:
        """Чем дальше от центрального меридиана, тем сильнее разворот."""
        projection = Albers()
        assert abs(projection.convergence(40.0)) > abs(projection.convergence(80.0))

    def test_west_turns_counterclockwise(self) -> None:
        """К западу от центрального меридиана разворот отрицательный."""
        assert Albers().convergence(40.0) < 0

    def test_caucasus_is_turned_by_about_fifty_degrees(self) -> None:
        """
        На Северном Кавказе разворот близок к пятидесяти градусам.

        Из-за него прямоугольное окно врезки оказывалось почти квадратным
        и наполовину пустым; врезки разворачиваются на этот угол.
        """
        assert Albers().convergence(43.6) == pytest.approx(-47.6, abs=1.0)


class TestViewport:
    """Перевод координат проекции в единицы чертежа."""

    def test_fit_keeps_proportions(self) -> None:
        """Вписывание не сплющивает изображение: масштаб по осям один."""
        points = [(0.0, 0.0), (2.0, 0.0), (2.0, 1.0), (0.0, 1.0)]
        viewport = fit_width(points, 100.0)
        assert viewport.height == pytest.approx(50.0)

    def test_padding_is_added_on_both_sides(self) -> None:
        """Поле откладывается с каждой стороны."""
        points = [(0.0, 0.0), (2.0, 0.0), (2.0, 1.0), (0.0, 1.0)]
        viewport = fit_width(points, 100.0, padding=10.0)
        assert viewport.place(0.0, 1.0) == pytest.approx((10.0, 10.0))
        assert viewport.place(2.0, 0.0) == pytest.approx((90.0, 50.0))
        assert viewport.height == pytest.approx(60.0)

    def test_vertical_axis_is_flipped(self) -> None:
        """Север оказывается наверху чертежа: ось Y разворачивается."""
        viewport = Viewport(scale=2.0, shift_x=0.0, shift_y=100.0, width=10.0, height=10.0)
        assert viewport.place(0.0, 10.0)[1] < viewport.place(0.0, 0.0)[1]

    def test_degenerate_extent_is_refused(self) -> None:
        """Вырожденный набор точек не даёт чертежа."""
        with pytest.raises(ValueError, match="Вырожденный"):
            fit_width([(1.0, 1.0), (1.0, 2.0)], 100.0)

    def test_empty_extent_is_refused(self) -> None:
        """Габарит пустого набора не определён."""
        with pytest.raises(ValueError, match="пустого"):
            bounds([])
