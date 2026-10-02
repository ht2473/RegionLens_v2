"""Проверки витрины: живая карта, карточка региона, главное о стране."""

from __future__ import annotations

import json
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from apps.catalog.models import Territory
from apps.core import showcase
from apps.core.charts import sparkline_path
from apps.warehouse.queries import featured_set

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

WAGE = "Y477110378:00"
UNEMPLOYMENT = "Y477110418:00"


def _state(content: str) -> dict[str, Any]:
    """Состояние живой карты, встроенное в разметку."""
    start = content.index('id="live-map-state"')
    body = content[content.index(">", start) + 1 : content.index("</script>", start)]
    return json.loads(body)


class TestLiveSeries:
    """Какие показатели открываются на живой карте."""

    def test_only_headline_questions(self) -> None:
        """На карте — главные показатели с вопросом-входом: вопрос делает их понятными."""
        items = showcase.live_series()
        assert items
        assert all(item.headline and item.question_ru for item in items)

    def test_home_role_opens_the_map(self) -> None:
        """Без ключа карта открывается рядом с ролью ``home``."""
        chosen = showcase.resolve_live_series(None)
        assert chosen is not None
        assert chosen.role == "home"

    def test_foreign_key_does_not_open_arbitrary_series(self) -> None:
        """Ключ ряда вне живой карты не открывает произвольный ряд из полного перечня."""
        population = next(item for item in featured_set().series if item.role == "population")
        chosen = showcase.resolve_live_series(population.key)
        assert chosen is not None
        assert chosen.key != population.key


class TestScaleEnds:
    """Концы шкалы живой карты подписаны оценкой там, где она проверена."""

    def test_negative_series_is_better_at_the_low_end(self) -> None:
        """У безработицы меньше — лучше: самый насыщенный цвет — это хуже."""
        item = showcase.resolve_live_series(UNEMPLOYMENT)
        assert item is not None
        ends = showcase.scale_ends(item)
        assert (ends["low_tone"], ends["high_tone"]) == ("good", "bad")

    def test_positive_series_is_better_at_the_high_end(self) -> None:
        """У зарплаты больше — лучше."""
        item = showcase.resolve_live_series(WAGE)
        assert item is not None
        ends = showcase.scale_ends(item)
        assert (ends["low_tone"], ends["high_tone"]) == ("bad", "good")

    def test_absolute_series_is_not_assessed(self) -> None:
        """У абсолютной величины оценки нет: «больше» у численности не лучше и не хуже."""
        population = next(item for item in featured_set().series if item.role == "population")
        assert showcase.scale_ends(population) == {
            "low": "",
            "low_tone": "",
            "high": "",
            "high_tone": "",
        }


class TestFrames:
    """Кадры живой карты собираются сервером целиком."""

    def test_every_year_covers_every_region(self, warehouse: Any) -> None:
        """В каждом кадре по одной записи на субъект карты — в одном порядке."""
        item = showcase.resolve_live_series(WAGE)
        assert item is not None
        frames = showcase.live_frames(item)
        codes = frames["codes"]
        assert len(codes) == Territory.objects.comparable().count()
        assert frames["years"]
        for year in frames["years"]:
            frame = frames["frames"][str(year)]
            for field in ("classes", "values", "places", "standings", "tones", "versus"):
                assert len(frame[field]) == len(codes), field
            assert frame["legend"]
            assert frame["highest"] and frame["lowest"]

    def test_classes_follow_the_palette(self, warehouse: Any) -> None:
        """Номер класса указывает на переменную шкалы из того же кадра."""
        item = showcase.resolve_live_series(WAGE)
        assert item is not None
        frame = showcase.live_frames(item)["frames"][str(showcase.live_frames(item)["years"][-1])]
        used = {index for index in frame["classes"] if index is not None}
        assert used <= set(range(len(frame["palette"])))
        assert all(colour.startswith("--scale-seq-") for colour in frame["palette"])

    def test_percent_series_compared_in_points(self, warehouse: Any) -> None:
        """Процентная величина сравнивается со страной в процентных пунктах."""
        item = showcase.resolve_live_series(UNEMPLOYMENT)
        assert item is not None and item.key == UNEMPLOYMENT
        frames = showcase.live_frames(item)
        frame = frames["frames"][str(frames["years"][-1])]
        phrases = [text for text in frame["versus"] if text and text != "на уровне России"]
        assert phrases
        assert all("п. п." in text for text in phrases)

    def test_endpoint_returns_all_years(self, client: Client, warehouse: Any) -> None:
        """Ответ для ползунка — все годы разом, с разрешением хранить его."""
        response = client.get(reverse("core:home-frames"), {"series": WAGE})
        assert response.status_code == 200
        payload = response.json()
        assert payload["series"] == WAGE
        assert set(payload["frames"]) == {str(year) for year in payload["years"]}
        assert "public" in response["Cache-Control"]


