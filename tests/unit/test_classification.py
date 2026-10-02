"""Проверки свойств каждого способа разбиения значений на классы шкалы."""

from __future__ import annotations

import pytest

from apps.maps.classification import (
    MAX_CLASSES,
    MIN_CLASSES,
    Classification,
    classify,
    classify_symmetric,
    diverging_palette,
    histogram,
    sequential_palette,
)

pytestmark = pytest.mark.unit

# Ряд с выраженной правой асимметрией — типичная форма региональных показателей:
# несколько крупных субъектов и длинный хвост малых.
SKEWED = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 14.0, 20.0, 45.0, 120.0]


class TestQuantiles:
    """Разбиение по квантилям."""

    def test_classes_are_evenly_filled(self) -> None:
        """Классы наполняются приблизительно поровну."""
        result = classify(list(range(100)), method="quantile", class_count=4)
        assert result is not None
        assert max(result.counts) - min(result.counts) <= 1

    def test_all_values_are_classified(self) -> None:
        """Каждое значение попадает ровно в один класс."""
        result = classify(SKEWED, method="quantile", class_count=4)
        assert result is not None
        assert sum(result.counts) == len(SKEWED)


class TestEqualInterval:
    """Разбиение на равные интервалы."""

    def test_intervals_have_equal_width(self) -> None:
        """Ширина всех классов совпадает."""
        result = classify(SKEWED, method="equal", class_count=4)
        assert result is not None
        widths = [
            result.breaks[index + 1] - result.breaks[index] for index in range(result.class_count)
        ]
        assert max(widths) == pytest.approx(min(widths))

    def test_skewed_data_leaves_sparse_upper_classes(self) -> None:
        """На асимметричных данных верхние классы почти пусты — свойство способа."""
        result = classify(SKEWED, method="equal", class_count=4)
        assert result is not None
        assert result.counts[0] > result.counts[-1]


class TestJenks:
    """Естественные границы (метод Фишера — Дженкса)."""

    def test_separates_distinct_groups(self) -> None:
        """Границы проходят между явно обособленными группами значений."""
        values = [1.0, 1.1, 1.2, 10.0, 10.1, 10.2, 100.0, 100.1, 100.2]
        result = classify(values, method="jenks", class_count=3)
        assert result is not None
        assert result.counts == (3, 3, 3)

    def test_minimises_within_class_spread(self) -> None:
        """Внутриклассовый разброс меньше, чем при равных интервалах, — по отнесению значений."""
        values_ = [1.0, 2.0, 3.0, 4.0, 5.0, 30.0, 31.0, 32.0, 33.0, 90.0, 91.0, 92.0, 93.0]

        def spread(result: Classification) -> float:
            groups: dict[int, list[float]] = {}
            for value in values_:
                groups.setdefault(result.class_of(value), []).append(value)
            total = 0.0
            for values in groups.values():
                mean = sum(values) / len(values)
                total += sum((value - mean) ** 2 for value in values)
            return total

        jenks = classify(values_, method="jenks", class_count=3)
        equal = classify(values_, method="equal", class_count=3)
        assert jenks is not None and equal is not None
        assert jenks.class_count == equal.class_count
        assert spread(jenks) < spread(equal)

    def test_lone_maximum_collapses_upper_class(self) -> None:
        """Одиночный выброс наверху уменьшает число классов на единицу, и легенда называет это."""
        result = classify([*range(1, 20), 5000.0], method="jenks", class_count=5)
        assert result is not None
        assert result.class_count < 5
        assert sum(result.counts) == 20


