"""
Правильность анализа на собранном складе: постоянный состав, разрывы в инструментах,
год по умолчанию, границы при скрытых значениях, реальное выражение и статусы рядов.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import translation

from tests.support import synthetic

pytestmark = [pytest.mark.integration, pytest.mark.django_db]


def series_key(code: str) -> str:
    """Ключ первого разреза показателя."""
    from apps.catalog.models import Series

    return (
        Series.objects.filter(indicator__code=code).order_by("key").values_list("key", flat=True)[0]
    )


class TestConstantComposition:
    """Динамика мер по умолчанию — на постоянном составе субъектов."""

    def test_inequality_uses_constant_composition(self, client: Client, warehouse: Any) -> None:
        """Крым и Севастополь входят в ряд в 2014 году — и в постоянный состав не входят."""
        response = client.get(
            reverse("analytics:inequality"), {"series": series_key(synthetic.POPULATION_CODE)}
        )
        context = response.context
        composition = context["composition"]

        assert context["composition_mode"] == "constant"
        assert context["constant_used"] is True
        assert [row["first_year"] for row in composition["entered"]] == [2014, 2014]
        assert {row["count"] for row in context["timeline"]} == {composition["constant"]}
        assert context["period_change"]["count"] == composition["constant"]

    def test_switch_returns_all_constituent_entities(self, client: Client, warehouse: Any) -> None:
        """Переключатель «все субъекты» считает каждый год по всем, у кого есть значение."""
        response = client.get(
            reverse("analytics:inequality"),
            {"series": series_key(synthetic.POPULATION_CODE), "composition": "all"},
        )
        counts = [row["count"] for row in response.context["timeline"]]

        assert response.context["constant_used"] is False
        assert counts[0] < counts[-1]

    def test_window_after_entry_needs_no_exclusions(self, client: Client, warehouse: Any) -> None:
        """На отрезке после входа новых субъектов состав не меняется."""
        response = client.get(
            reverse("analytics:inequality"),
            {"series": series_key(synthetic.POPULATION_CODE), "first": 2015, "last": 2024},
        )
        composition = response.context["composition"]
        assert composition["changes"] is False
        assert response.context["first_year"] == 2015

    def test_sigma_convergence_uses_constant_composition(
        self, client: Client, warehouse: Any
    ) -> None:
        """Разброс в конвергенции считается по одним и тем же субъектам."""
        response = client.get(
            reverse("analytics:convergence"), {"series": series_key(synthetic.WAGE_CODE)}
        )
        context = response.context
        assert context["constant_used"] is True
        assert {point.count for point in context["sigma"]} == {context["composition"]["constant"]}


class TestBreaksInTools:
    """Разрывы сопоставимости в «Неравенстве» и «Конвергенции»."""

    def test_inequality_warns_about_methodology_break(self, client: Client, warehouse: Any) -> None:
        """Смена методики в 2017 году отмечена на графике и вызывает предупреждение."""
        response = client.get(
            reverse("analytics:inequality"), {"series": series_key(warehouse.break_code)}
        )
        context = response.context
        marks = context["timeline_option"]["series"][0]["markLine"]["data"]

        assert [item["year"] for item in context["methodological_breaks"]] == [2017]
        assert context["comparable_from"] == 2017
        assert "2017" in [item["xAxis"] for item in marks]
        assert "Внутри периода менялись правила счёта" in response.content.decode()

    def test_period_after_break_has_no_warning(self, client: Client, warehouse: Any) -> None:
        """Разрыв в первом году отрезка ничего не разделяет."""
        response = client.get(
            reverse("analytics:inequality"),
            {"series": series_key(warehouse.break_code), "first": 2017},
        )
        assert response.context["methodological_breaks"] == []

    def test_convergence_marks_break_on_sigma_chart(self, client: Client, warehouse: Any) -> None:
        """Сигма-график несёт отметку разрыва, бета — предупреждение."""
        response = client.get(
            reverse("analytics:convergence"), {"series": series_key(warehouse.break_code)}
        )
        context = response.context
        marks = context["sigma_option"]["series"][0]["markLine"]["data"]

        assert "2017" in [item["xAxis"] for item in marks]
        assert context["methodological_breaks"]


class TestDefaultYear:
    """Год по умолчанию — последний полный."""

    def test_surface_opens_on_last_full_year(self, client: Client, warehouse: Any) -> None:
        """Последний год заполнен по двадцати субъектам: карта открывается предпоследним."""
        response = client.get(
            reverse("maps:choropleth"), {"series": series_key(synthetic.THIN_LAST_CODE)}
        )
        assert synthetic.LAST_YEAR in response.context["years"]
        assert response.context["year"] == synthetic.LAST_YEAR - 1

    def test_incomplete_year_opens_by_request(self, client: Client, warehouse: Any) -> None:
        """Неполный год остаётся доступен по выбору."""
        response = client.get(
            reverse("maps:choropleth"),
            {"series": series_key(synthetic.THIN_LAST_CODE), "year": synthetic.LAST_YEAR},
        )
        assert response.context["year"] == synthetic.LAST_YEAR

    def test_inequality_opens_on_last_full_year(self, client: Client, warehouse: Any) -> None:
        """Меры неравенства по умолчанию — за полный год."""
        response = client.get(
            reverse("analytics:inequality"), {"series": series_key(synthetic.THIN_LAST_CODE)}
        )
        assert response.context["year"] == synthetic.LAST_YEAR - 1
        assert response.context["last_year"] == synthetic.LAST_YEAR - 1

    def test_nearest_year_skips_incomplete(self, warehouse: Any) -> None:
        """Подстановка ближайшего года в корреляциях берёт полный год."""
        from apps.warehouse.queries import region_matrix

        key = series_key(synthetic.THIN_LAST_CODE)
        matrix = region_matrix([key], synthetic.LAST_YEAR)
        assert matrix[key]["year"] == synthetic.LAST_YEAR - 1


class TestHiddenBounds:
    """Границы мер при скрытых значениях."""

    def hidden_year(self, key: str) -> int:
        from apps.warehouse.queries import hidden_counts

        return max(hidden_counts(key).items(), key=lambda item: item[1])[0]

    def test_bounds_shown_for_year_with_hidden_values(self, client: Client, warehouse: Any) -> None:
        """В году со скрытыми значениями меры сопровождаются границами."""
        key = series_key(synthetic.LABOUR_FORCE_CODE)
        year = self.hidden_year(key)
        response = client.get(reverse("analytics:inequality"), {"series": key, "year": year})
        context = response.context
        bounds = context["bounds"]["gini"]["bounds"]

        assert context["hidden_year"] > 0
        assert bounds.low <= bounds.high
        assert "Значения скрыты по субъектам" in response.content.decode()

    def test_no_bounds_with_population_weights(self, client: Client, warehouse: Any) -> None:
        """Границы считаются для равнозначных территорий."""
        key = series_key(synthetic.LABOUR_FORCE_CODE)
        response = client.get(
            reverse("analytics:inequality"),
            {"series": key, "year": self.hidden_year(key), "weighting": "population"},
        )
        assert response.context["bounds"] == {}


class TestRealTerms:
    """Денежные ряды — в реальном выражении."""

    def test_passport_names_real_wage_change(self, warehouse: Any) -> None:
        """Изменение зарплаты за пять лет — реальное, по индексу реальной зарплаты."""
        from apps.catalog.passport import build_passport
        from apps.warehouse.queries import series_values

        with translation.override("ru"):
            passport = build_passport("RU-MOW")
        wage = passport.by_key()[series_key(synthetic.WAGE_CODE)]
        index = series_values([series_key(synthetic.REAL_WAGE_CODE)], ["RU-MOW"])
        chain = 1.0
        for year in range(wage.past_year + 1, wage.year + 1):
            chain *= index[series_key(synthetic.REAL_WAGE_CODE)]["RU-MOW"][year] / 100

        assert wage.real_ratio == pytest.approx(chain)
        assert wage.trend.startswith("реальный рост")
        assert "в рублях" in wage.trend

    def test_passport_counts_baskets(self, warehouse: Any) -> None:
        """Паспорт называет, сколько фиксированных наборов на доход и зарплату."""
        from apps.catalog.passport import BASKET_INCOME, BASKET_WAGE, build_passport

        with translation.override("ru"):
            passport = build_passport("RU-MOW")
        assert "фиксированного набора" in passport.baskets
        assert set(passport.basket_facts()) == {BASKET_INCOME, BASKET_WAGE}

    def test_country_tile_shows_real_and_nominal(self, warehouse: Any) -> None:
        """Плитка «Россия» у зарплаты — реально, а в рублях — рядом."""
        from apps.catalog.indicator import country_tile
        from apps.warehouse.queries import featured_set

        item = featured_set().by_key()[series_key(synthetic.WAGE_CODE)]
        tile = country_tile(item, [])
        assert tile is not None
        assert tile.change
        assert tile.nominal

    def test_surface_says_prices_of_the_year(self, client: Client, warehouse: Any) -> None:
        """Денежный ряд на карте подписан «в ценах N года»."""
        response = client.get(
            reverse("maps:choropleth"), {"series": series_key(synthetic.WAGE_CODE), "year": 2020}
        )
        assert "в ценах 2020 года" in response.content.decode()


class TestSeriesStatus:
    """Состояние публикации рядов на страницах и в документах."""

    def test_series_page_says_publication_stopped(self, client: Client, warehouse: Any) -> None:
        """Страница численности населения говорит, что публикация прекращена."""
        from apps.catalog.models import Indicator

        indicator = Indicator.objects.get(code=synthetic.POPULATION_CODE)
        response = client.get(reverse("catalog:series-detail", kwargs={"slug": indicator.slug}))
        assert "Публикация прекращена в 2025 году" in response.content.decode()

    def test_series_page_says_publication_continues(self, client: Client, warehouse: Any) -> None:
        """Страница зарплаты называет, где публикация продолжается."""
        from apps.catalog.models import Indicator

        indicator = Indicator.objects.get(code=synthetic.WAGE_CODE)
        response = client.get(reverse("catalog:series-detail", kwargs={"slug": indicator.slug}))
        assert "Публикация продолжается" in response.content.decode()

    def test_documents_carry_the_status(self, warehouse: Any) -> None:
        """Отчёт по ряду несёт реквизит «Публикация», паспорт — перечень прекращённых."""
        from apps.exports.reports.builders import build_series_report, build_territory_report

        series = build_series_report(
            {"series": series_key(synthetic.POPULATION_CODE)}, max_rows=100
        )
        assert dict(series.meta)["Публикация"].startswith("Публикация прекращена в 2025 году")

        territory = build_territory_report({"territory": "RU-MOW"}, max_rows=100)
        paragraphs = [text for section in territory.sections for text in section.paragraphs]
        assert any(
            text.startswith("Росстат прекратил публикацию показателей") for text in paragraphs
        )
