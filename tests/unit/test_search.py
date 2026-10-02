"""
Проверки разбора поискового запроса; сопоставление со столбцом — в test_pages.py.

При локали базы ``C`` штатный icontains различает регистр кириллицы.
"""

from __future__ import annotations

import pytest

from apps.core.search import correct, escape_like, normalize, search_q, terms, words_of

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


class TestWordsOf:
    """Словарь слов для исправления опечаток."""

    def test_short_words_are_left_out(self) -> None:
        """
        Предлоги и союзы в словарь не идут.

        Они есть почти в каждом названии, а исправлять по ним нечего: слово
        из двух букв похоже на десяток других так же сильно.
        """
        assert words_of("Ввод в действие жилых домов") == {"ввод", "действие", "жилых", "домов"}

    def test_case_and_yo_are_folded(self) -> None:
        """Словарь хранит слова в том же виде, в каком приходит запрос."""
        assert words_of("Приём ЖИЛЬЯ") == {"прием", "жилья"}


class TestCorrect:
    """Исправление опечаток по словарю."""

    VOCABULARY = sorted(
        words_of(
            "Республика Татарстан",
            "Ненецкий автономный округ",
            "Уровень безработицы населения",
            "Валовой региональный продукт на душу населения",
        )
    )

    @pytest.mark.parametrize(
        ("query", "expected"),
        [
            ("татарстн", "татарстан"),
            ("ннецкий", "ненецкий"),
            ("безрабтицы", "безработицы"),
            ("валовый регионльный", "валовой региональный"),
        ],
    )
    def test_typos_are_repaired(self, query: str, expected: str) -> None:
        """Опечатка в длинном слове исправляется по ближайшему слову словаря."""
        assert correct(query, self.VOCABULARY) == expected

    def test_correct_query_is_left_alone(self) -> None:
        """Запрос, слова которого есть в словаре, не переписывается."""
        assert correct("республика татарстан", self.VOCABULARY) == "республика татарстан"

    def test_short_words_are_not_guessed(self) -> None:
        """
        Короткое слово не исправляется.

        У трёхбуквенного слова близких соседей столько, что исправление
        становится угадыванием: «врп» превратилось бы в первое похожее.
        """
        assert correct("врп", self.VOCABULARY) == "врп"

    def test_unknown_word_survives(self) -> None:
        """Слово, ни на что не похожее, остаётся как есть: это не опечатка."""
        assert correct("криптовалюта", self.VOCABULARY) == "криптовалюта"

    def test_empty_vocabulary_changes_nothing(self) -> None:
        """Без словаря исправлять не по чему, и запрос уходит нетронутым."""
        assert correct("татарстн", []) == "татарстн"
