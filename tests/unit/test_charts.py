"""
Проверки настроек графиков: подписи у концов линий, выделенная линия, общий базовый год.
"""

from __future__ import annotations

import pytest

from apps.analytics.charts import matrix_option
from apps.compare.panel import _base_year, _rebased
from apps.core import chart_layout as layout
from apps.core.charts import bump_option, timeline_option

pytestmark = pytest.mark.unit

YEARS = [2019, 2020, 2021, 2022]


def line(name: str, values: list[float | None], **flags: object) -> dict[str, object]:
    """Собрать описание линии для проверок."""
    return {"name": name, "values": values, **flags}


def data_series(option: dict) -> list[dict]:
    """Отобрать ряды данных, отбросив служебные ряды коридора."""
    return [entry for entry in option["series"] if entry.get("triggerLineEvent")]


class TestEndLabels:
    """Подписи у концов линий вместо легенды."""

    def test_legend_is_off(self) -> None:
        """
        Легенда не показывается.

        Легенда заставляет переводить взгляд от линии к образцу цвета и обратно,
        а на четырёх и более рядах — ещё и удерживать в памяти, какой оттенок чей.
        """
        option = timeline_option(YEARS, [line("Москва", [1, 2, 3, 4])])
        assert option["legend"]["show"] is False

    def test_every_line_is_labelled_at_its_end(self) -> None:
        """Каждая линия данных подписана своим названием."""
        option = timeline_option(YEARS, [line("Москва", [1, 2, 3, 4]), line("Тула", [4, 3, 2, 1])])
        for entry in data_series(option):
            assert entry["endLabel"]["show"] is True
            assert entry["endLabel"]["formatter"] == "{a}"

    def test_field_leaves_room_for_the_labels(self) -> None:
        """Поле графика сужается справа: иначе подпись уходит за холст."""
        option = timeline_option(YEARS, [line("Москва", [1, 2, 3, 4])])
        assert option["grid"]["right"] == layout.END_LABEL_BAND

    def test_labels_are_pushed_apart_when_they_meet(self) -> None:
        """
        Сошедшиеся подписи разводятся по вертикали.

        У субъектов с близкими значениями концы линий приходятся на одну точку,
        и без разведения подписи легли бы одна на другую.
        """
        option = timeline_option(YEARS, [line("Москва", [1, 2, 3, 4])])
        assert data_series(option)[0]["labelLayout"] == {"moveOverlap": "shiftY"}

    def test_labels_can_be_turned_off(self) -> None:
        """На графике размером с плитку подписи не поместятся и отключаются."""
        option = timeline_option(YEARS, [line("Москва", [1, 2, 3, 4])], end_labels=False)
        assert "endLabel" not in data_series(option)[0]
        assert option["grid"]["right"] != layout.END_LABEL_BAND

    def test_ranking_movement_is_labelled_too(self) -> None:
        """График движения позиций подписывается тем же способом."""
        option = bump_option(YEARS, [line("Москва", [1, 1, 2, 2])], max_rank=85)
        assert option["legend"]["show"] is False
        assert option["series"][0]["endLabel"]["show"] is True

    def test_label_takes_the_colour_of_its_line(self) -> None:
        """Цвет подписи задан явно — текстовой ступенью цвета своей линии, без обводки."""
        option = timeline_option(YEARS, [line("Москва", [1, 2, 3, 4]), line("Тула", [4, 3, 2, 1])])
        for index, entry in enumerate(data_series(option)):
            assert entry["lineStyle"]["color"] == layout.palette_colour(index)
            assert entry["endLabel"]["color"] == layout.palette_ink(index)
            assert entry["endLabel"]["textBorderWidth"] == 0

    def test_country_and_named_colours_label_themselves(self) -> None:
        """
        У линии с заданным цветом подпись набирается им же, а текстовая ступень
        берётся из описания линии.
        """
        option = timeline_option(
            YEARS,
            [
                line(
                    "Середина регионов",
                    [1, 2, 3, 4],
                    colour="var(--accent)",
                    ink="var(--accent-ink)",
                ),
                line("Россия", [2, 2, 2, 2], country=True),
            ],
        )
        median, country = data_series(option)
        assert median["endLabel"]["color"] == "var(--accent-ink)"
        assert country["endLabel"]["color"] == country["lineStyle"]["color"]

    def test_corridor_keeps_the_colours_of_the_lines(self) -> None:
        """Отсчёт цветов — после рядов коридора, как у библиотеки."""
        option = timeline_option(
            YEARS,
            [line("Москва", [1, 2, 3, 4])],
            corridor={"lower": [0, 0, 0, 0], "band": [1, 1, 1, 1]},
        )
        first = data_series(option)[0]
        assert first["lineStyle"]["color"] == layout.palette_colour(2)
        assert first["endLabel"]["color"] == layout.palette_ink(2)