class TestDegenerateInput:
    """Поведение на данных, не образующих шкалы."""

    def test_empty_input_returns_nothing(self) -> None:
        """Пустой ряд не даёт разбиения."""
        assert classify([], method="quantile") is None

    def test_single_distinct_value_returns_nothing(self) -> None:
        """Ряд из одинаковых значений не образует шкалы."""
        assert classify([5.0] * 20, method="quantile") is None

    def test_missing_values_are_ignored(self) -> None:
        """Пропуски не участвуют в разбиении и не занимают класс."""
        result = classify([1.0, None, 2.0, None, 3.0, 4.0], method="quantile", class_count=3)  # type: ignore[list-item]
        assert result is not None
        assert result.values_count == 4

    def test_class_count_is_clamped(self) -> None:
        """Число классов ограничено допустимым диапазоном."""
        low = classify(list(range(50)), method="quantile", class_count=1)
        high = classify(list(range(50)), method="quantile", class_count=99)
        assert low is not None and high is not None
        assert low.class_count == MIN_CLASSES
        assert high.class_count == MAX_CLASSES

    def test_repeated_values_do_not_create_empty_classes(self) -> None:
        """
        Повторяющееся значение не порождает вырожденных классов.

        Половина субъектов с нулевым значением — обычная ситуация, и легенда
        не должна показывать классы с совпадающими границами.
        """
        values = [0.0] * 40 + [1.0, 2.0, 3.0, 4.0, 5.0]
        result = classify(values, method="quantile", class_count=5)
        assert result is not None
        assert all(
            result.breaks[index] < result.breaks[index + 1] for index in range(result.class_count)
        )


class TestClassOf:
    """Отнесение значения к классу."""

    def test_missing_value_has_no_class(self) -> None:
        """Пропуск не относится ни к одному классу шкалы."""
        result = classify(SKEWED, method="quantile", class_count=4)
        assert result is not None
        assert result.class_of(None) is None

    def test_maximum_belongs_to_last_class(self) -> None:
        """Наибольшее значение попадает в верхний класс, а не за пределы шкалы."""
        result = classify(SKEWED, method="quantile", class_count=4)
        assert result is not None
        assert result.class_of(max(SKEWED)) == result.class_count - 1


class TestSymmetricScale:
    """Расходящаяся шкала для изменений."""

    def test_breaks_are_symmetric_around_zero(self) -> None:
        """Границы классов симметричны относительно нуля."""
        result = classify_symmetric([-8.0, -3.0, -1.0, 2.0, 5.0, 11.0], class_count=6)
        assert result is not None
        assert result.breaks[0] == pytest.approx(-result.breaks[-1])
        assert 0.0 in result.breaks

    def test_sign_determines_side_of_scale(self) -> None:
        """Рост и снижение всегда оказываются по разные стороны шкалы."""
        result = classify_symmetric([-8.0, -3.0, -1.0, 2.0, 5.0, 11.0], class_count=6)
        assert result is not None
        middle = result.class_count // 2
        assert result.class_of(-0.5) is not None
        assert result.class_of(-0.5) < middle
        assert result.class_of(0.5) >= middle

    def test_zero_change_returns_nothing(self) -> None:
        """При отсутствии изменений шкала не строится."""
        assert classify_symmetric([0.0, 0.0, 0.0, 0.0]) is None


class TestPalettes:
    """Подбор переменных оформления под число классов."""

    @pytest.mark.parametrize("count", range(MIN_CLASSES, MAX_CLASSES + 1))
    def test_sequential_palette_matches_class_count(self, count: int) -> None:
        """Число цветов совпадает с числом классов, цвета не повторяются."""
        palette = sequential_palette(count)
        assert len(palette) == count
        assert len(set(palette)) == count

    def test_sequential_palette_spans_whole_scale(self) -> None:
        """Крайние классы получают крайние ступени шкалы."""
        palette = sequential_palette(3)
        assert palette[0] == "--scale-seq-1"
        assert palette[-1] == "--scale-seq-7"

    def test_diverging_palette_is_balanced(self) -> None:
        """На каждую сторону расходящейся шкалы приходится поровну цветов."""
        palette = diverging_palette(6)
        assert len(palette) == 6
        assert sum("neg" in name for name in palette) == sum("pos" in name for name in palette)


class TestHistogram:
    """Гистограмма распределения для легенды."""

    def test_counts_all_values(self) -> None:
        """В гистограмму попадают все значения ряда."""
        bars = histogram(SKEWED, bins=10)
        assert sum(bar["count"] for bar in bars) == len(SKEWED)

    def test_heights_are_normalised(self) -> None:
        """Высота столбца выражена долей от наибольшего."""
        bars = histogram(SKEWED, bins=10)
        assert max(bar["height"] for bar in bars) == pytest.approx(1.0)

    def test_constant_series_has_no_histogram(self) -> None:
        """Ряд из одинаковых значений не даёт распределения."""
        assert histogram([3.0] * 10) == []
