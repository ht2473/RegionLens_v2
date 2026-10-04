"""
Проверки мер скорости: перечень рядов отдельным кэшируемым ответом, кэш выборок склада
до пересборки, меню шапки по нажатию, переходы между страницами.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from apps.catalog.selectors import series_options_version
from apps.warehouse import duckdb_client
from tests.support.synthetic import GRP_PER_CAPITA_CODE

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------------------
# Перечень рядов отдельным ответом
# ---------------------------------------------------------------------------------------


def test_page_carries_only_selected_series(client: Client, warehouse: Any) -> None:
    """Страница карты несёт один выбранный ряд и адрес полного перечня."""
    body = client.get(reverse("maps:choropleth")).content.decode()
    # Адрес перечня объявлен на элементе выбора с поиском, обёртке списка.
    choice = re.search(r"<rl-combobox[^>]*>.*?</rl-combobox>", body, re.S)

    assert choice is not None
    assert choice.group(0).count("<option") == 1
    assert 'id="series-select"' in choice.group(0)
    assert "data-source=" in choice.group(0)
    assert duckdb_client.warehouse_generation() in choice.group(0)
    # Без сценариев перечень раскрывается ссылкой.
    assert "series_list=1" in body


def test_full_list_without_scripts(client: Client, warehouse: Any) -> None:
    """Со ссылкой из noscript страница открывается с полным перечнем."""
    body = client.get(reverse("maps:choropleth"), {"series_list": "1"}).content.decode()
    select = re.search(r'<select[^>]*id="series-select".*?</select>', body, re.S)

    assert select is not None
    assert select.group(0).count("<option") > 1
    assert select.group(0).count(" selected") == 1


def test_options_response_is_immutable(client: Client, warehouse: Any) -> None:
    """Перечень с действующим отпечатком хранится браузером год, с устаревшим — нет."""
    url = reverse("catalog:series-options")
    fresh = client.get(url, {"v": series_options_version()})
    stale = client.get(url, {"v": "old"})

    assert fresh.status_code == 200
    assert "immutable" in fresh["Cache-Control"]
    assert "max-age=31536000" in fresh["Cache-Control"]
    assert "no-cache" in stale["Cache-Control"]
    assert fresh.content.decode().startswith("<optgroup")
    assert fresh.content == stale.content


def test_options_follow_language(client: Client, warehouse: Any) -> None:
    """Английская страница получает перечень по английскому адресу."""
    from django.utils import translation

    with translation.override("en"):
        url = reverse("catalog:series-options")
    assert url.startswith("/en/")
    assert client.get(url).status_code == 200


# ---------------------------------------------------------------------------------------
# Кэш выборок по отпечатку склада
# ---------------------------------------------------------------------------------------


@pytest.fixture
def warehouse_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Перечень запросов к складу, выполненных выборками рядов."""
    from apps.warehouse.queries import series as series_queries

    calls: list[str] = []
    original = series_queries.fetch_dicts

    def counting(sql: str, params: Any = None) -> Any:
        calls.append(sql)
        return original(sql, params)

    monkeypatch.setattr(series_queries, "fetch_dicts", counting)
    return calls


def test_queries_are_cached_until_rebuild(
    warehouse: Any, warehouse_calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Повторная выборка не обращается к складу, пока файл склада тот же."""
    from apps.warehouse.queries import common, series_values_by_territory

    key = f"{GRP_PER_CAPITA_CODE}:00"
    first = series_values_by_territory(key, 2020)
    assert series_values_by_territory(key, 2020) == first
    assert len(warehouse_calls) == 1

    # Другой отпечаток склада — другой ключ: выборка выполняется заново.
    monkeypatch.setattr(common, "source_generation", lambda: "rebuilt")
    series_values_by_territory(key, 2020)
    assert len(warehouse_calls) == 2


def test_home_reuses_warehouse_queries(
    client: Client, warehouse: Any, warehouse_calls: list[str]
) -> None:
    """Главная во второй раз не обращается к складу за ключевыми показателями."""
    assert client.get(reverse("core:home")).status_code == 200
    first = len(warehouse_calls)
    assert client.get(reverse("core:home")).status_code == 200

    assert first > 0
    assert len(warehouse_calls) == first


# ---------------------------------------------------------------------------------------
# Меню шапки
# ---------------------------------------------------------------------------------------


def test_section_menu_is_a_button(client: Client, db: None, reference_seed: None) -> None:
    """Раздел со вложенными пунктами — кнопка, а обзор раздела — первая строка панели."""
    body = client.get(reverse("core:home")).content.decode()

    assert re.search(r'<button type="button" class="main-nav__link[^"]*"\s+popovertarget=', body)
    assert "data-menu-hover" not in body
    # Панель раздела — всплывающий слой браузера, а не блок с атрибутом hidden.
    assert re.search(r'class="menu-popover main-nav__dropdown" popover', body)
    assert f'class="main-nav__overview" href="{reverse("analytics:index")}"' in body


# ---------------------------------------------------------------------------------------
# Смена страниц
# ---------------------------------------------------------------------------------------


def test_view_transitions_are_declared() -> None:
    """Смена страниц объявлена средствами браузера и уважает уменьшение анимации."""
    from django.conf import settings

    css = (settings.BASE_DIR / "static" / "css" / "base.css").read_text(encoding="utf-8")
    block = css[css.index("@media (prefers-reduced-motion: no-preference)") :]
    assert "@view-transition" in block[: block.index("}\n}")]