class TestHighlight:
    """Выделенная линия и приглушённое окружение."""

    def test_primary_line_is_thicker_and_on_top(self) -> None:
        """Выделенная линия толще остальных и рисуется поверх них."""
        option = timeline_option(
            YEARS, [line("Тула", [1, 2, 3, 4], primary=True), line("Москва", [4, 3, 2, 1])]
        )
        primary, other = data_series(option)
        assert primary["lineStyle"]["width"] > other.get("lineStyle", {}).get("width", 2)
        assert primary["z"] > 0

    def test_muted_line_fades_but_stays_visible(self) -> None:
        """
        Линия окружения приглушается, но не исчезает.

        Она и нужна для сравнения: без неё нельзя отличить особенность субъекта
        от общей для страны тенденции.
        """
        option = timeline_option(YEARS, [line("Россия", [1, 2, 3, 4], muted=True)])
        entry = data_series(option)[0]
        assert 0 < entry["lineStyle"]["opacity"] < 1

    def test_muted_label_fades_less_than_its_line(self) -> None:
        """
        Подпись приглушается слабее линии.

        На тёмной теме надпись в четыре десятых силы уже неразличима, тогда как
        линия ещё видна.
        """
        option = timeline_option(YEARS, [line("Россия", [1, 2, 3, 4], muted=True)])
        entry = data_series(option)[0]
        assert entry["endLabel"]["opacity"] > entry["lineStyle"]["opacity"]

    def test_lines_answer_to_a_click(self) -> None:
        """
        Щелчок ловится всей линией, а не только её точкой.

        Точка на линии имеет пять точек в поперечнике: попасть в неё указателем
        труднее, чем в саму линию.
        """
        option = timeline_option(YEARS, [line("Москва", [1, 2, 3, 4])])
        assert option["series"][0]["triggerLineEvent"] is True


class TestCorridor:
    """Коридор межквартильного размаха."""

    def test_corridor_is_drawn_behind_the_lines(self) -> None:
        """Полоса коридора идёт в наборе первой, то есть под линиями."""
        option = timeline_option(
            YEARS,
            [line("Тула", [2, 3, 4, 5])],
            corridor={"lower": [1, 1, 2, 2], "band": [2, 3, 3, 4]},
        )
        assert option["series"][0]["stack"] == "corridor"
        assert option["series"][1]["stack"] == "corridor"
        assert option["series"][2]["name"] == "Тула"

    def test_corridor_is_not_labelled(self) -> None:
        """Служебные ряды коридора не подписываются, не попадают в легенду и не ловят щелчок."""
        option = timeline_option(
            YEARS,
            [line("Тула", [2, 3, 4, 5])],
            corridor={"lower": [1, 1, 2, 2], "band": [2, 3, 3, 4]},
        )
        for entry in option["series"][:2]:
            assert "endLabel" not in entry
            assert "triggerLineEvent" not in entry


class TestBreakMarks:
    """Отметки разрывов сопоставимости."""

    def test_mark_is_labelled_with_the_year_only(self) -> None:
        """
        Отметка подписывается годом.

        Название вида разрыва библиотека печатала вдоль вертикальной черты,
        и надпись пересекала график сверху донизу поверх самих линий.
        """
        option = timeline_option(
            YEARS,
            [line("Тула", [1, 2, 3, 4])],
            breaks=[{"year": 2021, "label": "Изменение круга наблюдаемых объектов"}],
        )
        mark = option["series"][0]["markLine"]
        assert mark["data"][0]["name"] == "2021"
        assert mark["label"]["rotate"] == 0

    def test_kind_of_break_is_kept_for_the_tooltip(self) -> None:
        """Что именно изменилось, остаётся доступным в подсказке отметки."""
        option = timeline_option(
            YEARS,
            [line("Тула", [1, 2, 3, 4])],
            breaks=[{"year": 2021, "label": "Изменение круга наблюдаемых объектов"}],
        )
        assert option["series"][0]["markLine"]["data"][0]["value"].startswith("Изменение")


