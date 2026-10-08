"""
Проверки описаний представлений: перенос параметров между ними, ссылка на вид,
полоса точек распределения и строки вывода под графиками.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from django.test import RequestFactory

from apps.core.charts import STRIP_NO_CLASS, strip_option
from apps.exports.constants import REPORT_KINDS_BY_CODE, ReportKind
from apps.surface.findings import (
    distribution_finding,
    map_finding,
    ranking_finding,
    relation_finding,
    timeline_finding,
)
from apps.surface.panels import (
    PANEL_COMPARE,
    PANEL_DISTRIBUTION,
    PANEL_MAP,
    PANEL_RANKINGS,
    PANEL_TABLE,
    PANELS,
    PANELS_BY_CODE,
    build_tabs,
    panel_url,
    view_query,
)
from apps.surface.state import SurfaceState, build_query

pytestmark = pytest.mark.unit


def state(year: int | None = 2023) -> SurfaceState:
    """Собрать состояние без обращения к базе данных."""
    return SurfaceState(series=None, years=[2020, 2023], year=year, territories=[])


class TestQuery:
    """Сборка строки запроса."""

    def test_empty_values_are_dropped(self) -> None:
        """
        Пустое значение в адрес не попадает.

        «Не сравнивать» и «все округа» — это отсутствие отбора, а не его значение,
        и в адресе они выглядели бы состоянием, которого на экране нет.
        """
        assert build_query([("series", "X"), ("district", ""), ("year", "2023")]) == (
            "series=X&year=2023"
        )

    def test_repeated_names_are_kept(self) -> None:
        """Территорий может быть несколько, и каждая остаётся в адресе."""
        query = build_query([("territory", "RU-MOW"), ("territory", "RU-SPE")])
        assert query == "territory=RU-MOW&territory=RU-SPE"

    def test_empty_selection_gives_empty_query(self) -> None:
        """При пустом состоянии адрес остаётся без строки запроса."""
        assert build_query([]) == ""


class TestPanelUrls:
    """Перенос состояния между представлениями."""

    def test_shared_state_is_carried_everywhere(self) -> None:
        """Год переносится на любое представление."""
        request = RequestFactory().get("/")
        for panel in PANELS:
            assert "year=2023" in panel_url(panel, state(), request)

    def test_build_parameters_are_carried_only_where_understood(self) -> None:
        """
        Параметр построения переносится только в то представление, которое его понимает.

        Способ разбиения шкалы общий у карты и распределения. Рейтингу он неизвестен,
        и в его адресе означал бы состояние, которого на экране нет.
        """
        request = RequestFactory().get("/", {"method": "equal", "classes": "7"})
        source = PANELS_BY_CODE[PANEL_MAP]

        kindred = panel_url(PANELS_BY_CODE[PANEL_DISTRIBUTION], state(), request, source=source)
        stranger = panel_url(PANELS_BY_CODE[PANEL_RANKINGS], state(), request, source=source)

        assert "method=equal" in kindred
        assert "classes=7" in kindred
        assert "method" not in stranger
        assert "classes" not in stranger

    def test_reset_link_drops_only_territories(self) -> None:
        """Ссылка снятия выбора отличается от текущего адреса только территориями."""
        request = RequestFactory().get("/", {"classes": "6"})
        panel = PANELS_BY_CODE[PANEL_MAP]
        address = panel_url(panel, state(), request, with_territories=False)
        assert "territory" not in address
        assert "year=2023" in address
        assert "classes=6" in address

    def test_tabs_cover_every_panel_and_mark_the_open_one(self) -> None:
        """Переключатель перечисляет все представления и отмечает открытое."""
        request = RequestFactory().get("/")
        current = PANELS_BY_CODE[PANEL_RANKINGS]
        tabs = build_tabs(current, state(), request)

        assert [tab["code"] for tab in tabs] == [panel.code for panel in PANELS]
        assert [tab["code"] for tab in tabs if tab["active"]] == [PANEL_RANKINGS]


class TestStripOption:
    """Полоса точек распределения: точка — регион."""

    @staticmethod
    def option() -> dict[str, Any]:
        """Собрать полосу из двух регионов с медианой и Россией."""
        return strip_option(
            [
                {
                    "name": "Москва",
                    "label": "МСК",
                    "value": 180.0,
                    "colour": "var(--scale-seq-5)",
                    "selected": True,
                    "tooltip": "<strong>Москва</strong><br>180",
                },
                {"name": "Тула", "label": "ТУЛ", "value": 60.0, "colour": "", "tooltip": "Тула"},
            ],
            marks=[
                {"value": 75.0, "label": "медиана"},
                {"value": 100.0, "label": "Россия", "strong": True},
            ],
            unit="руб.",
        )

    def test_layout_is_left_to_the_browser(self) -> None:
        """
        Сдвиг точек считает браузер: он знает ширину холста.

        Разложенная на сервере полоса на телефоне слипалась бы — точка там шире шага.
        """
        option = self.option()
        assert option["swarm"] == {"series": 0}
        assert option["yAxis"]["show"] is False
        assert all(item["value"][1] == 0 for item in option["series"][0]["data"])

    def test_selected_region_is_larger_and_labelled(self) -> None:
        """Отмеченный регион крупнее соседей и подписан сокращением."""
        chosen, other = self.option()["series"][0]["data"]
        assert chosen["symbolSize"] > self.option()["series"][0]["symbolSize"]
        assert chosen["label"]["formatter"] == "МСК"
        assert "label" not in other

    def test_point_without_class_is_muted(self) -> None:
        """Точка без класса шкалы — нейтральным цветом, а не первым цветом палитры."""
        assert self.option()["series"][0]["data"][1]["itemStyle"]["color"] == STRIP_NO_CLASS

    def test_country_mark_is_stronger(self) -> None:
        """Россия отмечена сплошной линией, медиана — пунктиром."""
        marks = self.option()["series"][0]["markLine"]["data"]
        assert marks[0]["lineStyle"]["type"] == "dashed"
        assert marks[1]["lineStyle"]["type"] == "solid"


def item(**fields: Any) -> SimpleNamespace:
    """Описание ряда для выводов: доля, точность, единица, складываемость."""
    defaults = {"is_percentage": False, "precision": 0, "unit_label": "руб.", "absolute": False}
    return SimpleNamespace(**{**defaults, **fields})


def region(name: str, value: float | None, rank: int | None) -> dict[str, Any]:
    """Строка карты для выводов."""
    return {"name": name, "value": value, "mapped": value, "rank_desc": rank}


class TestFindings:
    """Строка вывода под графиком — по правилам, с числами из данных."""

    def test_map_names_the_extremes_and_the_gap(self) -> None:
        """Карта: где больше и меньше всего и во сколько раз."""
        rows = [region("Москва", 300.0, 1), region("Тула", 150.0, 2), region("Тыва", 100.0, 3)]
        text = map_finding(rows, item(), None)
        assert "Москва" in text and "Тыва" in text
        assert "в 3 раза" in text

    def test_percentages_differ_in_points(self) -> None:
        """У процентов разница — в пунктах: кратность долей вводила бы в заблуждение."""
        rows = [region("Тыва", 30.0, 1), region("Москва", 5.5, 2)]
        assert "24,5 п. п." in map_finding(rows, item(is_percentage=True, precision=1), None)

    def test_change_map_counts_regions_that_grew(self) -> None:
        """Сравнение лет: у скольких регионов рост и где он наибольший."""
        rows = [region("Москва", 10.0, None), region("Тула", -2.0, None), region("Тыва", 4.0, None)]
        text = map_finding(rows, item(), [2015, 2025])
        assert "у 2 из 3 регионов" in text
        assert "Москва" in text

    def test_distribution_compares_with_the_country(self) -> None:
        """Распределение: середина регионов и сколько из них ниже России."""
        rows = [region(name, value, None) for name, value in (("А", 1.0), ("Б", 2.0), ("В", 9.0))]
        statistics = {"p25_value": 1.5, "p75_value": 5.0, "median_value": 2.0, "country_value": 3.0}
        text = distribution_finding(rows, statistics, item())
        assert "от 2 до 5 руб." in text
        assert "у 2 из 3" in text

    def test_additive_value_is_not_compared_with_the_sum(self) -> None:
        """У складываемой величины значение России — сумма, с регионами её не сравнивают."""
        rows = [region(name, value, None) for name, value in (("А", 1.0), ("Б", 2.0), ("В", 9.0))]
        statistics = {
            "p25_value": 1.5,
            "p75_value": 5.0,
            "median_value": 2.0,
            "country_value": 12.0,
        }
        text = distribution_finding(rows, statistics, item(absolute=True))
        assert "России" not in text
        assert "в 4,5 раза больше середины" in text

    def test_ranking_names_the_largest_moves(self) -> None:
        """Рейтинг: наибольший подъём и падение — со знаком, без согласования по роду."""
        movers = {
            "risen": [{"name": "Воронежская область", "movement": 11}],
            "fallen": [{"name": "Кузбасс", "movement": -6}],
        }
        text = ranking_finding(movers, [2024, 2025])
        assert "Воронежская область (+11)" in text
        assert "Кузбасс (−6)" in text
        assert ranking_finding(movers, None) == ""

    def test_timeline_uses_years_known_to_every_line(self) -> None:
        """Динамика: изменение — за годы, в которых значения есть у всех линий."""
        lines = [
            {"name": "Тула", "values": [None, 10.0, 30.0]},
            {"name": "Тыва", "values": [5.0, 10.0, 12.0]},
            {"name": "Россия", "values": [1.0, 10.0, 20.0], "country": True},
        ]
        text = timeline_finding([2020, 2021, 2022], lines, item())
        assert text.startswith("С 2021 по 2022 год")
        assert "Тула — рост в 3 раза" in text
        assert "Россия — рост в 2 раза" in text

    def test_unconfirmed_relation_says_so(self) -> None:
        """Связь, не отличимая от нуля, направления не получает."""
        pair = SimpleNamespace(
            is_available=True, coefficient=-0.1, is_significant=False, strength="слабая"
        )
        text = relation_finding("Зарплата", "Бедность", pair)
        assert "нельзя сказать" in text
        confirmed = SimpleNamespace(
            is_available=True, coefficient=-0.44, is_significant=True, strength="умеренная"
        )
        assert "меньше «Бедность»" in relation_finding("Зарплата", "Бедность", confirmed)


class TestViewQuery:
    """Описание текущего вида: адрес, который открывает то же самое."""

    def test_build_parameters_are_kept(self) -> None:
        """
        Вид описывается целиком, вместе с параметрами построения.

        В отличие от перехода на другое представление, здесь ничего не отбрасывается:
        ссылкой делятся ради того, что на экране, а не ради части этого.
        """
        request = RequestFactory().get("/", {"method": "equal", "classes": "7", "mode": "tiles"})
        query = view_query(PANELS_BY_CODE[PANEL_MAP], state(), request)
        assert "method=equal" in query
        assert "classes=7" in query
        assert "mode=tiles" in query
        assert "year=2023" in query

    def test_foreign_parameters_are_dropped(self) -> None:
        """Чужой параметр в адрес вида не попадает: представление его не понимает."""
        request = RequestFactory().get("/", {"order": "asc", "method": "equal"})
        query = view_query(PANELS_BY_CODE[PANEL_MAP], state(), request)
        assert "order" not in query
        assert "method=equal" in query

    def test_override_replaces_the_value(self) -> None:
        """Изменение одного параметра сохраняет остальные."""
        request = RequestFactory().get("/", {"method": "equal", "classes": "7"})
        query = view_query(PANELS_BY_CODE[PANEL_MAP], state(), request, method="quantile")
        assert "method=quantile" in query
        assert "method=equal" not in query
        assert "classes=7" in query

    def test_empty_override_removes_the_parameter(self) -> None:
        """
        Пустое значение убирает имя из адреса.

        «Показать весь ряд» — это отсутствие ограничения, а не его значение,
        и в адресе оно выглядело бы состоянием, которого на экране нет.
        """
        request = RequestFactory().get("/", {"classes": "7"})
        query = view_query(PANELS_BY_CODE[PANEL_MAP], state(), request, classes="")
        assert "classes" not in query


class TestExportKinds:
    """Вид отчёта отвечает тому, что показано на холсте."""

    def test_every_panel_declares_a_known_kind(self) -> None:
        """Вид отчёта у каждого представления существует и выгружается."""
        for panel in PANELS:
            assert panel.export_kind in REPORT_KINDS_BY_CODE

    def test_year_slice_and_full_table_are_told_apart(self) -> None:
        """Срез по субъектам за год и таблица «субъекты × годы» — разные выгрузки."""
        kinds = {panel.code: panel.export_kind for panel in PANELS}
        assert kinds[PANEL_MAP] == ReportKind.RANKING
        assert kinds[PANEL_RANKINGS] == ReportKind.RANKING
        assert kinds[PANEL_DISTRIBUTION] == ReportKind.RANKING
        assert kinds[PANEL_COMPARE] == ReportKind.SERIES
        assert kinds[PANEL_TABLE] == ReportKind.SERIES
