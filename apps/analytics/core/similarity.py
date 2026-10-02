"""Сходство регионов: ближайшие точки в пространстве стандартизованных признаков."""

from __future__ import annotations

from typing import Any

import numpy as np

# Минимальное число территорий и признаков, при котором сравнение осмысленно.
MIN_TERRITORIES = 20
MIN_FEATURES = 2


def prepare(
    columns: list[dict[str, Any]],
    territories: list[dict[str, str]],
) -> tuple[np.ndarray, list[dict[str, str]], list[str]]:
    """
    Собрать и стандартизовать матрицу признаков.

    Территории без какого-либо признака исключаются и возвращаются отдельно, без восстановления.
    """
    if not columns or not territories:
        return np.empty((0, 0)), [], []

    raw = np.asarray(
        [
            [np.nan if value is None else float(value) for value in column["values"]]
            for column in columns
        ],
        dtype=float,
    ).T

    complete = np.all(np.isfinite(raw), axis=1)
    kept = [territory for index, territory in enumerate(territories) if complete[index]]
    excluded = [
        territory["name"] for index, territory in enumerate(territories) if not complete[index]
    ]

    matrix = raw[complete]
    if matrix.shape[0] == 0:
        return matrix, kept, excluded

    spread = np.std(matrix, axis=0, ddof=1)
    spread = np.where(spread > 0, spread, 1.0)
    return (matrix - np.mean(matrix, axis=0)) / spread, kept, excluded


def neighbours(
    matrix: np.ndarray,
    territories: list[dict[str, str]],
    code: str,
    *,
    limit: int = 6,
) -> list[dict[str, Any]]:
    """
    Найти территории, ближайшие к указанной, и признак наибольшего расхождения с каждой.

    Расстояние — в стандартных отклонениях и годится только для упорядочивания.
    """
    index = next(
        (position for position, item in enumerate(territories) if item["code"] == code), -1
    )
    if index < 0 or matrix.shape[0] < 2:  # noqa: PLR2004 - «не с кем сравнивать»
        return []

    differences = matrix - matrix[index]
    distances = np.sqrt(np.sum(np.square(differences), axis=1))
    order = [position for position in np.argsort(distances) if position != index][:limit]

    return [
        {
            "index": int(position),
            "code": territories[position]["code"],
            "name": territories[position]["name"],
            "distance": float(distances[position]),
            # Номер признака; подпись подставляет вызывающая сторона.
            "apart": int(np.argmax(np.abs(differences[position]))),
            "apart_gap": float(np.max(np.abs(differences[position]))),
        }
        for position in order
    ]