class TestChosenYear:
    """Отметка года, выбранного на рабочей поверхности."""

    def test_year_is_marked_on_the_time_axis(self) -> None:
        """
        Выбранный год отмечен чертой.

        Карта, рейтинг и таблица показывают именно его, и по графику должно быть
        видно, какому месту во времени они отвечают.
        """
        option = timeline_option(YEARS, [line("Тула", [1, 2, 3, 4])], year_mark=2021)
        marks = [item["xAxis"] for item in option["series"][0]["markLine"]["data"]]
        assert marks == ["2021"]

    def test_mark_is_not_labelled(self) -> None:
        """
        Отметка года не подписывается.

        Тот же год написан делением оси прямо под чертой, и вторая надпись
        легла бы поверх первой.
        """
        option = timeline_option(YEARS, [line("Тула", [1, 2, 3, 4])], year_mark=2021)
        assert option["series"][0]["markLine"]["data"][0]["label"]["show"] is False

    def test_mark_lives_beside_the_breaks(self) -> None:
        """Отметка года не вытесняет отметки разрывов: они об одном и том же графике."""
        option = timeline_option(
            YEARS,
            [line("Тула", [1, 2, 3, 4])],
            breaks=[{"year": 2020, "label": "Изменение методики"}],
            year_mark=2022,
        )
        marks = [item["xAxis"] for item in option["series"][0]["markLine"]["data"]]
        assert marks == ["2020", "2022"]

    def test_no_mark_without_a_year(self) -> None:
        """Без выбранного года и без разрывов отметок на графике нет."""
        option = timeline_option(YEARS, [line("Тула", [1, 2, 3, 4])])
        assert "markLine" not in option["series"][0]


class TestBumpHighlight:
    """Выделенная линия на графике движения позиций."""

    LINES = [
        line("МСК", [1, 1, 2, 1], primary=True),
        line("СПБ", [2, 2, 1, 2], muted=True),
    ]

    def test_selected_line_is_thicker_and_on_top(self) -> None:
        """
        Выбранная территория выделена и нарисована поверх остальных.

        Линии рейтинга пересекаются чаще прочих, и под приглушённой соседкой
        выделенная терялась бы на половине длины.
        """
        option = bump_option(YEARS, self.LINES, max_rank=8)
        primary = option["series"][0]
        assert primary["lineStyle"]["width"] > 1
        assert primary["z"] > 1

    def test_surroundings_are_dimmed(self) -> None:
        """Окружение приглушено — и линия, и её точки."""
        option = bump_option(YEARS, self.LINES, max_rank=8)
        muted = option["series"][1]
        assert muted["lineStyle"]["opacity"] == layout.MUTED_OPACITY
        assert muted["itemStyle"]["opacity"] == layout.MUTED_OPACITY

    def test_labels_follow_the_highlight(self) -> None:
        """Подпись следует за линией: иначе выделение читалось бы только на самой линии."""
        option = bump_option(YEARS, self.LINES, max_rank=8)
        assert option["series"][0]["endLabel"]["fontWeight"] == "bold"
        assert option["series"][1]["endLabel"]["opacity"] < 1


class TestRebasing:
    """Приведение к базовому году в сравнении территорий."""

    def test_base_is_the_earliest_year_known_to_everyone(self) -> None:
        """Базовым становится самый ранний год, известный всем линиям."""
        lines = [
            {"values": [None, 10.0, 20.0, 30.0]},
            {"values": [5.0, 10.0, 15.0, 20.0]},
        ]
        assert _base_year(YEARS, lines) == 2020

    def test_no_common_year_gives_no_base(self) -> None:
        """Без общего года приводить не от чего."""
        lines = [{"values": [1.0, None]}, {"values": [None, 1.0]}]
        assert _base_year([2019, 2020], lines) is None

    def test_zero_cannot_be_a_base(self) -> None:
        """Ноль базой не бывает: деление на него не определено."""
        lines = [{"values": [0.0, 10.0]}, {"values": [5.0, 10.0]}]
        assert _base_year([2019, 2020], lines) == 2020

    def test_base_year_becomes_a_hundred(self) -> None:
        """В базовом году все линии сходятся в сотне."""
        rebased = _rebased({"name": "Тула", "values": [50.0, 75.0, 100.0]}, [1, 2, 3], 1)
        assert rebased["values"] == [100.0, 150.0, 200.0]

    def test_gaps_survive_rebasing(self) -> None:
        """Пропуск остаётся пропуском: пересчитывать нечего."""
        rebased = _rebased({"name": "Тула", "values": [50.0, None, 100.0]}, [1, 2, 3], 1)
        assert rebased["values"][1] is None

    def test_other_properties_are_kept(self) -> None:
        """Признаки линии — ориентир, выделение — приведение не теряет."""
        rebased = _rebased({"name": "Россия", "values": [50.0, 100.0], "country": True}, [1, 2], 1)
        assert rebased["country"] is True
        assert rebased["name"] == "Россия"


