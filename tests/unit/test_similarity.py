"""
Проверки сходства на данных с тремя сгущениями; территория с пропуском признака
исключается, а не достраивается.
"""

from __future__ import annotations

import numpy as np
import pytest

from apps.analytics.core import similarity

pytestmark = pytest.mark.unit

# Число территорий в искусственном наборе.
SIZE = 60


def separated_columns() -> tuple[list[dict[str, object]], list[dict[str, str]]]:
    """
    Построить признаки с тремя хорошо разделёнными группами территорий.

    Группы разнесены далеко друг от друга, поэтому любой разумный алгоритм обязан
    восстановить их состав.
    """
    generator = np.random.default_rng(20260321)
    centres = [(0.0, 0.0), (50.0, 50.0), (100.0, 0.0)]

    first: list[float | None] = []
    second: list[float | None] = []
    for index in range(SIZE):
        centre = centres[index % len(centres)]
        first.append(float(centre[0] + generator.normal(0, 2)))
        second.append(float(centre[1] + generator.normal(0, 2)))

    columns = [
        {"key": "a", "label": "Первый признак", "short": "Первый", "values": first},
        {"key": "b", "label": "Второй признак", "short": "Второй", "values": second},
    ]
    territories = [
        {"code": f"R{index:02d}", "name": f"Территория {index}"} for index in range(SIZE)
    ]
    return columns, territories


class TestPrepare:
    """Подготовка матрицы признаков."""

    def test_features_are_standardized(self) -> None:
        """Признаки приводятся к единичному разбросу."""
        columns, territories = separated_columns()
        matrix, _, _ = similarity.prepare(columns, territories)
        assert np.allclose(np.std(matrix, axis=0, ddof=1), 1.0)

    def test_incomplete_territory_is_excluded(self) -> None:
        """Территория без одного из признаков в расчёт не входит."""
        columns, territories = separated_columns()
        columns[0]["values"][0] = None

        matrix, kept, excluded = similarity.prepare(columns, territories)
        assert matrix.shape[0] == SIZE - 1
        assert len(kept) == SIZE - 1
        assert excluded == ["Территория 0"]

    def test_empty_input_gives_empty_matrix(self) -> None:
        """Без признаков матрица пуста."""
        matrix, kept, excluded = similarity.prepare([], [])
        assert matrix.size == 0
        assert kept == []
        assert excluded == []


class TestNeighbours:
    """Ближайшие территории в признаковом пространстве."""

    @staticmethod
    def prepared() -> tuple[np.ndarray, list[dict[str, str]], list[dict[str, object]]]:
        """Стандартизованная матрица с тремя разнесёнными группами."""
        columns, territories = separated_columns()
        matrix, kept, _ = similarity.prepare(columns, territories)
        return matrix, kept, columns

    def test_nearest_come_first(self) -> None:
        """Перечень упорядочен по расстоянию: ближайший стоит первым."""
        matrix, kept, _ = self.prepared()
        found = similarity.neighbours(matrix, kept, kept[0]["code"], limit=5)
        distances = [item["distance"] for item in found]
        assert distances == sorted(distances)

    def test_territory_is_not_similar_to_itself(self) -> None:
        """Сама территория в перечень похожих не попадает."""
        matrix, kept, _ = self.prepared()
        found = similarity.neighbours(matrix, kept, kept[0]["code"], limit=5)
        assert all(item["code"] != kept[0]["code"] for item in found)

    def test_neighbours_come_from_the_same_cloud(self) -> None:
        """
        Ближайшие принадлежат тому же сгущению.

        Группы в наборе разнесены далеко, и попадание чужой территории в первую
        пятёрку означало бы ошибку в расчёте расстояния.
        """
        matrix, kept, _ = self.prepared()
        found = similarity.neighbours(matrix, kept, kept[0]["code"], limit=5)
        # Территория с номером n отнесена к сгущению n % 3; первая — к нулевому.
        assert all(int(item["code"][1:]) % 3 == 0 for item in found)

    def test_limit_is_respected(self) -> None:
        """Показывается ровно столько территорий, сколько запрошено."""
        matrix, kept, _ = self.prepared()
        assert len(similarity.neighbours(matrix, kept, kept[0]["code"], limit=3)) == 3

    def test_unknown_territory_gives_nothing(self) -> None:
        """
        Территории вне расчёта соответствует пустой перечень.

        Субъект с неполными данными в матрицу не входит, и искать сходство
        с ним не с чем.
        """
        matrix, kept, _ = self.prepared()
        assert similarity.neighbours(matrix, kept, "НЕТ-ТАКОЙ") == []

    def test_widest_gap_names_a_feature(self) -> None:
        """Названо, чем территории расходятся сильнее всего: номер признака."""
        matrix, kept, columns = self.prepared()
        found = similarity.neighbours(matrix, kept, kept[0]["code"], limit=3)
        assert all(0 <= item["apart"] < len(columns) for item in found)
        assert all(item["apart_gap"] >= 0 for item in found)
