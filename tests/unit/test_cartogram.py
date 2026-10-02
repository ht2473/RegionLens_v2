"""
Проверки картограммы: контуры замкнуты, точка подписи внутри субъекта, врезка увеличивает,
подписи не наезжают друг на друга.
"""

from __future__ import annotations

import re
from itertools import pairwise

import pytest

from apps.maps import cartogram
from apps.maps.cartogram import INSETS, Placement, anchor, build_map, layout

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def plan() -> cartogram.Layout:
    """Геометрия чертежа, собранная по файлу границ из репозитория."""
    result = layout()
    assert result is not None, "Файл границ не подготовлен"
    return result


def rows_for(plan: cartogram.Layout, *, missing: tuple[str, ...] = ()) -> list[dict[str, object]]:
    """Собрать наблюдения по всем субъектам с геометрией."""
    return [
        {
            "code": code,
            "name": code,
            "abbreviation": code[-3:],
            "href": f"/ru/regions/{code}/",
            "title": f"{code} — 1",
            "label": "1",
            "mapped": None if code in missing else float(index + 1),
            "class_index": None if code in missing else index % 5,
            "colour": "" if code in missing else f"--scale-seq-{index % 5 + 1}",
            "light_label": False,
        }
        for index, code in enumerate(plan.shapes)
    ]


class TestLayout:
    """Геометрия чертежа."""

    def test_every_region_has_a_contour(self, plan: cartogram.Layout) -> None:
        """Каждый субъект справочника получил контур."""
        assert len(plan.shapes) == 85

    def test_contours_are_closed(self, plan: cartogram.Layout) -> None:
        """
        Каждое кольцо контура начинается командой перемещения и замыкается.

        Незамкнутое кольцо заливается по прямой между концами: субъект приобретает
        на карте форму, которой у него нет.
        """
        for shape in plan.shapes.values():
            assert shape.path.startswith("M")
            assert shape.path.endswith("z")
            assert shape.path.count("M") == shape.path.count("z")

    def test_contours_carry_no_stray_characters(self, plan: cartogram.Layout) -> None:
        """В описании контура только команды и целые числа."""
        allowed = re.compile(r"^[Mlz\d\s.-]+$")
        for shape in plan.shapes.values():
            assert allowed.match(shape.path), shape.code

    def test_contours_stay_inside_the_canvas(self, plan: cartogram.Layout) -> None:
        """Ни один контур не выходит за кромку чертежа."""
        for shape in plan.shapes.values():
            left, top, right, bottom = shape.box
            assert left >= -0.5
            assert top >= -0.5
            assert right <= plan.width + 0.5
            assert bottom <= plan.map_height + 0.5

    def test_canvas_is_wider_than_tall(self, plan: cartogram.Layout) -> None:
        """Пропорции чертежа отвечают пропорциям страны, вытянутой по долготе."""
        assert plan.width > plan.map_height

    def test_band_of_insets_lies_below_the_map(self, plan: cartogram.Layout) -> None:
        """Полоса врезок не налезает на карту."""
        for inset in plan.insets:
            assert inset.frame[1] >= plan.map_height

    def test_layout_is_computed_once(self) -> None:
        """Повторный вызов возвращает ту же геометрию, а не считает её заново."""
        assert layout() is layout()


class TestAnchors:
    """Точка подписи субъекта."""

    def test_anchor_lies_inside_a_convex_shape(self) -> None:
        """У выпуклой фигуры точка подписи оказывается в её середине."""
        square = (((0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0), (0.0, 0.0)),)
        assert anchor(square) == pytest.approx((50.0, 50.0), abs=6.0)

    def test_anchor_avoids_the_hollow_of_a_concave_shape(self) -> None:
        """
        У вогнутой фигуры подпись не ставится в центр тяжести.

        Центр тяжести подковы лежит вне подковы: подпись Краснодарского края
        оказалась бы в Адыгее, а подпись Якутии — в море.
        """
        horseshoe = (
            (
                (0.0, 0.0),
                (100.0, 0.0),
                (100.0, 100.0),
                (70.0, 100.0),
                (70.0, 30.0),
                (30.0, 30.0),
                (30.0, 100.0),
                (0.0, 100.0),
                (0.0, 0.0),
            ),
        )
        x, y = anchor(horseshoe)
        assert not (30.0 < x < 70.0 and y > 30.0), "подпись попала в вырез фигуры"

    def test_anchor_is_inside_every_region(self, plan: cartogram.Layout) -> None:
        """Точка подписи каждого субъекта лежит внутри его контура."""
        for code, shape in plan.shapes.items():
            assert cartogram._inside(plan.anchor(code), shape.rings), code

    def test_anchor_is_remembered(self, plan: cartogram.Layout) -> None:
        """Найденная точка подписи не ищется второй раз."""
        code = next(iter(plan.shapes))
        assert plan.anchor(code) == plan.anchor(code)
        assert code in plan.anchors