class TestCorrelationMatrix:
    """Матрица парных связей."""

    LABELS = [
        "Валовой региональный продукт на душу населения",
        "Численность населения с денежными доходами ниже границы бедности",
        "Численность населения",
    ]

    def cells(self) -> list[dict[str, object]]:
        """Собрать ячейки матрицы: диагональ, одна связь и один пропуск."""
        return [
            {"x": 0, "y": 0, "value": 1.0},
            {"x": 1, "y": 0, "value": -0.68},
            {"x": 2, "y": 0, "value": None},
        ]

    def test_axes_carry_numbers_rather_than_names(self) -> None:
        """
        По осям отложены номера показателей.

        Названия здесь от двух десятков до сотни знаков, и на оси они обрезались
        до двух десятков, после чего разные показатели становились неразличимы.
        """
        option = matrix_option(self.LABELS, self.cells())
        assert option["xAxis"]["data"] == ["1", "2", "3"]
        assert option["yAxis"]["data"] == ["1", "2", "3"]

    def test_empty_cell_is_left_empty(self) -> None:
        """
        Незначимая связь не рисуется вовсе.

        Подложка ячеек не заливается: серая клетка читается как значение,
        которого нет.
        """
        option = matrix_option(self.LABELS, self.cells())
        assert len(option["series"][0]["data"]) == 2
        assert option["xAxis"]["splitArea"]["show"] is False
        assert option["yAxis"]["splitArea"]["show"] is False

    def test_tooltip_names_both_indicators(self) -> None:
        """Подсказка называет оба показателя целиком: на осях стоят номера."""
        option = matrix_option(self.LABELS, self.cells())
        name = option["series"][0]["data"][1]["name"]
        assert self.LABELS[0] in name
        assert self.LABELS[1] in name

    def test_tooltip_and_label_are_ready_text(self) -> None:
        """Подсказка и подпись клетки приходят готовыми, число — с запятой."""
        option = matrix_option(self.LABELS, self.cells())
        cell = option["series"][0]["data"][1]
        tooltip = cell["tooltip"]["formatter"]
        assert "{" not in tooltip
        assert "&middot;" not in tooltip and "&middot;" not in cell["name"]
        assert "0,68" in tooltip
        assert cell["label"]["formatter"].replace("−", "-").endswith("0,68")
        assert "formatter" not in option["tooltip"]

    def test_colour_scale_has_no_draggable_handles(self) -> None:
        """
        У шкалы цвета нет ползунков.

        Их подписи со значениями библиотека рисует над полосой, и они наезжали
        на номера показателей под матрицей.
        """
        assert matrix_option(self.LABELS, self.cells())["visualMap"]["calculable"] is False


class TestTerritoryLinks:
    """Связь линий графика с территориями."""

    def test_lines_name_their_territories(self) -> None:
        """Соответствие «линия — код территории» приходит вместе с настройками."""
        option = timeline_option(
            YEARS,
            [line("Москва", [1, 2, 3, 4], code="RU-MOW")],
        )
        assert option["territories"] == {"Москва": "RU-MOW"}

    def test_ranking_movement_carries_the_same_link(self) -> None:
        """График движения позиций связан с таблицей так же."""
        option = bump_option(YEARS, [line("МСК", [1, 1, 2, 1], code="RU-MOW")], max_rank=8)
        assert option["territories"] == {"МСК": "RU-MOW"}

    def test_lines_without_territories_declare_nothing(self) -> None:
        """
        Пустого соответствия не бывает.

        У графика мер неравенства линии — это меры, и раздел, сообщающий о связи
        с территориями, утверждал бы связь, которой нет.
        """
        option = timeline_option(YEARS, [line("Коэффициент Джини", [1, 2, 3, 4])])
        assert "territories" not in option

    def test_surroundings_stay_visible_under_the_pointer(self) -> None:
        """Приглушение при наведении задано явно: своё у библиотеки почти скрывает окружение."""
        option = timeline_option(YEARS, [line("Москва", [1, 2, 3, 4], code="RU-MOW")])
        blur = data_series(option)[0]["blur"]
        assert blur["lineStyle"]["opacity"] == layout.BLUR_OPACITY
        assert blur["lineStyle"]["opacity"] > 0.1
