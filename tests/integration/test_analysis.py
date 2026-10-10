"""Проверки страниц анализа на собранном складе: каждая показывает результат расчёта."""

from __future__ import annotations

from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from tests.support import synthetic

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

# Инструменты раздела «Анализ».
ANALYTICS_TOOLS = (
    "analytics:inequality",
    "analytics:convergence",
    "analytics:correlation",
    "analytics:spatial",
    "analytics:index-builder",
    "analytics:revisions",
)


@pytest.fixture
def series_key(warehouse: Any) -> str:
    """Ключ ряда численности населения: строго положительные значения — определены все меры."""
    from apps.catalog.models import Series

    return Series.objects.get(indicator__code=synthetic.POPULATION_CODE).key


@pytest.fixture
def series_keys(warehouse: Any) -> list[str]:
    """Ключи нескольких пригодных рядов: нужны матрице связей и сводному индексу."""
    from apps.catalog.models import Series

    return list(
        Series.objects.filter(is_analysis_ready=True).order_by("key").values_list("key", flat=True)
    )


class TestChoropleth:
    """Картограмма."""

    def test_map_is_filled_with_values(self, client: Client, series_key: str) -> None:
        """Картограмма построена: у субъектов есть значения и шкала классов."""
        response = client.get(reverse("maps:choropleth"), {"series": series_key, "year": 2018})

        assert response.status_code == 200
        assert response.context["warehouse_ready"] is True
        assert len(response.context["rows"]) > 80
        assert response.context["intervals"]

    @pytest.mark.parametrize("method", ["quantile", "equal", "jenks", "stddev"])
    def test_every_classification_method_works(
        self, client: Client, series_key: str, method: str
    ) -> None:
        """
        Все четыре способа разбиения на классы дают заполненную шкалу.

        Способ разбиения определяет, что именно увидит читатель карты: одни и те же
        данные при равных интервалах и при квантилях выглядят по-разному.
        """
        response = client.get(
            reverse("maps:choropleth"),
            {"series": series_key, "year": 2018, "method": method, "classes": 5},
        )
        assert response.status_code == 200
        assert len(response.context["intervals"]) == 5

    def test_tile_mode_covers_all_regions(self, client: Client, series_key: str) -> None:
        """Плиточная карта строится по тем же данным, что и географическая."""
        response = client.get(
            reverse("maps:choropleth"),
            {"series": series_key, "year": 2018, "mode": "tiles"},
        )
        assert response.status_code == 200
        assert response.context["mode"] == "tiles"

    def test_map_is_drawn_by_the_server(self, client: Client, series_key: str) -> None:
        """
        Географическая карта приходит готовым чертежом, а не собирается в браузере.

        Ради этого затевалась переработка карты: разметка должна работать без
        сценариев, а страница — обходиться без библиотеки графиков.
        """
        response = client.get(
            reverse("maps:choropleth"),
            {"series": series_key, "year": 2018, "mode": "geo"},
        )
        markup = response.content.decode()

        assert response.status_code == 200
        assert response.context["mode"] == "geo"
        assert response.context["geo_map"] is not None
        assert 'class="geo-map"' in markup
        # Библиотека графиков не подключается тегом: адрес для догрузки есть.
        assert "<script" in markup
        assert 'script src="/static/vendor/echarts' not in markup
        assert 'echarts.min.js" defer' not in markup

    def test_map_carries_every_region(self, client: Client, series_key: str) -> None:
        """На чертеже есть контур каждого субъекта, и описан он один раз."""
        response = client.get(
            reverse("maps:choropleth"),
            {"series": series_key, "year": 2018, "mode": "geo"},
        )
        drawing = response.context["geo_map"]
        assert len(drawing["definitions"]) == len(drawing["regions"]) == 85

    def test_selected_region_is_labelled(self, client: Client, series_key: str) -> None:
        """Выделение субъекта ссылкой подписывает его прямо на карте."""
        from apps.catalog.models import Territory

        code = Territory.objects.comparable().values_list("code", flat=True).first()
        response = client.get(
            reverse("maps:choropleth"),
            {"series": series_key, "year": 2018, "mode": "geo", "territory": code},
        )
        assert response.context["selected_codes"] == [code]

    def test_unknown_selected_region_is_ignored(self, client: Client, series_key: str) -> None:
        """Устаревшая ссылка с несуществующим кодом открывает работающую карту."""
        response = client.get(
            reverse("maps:choropleth"),
            {"series": series_key, "year": 2018, "mode": "geo", "territory": "RU-НЕТ"},
        )
        assert response.status_code == 200
        assert response.context["selected_codes"] == []

    def test_comparison_year_shows_change(self, client: Client, series_key: str) -> None:
        """Режим сравнения с другим годом добавляет изменение значения."""
        response = client.get(
            reverse("maps:choropleth"),
            {"series": series_key, "year": 2018, "compare": 2010},
        )
        assert response.status_code == 200
        assert response.context["compare_year"] == 2010
        assert any(row.get("change") is not None for row in response.context["rows"])

    def test_later_comparison_year_reads_as_period(self, client: Client, series_key: str) -> None:
        """
        Год сравнения позже года карты даёт то же изменение, что и в прямом порядке.

        Иначе ``year=2010&compare=2018`` читалось бы «С 2018 по 2010 год», а рост красился
        бы как падение.
        """
        backward = client.get(
            reverse("maps:choropleth"),
            {"series": series_key, "year": 2010, "compare": 2018},
        )
        forward = client.get(
            reverse("maps:choropleth"),
            {"series": series_key, "year": 2018, "compare": 2010},
        )
        for response in (backward, forward):
            assert (response.context["period_start"], response.context["period_end"]) == (
                2010,
                2018,
            )
        change_back = {row["code"]: row["change"] for row in backward.context["rows"]}
        change_fwd = {row["code"]: row["change"] for row in forward.context["rows"]}
        common = [
            code
            for code, value in change_fwd.items()
            if value is not None and change_back.get(code) is not None
        ]
        assert common
        assert all(change_back[code] == pytest.approx(change_fwd[code]) for code in common)
        assert "С 2010 по 2018 год" in backward.content.decode()


