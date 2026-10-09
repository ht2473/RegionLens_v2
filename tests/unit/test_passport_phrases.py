"""Проверки правил выводов паспорта: ошибка правила читается как утверждение о регионе."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from django.utils import translation

from apps.catalog.passport import (
    BASKET_COST,
    BASKET_INCOME,
    BASKET_WAGE,
    TONE_BAD,
    TONE_GOOD,
    TONE_NEUTRAL,
    Passport,
    Position,
    _with_places,
    ordinal,
)
from apps.warehouse.queries.common import FeaturedSeries, RealBasis

pytestmark = pytest.mark.unit

NBSP = "\u00a0"


@pytest.fixture(autouse=True)
def russian() -> Iterator[None]:
    """Выводы проверяются на русском — языке, на котором они написаны."""
    with translation.override("ru"):
        yield


def series(**overrides: Any) -> FeaturedSeries:
    """Показатель набора с разумными значениями по умолчанию."""
    values: dict[str, Any] = {
        "key": "Y000:00",
        "short_title_ru": "Средняя зарплата",
        "short_title_en": "Average wage",
        "unit_label_ru": "руб. в месяц",
        "unit_label_en": "RUB per month",
        "polarity": "positive",
        "theme": "income",
        "precision": 0,
        "order": 1,
    }
    values.update(overrides)
    return FeaturedSeries(**values)


def position(item: FeaturedSeries | None = None, **overrides: Any) -> Position:
    """Положение территории: 20-е место из 85, значение 100, по России 100."""
    values: dict[str, Any] = {
        "series": item or series(),
        "year": 2024,
        "value": 100.0,
        "rank_desc": 20,
        "rank_asc": 66,
        "percentile": 0.77,
        "territories": 85,
        "country_value": 100.0,
    }
    values.update(overrides)
    return Position(**values)


class TestStanding:
    """Положение среди регионов словами."""

    @pytest.mark.parametrize(
        ("percentile", "expected", "tone"),
        [
            (0.85, "одно из лучших значений в стране", TONE_GOOD),
            (0.6, "лучше, чем в большинстве регионов", TONE_GOOD),
            (0.5, "в середине списка регионов", TONE_NEUTRAL),
            (0.4, "хуже, чем в большинстве регионов", TONE_BAD),
            (0.15, "одно из худших значений в стране", TONE_BAD),
        ],
    )
    def test_bounds_match_strength_selection(
        self, percentile: float, expected: str, tone: str
    ) -> None:
        """Граница 0,85 — уже «одно из лучших», 0,15 — уже «одно из худших»."""
        found = position(percentile=percentile)
        assert found.standing == expected
        assert found.tone == tone

    def test_negative_polarity_turns_the_scale(self) -> None:
        """У дестимулятора низкое значение — лучшее, и место считается снизу."""
        found = position(series(polarity="negative"), percentile=0.05, rank_asc=5, rank_desc=81)
        assert found.standing == "одно из лучших значений в стране"
        assert found.place == 5

    def test_neutral_series_is_not_judged(self) -> None:
        """Без направленности говорится «выше» и «ниже», и тон нейтрален."""
        found = position(series(polarity="neutral"), percentile=0.9)
        assert found.standing == "одно из самых высоких значений в стране"
        assert found.tone == TONE_NEUTRAL
        assert not found.is_assessed

    def test_absolute_value_is_not_judged(self) -> None:
        """
        Абсолютная величина не оценивается, даже с направленностью.

        Место по объёму выбросов говорит о размере промышленности: первое место
        отдаётся наибольшему значению, а не «лучшему».
        """
        found = position(
            series(polarity="negative", absolute=True), percentile=0.9, rank_desc=9, rank_asc=77
        )
        assert found.standing == "одно из самых высоких значений в стране"
        assert found.tone == TONE_NEUTRAL
        assert found.place == 9
        assert not found.is_assessed
        assert found.versus_country == ""

    def test_place_text(self) -> None:
        """Место записано порядковым числительным."""
        assert position().place_text == "20-е место из 85"


class TestVersusCountry:
    """Сравнение со значением по России."""

    def test_percentages_are_compared_in_points(self) -> None:
        """3,0 % против 2,5 % — «на 0,5 п. п. выше», а не «на 20 % выше»."""
        found = position(series(unit_label_ru="%", precision=1), value=3.0, country_value=2.5)
        assert found.versus_country == "на 0,5 п. п. выше, чем по России"

    def test_equal_after_rounding(self) -> None:
        """Разница, исчезающая при округлении, — «на уровне России»."""
        found = position(series(unit_label_ru="%", precision=1), value=2.51, country_value=2.5)
        assert found.versus_country == "на уровне России"

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (115.0, f"на 15{NBSP}% выше, чем по России"),
            (85.0, f"на 15{NBSP}% ниже, чем по России"),
            (180.0, "в 1,8 раза выше, чем по России"),
            (200.0, "в 2 раза выше, чем по России"),
            (500.0, "в 5 раз выше, чем по России"),
            (1230.0, "в 12 раз выше, чем по России"),
            (2200.0, "в 22 раза выше, чем по России"),
            (50.0, "в 2 раза ниже, чем по России"),
            (100.4, "на уровне России"),
        ],
    )
    def test_ratio_wording(self, value: float, expected: str) -> None:
        """Малая разница — долей, большая — кратностью с согласованным словом «раз»."""
        assert position(value=value).versus_country == expected

    def test_values_crossing_zero_give_direction_only(self) -> None:
        """При естественной убыли отношение бессмысленно: называется только направление."""
        found = position(series(unit_label_ru="на 1000 жителей"), value=-1.7, country_value=-3.5)
        assert found.versus_country == "выше, чем по России"

    def test_no_country_value(self) -> None:
        """Без значения по России сравнения нет."""
        assert position(country_value=None).versus_country == ""


class TestTrend:
    """Изменение за пять лет."""

    def test_change_with_country(self) -> None:
        """Изменение региона идёт рядом с изменением страны."""
        found = position(
            value=110.0, past_value=100.0, country_value=120.0, country_past_value=100.0
        )
        assert found.trend == (f"рост на 10{NBSP}% за 5 лет (по России — рост на 20{NBSP}%)")

    def test_break_blocks_the_change(self) -> None:
        """Через разрыв сопоставимости изменение не считается, и называется год."""
        found = position(value=110.0, past_value=100.0, past_year=2019, break_year=2022)
        assert found.trend == "за 5 лет не сравнивается: разрыв сопоставимости в 2022 году"
        assert found.trend_tone == TONE_NEUTRAL

    def test_territory_break_hides_only_the_country(self) -> None:
        """Смена состава территорий прерывает ряд страны, но не ряд субъекта."""
        found = position(
            value=110.0,
            past_value=100.0,
            country_value=120.0,
            country_past_value=100.0,
            country_break=True,
        )
        assert found.trend == f"рост на 10{NBSP}% за 5 лет"

    def test_growth_rates_have_no_five_year_change(self) -> None:
        """Темп «% к прошлому году» за пять лет не сравнивается."""
        found = position(series(unit_label_ru="% к прошлому году"), value=105.0, past_value=101.0)
        assert found.trend == ""

    def test_percentages_change_in_points(self) -> None:
        """Изменение доли — в процентных пунктах."""
        found = position(
            series(unit_label_ru="%", precision=1), value=12.3, past_value=9.6, country_value=None
        )
        assert found.trend == "рост на 2,7 п. п. за 5 лет"

    def test_lagging_growth_is_bad(self) -> None:
        """Рост зарплаты на 70 % при росте по стране на 86 % — отставание."""
        found = position(
            value=170.0, past_value=100.0, country_value=186.0, country_past_value=100.0
        )
        assert found.trend_tone == TONE_BAD

    def test_faster_decline_of_a_negative_indicator_is_good(self) -> None:
        """Младенческая смертность, снизившаяся сильнее, чем по стране, — улучшение."""
        item = series(polarity="negative", unit_label_ru="на 1000 родившихся", precision=1)
        found = position(item, value=2.6, past_value=4.9, country_value=4.0, country_past_value=4.9)
        assert found.trend_tone == TONE_GOOD

    def test_direction_decides_without_country(self) -> None:
        """Без изменения по стране оценивается направление."""
        item = series(polarity="negative")
        found = position(item, value=120.0, past_value=100.0, country_value=None)
        assert found.trend_tone == TONE_BAD


class TestMoneyTrend:
    """Изменение денежных рядов: реально, в рублях — в скобках, оценка — по реальному."""

    MONEY = RealBasis(method="index", index="Y477110362:00")

    def test_chukotka_real_growth_is_not_lagging(self) -> None:
        """
        Зарплата Чукотки за 2019–2024 годы: +76 % в рублях при +86 % по России — отставание
        в номинале, но реально +43 % при +29 % по России — опережение.
        """
        found = position(
            series(real=self.MONEY),
            value=176.0,
            past_value=100.0,
            past_year=2019,
            country_value=186.1,
            country_past_value=100.0,
            real_ratio=1.43,
            country_real_ratio=1.292,
        )
        assert found.trend == (
            f"реальный рост на 43{NBSP}% за 5 лет (в рублях — рост на 76{NBSP}%); "
            f"по России — реальный рост на 29{NBSP}%"
        )
        assert found.trend_tone == TONE_GOOD

    def test_without_index_only_roubles_and_no_assessment(self) -> None:
        """Без индекса за какой-то год называется изменение в рублях, без оценки."""
        found = position(
            series(real=self.MONEY),
            value=176.0,
            past_value=100.0,
            country_value=186.1,
            country_past_value=100.0,
        )
        assert found.trend == (
            f"рост на 76{NBSP}% в рублях за 5 лет (по России — рост на 86{NBSP}%)"
        )
        assert found.trend_tone == TONE_NEUTRAL

    def test_real_decline(self) -> None:
        """Реальное снижение называется снижением и при росте в рублях."""
        found = position(
            series(real=self.MONEY),
            value=105.0,
            past_value=100.0,
            country_value=None,
            real_ratio=0.93,
        )
        assert found.trend == (
            f"реальное снижение на 7{NBSP}% за 5 лет (в рублях — рост на 5{NBSP}%)"
        )
        assert found.trend_tone == TONE_BAD

    def test_break_still_blocks_money(self) -> None:
        """Разрыв сопоставимости отменяет и реальное изменение."""
        found = position(
            series(real=self.MONEY),
            value=176.0,
            past_value=100.0,
            past_year=2019,
            break_year=2022,
            real_ratio=1.4,
        )
        assert found.trend == "за 5 лет не сравнивается: разрыв сопоставимости в 2022 году"
        assert found.change is None


class TestBaskets:
    """Покупательная способность: доход и зарплата в фиксированных наборах."""

    def passport(self, *positions: Position) -> Passport:
        return Passport(positions=list(positions), strengths=[], weaknesses=[], themes=[], total=59)

    def test_facts_for_income_and_wage_tiles(self) -> None:
        """У плиток дохода и зарплаты — число наборов и то же по России."""
        found = self.passport(
            position(series(key=BASKET_COST), value=37_866.0, country_value=23_780.0),
            position(series(key=BASKET_INCOME), value=187_687.0, country_value=63_959.0),
            position(series(key=BASKET_WAGE), value=188_561.0, country_value=None),
        )
        assert found.basket_facts() == {
            BASKET_INCOME: {"value": "5,0", "country": "2,7"},
            BASKET_WAGE: {"value": "5,0", "country": ""},
        }

    def test_income_and_wage_in_baskets(self) -> None:
        """Доход и зарплата делятся на стоимость набора того же года; по России — рядом."""
        found = self.passport(
            position(series(key=BASKET_COST), value=37_866.0, country_value=23_780.0),
            position(series(key=BASKET_INCOME), value=187_687.0, country_value=63_959.0),
            position(series(key=BASKET_WAGE), value=188_561.0, country_value=89_069.0),
        )
        assert found.baskets == (
            "На средний доход можно купить 5,0 фиксированного набора товаров и услуг, "
            "на среднюю зарплату — 5,0 (по России — 2,7 и 3,7); 2024 год, расчёт RegionLens."
        )

    def test_different_years_are_not_divided(self) -> None:
        """Доход другого года на стоимость набора не делится."""
        found = self.passport(
            position(series(key=BASKET_COST), value=30_000.0),
            position(series(key=BASKET_INCOME), year=2023, value=60_000.0),
        )
        assert found.baskets == ""


class TestWording:
    """Мелкие правила записи."""

    @pytest.mark.parametrize(
        ("number", "expected"),
        [
            (1, "1st"),
            (2, "2nd"),
            (3, "3rd"),
            (4, "4th"),
            (11, "11th"),
            (12, "12th"),
            (13, "13th"),
            (21, "21st"),
            (22, "22nd"),
            (101, "101st"),
            (111, "111th"),
        ],
    )
    def test_english_ordinals(self, number: int, expected: str) -> None:
        """Английские порядковые, включая исключения 11–13."""
        with translation.override("en"):
            assert ordinal(number) == expected

    def test_russian_ordinal(self) -> None:
        """Русское порядковое — цифрами с окончанием."""
        assert ordinal(8) == "8-е"

    def test_places_keep_abbreviations(self) -> None:
        """
        В перечне строчная первая буква, кроме сокращений вроде «ВРП»; число регионов —
        один раз, у первого места.
        """
        items = [
            position(series(short_title_ru="ВРП на душу населения"), rank_desc=1),
            position(series(short_title_ru="Уровень бедности"), rank_desc=2),
            position(series(short_title_ru="Средняя зарплата"), rank_desc=3),
        ]
        assert _with_places(items) == (
            "ВРП на душу населения (1-е место из 85), уровень бедности (2-е) "
            "и средняя зарплата (3-е)"
        )


class TestLead:
    """Вводная паспорта: три строки с числами."""

    def test_three_lines_with_numbers(self) -> None:
        """Население с местом, лучшие и худшие места; полнота — только когда сведений меньше."""
        people = series(
            key="Y001:00",
            role="population",
            short_title_ru="Численность населения",
            unit_label_ru="тыс. чел.",
            polarity="neutral",
            absolute=True,
            precision=1,
        )
        strong = position(series(short_title_ru="Уровень бедности"), rank_desc=3)
        weak = position(series(short_title_ru="Средняя зарплата"), rank_desc=80)
        population = position(people, value=4003.0, rank_desc=8)
        found = Passport(
            positions=[population, strong, weak],
            strengths=[strong],
            weaknesses=[weak],
            themes=[],
            total=3,
        )
        assert [line.label for line in found.lead] == [
            "Население",
            "В числе лучших",
            "В числе худших",
        ]
        assert found.summary[0].startswith(f"Население — 4{NBSP}003,0 тыс. чел., 8-е место из 85")
        assert found.summary[1] == "В числе лучших — уровень бедности (3-е место из 85)."
        assert found.summary[2] == "В числе худших — средняя зарплата (80-е место из 85)."

    def test_incomplete_data_line(self) -> None:
        """Сведений меньше, чем показателей в наборе, — четвёртая строка."""
        found = Passport(positions=[position()], strengths=[], weaknesses=[], themes=[], total=69)
        assert found.summary[-1] == "Полнота данных — по 1 из 69 основных показателей."
        assert found.lead[0].text == "нет ни одного основного показателя"

    def test_no_data_no_lead(self) -> None:
        """Без сведений вводной нет: «ни одного в числе лучших» было бы неправдой."""
        assert Passport(positions=[], strengths=[], weaknesses=[], themes=[], total=69).lead == []
