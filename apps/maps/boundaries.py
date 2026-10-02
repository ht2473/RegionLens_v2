"""
Файл границ субъектов: наличие, контуры и сведения об источнике.

Разбирается один раз за жизнь процесса; без файла карта строится плитками.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)

# Путь к файлу границ относительно каталога статики.
BOUNDARIES_STATIC_PATH = "geo/russia-regions.geojson"

# Кольцо контура: замкнутая последовательность географических координат.
Ring = tuple[tuple[float, float], ...]

# Наименьшее число точек, при котором кольцо описывает фигуру, а не отрезок.
MIN_RING_POINTS = 3


@dataclass(frozen=True, slots=True)
class Boundary:
    """
    Контур одного субъекта: части и отверстия одним списком колец.

    Фигура заливается по правилу «чёт — нечет», и кольцо внутри кольца становится отверстием.
    """

    code: str
    rings: tuple[Ring, ...]

    @property
    def points(self) -> list[tuple[float, float]]:
        """Все точки контура одним списком — для расчёта габаритов."""
        return [point for ring in self.rings for point in ring]


def boundaries_path() -> Path:
    """Определить путь к файлу границ в каталоге статики проекта."""
    return Path(settings.BASE_DIR) / "static" / BOUNDARIES_STATIC_PATH


def boundaries_available() -> bool:
    """Проверить, подготовлен ли файл границ."""
    return boundaries_path().exists()


@lru_cache(maxsize=1)
def _document() -> dict[str, Any]:
    """Прочитать и разобрать файл границ."""
    path = boundaries_path()
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:  # pragma: no cover - порча файла
        logger.warning("Не удалось прочитать файл границ: %s", error)
        return {}
    return payload if isinstance(payload, dict) else {}


def boundaries_meta() -> dict[str, Any]:
    """Прочитать сведения об источнике границ."""
    meta = _document().get("meta", {})
    return meta if isinstance(meta, dict) else {}


@lru_cache(maxsize=1)
def boundaries_geometry() -> dict[str, Boundary]:
    """Собрать контуры субъектов по кодам территорий в порядке файла."""
    result: dict[str, Boundary] = {}
    for feature in _document().get("features", []):
        code = feature.get("properties", {}).get("code")
        geometry = feature.get("geometry") or {}
        rings = _rings(geometry)
        if code and rings:
            result[code] = Boundary(code=code, rings=rings)
    return result


def _rings(geometry: dict[str, Any]) -> tuple[Ring, ...]:
    """Разложить геометрию объекта на кольца независимо от её типа."""
    kind = geometry.get("type")
    if kind == "Polygon":
        parts = [geometry.get("coordinates", [])]
    elif kind == "MultiPolygon":
        parts = geometry.get("coordinates", [])
    else:
        return ()

    return tuple(
        tuple((float(point[0]), float(point[1])) for point in ring)
        for part in parts
        for ring in part
        if len(ring) >= MIN_RING_POINTS
    )