class TestCompareBasis:
    """Основание построения графика динамики в сравнении территорий."""

    def test_values_are_shown_as_they_are_by_default(self, client: Client, series_key: str) -> None:
        """По умолчанию график показывает значения, а не их отношение к году."""
        response = client.get(reverse("compare:index"), {"series": series_key})
        assert response.context["basis"] == "value"
        assert response.context["timeline_base_year"] is None

    def test_rebasing_starts_every_line_from_a_hundred(
        self, client: Client, series_key: str
    ) -> None:
        """
        Приведение к базовому году ставит все линии в одну точку.

        Уровни субъектов различаются кратно, и на общей оси движение видно только
        у верхней линии; приведение сравнивает не уровень, а рост.
        """
        response = client.get(reverse("compare:index"), {"series": series_key, "basis": "index"})
        option = response.context["timeline_option"]
        base = response.context["timeline_base_year"]
        assert base in response.context["timeline_years"]

        position = response.context["timeline_years"].index(base)
        for entry in option["series"]:
            assert entry["data"][position] == 100.0

    def test_unknown_basis_falls_back_to_values(self, client: Client, series_key: str) -> None:
        """Искажённый адрес открывает работающую страницу."""
        response = client.get(
            reverse("compare:index"), {"series": series_key, "basis": "непонятно"}
        )
        assert response.status_code == 200
        assert response.context["basis"] == "value"