class TestHomePage:
    """Витрина целиком."""

    def test_live_map_is_embedded(self, client: Client, warehouse: Any) -> None:
        """Карта нарисована сервером, кадр выбранного года встроен в разметку."""
        response = client.get(reverse("core:home"))
        content = response.content.decode("utf-8")
        assert "<rl-live-map" in content
        assert "data-geo-map" in content
        state = _state(content)
        assert state["series"] == showcase.resolve_live_series(None).key  # type: ignore[union-attr]
        assert state["year"] == state["years"][-1]
        assert state["framesUrl"].startswith(reverse("core:home-frames"))

    def test_year_from_address(self, client: Client, warehouse: Any) -> None:
        """Год из адреса открывает карту за этот год: вид экрана адресуем ссылкой."""
        years = _state(client.get(reverse("core:home")).content.decode("utf-8"))["years"]
        early = years[0]
        content = client.get(reverse("core:home"), {"series": WAGE, "year": early}).content
        assert _state(content.decode("utf-8"))["year"] == early

    def test_choice_replaces_only_the_map(self, client: Client, warehouse: Any) -> None:
        """Смена показателя отдаёт один блок карты, а не страницу целиком."""
        response = client.get(
            reverse("core:home"),
            {"series": UNEMPLOYMENT},
            HTTP_HX_REQUEST="true",
            HTTP_HX_TARGET=showcase.LIVE_MAP_ID,
        )
        content = response.content.decode("utf-8")
        assert content.lstrip().startswith("<rl-live-map")
        assert "<html" not in content
        assert _state(content)["series"] == UNEMPLOYMENT

    def test_country_tiles_use_points_for_percentages(self, client: Client, warehouse: Any) -> None:
        """Изменение процентной величины на плитке страны — в процентных пунктах."""
        tiles = client.get(reverse("core:home")).context["tiles"]
        assert tiles
        for tile in tiles:
            if tile["series"].is_percentage and tile["change"]:
                assert tile["change"].endswith("п. п.")
            elif tile["change"]:
                assert tile["change"].endswith("%")


class TestTerritoryCard:
    """Карточка региона во всплывающей панели."""

    def test_chosen_series_goes_first(self, client: Client, warehouse: Any) -> None:
        """Показатель, открытый на карте, стоит в карточке первым."""
        territory = Territory.objects.get(code="RU-TA")
        response = client.get(
            reverse("catalog:territory-card", kwargs={"slug": territory.slug}),
            {"series": UNEMPLOYMENT},
        )
        assert response.status_code == 200
        positions = response.context["positions"]
        assert positions
        assert positions[0].series.key == UNEMPLOYMENT
        content = response.content.decode("utf-8")
        assert "<html" not in content
        assert reverse("catalog:territory-detail", kwargs={"slug": territory.slug}) in content

    def test_only_regions_have_cards(self, client: Client, warehouse: Any) -> None:
        """У страны и округов карточки нет: на живой карте их не выбрать."""
        country = Territory.objects.get(code="RU")
        response = client.get(reverse("catalog:territory-card", kwargs={"slug": country.slug}))
        assert response.status_code == 404


class TestSparkline:
    """Миниатюрный график плитки."""

    def test_gap_breaks_the_line(self) -> None:
        """Пропуск разрывает линию: путь начинается заново после отсутствующего года."""
        spark = sparkline_path([1.0, 2.0, None, 3.0, 4.0])
        assert spark is not None
        assert spark["path"].count("M") == 2

    def test_single_point_draws_nothing(self) -> None:
        """По одной точке линии нет."""
        assert sparkline_path([None, 5.0, None]) is None
