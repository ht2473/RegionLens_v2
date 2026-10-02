"""Проверки вёрстки: ни одна страница ни на одной ширине не выходит за правый край экрана."""

from __future__ import annotations

from typing import Any

import pytest
from django.urls import reverse
from playwright.sync_api import Page

from tests.support.routes import PUBLIC_PAGES

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]

# Ширины экрана от 320 до широкого монитора; 1180 — порог выдвижного меню.
WIDTHS = (320, 360, 390, 480, 640, 768, 1024, 1180, 1280, 1366, 1440, 1920)

# Разделы без входа — именами маршрутов из tests/support/routes.py, не адресами.
STATIC_PAGES = PUBLIC_PAGES


def detail_paths() -> list[str]:
    """Собрать адреса страниц отдельных записей — с настоящей длиной названий."""
    from apps.catalog.models import Series, Territory

    paths: list[str] = []
    territory = Territory.objects.comparable().order_by("code").first()
    if territory is not None:
        paths.append(reverse("catalog:territory-detail", kwargs={"slug": territory.slug}))
    series = Series.objects.select_related("indicator").order_by("key").first()
    if series is not None:
        paths.append(
            reverse("catalog:series-detail", kwargs={"slug": series.indicator.slug})
            + f"?series={series.key}"
        )
    return paths


# Скрипт измерения: возвращает величину переполнения и виновников.
OVERFLOW_SCRIPT = """() => {
    const docW = document.documentElement.clientWidth;
    const culprits = [...document.querySelectorAll('body *')]
        .filter(el => el.getBoundingClientRect().right > docW + 1)
        .slice(0, 3)
        .map(el => el.tagName + '.' + (el.className || '').toString().slice(0, 30));
    return {
        overflow: document.documentElement.scrollWidth - docW,
        culprits,
    };
}"""


@pytest.mark.parametrize("width", WIDTHS)
def test_no_horizontal_overflow(page: Page, site: Any, width: int) -> None:
    """Ни одна страница не выходит за правый край экрана — за один запуск браузера."""
    page.set_viewport_size({"width": width, "height": 900})

    paths = [reverse(route) for route in STATIC_PAGES] + detail_paths()

    failures: list[str] = []
    for path in paths:
        page.goto(f"{site.url}{path}")
        page.wait_for_load_state("networkidle")
        # Страница, которой нет, переполнения не даёт: без этой проверки устаревший
        # адрес превращает проверку вёрстки в проверку страницы «не найдено».
        assert page.title(), path
        result = page.evaluate(OVERFLOW_SCRIPT)
        if result["overflow"] > 1:
            failures.append(f"{path}: +{result['overflow']} пикселей, {result['culprits']}")

    assert not failures, "переполнение по горизонтали:\n  " + "\n  ".join(failures)


@pytest.mark.parametrize("width", [390, 768, 1440])
def test_content_area_is_readable(page: Page, site: Any, width: int) -> None:
    """
    Основная область содержимого занимает разумную долю экрана.

    Проверка ловит противоположный дефект: колонка, схлопнувшаяся до нескольких
    десятков пикселей из-за неверно заданной сетки.
    """
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(f"{site.url}/ru/indicators/")
    page.wait_for_load_state("networkidle")

    main_width = page.evaluate(
        "() => document.querySelector('main#main-content').getBoundingClientRect().width"
    )
    assert main_width >= width * 0.6


@pytest.mark.parametrize("width", [320, 390])
def test_signed_in_header_fits_the_phone(
    page: Page, site: Any, registered_user: Any, width: int
) -> None:
    """Шапка вошедшего с длинным именем не выходит за край телефона."""
    page.goto(f"{site.url}/ru/login/")
    page.fill("input[name='username']", registered_user.email)
    page.fill("input[name='password']", "e2e-password-12345")
    page.click("button[type='submit']")
    page.wait_for_load_state("networkidle")

    page.set_viewport_size({"width": width, "height": 800})
    page.goto(f"{site.url}/ru/cabinet/saved/")
    page.wait_for_load_state("networkidle")
    result = page.evaluate(OVERFLOW_SCRIPT)
    assert result["overflow"] <= 1, result["culprits"]
