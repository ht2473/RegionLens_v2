"""
Проверки признака ненормированной величины — по категории единицы и названию.

Примеры из набора Росстата, включая пары «всего» и «на душу населения» с одной единицей.
"""

from __future__ import annotations

import pytest

from apps.catalog.constants import is_unnormalised_title

pytestmark = pytest.mark.unit


class TestIsUnnormalisedTitle:
    """Распознавание величины, не приведённой к основе."""

    @pytest.mark.parametrize(
        ("title", "unit_kind"),
        [
            ("Валовой региональный продукт млн руб.", "currency"),
            ("Численность населения тыс. чел.", "absolute"),
            ("Валовой сбор зерна (в весе после доработки) тыс. т", "physical"),
            ("Ввод в действие жилых домов тыс. м²", "physical"),
            ("Ввод в действие водопроводных сетей км", "physical"),
            ("Валовое накопление основного капитала млн руб.", "currency"),
        ],
    )
    def test_totals_are_flagged(self, title: str, unit_kind: str) -> None:
        """Итоговые величины по региону помечаются."""
        assert is_unnormalised_title(title, unit_kind) is True

    @pytest.mark.parametrize(
        ("title", "unit_kind"),
        [
            # Та же единица измерения, что и у величины всего: различает формулировка.
            ("Валовой региональный продукт на душу населения руб.", "currency"),
            ("Инвестиции в основной капитал на душу населения руб.", "currency"),
            ("Среднедушевые денежные доходы населения руб./мес.", "currency"),
            ("Среднемесячная номинальная начисленная заработная плата руб.", "currency"),
            ("Ввод в действие жилых домов: На 1000 человек населения м²", "physical"),
            (
                "Ввод в действие мощностей больничных организаций: "
                "На 100000 человек населения коек",
                "physical",
            ),
            ("Внесение удобрений на один гектар посева кг", "unknown"),
        ],
    )
    def test_normalised_titles_are_not_flagged(self, title: str, unit_kind: str) -> None:
        """Величины, приведённые к основе, не помечаются."""
        assert is_unnormalised_title(title, unit_kind) is False

    @pytest.mark.parametrize("unit_kind", ["share", "index", "rate"])
    def test_relative_unit_kinds_are_never_flagged(self, unit_kind: str) -> None:
        """
        Доли, индексы и относительные показатели нормированы по построению.

        Проверяется на названии без единого признака нормирования: категория единицы
        измерения должна перевешивать формулировку.
        """
        assert is_unnormalised_title("Валовой региональный продукт", unit_kind) is False

    def test_non_breaking_spaces_do_not_hide_the_marker(self) -> None:
        """
        Неразрывные пробелы в разрядах не мешают распознаванию.

        Названия источника содержат неразрывные и узкие пробелы вперемежку с обычными,
        и без приведения пробелов «на 1 000 человек» не совпало бы с признаком.
        """
        # Неразрывный и узкий неразрывный пробелы — кодами: в тексте они неотличимы от обычного.
        title = f"Число коек на{chr(0x00A0)}1{chr(0x202F)}000 человек шт."
        assert is_unnormalised_title(title, "absolute") is False
