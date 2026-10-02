"""Проверки файла основного набора: ошибка в нём не роняет страниц, а молча их искажает."""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

import pytest
from django.conf import settings

from apps.warehouse.queries.common import FeaturedSeries, featured_set
from tests.support import synthetic

pytestmark = pytest.mark.unit

# Допустимая направленность.
POLARITIES = {"positive", "negative", "neutral"}

# Размер набора: достаточно для портрета региона по всем темам и не больше,
# чем читается в паспорте одной таблицей.
MIN_SERIES = 40
MAX_SERIES = 70

# Плиток главных показателей — столько, сколько читается за один взгляд.
MIN_HEADLINE = 6
MAX_HEADLINE = 10

# Сколько первых рядов задают наборы по умолчанию многомерных инструментов.
DEFAULT_TOOLS_SERIES = 12


@pytest.fixture(scope="module")
def payload() -> dict[str, Any]:
    """Содержимое файла набора."""
    path = settings.REFERENCE_DIR / "featured_series.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_size_matches_plan(payload: dict[str, Any]) -> None:
    """В наборе от сорока до семидесяти рядов."""
    assert MIN_SERIES <= len(payload["series"]) <= MAX_SERIES


def test_keys_and_orders_are_unique(payload: dict[str, Any]) -> None:
    """Ряд встречается один раз, порядковые номера не повторяются."""
    keys = Counter(item["key"] for item in payload["series"])
    orders = Counter(item["order"] for item in payload["series"])
    assert [key for key, count in keys.items() if count > 1] == []
    assert [order for order, count in orders.items() if count > 1] == []


def test_every_series_has_a_known_theme(payload: dict[str, Any]) -> None:
    """Тема ряда объявлена в перечне тем, и пустых тем нет."""
    themes = {theme["slug"] for theme in payload["themes"]}
    used = {item["theme"] for item in payload["series"]}
    assert used <= themes
    assert themes <= used


def test_polarity_is_explicit(payload: dict[str, Any]) -> None:
    """Направленность задана явно: «неизвестно» в основном наборе не бывает."""
    assert {item["polarity"] for item in payload["series"]} <= POLARITIES


def test_service_roles_are_single(payload: dict[str, Any]) -> None:
    """
    Служебные роли назначены ровно по одному ряду.

    default приведён к душе населения, population абсолютен, home — главный с вопросом-входом.
    """
    roles = {item.get("role", "") for item in payload["series"]}
    assert roles == {"", "population", "default", "home"}
    by_role = {
        role: [item for item in payload["series"] if item.get("role") == role]
        for role in ("population", "default", "home")
    }
    assert len(by_role["population"]) == 1
    assert len(by_role["default"]) == 1
    assert len(by_role["home"]) == 1
    assert by_role["home"][0].get("headline") is True
    assert by_role["home"][0].get("question_ru")
    assert by_role["population"][0].get("absolute") is True
    assert not by_role["default"][0].get("absolute")


def test_headline_count(payload: dict[str, Any]) -> None:
    """Главных показателей столько, сколько плиток читается за один взгляд."""
    headline = [item for item in payload["series"] if item.get("headline")]
    assert MIN_HEADLINE <= len(headline) <= MAX_HEADLINE


def test_texts_exist_in_both_languages(payload: dict[str, Any]) -> None:
    """Название и единица есть на обоих языках; вопрос — на обоих или ни на одном."""
    for item in payload["series"]:
        assert item["short_title_ru"] and item["short_title_en"], item["key"]
        assert item["unit_label_ru"] and item["unit_label_en"], item["key"]
        assert bool(item.get("question_ru")) == bool(item.get("question_en")), item["key"]
    for theme in payload["themes"]:
        assert theme["title_ru"] and theme["title_en"] and theme["lead_ru"] and theme["lead_en"]


def test_default_tool_series_exist_in_test_dataset(payload: dict[str, Any]) -> None:
    """Первые двенадцать рядов есть в синтетическом наборе: из них наборы по умолчанию."""
    first = sorted(payload["series"], key=lambda item: item["order"])[:DEFAULT_TOOLS_SERIES]
    codes = {spec.code for spec in synthetic.SERIES_SPECS}
    assert {item["key"].split(":")[0] for item in first} <= codes