class TestInsets:
    """Врезки для субъектов, неразличимых на общей карте."""

    def test_every_declared_inset_is_laid_out(self, plan: cartogram.Layout) -> None:
        """Все объявленные врезки построены."""
        assert {inset.key for inset in plan.insets} == {inset.key for inset in INSETS}

    def test_insets_do_not_overlap(self, plan: cartogram.Layout) -> None:
        """Рамки врезок стоят в ряд, не налезая одна на другую."""
        frames = sorted(inset.frame for inset in plan.insets)
        for first, second in pairwise(frames):
            assert first[0] + first[2] <= second[0]

    def test_insets_stay_inside_the_canvas(self, plan: cartogram.Layout) -> None:
        """Полоса врезок помещается в ширину чертежа."""
        for inset in plan.insets:
            assert inset.frame[0] >= 0
            assert inset.frame[0] + inset.frame[2] <= plan.width

    def test_every_inset_enlarges_its_subject(self, plan: cartogram.Layout) -> None:
        """
        Врезка увеличивает содержимое, а не просто повторяет его.

        Увеличение — единственная причина существования врезки: Севастополь занимает
        на общей карте квадрат со стороной в три тысячных её ширины.
        """
        for inset in plan.insets:
            assert inset.scale > 1.8, inset.key

    def test_focus_regions_fit_into_the_frame(self, plan: cartogram.Layout) -> None:
        """Субъект, ради которого сделана врезка, помещается в неё целиком."""
        for inset in plan.insets:
            left, top, width, height = inset.frame
            for code in inset.focus:
                points = [inset.place(point) for ring in plan.shapes[code].rings for point in ring]
                assert min(point[0] for point in points) >= left - 0.5, code
                assert max(point[0] for point in points) <= left + width + 0.5, code
                assert min(point[1] for point in points) >= top - 0.5, code
                assert max(point[1] for point in points) <= top + height + 0.5, code

    def test_inset_contains_the_neighbours_of_its_subject(self, plan: cartogram.Layout) -> None:
        """Во врезку попадает и окружение: иначе непонятно, что за место показано."""
        for inset in plan.insets:
            assert set(inset.codes) > set(inset.focus), inset.key

    def test_insets_are_turned_north_up(self, plan: cartogram.Layout) -> None:
        """
        Содержимое врезки развёрнуто севером вверх.

        Все врезки приходятся на запад страны, где коническая проекция разворачивает
        местность почти на пятьдесят градусов.
        """
        for inset in plan.insets:
            assert -70.0 < inset.angle < -30.0, inset.key


class TestLabelSpreading:
    """Разведение подписей."""

    def make(self, x: float, y: float, *, value: str = "") -> Placement:
        """Собрать подпись для проверки разведения."""
        return Placement(
            x=x,
            y=y,
            text="АБВ",
            value=value,
            light=False,
            selected=False,
            width=60.0,
            height=30.0,
            fixed=bool(value),
        )

    def test_overlapping_labels_are_pushed_apart(self) -> None:
        """Наложившиеся подписи расходятся."""
        labels = [self.make(100.0, 100.0), self.make(110.0, 105.0)]
        cartogram._spread(labels, 100.0)
        assert abs(labels[0].y - labels[1].y) >= 30.0 or abs(labels[0].x - labels[1].x) >= 60.0

    def test_distant_labels_stay_put(self) -> None:
        """Подписи, которые и так не пересекаются, не двигаются."""
        labels = [self.make(0.0, 0.0), self.make(400.0, 400.0)]
        cartogram._spread(labels, 100.0)
        assert (labels[0].x, labels[0].y) == (0.0, 0.0)
        assert (labels[1].x, labels[1].y) == (400.0, 400.0)

    def test_label_with_a_value_holds_its_place(self) -> None:
        """Подпись крайнего значения остаётся на месте, двигается соседняя."""
        labels = [self.make(100.0, 100.0, value="42"), self.make(105.0, 105.0)]
        cartogram._spread(labels, 100.0)
        assert (labels[0].x, labels[0].y) == (100.0, 100.0)
        assert (labels[1].x, labels[1].y) != (105.0, 105.0)

    def test_shift_is_limited(self) -> None:
        """Подпись не уходит от своего субъекта дальше предела."""
        labels = [self.make(100.0, 100.0, value="42"), self.make(101.0, 101.0)]
        cartogram._spread(labels, 5.0)
        assert abs(labels[1].x - 101.0) <= 5.0
        assert abs(labels[1].y - 101.0) <= 5.0

    def test_shifted_label_is_marked_for_a_leader(self) -> None:
        """Заметно отодвинутая подпись помечается для соединительной черты."""
        labels = [self.make(100.0, 100.0, value="42"), self.make(101.0, 101.0)]
        cartogram._spread(labels, 100.0)
        assert labels[1].shifted
        assert not labels[0].shifted


