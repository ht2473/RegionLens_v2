"""
Правила приведения строк источника к единице ряда (проверка данных 18.11.2026).

Примеры — из набора Росстата: разрез называет проценты при единице «Миллионов рублей»,
выпуски объявляют момент «на 1 января» и «на 31 декабря», значение по России в млрд руб.
объявлено в самом разрезе.
"""

from __future__ import annotations

import pytest

from apps.catalog.constants import UnitKind
from apps.catalog.units import short_unit_label
from apps.warehouse.etl.classify import (
    classify_unit,
    declared_percent,
    text_country_scale,
    unit_base,
    unit_moment,
)

pytestmark = pytest.mark.unit


class TestDeclaredPercent:
    """Проценты, названные в разрезе как единица."""

    @pytest.mark.parametrize(
        ("subsection", "expected"),
        [
            ("Уровень фактических платежей населения, процентов", "Процентов"),
            (
                "Утилизация отходов: в процентах от общего объема образовавшихся отходов",
                "В процентах от общего объема образовавшихся отходов",
            ),
            (
                "Индексы цен, в процентах к концу предыдущего года: первичный рынок",
                ("В процентах к концу предыдущего года"),
            ),
        ],
    )
    def test_percent_is_found(self, subsection: str, expected: str) -> None:
        """Проценты после запятой или двоеточия — единица разреза."""
        assert declared_percent(subsection) == expected

    @pytest.mark.parametrize(
        "subsection",
        [
            "Процент износа: от 0 до 30",
            "В том числе по 20-процентным группам населения: первая",
            "Совокупная задолженность по налогам, пеням, налоговым санкциям и процентам",
            "Всего, тыс. человек",
            "",
        ],
    )
    def test_grouping_is_not_a_unit(self, subsection: str) -> None:
        """Группировка по проценту и «проценты» как предмет счёта — не единица."""
        assert declared_percent(subsection) == ""


class TestClassifyUnitFromSubsection:
    """Единица ряда, когда набор называет её неверно или не называет вовсе."""

    def test_percent_subsection_overrides_money(self) -> None:
        """«Уровень платежей, процентов» при единице «Миллионов рублей» — проценты."""
        item = classify_unit(
            "Миллионов рублей",
            indicator_name="Объем и уровень фактических платежей населения",
            subsection="Уровень фактических платежей населения, процентов",
        )
        assert item.kind == UnitKind.SHARE
        assert item.short_name_ru == "%"
        assert item.country_scale == 1.0

    def test_index_unit_is_kept(self) -> None:
        """Относительная единица набора не заменяется."""
        item = classify_unit(
            "В процентах к предыдущему году", subsection="Индекс, в процентах к предыдущему году"
        )
        assert item.kind == UnitKind.INDEX
        assert item.name_ru == "В процентах к предыдущему году"

    def test_qualifier_takes_unit_from_subsection(self) -> None:
        """Уточнение без единицы дополняется единицей из разреза."""
        item = classify_unit(
            "На конец года", subsection="Удельный вес в общей численности населения, процентов"
        )
        assert item.name_ru == "На конец года, процентов"
        assert item.short_name_ru == "%"

    def test_qualifier_keeps_multiplier(self) -> None:
        """«На конец года» при разрезе «Тысяч человек» — тысячи, а не человек."""
        item = classify_unit(
            "На конец года",
            indicator_name="Численность населения в трудоспособном возрасте",
            subsection="Тысяч человек",
        )
        assert item.short_name_ru == "тыс. чел."
        assert item.multiplier == 1000

    def test_unit_after_colon(self) -> None:
        """Единица набора — только объявление для России; единица — после двоеточия разреза."""
        item = classify_unit(
            "; для значений в целом по России: млрд руб",
            subsection="Оборот общественного питания: млн руб.",
        )
        assert item.kind == UnitKind.CURRENCY
        assert item.short_name_ru == "млн руб."

    def test_trailing_level_unit_overrides_share(self) -> None:
        """«Средний возраст, лет» при единице «В процентах к итогу» — годы."""
        item = classify_unit("В процентах к итогу", subsection="Средний возраст, лет")
        assert item.name_ru == "Лет"
        assert item.short_name_ru == "лет"

    def test_group_label_is_not_a_unit(self) -> None:
        """«В том числе в возрасте, лет: 20-29» — подпись группы, единица — проценты."""
        item = classify_unit("В процентах к итогу", subsection="В том числе в возрасте, лет: 20-29")
        assert item.kind == UnitKind.SHARE

    def test_compound_subsection_gives_unit(self) -> None:
        """«Миллионов рублей (по Российской Федерации — млрд рублей)» — единица ряда."""
        item = classify_unit(
            "В фактически действовавших ценах",
            subsection="Миллионов рублей (по Российской Федерации — млрд рублей)",
        )
        assert item.kind == UnitKind.CURRENCY
        assert item.short_name_ru == "млн руб."


class TestCountryScaleInText:
    """Приведение значения по России, объявленное в тексте разреза."""

    def test_billions_for_country(self) -> None:
        """Регионы в миллионах, Россия в миллиардах — множитель 1000."""
        scale = text_country_scale("Миллионов рублей (по Российской Федерации — млрд рублей)")
        assert scale == pytest.approx(1000)

    def test_without_declaration(self) -> None:
        """Нет объявления — нет приведения."""
        assert text_country_scale("На душу населения, руб.", "Расходы населения") is None


class TestRowUnits:
    """Признаки строки для приведения к единице ряда."""

    def test_moment(self) -> None:
        """«На 31 декабря» и «на 1 января» различаются, прочее — без момента."""
        assert unit_moment("На 1 января, тыс. человек") == "jan1"
        assert unit_moment("На 31 декабря, человек") == "dec31"
        assert unit_moment("На конец года") == ""

    def test_same_unit_different_multiplier(self) -> None:
        """«Человек» и «тыс. человек» — одна единица, множители разные."""
        people = unit_base("На 1 января, человек")
        thousands = unit_base("На 1 января, тыс. человек")
        assert people[:2] == thousands[:2] == (UnitKind.ABSOLUTE, "чел.")
        assert people[2] is None
        assert thousands[2] == 1000

    def test_level_and_relative_differ(self) -> None:
        """«Млн рублей» и «% к предыдущему году» — разные категории."""
        assert unit_base("По данным бухгалтерской отчетности, миллионов рублей")[0] == (
            UnitKind.CURRENCY
        )
        assert (
            unit_base("По данным бухгалтерской отчетности, в процентах к предыдущему году")[0]
            == UnitKind.INDEX
        )


class TestQualifierLabels:
    """Уточнения источника без единицы не выводятся подписью."""

    @pytest.mark.parametrize(
        "name",
        [
            "В течение года",
            "Без субъектов малого предпринимательства, по данным выборочного обследования",
            "Площадки и поля",
        ],
    )
    def test_qualifier_has_no_label(self, name: str) -> None:
        """Уточнение момента или охвата — пустая подпись."""
        assert short_unit_label(name) == ""

    def test_lowercase_units(self) -> None:
        """Единицы, записанные одним словом с заглавной, — строчными."""
        assert short_unit_label("Месяцев") == "мес."
        assert short_unit_label("Семей") == "семей"
