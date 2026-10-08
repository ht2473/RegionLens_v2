"""
Свои данные целиком в браузере: файл → «Что в таблице» → «Показатели» → исследование
с картой → карта на холсте → страница таблицы; на 1440 и 390 точках, по-английски;
разметка каждого шага — под аудитом доступности.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Browser, Page, expect

from tests.e2e.test_accessibility import AUDIT_SCRIPT
from tests.e2e.test_responsive import OVERFLOW_SCRIPT

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "userdata" / "environment.csv"
TITLE = "Население в городах с высоким"


@pytest.fixture
def own_data(settings: Any, tmp_path: Path, warehouse_committed: Path, site: Any) -> Iterator[Any]:
    """Каталог таблиц на время сценария; сервер — с собранным складом."""
    settings.USERDATA_DIR = tmp_path / "userdata"
    yield site
    shutil.rmtree(settings.USERDATA_DIR, ignore_errors=True)


def _walk(page: Page, base: str, language: str = "ru") -> list[str]:
    """Пройти мастер; вернуть нарушения разметки по шагам."""
    problems: list[str] = []

    def audit() -> None:
        page.wait_for_load_state("networkidle")
        problems.extend(f"{page.url}: {item}" for item in page.evaluate(AUDIT_SCRIPT))
        overflow = page.evaluate(OVERFLOW_SCRIPT)
        if overflow["overflow"] > 0:
            problems.append(f"{page.url}: переполнение {overflow}")

    page.goto(f"{base}/{language}/own-data/new/")
    audit()
    page.set_input_files("input[type=file]", str(FIXTURE))
    page.locator("form[data-upload-form] button[type=submit]").click()
    page.wait_for_url("**/file/")
    audit()
    page.locator("button.button--primary[type=submit]").last.click()
    page.wait_for_url("**/table/")
    audit()
    page.locator("button[name=action][value=next]").click()
    page.wait_for_url("**/series/")
    audit()
    page.locator(".series-form button.button--primary[type=submit]").click()
    page.wait_for_url("**/own-data/studies/**")
    expect(page.locator(".study-card .geo-map, .study-card .tile-map")).to_have_count(1)
    audit()
    return problems


def _open_card(page: Page) -> None:
    """Карточка карты в исследовании → та же карта на холсте."""
    page.locator(".study-card__open").first.click()
    page.wait_for_url("**/map/**")
    page.wait_for_load_state("networkidle")


def test_journey_desktop(page: Page, own_data: Any) -> None:
    page.set_viewport_size({"width": 1440, "height": 900})
    problems = _walk(page, own_data.url)
    expect(page.locator(".study-card__title").first).to_contain_text(TITLE)
    expect(page.locator(".study-page .own-private")).to_contain_text("без входа — хранится до")
    _open_card(page)
    expect(page.locator(".surface-head__private")).to_have_text("видно только вам")
    page.locator(".surface-head__about a").click()
    # Ссылка ведёт к ряду на странице таблицы: он выбран, рядом — его карта.
    page.wait_for_url(re.compile(r"/own-data/[^/]+/\?show="))
    problems.extend(page.evaluate(AUDIT_SCRIPT))
    expect(page.locator(".dataset-preview .geo-map, .dataset-preview .tile-map")).to_be_visible()
    assert not problems, "\n".join(problems)


def test_journey_phone(browser: Browser, own_data: Any) -> None:
    context = browser.new_context(
        viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True
    )
    page = context.new_page()
    session = context.new_cdp_session(page)
    session.send(
        "Emulation.setEmulatedMedia",
        {"features": [{"name": "hover", "value": "none"}, {"name": "pointer", "value": "coarse"}]},
    )
    try:
        problems = _walk(page, own_data.url)
        expect(page.locator(".study-card__title").first).to_contain_text(TITLE)
        assert not problems, "\n".join(problems)
    finally:
        context.close()


def test_journey_english(page: Page, own_data: Any) -> None:
    page.set_viewport_size({"width": 1440, "height": 900})
    problems = _walk(page, own_data.url, "en")
    _open_card(page)
    expect(page.locator(".surface-head__private")).to_have_text("visible only to you")
    assert page.locator("h1 [lang=ru]").count() == 1
    assert not problems, "\n".join(problems)


def _analysis(page: Page, base: str) -> list[str]:
    """
    После сборки: страница таблицы с выбранным рядом, своя формула с показателем нажатием,
    предпросмотр и сохранение, «С чем связан показатель», неравенство по ряду таблицы.
    """
    problems: list[str] = []

    def audit() -> None:
        page.wait_for_load_state("networkidle")
        problems.extend(f"{page.url}: {item}" for item in page.evaluate(AUDIT_SCRIPT))
        overflow = page.evaluate(OVERFLOW_SCRIPT)
        if overflow["overflow"] > 0:
            problems.append(f"{page.url}: переполнение {overflow}")

    _open_card(page)
    page.locator(".surface-head__about a").click()
    page.wait_for_url(re.compile(r"/own-data/[^/]+/"))
    audit()
    expect(page.locator(".dataset-preview").first).to_be_visible()
    table_url = page.url.split("?")[0]
    page.locator("button[popovertarget=dataset-edit]").click()
    page.locator("#dataset-edit a[href$='/formula/']").click()
    page.wait_for_url("**/formula/")
    audit()
    page.fill("#formula-title", "Вдвое")
    page.locator(".segmented__option", has_text="Своя формула").click()
    field = page.locator("#formula-expression")
    field.click()
    page.locator("[data-formula-chip]", has_text=TITLE).first.click()
    expect(field).to_have_value(re.compile(r"^\[.+\]$"))
    field.press("End")
    field.type(" * 2")
    # Предпросмотр приходит сам: карта и сколько субъектов посчитано.
    expect(page.locator("#formula-preview .formula-preview__count")).to_be_visible()
    audit()
    page.locator("button[name=action][value=save]").click()
    page.wait_for_url(re.compile(r"/own-data/[^/]+/\?show="))
    page.goto(table_url + "related/")
    audit()
    expect(page.locator("h1")).to_be_visible()
    page.goto(table_url)
    key = page.locator(".dataset-view").first.get_attribute("href") or ""
    page.goto(f"{base}/ru/analytics/inequality/?{key.split('?', 1)[1]}")
    audit()
    expect(page.locator(".tool-subject__title")).to_contain_text(TITLE)
    return problems


def test_analysis_desktop(page: Page, own_data: Any) -> None:
    page.set_viewport_size({"width": 1440, "height": 900})
    problems = _walk(page, own_data.url)
    problems += _analysis(page, own_data.url)
    assert not problems, "\n".join(problems)


def test_analysis_phone(browser: Browser, own_data: Any) -> None:
    context = browser.new_context(
        viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True
    )
    page = context.new_page()
    try:
        problems = _walk(page, own_data.url)
        problems += _analysis(page, own_data.url)
        assert not problems, "\n".join(problems)
    finally:
        context.close()