class TestTerritoryTimeline:
    """График динамики в паспорте субъекта."""

    def test_corridor_stands_behind_the_region(self, client: Client, series_key: str) -> None:
        """
        За линией субъекта лежит коридор между первым и третьим квартилем.

        Он отвечает на вопрос, которого не решают ни округ, ни страна: обычен ли
        уровень региона или он выделяется.
        """
        from apps.catalog.models import Territory

        territory = Territory.objects.comparable().first()
        response = client.get(
            reverse("catalog:territory-detail", kwargs={"slug": territory.slug}),
            {"series": series_key},
        )
        assert response.status_code == 200
        option = response.context["timeline_option"]
        assert response.context["timeline_corridor"] is True
        assert option["series"][0]["stack"] == "corridor"

    def test_region_line_is_the_highlighted_one(self, client: Client, series_key: str) -> None:
        """Линия самого субъекта выделена, округ и страна приглушены."""
        from apps.catalog.models import Territory

        territory = Territory.objects.comparable().first()
        response = client.get(
            reverse("catalog:territory-detail", kwargs={"slug": territory.slug}),
            {"series": series_key},
        )
        lines = [
            entry
            for entry in response.context["timeline_option"]["series"]
            if entry.get("triggerLineEvent")
        ]
        assert lines[0]["name"] == territory.name
        assert lines[0]["z"] > 0
        assert all(entry["lineStyle"]["opacity"] < 1 for entry in lines[1:])


class TestRankings:
    """Рейтинги субъектов."""

    def test_ranking_is_ordered_by_value(self, client: Client, series_key: str) -> None:
        """Рейтинг упорядочен по значению, позиции идут подряд."""
        response = client.get(reverse("rankings:index"), {"series": series_key, "year": 2018})

        rows = response.context["rows"]
        assert len(rows) > 80
        values = [row["value"] for row in rows]
        assert values == sorted(values, reverse=True)

    def test_ascending_order_is_supported(self, client: Client, series_key: str) -> None:
        """
        Порядок можно перевернуть.

        Для показателей, где меньше — лучше, естественный порядок обратный,
        и рейтинг обязан это допускать.
        """
        response = client.get(
            reverse("rankings:index"),
            {"series": series_key, "year": 2018, "order": "asc"},
        )
        values = [row["value"] for row in response.context["rows"]]
        assert values == sorted(values)

    def test_district_filter_narrows_ranking(self, client: Client, series_key: str) -> None:
        """Отбор по федеральному округу оставляет только его субъекты."""
        response = client.get(
            reverse("rankings:index"),
            {"series": series_key, "year": 2018, "district": "FD-CFO"},
        )
        rows = response.context["rows"]
        assert 0 < len(rows) < 30

    def test_movement_against_base_year_is_computed(self, client: Client, series_key: str) -> None:
        """Движение позиций относительно базового года рассчитано."""
        response = client.get(
            reverse("rankings:index"),
            {"series": series_key, "year": 2018, "base": 2010},
        )
        assert response.context["previous_year"] == 2010
        assert response.context["movers"]

    def test_later_base_year_reads_as_period(self, client: Client, series_key: str) -> None:
        """Год сравнения позже года рейтинга — тот же период от раннего года к позднему."""
        backward = client.get(
            reverse("rankings:index"),
            {"series": series_key, "year": 2010, "base": 2018},
        )
        forward = client.get(
            reverse("rankings:index"),
            {"series": series_key, "year": 2018, "base": 2010},
        )
        for response in (backward, forward):
            assert (response.context["period_start"], response.context["period_end"]) == (
                2010,
                2018,
            )
        assert backward.context["movers"] == forward.context["movers"]

        moved_back = {row["territory_code"]: row["movement"] for row in backward.context["rows"]}
        moved_fwd = {row["territory_code"]: row["movement"] for row in forward.context["rows"]}
        common = [code for code, value in moved_fwd.items() if value and moved_back.get(code)]
        assert common
        assert all(moved_back[code] == moved_fwd[code] for code in common)

    def test_fallen_regions_are_labelled_without_minus(
        self, client: Client, series_key: str
    ) -> None:
        """В перечне опустившихся знак передаёт стрелка, а число — модуль движения."""
        response = client.get(
            reverse("rankings:index"),
            {"series": series_key, "year": 2018, "base": 2010},
        )
        fallen = response.context["movers"]["fallen"]
        assert fallen
        assert all(item["movement_abs"] == -item["movement"] > 0 for item in fallen)