def test_loader_orders_and_groups() -> None:
    """Загрузчик упорядочивает ряды, сохраняет порядок тем и отдаёт отпечаток файла."""
    featured = featured_set()
    orders = [item.order for item in featured.series]
    assert orders == sorted(orders)
    assert [theme.slug for theme, _items in featured.grouped()] == [
        theme.slug for theme in featured.themes
    ]
    assert len(featured.digest) == 10
    assert set(featured.by_key()) == {item.key for item in featured.series}


@pytest.mark.parametrize(
    ("unit", "percentage", "growth"),
    [
        ("%", True, False),
        ("% жителей", True, False),
        ("% к прошлому году", True, True),
        ("руб. в месяц", False, False),
    ],
)
def test_unit_kind_is_read_from_label(unit: str, percentage: bool, growth: bool) -> None:
    """Проценты и темпы роста распознаются по подписи единицы."""
    item = FeaturedSeries(
        key="X:00",
        short_title_ru="Показатель",
        short_title_en="Indicator",
        unit_label_ru=unit,
        unit_label_en=unit,
        polarity="positive",
        theme="economy",
        precision=1,
        order=1,
    )
    assert item.is_percentage is percentage
    assert item.is_growth_index is growth


def test_real_basis_is_given_to_money_only(payload: dict[str, Any]) -> None:
    """Пересчёт в реальное выражение — только у денежных рядов и с известным способом."""
    methods = {"index", "volume", "prices"}
    for item in payload["series"]:
        basis = item.get("real")
        if basis is None:
            continue
        assert "руб" in item["unit_label_ru"], item["key"]
        assert basis["method"] in methods, item["key"]
        assert basis["index"], item["key"]
        assert bool(basis.get("total")) == (basis["method"] == "volume"), item["key"]


def test_money_without_real_basis_is_the_basket_only(payload: dict[str, Any]) -> None:
    """Денежный ряд без пересчёта — только стоимость набора: она сама цена."""
    unconverted = [
        item["key"]
        for item in payload["series"]
        if "руб" in item["unit_label_ru"] and "real" not in item
    ]
    assert unconverted == ["Y477110395:00"]


def test_loader_reads_real_basis() -> None:
    """Загрузчик превращает запись о пересчёте в описание способа."""
    wage = featured_set().by_key()["Y477110378:00"]
    assert wage.real is not None
    assert wage.real.method == "index"
    assert wage.is_money


@pytest.fixture(scope="module")
def statuses() -> dict[str, Any]:
    """Содержимое справочника состояния публикации рядов."""
    path = settings.REFERENCE_DIR / "series_status.json"
    return json.loads(path.read_text(encoding="utf-8"))


class TestSeriesStatus:
    """Справочник состояния публикации рядов."""

    def test_records_are_complete(self, statuses: dict[str, Any]) -> None:
        """Прекращённый ряд назван с годом и причиной, продолжающийся — с источником."""
        keys = Counter(item["key"] for item in statuses["series"])
        assert [key for key, count in keys.items() if count > 1] == []
        for item in statuses["series"]:
            if item["status"] == "discontinued":
                assert item["since"] and item["reason_ru"] and item["reason_en"], item["key"]
            else:
                assert item["status"] == "continued", item["key"]
                assert item["source"] in statuses["sources"], item["key"]

    def test_featured_series_are_listed(self, statuses: dict[str, Any]) -> None:
        """Демография основного набора отмечена как прекращённая."""
        found = {item["key"]: item["status"] for item in statuses["series"]}
        assert found["Y477110461:00"] == "discontinued"
        assert found["Y477110256:00"] == "discontinued"
        assert found["Y477110378:00"] == "continued"

    def test_loader_gives_labels(self) -> None:
        """Статус коротко называет год прекращения."""
        from django.utils import translation

        from apps.catalog.status import series_status

        with translation.override("ru"):
            population = series_status("Y477110461:00")
            assert population is not None
            assert population.label == "публикация прекращена в 2025 году"
            assert series_status("Y477110999:00") is None