class TestBuildMap:
    """Сборка чертежа под данные."""

    def test_every_region_is_drawn_once(self, plan: cartogram.Layout) -> None:
        """Контур каждого субъекта описан один раз, а используется многократно."""
        drawing = build_map(rows_for(plan))
        assert drawing is not None
        identifiers = [item["id"] for item in drawing["definitions"]]
        assert len(identifiers) == len(set(identifiers)) == len(plan.shapes)

        used = {region["reference"] for region in drawing["regions"]}
        used |= {region["reference"] for inset in drawing["insets"] for region in inset["regions"]}
        assert used == {f"#{identifier}" for identifier in identifiers}

    def test_missing_value_is_hatched_rather_than_filled(self, plan: cartogram.Layout) -> None:
        """
        Пропуск закрашивается штриховкой, а не цветом шкалы.

        Отсутствие значения не является нулём и не должно занимать место
        на цветовой шкале.
        """
        code = next(iter(plan.shapes))
        drawing = build_map(rows_for(plan, missing=(code,)))
        assert drawing is not None
        region = next(item for item in drawing["regions"] if item["code"] == code)
        assert region["fill"] == f"url(#{drawing['no_data_pattern']})"
        assert region["class_index"] == ""

    def test_extremes_are_not_labelled(self, plan: cartogram.Layout) -> None:
        """Без выбора карта не подписана: крайние значения называют перечни под ней."""
        drawing = build_map(rows_for(plan))
        assert drawing is not None
        assert drawing["labels"] == []
        assert not any(label["value"] for inset in drawing["insets"] for label in inset["labels"])

    def test_selected_region_is_labelled_too(self, plan: cartogram.Layout) -> None:
        """Выделенный субъект подписывается."""
        rows = rows_for(plan)
        middle = rows[len(rows) // 2]["code"]
        drawing = build_map(rows, selected=[str(middle)])
        assert drawing is not None
        assert any(label["selected"] for label in drawing["labels"])

    def test_every_selected_region_is_labelled(self, plan: cartogram.Layout) -> None:
        """Подписываются все выбранные субъекты, а не первый из них."""
        rows = rows_for(plan)
        chosen = [rows[len(rows) // 3]["code"], rows[len(rows) // 2]["code"]]
        drawing = build_map(rows, selected=chosen)
        assert drawing is not None

        labelled = {label["name"] for label in drawing["labels"]}
        labelled |= {label["text"] for inset in drawing["insets"] for label in inset["labels"]}
        names = {row["code"]: row["name"] for row in rows}
        for code in chosen:
            assert names[code] in labelled or code in labelled

    def test_labels_do_not_overlap(self, plan: cartogram.Layout) -> None:
        """Подписи выбранных субъектов на общей карте разведены."""
        rows = rows_for(plan)
        chosen = [str(rows[index]["code"]) for index in range(0, len(rows), max(1, len(rows) // 6))]
        drawing = build_map(rows, selected=chosen)
        assert drawing is not None
        boxes = [
            (float(label["x"]), float(label["y"]), len(label["name"]))
            for label in drawing["labels"]
        ]
        for first in range(len(boxes)):
            for second in range(first + 1, len(boxes)):
                one, two = boxes[first], boxes[second]
                assert abs(one[1] - two[1]) > 20.0 or abs(one[0] - two[0]) > 40.0

    def test_unknown_region_is_skipped(self, plan: cartogram.Layout) -> None:
        """Ряд по территории без геометрии не ломает чертёж."""
        rows = rows_for(plan)
        rows.append({**rows[0], "code": "RU-XX"})
        drawing = build_map(rows)
        assert drawing is not None
        assert all(region["code"] != "RU-XX" for region in drawing["regions"])