class TestCompare:
    """Сопоставление территорий."""

    def test_several_territories_are_compared(self, client: Client, series_key: str) -> None:
        """Выбранные территории попадают в сравнение вместе с рядом динамики."""
        response = client.get(
            reverse("compare:index"),
            {"territory": ["RU-MOW", "RU-SPE", "RU-BEL"], "series": series_key},
        )

        assert response.status_code == 200
        assert [item.code for item in response.context["territories"]] == [
            "RU-MOW",
            "RU-SPE",
            "RU-BEL",
        ]
        assert response.context["timeline_option"] is not None

    def test_profile_is_built_for_key_indicators(self, client: Client, series_key: str) -> None:
        """Профиль территорий заполнен значениями ключевых показателей."""
        response = client.get(reverse("compare:index"), {"territory": ["RU-MOW", "RU-BEL"]})
        assert response.status_code == 200


class TestCatalogPagesWithData:
    """Карточка показателя и паспорт региона."""

    def test_series_card_shows_observations(self, client: Client, warehouse: Any) -> None:
        """Карточка показателя показывает ряд наблюдений и его характеристики."""
        from apps.catalog.models import Series

        series = Series.objects.filter(is_analysis_ready=True).order_by("key").first()
        response = client.get(
            reverse("catalog:series-detail", kwargs={"slug": series.indicator.slug}),
            {"series": series.key},
        )

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "empty-state" not in content

    def test_series_card_marks_comparability_breaks(self, client: Client, warehouse: Any) -> None:
        """
        Разрыв сопоставимости отмечен на карточке показателя.

        Ради этой отметки и разбираются методические примечания источника:
        без неё читатель сравнит несопоставимые части ряда.
        """
        from apps.catalog.models import Indicator

        indicator = Indicator.objects.get(code=warehouse.break_code)
        response = client.get(reverse("catalog:series-detail", kwargs={"slug": indicator.slug}))
        assert response.status_code == 200
        assert response.context["breaks"]

    def test_territory_passport_shows_values(self, client: Client, warehouse: Any) -> None:
        """Паспорт региона заполнен значениями показателей и позициями в рейтингах."""
        response = client.get(reverse("catalog:territory-detail", kwargs={"slug": "moskva"}))
        assert response.status_code == 200
        assert response.context["coverage"]["series_with_values"] > 0


