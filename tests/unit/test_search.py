"""
Проверки разбора поискового запроса; сопоставление со столбцом — в test_pages.py.

При локали базы ``C`` штатный icontains различает регистр кириллицы.
"""

from __future__ import annotations

import pytest

from apps.core.search import escape_like, normalize, search_q, terms

pytestmark = pytest.mark.unit


class TestNormalize:
    """Приведение строки к виду сравнения."""

    def test_case_is_removed_for_cyrillic(self) -> None:
        """Регистр кириллицы снимается."""
        assert normalize("БезРаботица") == "безработица"

    def test_yo_is_equated_to_ye(self) -> None:
        """«Ё» и «е» считаются одной буквой: в источнике они пишутся вперемешку."""
        assert normalize("Учёт") == normalize("Учет")


class TestTerms:
    """Разбор запроса на слова."""

    def test_query_is_split_into_words(self) -> None:
        """Слова отделяются друг от друга и приводятся к нижнему регистру."""
        assert terms("Валовой  региональный ПРОДУКТ") == [
            "валовой",
            "региональный",
            "продукт",
        ]

    def test_single_letter_words_are_dropped(self) -> None:
        """Предлоги есть почти в каждом названии и выдачу не сужают."""
        assert terms("доходы в регионах") == ["доходы", "регионах"]

    def test_short_query_survives_whole(self) -> None:
        """Короткий запрос целиком сохраняется: «ВРП» вводят именно так."""
        assert terms("ВРП") == ["врп"]

    def test_number_of_words_is_capped(self) -> None:
        """Длинный запрос обрезается: это вставленное название, а не поиск."""
        assert len(terms(" ".join(f"слово{index}" for index in range(20)))) == 6

    def test_units_survive_splitting(self) -> None:
        """Знаки единиц измерения — часть слова, а не разделитель."""
        assert terms("м²") == ["м²"]


class TestEscapeLike:
    """Экранирование образца."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("100 %", "100 \\%"),
            ("код_2", "код\\_2"),
            ("путь\\файл", "путь\\\\файл"),
        ],
    )
    def test_pattern_symbols_are_escaped(self, value: str, expected: str) -> None:
        """
        Без экранирования запрос «100 %» отвечал бы любой строке.

        Обратная косая черта экранируется первой, иначе экранирующие знаки,
        добавленные для процента, экранировались бы повторно.
        """
        assert escape_like(value) == expected


class TestSearchQ:
    """Сборка условия отбора."""

    def test_empty_query_does_not_narrow_selection(self) -> None:
        """Пустой запрос не должен превращаться в условие, отсекающее всё."""
        assert not search_q("", "name_ru").children

    def test_words_are_joined_by_and(self) -> None:
        """
        Два слова означают пересечение, а не объединение.

        При объединении запрос из двух слов давал бы выдачу шире, чем каждое
        слово по отдельности, — противоположность тому, чего ждёт пользователь.
        """
        condition = search_q("доходы населения", "name_ru")
        assert condition.connector == "AND"
        assert len(condition.children) == 2
