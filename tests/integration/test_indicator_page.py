"""
Проверки страницы показателя на синтетическом складе: ряды основного набора и ряды вне
него — с двумя разрезами, отрицательными значениями и малым покрытием.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from apps.catalog.indicator import describe_series, series_descriptor
from apps.catalog.models import Series, Territory
from apps.core import showcase
from apps.warehouse.queries import featured_set, series_ranked_panel
from tests.support.synthetic import (
    LABOUR_FORCE_CODE,
    NATURAL_GROWTH_CODE,
    PARTIAL_COVERAGE_CODE,
    WAGE_CODE,
)

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

WAGE = f"{WAGE_CODE}:00"


def _series(code: str) -> Series:
    """Первый по ключу ряд показателя."""
    return (
        Series.objects.filter(indicator__code=code)
        .select_related("indicator", "unit")
        .order_by("key")
        .first()
    )


def _page(
    client: Client, series: Series, headers: dict[str, str] | None = None, **params: Any
) -> Any:
    """Открыть страницу показателя с выбранным рядом."""
    return client.get(
        reverse("catalog:series-detail", kwargs={"slug": series.indicator.slug}),
        {"series": series.key, **params},
        headers=headers or {},
    )


class TestDescribeSeries:
    """Описание ряда: из основного набора или из источника."""

    def test_featured_series_keeps_manual_description(self, warehouse: Any) -> None:
        """Ряд набора описан вручную — короткое название и проверенная направленность."""
        item = describe_series(_series(WAGE_CODE))
        assert item is featured_set().by_key()[WAGE]
        assert item.polarity == "positive"

    def test_other_series_are_not_assessed(self, warehouse: Any) -> None:
        """Вне набора направленность не оценивается: эвристике склада не верим."""
        series = _series(LABOUR_FORCE_CODE)
        item = describe_series(series)
        assert item.key not in featured_set().by_key()
        assert item.polarity == "neutral"
        assert not item.theme
        # Название — показатель с разрезом: в карточке региона без разреза оно неоднозначно.
        assert item.short_title_ru.startswith(series.indicator.name_ru)
        assert series.name_ru in item.short_title_ru
        # Численность рабочей силы — абсолютная величина: у страны это сумма.
        assert item.absolute

    def test_unit_comes_from_the_warehouse(self, warehouse: Any) -> None:
        """Единица — из склада, где у каждого ряда своя восстановленная единица."""
        item = describe_series(_series(LABOUR_FORCE_CODE))
        assert item.unit_label_ru == "тыс. чел."

    def test_unknown_key_has_no_descriptor(self, warehouse: Any) -> None:
        """Неизвестный ряд не описывается — адрес кадров ответит «не найдено»."""
        assert series_descriptor("Y000000000:00") is None
        assert series_descriptor("") is None


class TestFrameLeaders:
    """Пять наибольших и пять наименьших в каждом кадре живой карты."""

    def test_leaders_follow_the_values(self, warehouse: Any) -> None:
        """Перечни — это начало и конец рейтинга года, по значению."""
        item = featured_set().by_key()[WAGE]
        frames = showcase.live_frames(item)
        year = frames["years"][-1]
        frame = frames["frames"][str(year)]
        values = {
            row["territory_code"]: row["value"]
            for row in series_ranked_panel(item.key)
            if row["year"] == year
        }
        ranked = sorted(values, key=lambda code: values[code])
        top = [frames["codes"][index] for index, _share in frame["top"]]
        bottom = [frames["codes"][index] for index, _share in frame["bottom"]]
        assert top == list(reversed(ranked[-showcase.FRAME_LEADERS :]))
        assert bottom == ranked[: showcase.FRAME_LEADERS]
        # Полоска первого в перечне «больше всего» — во всю длину.
        assert frame["top"][0][1] == "1.000"

    def test_no_bars_when_values_cross_zero(self, warehouse: Any) -> None:
        """У естественного прироста доля от наибольшего смысла не имеет — полосок нет."""
        item = describe_series(_series(NATURAL_GROWTH_CODE))
        frames = showcase.live_frames(item)
        frame = frames["frames"][str(frames["years"][-1])]
        assert all(share == "" for _index, share in frame["top"] + frame["bottom"])

    def test_endpoint_serves_any_series(self, client: Client, warehouse: Any) -> None:
        """Кадры отдаются и по ряду вне основного набора; по неизвестному — нет."""
        series = _series(LABOUR_FORCE_CODE)
        response = client.get(reverse("core:home-frames"), {"series": series.key})
        assert response.status_code == 200
        assert json.loads(response.content)["frames"]
        missing = client.get(reverse("core:home-frames"), {"series": "Y000000000:00"})
        assert missing.status_code == 404


class TestIndicatorPage:
    """Страница показателя целиком."""

    def test_featured_page_answers_in_words(self, client: Client, warehouse: Any) -> None:
        """Короткое название, главное словами, плитка «Россия» и живая карта."""
        series = _series(WAGE_CODE)
        response = _page(client, series)
        assert response.status_code == 200
        context = response.context
        item = featured_set().by_key()[WAGE]
        assert context["heading"] == item.short_title
        assert context["source_title"] == series.full_title

        live = context["live"]
        lead = context["lead"]
        assert live is not None and lead is not None
        # Шапка и карта называют одни и те же крайние регионы.
        assert lead.highest.name == live["top"][0]["name"]
        assert lead.lowest.name == live["bottom"][0]["name"]
        assert lead.gap.startswith("в ")
        assert context["country"] is not None

        content = response.content.decode("utf-8")
        assert 'id="indicator-map"' in content
        assert 'id="region-card"' in content
        # Путь к странице идёт через тему основного набора.
        theme = featured_set().theme(item.theme)
        assert theme is not None
        assert theme.title in [crumb.title for crumb in context["breadcrumbs"]]

    def test_year_from_address_drives_the_lead(self, client: Client, warehouse: Any) -> None:
        """Год из адреса открывает карту и главное словами за этот год."""
        series = _series(WAGE_CODE)
        years = showcase.live_frames(featured_set().by_key()[WAGE])["years"]
        response = _page(client, series, year=years[0])
        assert response.context["live"]["year"] == years[0]
        assert response.context["lead"].year == years[0]

    def test_other_series_are_described_without_assessment(
        self, client: Client, warehouse: Any
    ) -> None:
        """Вне набора положение — «выше» и «ниже», а не «лучше» и «хуже»."""
        response = _page(client, _series(LABOUR_FORCE_CODE))
        assert response.status_code == 200
        frame = response.context["live"]["frame"]
        standings = " ".join(text for text in frame["standings"] if text)
        assert "лучш" not in standings
        assert "худш" not in standings
        assert response.context["theme"] is None

    def test_variants_are_offered(self, client: Client, warehouse: Any) -> None:
        """У показателя с разрезами под шапкой — выбор разреза, выбранный отмечен."""
        series = _series(LABOUR_FORCE_CODE)
        response = _page(client, series)
        assert len(response.context["variants"]) == 2
        content = response.content.decode("utf-8")
        assert f'href="?series={series.key}"' in content
        assert 'aria-current="true"' in content

    def test_variant_switch_returns_the_page_fragment(self, client: Client, warehouse: Any) -> None:
        """Смена разреза заменяет страницу под шапкой сайта, а не весь документ."""
        response = _page(client, _series(LABOUR_FORCE_CODE), headers={"HX-Request": "true"})
        content = response.content.decode("utf-8")
        assert "<!DOCTYPE html>" not in content
        assert "indicator-head" in content
        assert "app-header" not in content

    def test_sparse_series_lists_extremes_without_map(self, client: Client, warehouse: Any) -> None:
        """Двадцать субъектов — меньше порога ранжирования: карты нет, крайние — перечнем."""
        response = _page(client, _series(PARTIAL_COVERAGE_CODE))
        assert response.status_code == 200
        assert response.context["live"] is None
        assert response.context["fallback_top"]
        assert response.context["fallback_bottom"]

    def test_breaks_are_explained_under_the_chart(self, client: Client, warehouse: Any) -> None:
        """Разрыв сопоставимости назван словами под графиком, куда ведёт ссылка из шапки."""
        from apps.catalog.models import Indicator

        indicator = Indicator.objects.get(code=warehouse.break_code)
        response = client.get(reverse("catalog:series-detail", kwargs={"slug": indicator.slug}))
        assert response.context["breaks"]
        content = response.content.decode("utf-8")
        assert 'id="indicator-breaks"' in content


class TestTerritoryCardForAnySeries:
    """Карточка региона со страницы показателя."""

    def test_opened_series_goes_first_outside_the_live_map(
        self, client: Client, warehouse: Any
    ) -> None:
        """Ряд вне пяти рядов витрины всё равно стоит в карточке первым."""
        series = _series(LABOUR_FORCE_CODE)
        territory = Territory.objects.get(code="RU-TA")
        response = client.get(
            reverse("catalog:territory-card", kwargs={"slug": territory.slug}),
            {"series": series.key},
        )
        assert response.status_code == 200
        positions = response.context["positions"]
        assert positions[0].series.key == series.key
        assert response.context["chosen"] == series.key
