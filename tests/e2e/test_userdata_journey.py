"""
Свои данные целиком в браузере: файл → «Что в таблице» → «Показатели» → карта → страница
таблицы; на 1440 и 390 точках, по-английски; разметка каждого шага — под аудитом доступности.
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
    page.wait_for_url("**/map/**")
    audit()
    return problems


def test_journey_desktop(page: Page, own_data: Any) -> None:
    page.set_viewport_size({"width": 1440, "height": 900})
    problems = _walk(page, own_data.url)
    expect(page.locator("h1")).to_contain_text(TITLE)
    expect(page.locator(".surface-head__private")).to_have_text("видно только вам")
    page.locator(".surface-head__about a").click()
    # Ссылка ведёт к ряду на странице таблицы: адрес с якорем.
    page.wait_for_url(re.compile(r"/own-data/[^/]+/#series-"))
    problems.extend(page.evaluate(AUDIT_SCRIPT))
    expect(page.locator(".own-series__item").first).to_be_visible()
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
        expect(page.locator("h1")).to_contain_text(TITLE)
        assert not problems, "\n".join(problems)
    finally:
        context.close()


def test_journey_english(page: Page, own_data: Any) -> None:
    page.set_viewport_size({"width": 1440, "height": 900})
    problems = _walk(page, own_data.url, "en")
    expect(page.locator(".surface-head__private")).to_have_text("visible only to you")
    assert page.locator("h1 [lang=ru]").count() == 1
    assert not problems, "\n".join(problems)
