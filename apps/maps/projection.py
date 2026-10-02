"""
Равновеликая коническая проекция Альберса для картограммы.

Параметры под территорию России: стандартные параллели 52° и 64°, центральный меридиан
100°, опорная параллель 56°. Исходная цилиндрическая проекция раздувает север втрое.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

# Параметры проекции для территории Российской Федерации.
STANDARD_PARALLEL_SOUTH = 52.0
STANDARD_PARALLEL_NORTH = 64.0
REFERENCE_LATITUDE = 56.0
CENTRAL_MERIDIAN = 100.0


@dataclass(frozen=True, slots=True)
class Albers:
    """
    Равновеликая коническая проекция Альберса.

    Координаты безразмерны: X — на восток, Y — на север; в чертёж переводит :class:`Viewport`.
    """

    parallel_south: float = STANDARD_PARALLEL_SOUTH
    parallel_north: float = STANDARD_PARALLEL_NORTH
    reference_latitude: float = REFERENCE_LATITUDE
    central_meridian: float = CENTRAL_MERIDIAN

    def point(self, longitude: float, latitude: float) -> tuple[float, float]:
        """Перевести географические координаты в координаты проекции."""
        cone, constant, radius_zero = _factors(self)
        radius = math.sqrt(max(0.0, constant - 2 * cone * math.sin(math.radians(latitude)))) / cone
        angle = cone * math.radians(longitude - self.central_meridian)
        return radius * math.sin(angle), radius_zero - radius * math.cos(angle)

    def convergence(self, longitude: float) -> float:
        """Вычислить сходимость меридианов на заданной долготе, в градусах."""
        cone, _constant, _radius_zero = _factors(self)
        return cone * (longitude - self.central_meridian)


@lru_cache(maxsize=8)
def _factors(projection: Albers) -> tuple[float, float, float]:
    """Вычислить постоянные проекции: сходимость конуса, постоянную площади и опорный радиус."""
    south = math.radians(projection.parallel_south)
    north = math.radians(projection.parallel_north)
    cone = (math.sin(south) + math.sin(north)) / 2
    constant = math.cos(south) ** 2 + 2 * cone * math.sin(south)
    reference = math.radians(projection.reference_latitude)
    radius_zero = math.sqrt(max(0.0, constant - 2 * cone * math.sin(reference))) / cone
    return cone, constant, radius_zero


@dataclass(frozen=True, slots=True)
class Viewport:
    """Перевод координат проекции в единицы чертежа: масштаб и сдвиг, ось Y вниз."""

    scale: float
    shift_x: float
    shift_y: float
    width: float
    height: float

    def place(self, x: float, y: float) -> tuple[float, float]:
        """Перевести точку проекции в координаты чертежа."""
        return x * self.scale + self.shift_x, self.shift_y - y * self.scale


def bounds(points: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    """Определить габарит набора точек проекции: запад, юг, восток, север."""
    if not points:
        raise ValueError("Габарит пустого набора точек не определён")
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def fit_width(points: list[tuple[float, float]], width: float, padding: float = 0.0) -> Viewport:
    """Вписать набор точек в чертёж заданной ширины; высота — по пропорциям проекции."""
    west, south, east, north = bounds(points)
    span_x = east - west
    span_y = north - south
    if span_x <= 0 or span_y <= 0:
        raise ValueError("Вырожденный габарит: карту построить нельзя")

    scale = (width - 2 * padding) / span_x
    return Viewport(
        scale=scale,
        shift_x=padding - west * scale,
        shift_y=padding + north * scale,
        width=width,
        height=span_y * scale + 2 * padding,
    )
