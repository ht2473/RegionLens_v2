"""Проверки фильтров оформления чисел: отсутствие данных, ноль и число различаются на экране."""

from __future__ import annotations

from typing import Any

import pytest

from apps.core.templatetags.formatting import (
    coverage_bar,
    dict_value,
    p_level,
    percent,
    percent_delta,
    position_share,
    quality_label,
    ru_delta,
    ru_number,
    year,
)

pytestmark = pytest.mark.unit

# Неразрывный пробел, которым разделяются разряды.
NBSP = " "


class TestNumber:
    """Форматирование чисел."""

    def test_thousands_are_separated_by_non_breaking_space(self) -> None:
        """Разряды разделяются неразрывным пробелом, а не обычным."""
        assert ru_number(1_234_567.891, 0) == f"1{NBSP}234{NBSP}568"

    def test_decimal_separator_is_comma(self) -> None:
        """Дробная часть отделяется запятой: это русская типографика."""
        assert ru_number(3.14159, 2) == "3,14"

    def test_precision_is_chosen_by_magnitude(self) -> None:
        """
        Без явной точности она подбирается по порядку величины.

        Крупные значения показываются целыми, мелкие — с двумя знаками:
        «1 234 567,89 человек» читается хуже, чем «1 234 568».
        """
        assert ru_number(1_234_567.891) == f"1{NBSP}234{NBSP}568"
        assert ru_number(0.4567) == "0,46"

    @pytest.mark.parametrize("value", [None, ""])
    def test_missing_value_is_a_dash(self, value: Any) -> None:
        """Пропуск показывается прочерком, а не нулём."""
        assert ru_number(value) == "—"

    def test_zero_is_a_number(self) -> None:
        """Ноль остаётся нулём и не превращается в прочерк."""
        assert ru_number(0, 0) == "0"

    def test_non_numeric_value_is_returned_as_is(self) -> None:
        """Нечисловое значение выводится без изменений, а не роняет страницу."""
        assert ru_number("нет данных") == "нет данных"


class TestDelta:
    """Изменения показателей."""

    def test_growth_is_signed(self) -> None:
        """Рост показывается со знаком плюс."""
        assert ru_delta(12.3) == "+12,3"

    def test_decline_keeps_minus(self) -> None:
        """Убыль сохраняет знак минус."""
        assert ru_delta(-12.3) == "−12,3".replace("−", "-")

    def test_missing_delta_is_a_dash(self) -> None:
        """Отсутствие изменения показывается прочерком."""
        assert ru_delta(None) == "—"


class TestPercent:
    """Доли и относительные изменения."""

    def test_share_is_shown_in_percent(self) -> None:
        """Доля переводится в проценты."""
        assert percent(0.1234) == f"12,3{NBSP}%"

    def test_relative_change_is_signed(self) -> None:
        """Относительное изменение показывается со знаком."""
        assert percent_delta(0.052) == f"+5,2{NBSP}%"

    def test_missing_share_is_a_dash(self) -> None:
        """Отсутствующая доля показывается прочерком."""
        assert percent(None) == "—"


class TestSignificanceLevel:
    """Уровень значимости."""

    def test_level_has_three_digits(self) -> None:
        """Уровень показывается тремя знаками."""
        assert p_level(0.2174) == "0,217"

    def test_tiny_level_is_not_zero(self) -> None:
        """Уровень меньше тысячной не превращается в «0,000»."""
        assert p_level(0.00001) == f"<{NBSP}0,001"

    def test_named_level_is_a_whole_record(self) -> None:
        """С обозначением — запись целиком: равенство или неравенство."""
        assert p_level(0.2174, "p") == f"p{NBSP}={NBSP}0,217"
        assert p_level(0.0, "p") == f"p{NBSP}<{NBSP}0,001"

    def test_missing_level_is_a_dash(self) -> None:
        """Отсутствующий уровень показывается прочерком."""
        assert p_level(None) == "—"


class TestHelpers:
    """Вспомогательные фильтры."""

    def test_dictionary_lookup_by_variable_key(self) -> None:
        """Значение словаря доступно по вычисленному в шаблоне ключу."""
        assert dict_value({"gini": "Джини"}, "gini") == "Джини"

    @pytest.mark.parametrize("mapping", [None, "строка", 5])
    def test_lookup_of_non_mapping_is_empty(self, mapping: Any) -> None:
        """Обращение не к словарю не роняет отрисовку страницы."""
        assert dict_value(mapping, "gini") == ""

    def test_quality_labels_distinguish_reasons(self) -> None:
        """Причины пропуска подписаны по-разному."""
        assert quality_label(1) != quality_label(2)
        assert quality_label(99) == "неизвестно"
        assert quality_label(None) == "неизвестно"

    def test_coverage_bar_is_clamped(self) -> None:
        """Доля покрытия удерживается в границах от нуля до единицы."""
        assert "100%" in coverage_bar(1.7)
        assert "0%" in coverage_bar(-0.5)
        assert "0%" in coverage_bar("нечисло")

    def test_low_coverage_is_marked(self) -> None:
        """Низкое покрытие помечается отдельным оформлением."""
        assert "coverage-meter__fill--low" in coverage_bar(0.1)
        assert "coverage-meter__fill--low" not in coverage_bar(0.95)

    def test_year_is_shown_without_separator(self) -> None:
        """
        Год выводится без разделителя разрядов.

        Общая настройка форматирования чисел иначе превратила бы период
        «2001–2025» в «2 001–2 025».
        """
        assert year(2025) == "2025"
        assert year(None) == "—"
        assert year("нечисло") == "нечисло"


class TestPositionShare:
    """Положение в распределении вместо места в рейтинге."""

    def test_share_of_regions_below_is_named(self) -> None:
        """
        Названа доля субъектов, оставшихся ниже.

        Место скрывает величину разрыва: «одиннадцатое» не говорит, отделено ли оно
        от десятого долями процента или разницей в разы.
        """
        assert position_share(0.87) == f"выше 87{NBSP}% субъектов"

    def test_polarity_aware_wording(self) -> None:
        """
        Там, где учтена направленность показателя, сказано «лучше».

        У безработицы и бедности лучшим считается низкое значение, и «выше»
        означало бы обратное тому, что показано.
        """
        assert position_share(0.92, "better") == f"лучше 92{NBSP}% субъектов"

    def test_share_is_rounded_to_whole_percent(self) -> None:
        """Доли процента здесь не значат ничего: они меньше цены одного субъекта."""
        assert position_share(0.874) == f"выше 87{NBSP}% субъектов"

    def test_unknown_position_is_a_dash(self) -> None:
        """Неизвестное положение остаётся прочерком, а не нулём."""
        assert position_share(None) == "—"
