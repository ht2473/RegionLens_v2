"""Проверки компактной подписи единицы на объявлениях из набора Росстата."""

from __future__ import annotations

import pytest

from apps.catalog.units import MAX_SHORT_UNIT_LENGTH, short_unit_label

pytestmark = pytest.mark.unit


class TestShortUnitLabel:
    """Построение компактной подписи единицы измерения."""

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("Миллионов рублей", "млн руб."),
            ("Миллиардов рублей", "млрд руб."),
            ("Тысяч рублей", "тыс. руб."),
            ("Рублей", "руб."),
            ("Тысяч человек", "тыс. чел."),
            ("Человек", "чел."),
            ("Тысяч квадратных метров", "тыс. м²"),
            ("Тысяч кубических метров", "тыс. м³"),
            ("Тысяч штук", "тыс. шт."),
            ("Тонн", "т"),
        ],
    )
    def test_known_units_are_abbreviated(self, source: str, expected: str) -> None:
        """Общеупотребительные единицы заменяются принятыми обозначениями."""
        assert short_unit_label(source) == expected

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("В процентах от общего объема денежных доходов", "%"),
            ("В процентах от общего объема инвестиций в основной капитал", "%"),
            (
                "Без субъектов малого предпринимательства, в процентах от общей "
                "численности работников организаций обрабатывающих производств, на конец года",
                "%",
            ),
            ("По полной учетной стоимости, миллионов рублей", "млн руб."),
            ("Исходя из места привлечения средств; миллионов рублей", "млн руб."),
        ],
    )
    def test_unit_is_found_inside_a_phrase(self, source: str, expected: str) -> None:
        """Единица распознаётся в любом месте объявления, а не по началу строки."""
        assert short_unit_label(source) == expected

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("Число умерших на 1000 человек населения", "на 1000 чел."),
            ("Число умерших на 100 000 человек населения", "на 100 тыс. чел."),
            ("Число родившихся на 1000 человек населения", "на 1000 чел."),
            (
                "Число детей, умерших в возрасте до 1 года, на 1000 родившихся живыми",
                "на 1000 родившихся",
            ),
        ],
    )
    def test_ratio_is_not_reduced_to_its_base(self, source: str, expected: str) -> None:
        """
        Отношение к численности не сводится к единице основания.

        Коэффициент смертности измеряется не в людях, а в случаях на тысячу жителей:
        подпись «чел.» превратила бы его в число людей.
        """
        assert short_unit_label(source) == expected

    def test_own_unit_wins_over_the_base_of_a_ratio(self) -> None:
        """Единица, названная раньше основания, важнее него: «Кг топлива на 10 тысяч рублей»."""
        assert short_unit_label("Кг условного топлива/на 10 тысяч рублей") == "кг"

    @pytest.mark.parametrize(
        "source",
        [
            "На 1 января",
            "На 31 декабря",
            "На конец года",
            "По состоянию на 1 января",
            "В среднем за год",
            "В фактически действовавших ценах",
            "Не указана",
        ],
    )
    def test_declarations_without_a_unit_give_no_label(self, source: str) -> None:
        """
        Момент наблюдения и способ учёта единицей не являются.

        Такая строка в подписи оси утверждала бы, что величина измеряется датой.
        """
        assert short_unit_label(source) == ""

    def test_date_inside_a_declaration_does_not_become_a_ratio(self) -> None:
        """Оборот «на 1 января» — дата, а не отношение к основанию."""
        assert short_unit_label("На 1 января, мест") == "мест"
        assert short_unit_label("На 1 января, тыс. человек") == "тыс. чел."

    def test_multiplier_without_a_unit_is_kept(self) -> None:
        """Множитель без единицы сообщает, в каких долях приведены значения."""
        assert short_unit_label("По состоянию на конец отчетного периода, тысяч") == "тыс."

    def test_multiplier_is_read_through_intervening_words(self) -> None:
        """Между множителем и единицей встречаются посторонние слова."""
        assert short_unit_label("Тысяч плотных кубических метров") == "тыс. м³"

    def test_share_ignores_a_multiplier(self) -> None:
        """Доля безразмерна: множитель к ней не относится."""
        assert short_unit_label("В процентах от общего объема, тысяч") == "%"

    def test_unrecognised_declaration_breaks_at_a_word(self) -> None:
        """
        Нераспознанное объявление усекается по границе слова.

        Обрыв посреди слова читается как ошибка вёрстки, обрыв по слову — как сокращение.
        """
        label = short_unit_label(
            "Без субъектов малого предпринимательства, "
            "по данным выборочного обследования организаций"
        )
        assert label.endswith("…")
        assert len(label) <= MAX_SHORT_UNIT_LENGTH
        # Усечение проходит по пробелу: последнее слово подписи не обрывается.
        assert label.rstrip("…").split()[-1] in {"Без", "субъектов", "малого"}

    def test_empty_declaration_gives_empty_label(self) -> None:
        """Пустое объявление не порождает подписи."""
        assert short_unit_label("") == ""
        assert short_unit_label("   ") == ""