class TestAnalyticsTools:
    """Девять инструментов раздела «Анализ» на реальных данных."""

    def test_inequality_measures_are_computed(self, client: Client, series_key: str) -> None:
        """Меры неравенства рассчитаны и разложены по федеральным округам."""
        response = client.get(
            reverse("analytics:inequality"),
            {"series": series_key, "year": 2018, "weighting": "none"},
        )

        snapshot = response.context["snapshot"]
        assert snapshot["available"] is True
        assert 0 <= snapshot["by_code"]["gini"].value <= 1
        assert snapshot["by_code"]["theil"].value >= 0
        assert response.context["decomposition"].groups

    def test_atkinson_parameter_is_respected(self, client: Client, series_key: str) -> None:
        """
        Параметр неприятия неравенства меняет индекс Аткинсона.

        Чем выше параметр, тем сильнее учитывается отставание нижней части
        распределения, поэтому индекс обязан вырасти.
        """
        low = (
            client.get(
                reverse("analytics:inequality"),
                {"series": series_key, "year": 2018, "epsilon": "0.5"},
            )
            .context["snapshot"]["by_code"]["atkinson"]
            .value
        )
        high = (
            client.get(
                reverse("analytics:inequality"),
                {"series": series_key, "year": 2018, "epsilon": "1.5"},
            )
            .context["snapshot"]["by_code"]["atkinson"]
            .value
        )

        assert high > low

    @pytest.mark.parametrize("mode", ["absolute", "conditional"])
    def test_convergence_is_estimated(self, client: Client, series_key: str, mode: str) -> None:
        """Сигма- и бета-сходимость оценены за выбранный период."""
        response = client.get(
            reverse("analytics:convergence"),
            {"series": series_key, "first": 2005, "last": 2020, "mode": mode},
        )

        assert response.status_code == 200
        assert response.context["sigma"]
        assert response.context["beta"] is not None

    def test_inequality_robustness_to_composition(self, client: Client, series_key: str) -> None:
        """Меры при всех субъектах, без Москвы с областью и без Северного Кавказа."""
        response = client.get(
            reverse("analytics:inequality"),
            {"series": series_key, "year": 2018, "first": 2005, "last": 2020},
        )
        rows = response.context["robustness"]["rows"]
        assert [row["variant"] for row in rows] == ["all", "no-moscow", "no-skfo"]
        everyone, no_moscow, no_skfo = rows
        assert everyone["count"] == no_moscow["count"] + 2
        assert no_skfo["count"] < everyone["count"]
        # Строка «все субъекты» — те же меры, что в итоге страницы.
        assert everyone["gini"] == response.context["snapshot"]["by_code"]["gini"].value
        assert isinstance(response.context["robustness"]["stable"], bool)
        assert "Устойчивость вывода" in response.text

    def test_convergence_robustness_to_composition(self, client: Client, series_key: str) -> None:
        """Выводы о сигма- и бета-сходимости при разном составе субъектов."""
        response = client.get(
            reverse("analytics:convergence"),
            {"series": series_key, "first": 2005, "last": 2020},
        )
        robustness = response.context["robustness"]
        rows = robustness["rows"]
        assert [row["variant"] for row in rows] == ["all", "no-moscow", "no-skfo"]
        assert rows[0]["beta_value"] == response.context["beta"].beta
        verdicts = {"converging", "diverging", "none", "unavailable"}
        assert all(row["sigma"] in verdicts and row["beta"] in verdicts for row in rows)
        assert rows[1]["count"] < rows[0]["count"]
        assert isinstance(robustness["stable"], bool)
        assert "Устойчивость вывода" in response.text

    @pytest.mark.parametrize("method", ["spearman", "pearson", "kendall"])
    def test_correlation_matrix_is_built(
        self, client: Client, series_keys: list[str], method: str
    ) -> None:
        """Матрица связей построена всеми тремя способами."""
        response = client.get(
            reverse("analytics:correlation"),
            {"series": series_keys[:4], "year": 2018, "method": method},
        )

        matrix = response.context["matrix"]
        assert matrix is not None
        assert response.context["strongest"]

    def test_spatial_autocorrelation_is_measured(self, client: Client, series_key: str) -> None:
        """
        Индекс Морана рассчитан, локальные кластеры определены.

        Синтетический набор строится с зависимостью уровня от федерального округа,
        поэтому глобальная пространственная автокорреляция должна быть положительной.
        """
        response = client.get(
            reverse("analytics:spatial"),
            {"series": series_key, "year": 2018, "scheme": "all"},
        )

        moran = response.context["global_moran"]
        assert moran.value > 0
        assert moran.p_value < 0.05
        assert response.context["quadrants"]

    def test_series_picker_is_folded_on_the_first_visit(
        self, client: Client, series_keys: list[str]
    ) -> None:
        """Перечень показателей приходит свёрнутым: развёрнутый — около 400 КБ разметки."""
        response = client.get(reverse("analytics:correlation"), {"series": series_keys})
        content = response.content.decode("utf-8")

        assert response.context["picker_open"] is False
        assert "series-picker-frame" in content
        # В свёрнутом виде в разметке только отмеченные ряды, и их меньше,
        # чем предлагается к выбору.
        chosen = len(response.context["selected_keys"])
        assert content.count('name="series"') == chosen
        assert chosen < response.context["series_total"]

    def test_series_picker_unfolds_by_a_link(self, client: Client, series_keys: list[str]) -> None:
        """Развёрнутый перечень приходит отдельным фрагментом и хранит отметки."""
        response = client.get(
            reverse("catalog:series-picker"), {"picker": "1", "series": series_keys}
        )
        content = response.content.decode("utf-8")

        assert response.context["picker_open"] is True
        assert content.count('name="series"') == response.context["series_total"]
        assert content.count("checked") == len(series_keys)
        # Фрагмент — это сама обойма: она заменяется целиком.
        assert content.lstrip().startswith('<div id="series-picker-frame"')

    def test_series_picker_repeats_no_key(self, client: Client, series_keys: list[str]) -> None:
        """Ряд, названный дважды, отмечается один раз."""
        doubled = list(series_keys) + list(series_keys)
        response = client.get(reverse("catalog:series-picker"), {"series": doubled})

        assert response.context["selected_keys"] == list(series_keys)

    @pytest.mark.parametrize("weighting", ["equal", "entropy", "critic", "pca"])
    def test_index_builder_produces_ranking(
        self, client: Client, series_keys: list[str], weighting: str
    ) -> None:
        """Сводный индекс рассчитан, веса определены выбранным способом."""
        response = client.get(
            reverse("analytics:index-builder"),
            {"series": series_keys[:3], "year": 2018, "weighting": weighting},
        )

        scores = response.context["scores"]
        assert len(scores) > 80
        assert response.context["weights"]

    @pytest.mark.parametrize("aggregation", ["additive", "geometric", "topsis", "distance"])
    def test_index_aggregation_methods(
        self, client: Client, series_keys: list[str], aggregation: str
    ) -> None:
        """Все способы свёртки дают упорядоченный результат."""
        response = client.get(
            reverse("analytics:index-builder"),
            {"series": series_keys[:3], "year": 2018, "aggregation": aggregation},
        )
        scores = response.context["scores"]
        values = [score.value for score in scores]
        assert values == sorted(values, reverse=True)

    def test_index_comparison_shows_two_rankings(
        self, client: Client, series_keys: list[str]
    ) -> None:
        """Выбранное сочетание устойчивости показывается рядом с основным расчётом."""
        parameters = {"series": series_keys[:3], "year": 2018}
        plain = client.get(reverse("analytics:index-builder"), parameters)
        variant = plain.context["sensitivity"][0]["code"]

        response = client.get(
            reverse("analytics:index-builder"), {**parameters, "compare": variant}
        )

        assert response.context["compare"] == variant
        rows = response.context["ranking"]
        assert rows
        # Место во втором расчёте известно для каждого субъекта, оставшегося
        # в обоих рейтингах, а смещение согласовано с местами.
        compared = [row for row in rows if row["other_position"] is not None]
        assert compared
        for row in compared:
            assert row["shift"] == row["position"] - row["other_position"]

    def test_index_direction_is_set_by_the_user(self, client: Client, warehouse: Any) -> None:
        """
        Направленность показателя в индексе меняет пользователь.

        Предложенная — только подсказка: у основного набора она задана вручную,
        у остальных рядов угадана по формулировке и может быть неверной.
        """
        from apps.catalog.models import Series

        wage, unemployment = (
            Series.objects.get(indicator__code=code).key
            for code in (synthetic.WAGE_CODE, synthetic.UNEMPLOYMENT_CODE)
        )
        parameters = {"series": [wage, unemployment], "year": 2018}

        plain = client.get(reverse("analytics:index-builder"), parameters)
        flipped = client.get(
            reverse("analytics:index-builder"),
            {**parameters, "direction": f"{unemployment}:positive"},
        )

        before = {column["key"]: column for column in plain.context["columns"]}
        after = {column["key"]: column for column in flipped.context["columns"]}
        assert before[unemployment]["polarity"] == "negative"
        assert before[unemployment]["direction"]["origin"] == "featured"
        assert after[unemployment]["polarity"] == "positive"
        assert after[unemployment]["direction"]["origin"] == "user"
        assert [score.code for score in plain.context["scores"]] != [
            score.code for score in flipped.context["scores"]
        ]

    def test_series_without_direction_waits_for_it(self, client: Client, warehouse: Any) -> None:
        """Ряд без направленности остаётся в выборе, но в расчёт входит, только когда её зададут."""
        from apps.catalog.models import Series

        population, wage, unemployment = (
            Series.objects.get(indicator__code=code).key
            for code in (
                synthetic.POPULATION_CODE,
                synthetic.WAGE_CODE,
                synthetic.UNEMPLOYMENT_CODE,
            )
        )
        parameters = {"series": [population, wage, unemployment], "year": 2018}

        waiting = client.get(reverse("analytics:index-builder"), parameters)
        assert [item["key"] for item in waiting.context["undirected"]] == [population]
        assert population in waiting.context["selected_keys"]
        assert population not in [column["key"] for column in waiting.context["columns"]]

        set_ = client.get(
            reverse("analytics:index-builder"),
            {**parameters, "direction": f"{population}:positive"},
        )
        assert not set_.context["undirected"]
        assert population in [column["key"] for column in set_.context["columns"]]

    def test_expert_weights_follow_the_series(self, client: Client, warehouse: Any) -> None:
        """
        Экспертный вес привязан к ряду, а поля весов приходят вместе с расчётом.

        Рейл после расчёта не перерисовывается: поля в нём не появились бы до перезагрузки.
        """
        from apps.catalog.models import Series

        wage, unemployment = (
            Series.objects.get(indicator__code=code).key
            for code in (synthetic.WAGE_CODE, synthetic.UNEMPLOYMENT_CODE)
        )
        parameters = {"series": [wage, unemployment], "year": 2018, "weighting": "expert"}

        keyed = client.get(
            reverse("analytics:index-builder"),
            {**parameters, f"weight:{unemployment}": "3"},
        )

        assert keyed.context["expert_weights"] == [1.0, 3.0]
        assert f'name="weight:{unemployment}"' in keyed.content.decode("utf-8")

    def test_unknown_comparison_is_ignored(self, client: Client, series_keys: list[str]) -> None:
        """
        Незнакомое сочетание не показывается и не ломает страницу.

        Значение приходит из адреса, а адрес правят руками и переносят из чужих работ.
        """
        response = client.get(
            reverse("analytics:index-builder"),
            {"series": series_keys[:3], "year": 2018, "compare": "нет-такого"},
        )

        assert response.status_code == 200
        assert response.context["comparison"] is None
        assert response.context["compare"] == ""

    def test_revisions_are_listed(self, client: Client, warehouse: Any) -> None:
        """Витрина пересмотров показывает расхождения между выпусками."""
        response = client.get(reverse("analytics:revisions"))

        assert response.context["summary"]["observations"] > 0
        assert response.context["series_rows"]

    def test_revision_triangle_covers_the_whole_series(
        self, client: Client, warehouse: Any
    ) -> None:
        """Рядом со следом наблюдения приходит треугольник пересмотров."""
        from apps.catalog.models import Series

        series = Series.objects.filter(indicator__code=warehouse.revised_code).first()
        response = client.get(
            reverse("analytics:revision-trace"),
            {"series": series.key, "territory": "RU-BEL", "year": 2015},
        )

        triangle = response.context["triangle"]
        assert triangle["available"]
        assert triangle["editions"] == sorted(triangle["editions"])
        # В каждой строке столько ячеек, сколько выпусков: иначе таблица
        # разъезжается и значение попадает не под свой выпуск.
        assert all(len(row["cells"]) == len(triangle["editions"]) for row in triangle["rows"])

    def test_revision_trace_shows_versions(self, client: Client, warehouse: Any) -> None:
        """След наблюдения раскрывает все его версии по выпускам."""
        from apps.catalog.models import Series

        series = Series.objects.filter(indicator__code=warehouse.revised_code).first()
        response = client.get(
            reverse("analytics:revision-trace"),
            {"series": series.key, "territory": "RU-BEL", "year": 2015},
        )
        assert len(response.context["versions"]) == 2

    @pytest.mark.parametrize("route", ANALYTICS_TOOLS)
    def test_tool_returns_fragment_for_htmx(
        self, client: Client, series_key: str, route: str
    ) -> None:
        """
        Смена параметров обновляет только рабочую область.

        Инструменты пересчитываются часто, и повторная отправка всей страницы
        на каждое движение ползунка была бы заметна пользователю.
        """
        response = client.get(
            reverse(route),
            {"series": series_key},
            headers={"HX-Request": "true"},
        )
        content = response.content.decode("utf-8")
        assert response.status_code == 200
        assert "<!DOCTYPE html>" not in content
