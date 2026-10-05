"""
Представление своих данных в браузере: «На доску» с холста, доска с карточками видов,
закрытая ссылка и её читатель на телефоне, картинки карты и графиков; разметка — под
аудитом доступности, переполнения нет.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from django.core.cache import cache
from playwright.sync_api import Browser, Page, expect

from tests.e2e.test_accessibility import AUDIT_SCRIPT
from tests.e2e.test_cabinet_journeys import sign_in
from tests.e2e.test_responsive import OVERFLOW_SCRIPT
from tests.e2e.test_userdata_journey import _walk

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]


@pytest.fixture
def own_data(settings: Any, tmp_path: Path, warehouse_committed: Path, site: Any) -> Iterator[Any]:
    """Каталог таблиц на время сценария; сервер — с собранным складом."""
    settings.USERDATA_DIR = tmp_path / "userdata"
    cache.clear()
    yield site
    shutil.rmtree(settings.USERDATA_DIR, ignore_errors=True)


def _audit(page: Page, problems: list[str]) -> None:
    page.wait_for_load_state("networkidle")
    problems.extend(f"{page.url}: {item}" for item in page.evaluate(AUDIT_SCRIPT))
    overflow = page.evaluate(OVERFLOW_SCRIPT)
    if overflow["overflow"] > 0:
        problems.append(f"{page.url}: переполнение {overflow}")


def _board_with_link(page: Page, base: str) -> str:
    """Таблица → карта → «На доску» новой доской → карточка → ссылка на доску; вернуть ссылку."""
    _walk(page, base)
    page.locator("button[popovertarget=save-query]").click()
    page.locator("#save-query-board").select_option("new")
    page.fill("#save-query-board-title", "К курсовой")
    page.locator(".save-query__board button[name=open][value='1']").click()
    page.wait_for_url("**/own-data/boards/**")
    expect(page.locator(".board-card__body")).to_have_count(1)
    expect(page.locator(".board-card__body .geo-map, .board-card__body .tile-map")).to_have_count(1)
    page.fill("#board-new-text", "Вывод к карте")
    page.locator("form.board-text-form button[type=submit]").first.click()
    page.wait_for_load_state("networkidle")
    expect(page.locator(".board-text__body")).to_contain_text("Вывод к карте")
    page.locator("#own-shares form.own-share-form button[type=submit]").click()
    page.wait_for_load_state("networkidle")
    return page.locator("#own-share-url").input_value()


def test_board_and_link_desktop(
    page: Page, own_data: Any, registered_user: Any, browser: Browser
) -> None:
    page.set_viewport_size({"width": 1440, "height": 900})
    sign_in(page, own_data, registered_user.email)
    problems: list[str] = []
    link = _board_with_link(page, own_data.url)
    _audit(page, problems)
    # Картинка карты с подписью — файлами PNG и SVG.
    card = page.locator(".board-card__body").first
    card.hover()
    for kind in ("png", "svg"):
        with page.expect_download() as download:
            card.locator(".chart-save", has_text=kind.upper()).first.click()
        assert download.value.suggested_filename.endswith(f".{kind}")
    reader = browser.new_context(viewport={"width": 1440, "height": 900})
    try:
        other = reader.new_page()
        other.goto(link)
        expect(other.locator(".page-intro__kicker")).to_contain_text("открыто по закрытой ссылке")
        expect(other.locator(".board-card__body")).to_have_count(1)
        assert other.locator("form[action$='/edit/']").count() == 0
        _audit(other, problems)
    finally:
        reader.close()
    assert not problems, "\n".join(problems)


def test_board_link_phone(
    page: Page, own_data: Any, registered_user: Any, browser: Browser
) -> None:
    page.set_viewport_size({"width": 1440, "height": 900})
    sign_in(page, own_data, registered_user.email)
    link = _board_with_link(page, own_data.url)
    context = browser.new_context(
        viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True
    )
    phone = context.new_page()
    session = context.new_cdp_session(phone)
    session.send(
        "Emulation.setEmulatedMedia",
        {"features": [{"name": "hover", "value": "none"}, {"name": "pointer", "value": "coarse"}]},
    )
    problems: list[str] = []
    try:
        phone.goto(link)
        expect(phone.locator(".board-card__body")).to_have_count(1)
        _audit(phone, problems)
        # Карточки — друг под другом: ширина карточки — почти вся ширина экрана.
        width = phone.locator(".board-card").first.evaluate("e => e.getBoundingClientRect().width")
        assert width > 340
        phone.locator(".board-card__open").first.click()
        phone.wait_for_url("**/map/**")
        expect(phone.locator(".surface-head__private")).to_have_text("открыто по закрытой ссылке")
        _audit(phone, problems)
    finally:
        context.close()
    assert not problems, "\n".join(problems)


def test_chart_image_on_canvas(page: Page, own_data: Any) -> None:
    page.set_viewport_size({"width": 1440, "height": 900})
    _walk(page, own_data.url)
    page.goto(page.url.replace("/map/", "/compare/"))
    page.wait_for_load_state("networkidle")
    chart = page.locator("rl-chart").first
    chart.scroll_into_view_if_needed()
    chart.hover()
    group = page.locator(".chart-save-group").first
    expect(group).to_be_visible()
    for kind in ("png", "svg"):
        with page.expect_download() as download:
            group.locator(".chart-save", has_text=kind.upper()).click()
        name = download.value.suggested_filename
        assert name.startswith("RegionLens — ")
        assert name.endswith(f".{kind}")
        if kind == "svg":
            text = Path(download.value.path()).read_text(encoding="utf-8")
            assert "загружены пользователем" in text
