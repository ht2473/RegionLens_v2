"""
Проверки описаний представлений: перенос параметров между ними, ссылка на вид,
значения для гистограммы.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.test import RequestFactory

from apps.core.charts import histogram_option
from apps.exports.constants import REPORT_KINDS_BY_CODE, ReportKind
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


class TestHistogramLayers:
    """Разбор гистограммы распределения по классам шкалы."""

    @staticmethod
    def bins(count: int, low: float, width: float) -> list[dict[str, float]]:
        """Собрать описания интервалов гистограммы."""
        return [
            {"lower": low + width * index, "upper": low + width * (index + 1)}
            for index in range(count)
        ]

    @staticmethod
    def row(value: float | None, class_index: int | None) -> dict[str, Any]:
        """Собрать строку наблюдения."""
        return {"mapped": value, "class_index": class_index}

    def test_every_observation_is_counted_once(self) -> None:
        """
        Ни одно значение не теряется и не считается дважды.

        Потерянный при разборе субъект означал бы, что гистограмма показывает
        не то распределение, которое закрашено на карте.
        """
        from apps.surface.builders import _layer_counts

        rows = [self.row(value, value // 10) for value in (0, 5, 12, 19, 25, 39)]
        counts = _layer_counts(self.bins(4, 0, 10), rows, 4)
        assert sum(sum(layer) for layer in counts) == len(rows)

    def test_value_lands_in_its_own_class(self) -> None:
        """Значение попадает в долю своего класса, а не соседнего."""
        from apps.surface.builders import _layer_counts

        counts = _layer_counts(self.bins(2, 0, 10), [self.row(15, 1)], 2)
        assert counts[1] == [0, 1]
        assert counts[0] == [0, 0]

    def test_largest_value_stays_in_the_last_bin(self) -> None:
        """
        Наибольшее значение остаётся в последнем интервале.

        Верхняя граница последнего интервала совпадает с максимумом, и без особого
        правила он оказался бы за пределами гистограммы.
        """
        from apps.surface.builders import _layer_counts

        counts = _layer_counts(self.bins(2, 0, 10), [self.row(20, 0)], 1)
        assert counts[0] == [0, 1]

    def test_missing_values_are_skipped(self) -> None:
        """Пропуск в гистограмму не попадает: у него нет ни значения, ни класса."""
        from apps.surface.builders import _layer_counts

        counts = _layer_counts(self.bins(2, 0, 10), [self.row(None, None)], 1)
        assert sum(counts[0]) == 0


class TestHistogramOption:
    """Настройки гистограммы распределения."""

    @staticmethod
    def option() -> dict[str, Any]:
        """Собрать гистограмму из двух долей."""
        return histogram_option(
            ["0", "10"],
            [
                {"name": "0 — 10", "values": [3, 0], "colour": "var(--scale-seq-1)"},
                {"name": "10 — 20", "values": [0, 2], "colour": "var(--scale-seq-5)"},
            ],
            unit="субъектов",
        )

    def test_layers_are_stacked(self) -> None:
        """
        Доли складываются в один столбец.

        Интервал гистограммы шире класса шкалы у нижнего края распределения,
        и рядом стоящие доли читались бы как отдельные интервалы.
        """
        option = self.option()
        assert {entry["stack"] for entry in option["series"]} == {"bins"}

    def test_colours_are_kept_as_tokens(self) -> None:
        """Цвет передаётся именем переменной оформления, а не значением."""
        option = self.option()
        colours = [entry["itemStyle"]["color"] for entry in option["series"]]
        assert all(colour.startswith("var(--") for colour in colours)

    def test_legend_is_off(self) -> None:
        """
        Легенды нет: соответствие цветов классам показывает легенда карты.

        Перечень из пяти диапазонов над гистограммой повторял бы подписи оси.
        """
        assert self.option()["legend"]["show"] is False

    def test_axis_counts_whole_regions(self) -> None:
        """Деления оси целые: половины субъекта не бывает."""
        assert self.option()["yAxis"]["minInterval"] == 1


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
