"""
Проверки индекса Морана на расстановках с очевидным ответом и приведения строк
матрицы весов к единичной сумме.
"""

from __future__ import annotations

import numpy as np
import pytest

from apps.analytics.core import spatial

pytestmark = pytest.mark.unit

# Число территорий в искусственной цепочке: достаточно для расчёта индекса.
CHAIN_SIZE = 40


def chain(size: int = CHAIN_SIZE) -> tuple[list[str], dict[str, list[str]]]:
    """Построить цепочку территорий, где каждая граничит с двумя соседями."""
    codes = [f"R{index:02d}" for index in range(size)]
    neighbours = {
        codes[index]: [
            codes[position] for position in (index - 1, index + 1) if 0 <= position < size
        ]
        for index in range(size)
    }
    return codes, neighbours


class TestWeightMatrix:
    """Матрица пространственных весов."""

    def test_rows_sum_to_one(self) -> None:
        """Каждая строка приводится к единичной сумме."""
        codes, neighbours = chain()
        matrix = spatial.build_matrix(codes, neighbours)
        assert np.allclose(matrix.sum(axis=1), 1.0)

    def test_isolated_territory_has_empty_row(self) -> None:
        """Территория без соседей получает пустую строку, а не равные веса."""
        codes = ["A", "B", "C"]
        matrix = spatial.build_matrix(codes, {"A": ["B"], "B": ["A"], "C": []})
        assert matrix[2].sum() == pytest.approx(0.0)

    def test_self_link_is_ignored(self) -> None:
        """Территория не является соседом самой себе."""
        matrix = spatial.build_matrix(["A", "B"], {"A": ["A", "B"], "B": ["A"]})
        assert matrix[0, 0] == pytest.approx(0.0)

    def test_unknown_neighbour_is_ignored(self) -> None:
        """Сосед, отсутствующий в перечне территорий, в матрицу не попадает."""
        matrix = spatial.build_matrix(["A", "B"], {"A": ["B", "Z"], "B": ["A"]})
        assert matrix.shape == (2, 2)


