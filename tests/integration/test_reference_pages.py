"""
Справочные страницы: перечень регионов, полный перечень рядов, обзор анализа,
«О наборе данных» и окна шапки (popover и dialog).
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import translation

from apps.api.docs import rate_in_words
from apps.catalog.views.catalog import headcount
from apps.catalog.views.dataset import _coverage_bars

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

NBSP = "\u00a0"


class TestHeadcount:
    """Численность населения словами для перечня регионов."""

    def test_millions_and_thousands(self) -> None:
        """С тысячи тысяч — миллионы с одним знаком, меньше — тысячи без знаков."""
        with translation.override("ru"):
            assert headcount(4003.0).replace(NBSP, " ") == "4,0 млн чел."
            assert headcount(527.4).replace(NBSP, " ") == "527 тыс. чел."
            assert headcount(None) == ""


class TestCoverageBars:
    """Полосы покрытия на странице о наборе данных."""

    def test_bands_go_from_full_to_none_with_shares(self) -> None:
        """Полосы — от полного покрытия к отсутствию, доля считается от всех рядов."""
        rows = [
            {"bucket_code": "low", "bucket": "Низкое", "series_count": 83},
            {"bucket_code": "full", "bucket": "Полное", "series_count": 2038},
            {"bucket_code": "none", "bucket": "Нет данных", "series_count": 1},
            {"bucket_code": "high", "bucket": "Высокое", "series_count": 677},
            {"bucket_code": "medium", "bucket": "Среднее", "series_count": 91},
        ]
        with translation.override("ru"):
            bars = _coverage_bars(rows)

        assert [bar["bucket_code"] for bar in bars] == ["full", "high", "medium", "low", "none"]
        assert bars[0]["share"] == pytest.approx(70.5, abs=0.1)
        # Один ряд из трёх тысяч — не «0,0»: доля мала, но она есть.
        assert bars[-1]["share_text"].replace(NBSP, " ") == "< 0,1"
        # Исходные строки не меняются: выборка склада лежит в кэше.
        assert "share" not in rows[0]


class TestRegionList:
    """Перечень регионов — вход через регион."""

    def test_regions_grouped_with_population_and_map(self, client: Client, warehouse: Any) -> None:
        """
        Регионы сгруппированы по округам, у каждого — ссылка на паспорт и численность;
        карта ведёт в паспорта, отбор идёт по мере ввода, кнопки «Показать» нет.
        """
        response = client.get(reverse("catalog:territory-list"))
        assert response.status_code == 200
        body = response.content.decode("utf-8")

        rows = [row for group in response.context["grouped"] for row in group["rows"]]
        assert rows, "перечень пуст"
        assert any(row["people"] for row in rows), "численность не подставилась ни одному региону"
        assert all(group["short_title"] for group in response.context["grouped"])
        assert not any(
            "федеральный округ" in group["short_title"] for group in response.context["grouped"]
        )

        assert 'data-filter-input="#territory-groups"' in body
        assert "Показать</button>" not in body
        # Самые населённые — быстрыми входами, по убыванию численности.
        assert response.context["largest"]

    def test_server_filter_still_works(self, client: Client, warehouse: Any) -> None:
        """Без сценариев и по ссылке отбор выполняет сервер."""
        response = client.get(reverse("catalog:territory-list"), {"q": "zzz-нет-такого"})
        assert response.status_code == 200
        assert response.context["grouped"] == []


class TestFullSeriesList:
    """Полный перечень рядов: отборы применяются сразу, число найденного обновляется."""

    def test_fragment_carries_count_and_order(self, client: Client, warehouse: Any) -> None:
        """Ответ на отбор несёт и перечень, и строку с числом найденного над ним."""
        response = client.get(
            reverse("catalog:indicator-list"),
            {"view": "all", "ready": "1"},
            headers={"HX-Request": "true"},
        )
        body = response.content.decode("utf-8")
        assert 'class="catalog-all__count"' in body
        assert 'id="catalog-sort"' in body
        assert "Показать" not in body


class TestAnalyticsOverview:
    """Обзор раздела «Анализ»: инструмент — карточка вокруг вопроса."""

    def test_every_tool_is_a_question_link(self, client: Client, warehouse: Any) -> None:
        """Вопрос каждого инструмента ведёт на его страницу, продолжение разбора — ссылкой."""
        response = client.get(reverse("analytics:index"))
        body = response.content.decode("utf-8")
        for tool in response.context["tools"]:
            assert f'class="tool-card__question" href="{tool.url}"' in body
        assert f'href="{reverse("analytics:convergence")}"' in body


class TestHeaderWindows:
    """Окна шапки — средства браузера: popover у разделов и меню, dialog у палитры."""

    def test_header_uses_browser_windows(
        self, client: Client, db: None, reference_seed: None
    ) -> None:
        """Своего слоя меню (data-menu-*) в разметке больше нет."""
        body = client.get(reverse("core:home")).content.decode("utf-8")

        assert re.search(r'<dialog class="palette-dialog" id="palette"', body)
        assert re.search(r'<dialog class="mobile-nav" id="mobile-nav"', body)
        assert 'data-dialog-open="palette"' in body
        assert 'data-dialog-open="mobile-nav"' in body
        assert "data-menu-trigger" not in body
        assert "data-menu-lock" not in body
        # Закрытие выдвижного меню — обычная кнопка, а не кнопка отправки: иначе она
        # стала бы первой кнопкой отправки на каждой странице.
        assert 'form method="dialog"' not in body

    def test_signed_in_menu_is_a_popover(self, client: Client, make_user: Any) -> None:
        """Меню учётной записи — панель popover, привязанная к окну (шапка закреплена)."""
        client.force_login(make_user())
        body = client.get(reverse("core:home")).content.decode("utf-8")
        assert 'popovertarget="user-menu-panel"' in body
        assert re.search(r'id="user-menu-panel" popover data-anchor-fixed', body)


class TestApiDocs:
    """Страница программного интерфейса: без обещаний удалённого и с пределом словами."""

    def test_rate_in_words(self) -> None:
        """«1000/hour» из настроек читается словами; непонятная запись остаётся как есть."""
        with translation.override("ru"):
            assert rate_in_words("1000/hour").replace(NBSP, " ") == "1 000 обращений в час"
            assert rate_in_words("60/min").replace(NBSP, " ") == "60 обращений в минуту"
            assert rate_in_words("много") == "много"

    def test_page_promises_nothing_removed(self, client: Client, db: None) -> None:
        """Описание API не обещает ключей и точки me/: их нет."""
        body = client.get(reverse("api:docs")).content.decode("utf-8")
        assert "Bearer" not in body
        assert "/me/" not in body
        assert "1000/hour" not in body