class TestGlobalMoran:
    """Глобальный индекс Морана."""

    def test_clustered_values_give_positive_index(self) -> None:
        """Разложенные по половинам значения дают выраженную положительную автокорреляцию."""
        codes, neighbours = chain()
        matrix = spatial.build_matrix(codes, neighbours)
        values = np.array([0.0] * (CHAIN_SIZE // 2) + [100.0] * (CHAIN_SIZE // 2))

        result = spatial.global_moran(values, matrix)
        assert result is not None
        assert result.value > 0.8
        assert result.is_significant
        assert result.direction == "clustered"

    def test_alternating_values_give_negative_index(self) -> None:
        """Чередование значений через одного даёт отрицательную автокорреляцию."""
        codes, neighbours = chain()
        matrix = spatial.build_matrix(codes, neighbours)
        values = np.array([0.0 if index % 2 else 100.0 for index in range(CHAIN_SIZE)])

        result = spatial.global_moran(values, matrix)
        assert result is not None
        assert result.value < 0
        assert result.direction == "dispersed"

    def test_random_values_are_not_significant(self) -> None:
        """Случайная расстановка не даёт значимой пространственной структуры."""
        codes, neighbours = chain()
        matrix = spatial.build_matrix(codes, neighbours)
        values = np.random.default_rng(20260321).normal(size=CHAIN_SIZE)

        result = spatial.global_moran(values, matrix)
        assert result is not None
        assert not result.is_significant
        assert result.direction == "random"

    def test_expected_value_depends_on_size(self) -> None:
        """Точка отсчёта индекса равна −1/(n−1), а не нулю."""
        codes, neighbours = chain()
        matrix = spatial.build_matrix(codes, neighbours)
        result = spatial.global_moran(np.arange(CHAIN_SIZE, dtype=float), matrix)
        assert result is not None
        assert result.expected == pytest.approx(-1 / (CHAIN_SIZE - 1))

    def test_small_sample_is_rejected(self) -> None:
        """По десяти территориям индекс не рассчитывается."""
        codes, neighbours = chain(10)
        matrix = spatial.build_matrix(codes, neighbours)
        assert spatial.global_moran(np.arange(10, dtype=float), matrix) is None

    def test_constant_values_are_rejected(self) -> None:
        """Для показателя без разброса индекс не определён."""
        codes, neighbours = chain()
        matrix = spatial.build_matrix(codes, neighbours)
        assert spatial.global_moran(np.full(CHAIN_SIZE, 5.0), matrix) is None

    def test_result_is_reproducible(self) -> None:
        """Повторный расчёт с теми же данными даёт тот же уровень значимости."""
        codes, neighbours = chain()
        matrix = spatial.build_matrix(codes, neighbours)
        values = np.arange(CHAIN_SIZE, dtype=float)

        first = spatial.global_moran(values, matrix)
        second = spatial.global_moran(values, matrix)
        assert first is not None
        assert second is not None
        assert first.p_value == second.p_value


class TestLocalMoran:
    """Локальные индексы пространственной автокорреляции."""

    def setup_chain(self) -> tuple[list[str], np.ndarray, np.ndarray]:
        """
        Подготовить цепочку с плавно нарастающими значениями.

        При «половине нулей и половине сотен» перестановки слишком грубы для значимости.
        """
        codes, neighbours = chain()
        matrix = spatial.build_matrix(codes, neighbours)
        values = np.arange(CHAIN_SIZE, dtype=float)
        return codes, matrix, values

    def test_clusters_are_detected(self) -> None:
        """На концах цепочки обнаруживаются пространственные сгущения."""
        codes, matrix, values = self.setup_chain()
        entries = spatial.local_moran(values, matrix, codes, {})
        quadrants = {entry.quadrant for entry in entries if entry.is_significant}
        assert quadrants <= {spatial.QUADRANT_HIGH_HIGH, spatial.QUADRANT_LOW_LOW}
        assert len([entry for entry in entries if entry.is_significant]) > 3

    def test_isolated_territory_is_excluded(self) -> None:
        """Территория без соседей в расчёт локальных индексов не входит."""
        codes, neighbours = chain()
        neighbours[codes[0]] = []
        for code, related in neighbours.items():
            if codes[0] in related:
                neighbours[code] = [item for item in related if item != codes[0]]

        matrix = spatial.build_matrix(codes, neighbours)
        values = np.arange(CHAIN_SIZE, dtype=float)
        entries = spatial.local_moran(values, matrix, codes, {})
        assert codes[0] not in {entry.code for entry in entries}

    def test_quadrant_matches_signs(self) -> None:
        """Тип окружения определяется знаками значения и среднего у соседей."""
        codes, matrix, values = self.setup_chain()
        for entry in spatial.local_moran(values, matrix, codes, {}):
            if entry.standardized >= 0 and entry.spatial_lag >= 0:
                assert entry.quadrant == spatial.QUADRANT_HIGH_HIGH
            elif entry.standardized < 0 and entry.spatial_lag < 0:
                assert entry.quadrant == spatial.QUADRANT_LOW_LOW

    def test_names_are_attached(self) -> None:
        """Названия территорий подставляются из справочника."""
        codes, matrix, values = self.setup_chain()
        entries = spatial.local_moran(values, matrix, codes, {codes[5]: "Пятая"})
        assert any(entry.name == "Пятая" for entry in entries)

    def test_summary_groups_only_significant(self) -> None:
        """В сводку по типам окружения попадают только значимые территории."""
        codes, matrix, values = self.setup_chain()
        entries = spatial.local_moran(values, matrix, codes, {})
        total = sum(group["count"] for group in spatial.summarize_quadrants(entries))
        assert total == len([entry for entry in entries if entry.is_significant])


class TestNearestNeighbours:
    """Соседство по расстоянию между центрами территорий."""

    def test_returns_requested_count(self) -> None:
        """Каждая территория получает заданное число ближайших соседей."""
        coordinates = {f"R{index:02d}": (55.0 + index, 37.0) for index in range(10)}
        result = spatial.nearest_neighbours(list(coordinates), coordinates, count=3)
        assert all(len(items) == 3 for items in result.values())

    def test_nearest_is_adjacent_on_a_line(self) -> None:
        """На прямой ближайшим оказывается соседний по порядку пункт."""
        coordinates = {f"R{index:02d}": (55.0 + index, 37.0) for index in range(10)}
        result = spatial.nearest_neighbours(list(coordinates), coordinates, count=1)
        assert result["R00"] == ["R01"]

    def test_small_set_links_everyone(self) -> None:
        """Если территорий меньше запрошенного числа, соседями становятся все остальные."""
        coordinates = {"A": (55.0, 37.0), "B": (56.0, 37.0)}
        result = spatial.nearest_neighbours(list(coordinates), coordinates, count=5)
        assert result["A"] == ["B"]

    def test_territory_without_coordinates_is_skipped(self) -> None:
        """Территория без координат в схему соседства по расстоянию не входит."""
        coordinates = {"A": (55.0, 37.0), "B": (56.0, 37.0)}
        result = spatial.nearest_neighbours(["A", "B", "C"], coordinates, count=1)
        assert "C" not in result
