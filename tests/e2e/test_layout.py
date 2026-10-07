"""Проверка раскладки на 1440 и 390 точках и бюджет страницы: правила — tests/support/layout.py."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import pytest
from django.urls import reverse
from playwright.sync_api import Page

from tests.support.layout import audit
from tests.support.routes import PUBLIC_PAGES

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]

PAGES = (*PUBLIC_PAGES, "content:methodology", "content:glossary", "feedback:create")

# Бюджет страницы: запросы сценариев (больше всего — у поверхности с графиками) и узлы DOM.
MAX_SCRIPTS = 24
MAX_NODES = 5000

# Ответы поиска с самой широкой разметкой: два столбца рейтинга и таблица сравнения.
SEARCHES = ("где самые высокие зарплаты", "сравнить Москву и Санкт-Петербург")


def page_paths() -> list[str]:
    """Адреса разделов и страниц отдельных записей с настоящей длиной названий."""
    from apps.catalog.models import Series, Territory

    paths = [reverse(route) for route in PAGES]
    paths += [reverse("search:results") + "?" + urlencode({"q": query}) for query in SEARCHES]
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


@pytest.mark.parametrize("width", [1440, 390])
def test_layout_rules_and_budget(
    page: Page, site: Any, warehouse_committed: Any, width: int
) -> None:
    """Ни одна страница не нарушает правил раскладки и не выходит из бюджета."""
    page.set_viewport_size({"width": width, "height": 900})
    failures: list[str] = []
    for path in page_paths():
        page.goto(f"{site.url}{path}")
        page.wait_for_load_state("networkidle")
        assert page.title(), path
        result = audit(page)
        failures += [f"{path}: {problem}" for problem in result["problems"]]
        metrics = result["metrics"]
        if metrics["scripts"] > MAX_SCRIPTS:
            failures.append(f"{path}: сценариев {metrics['scripts']} > {MAX_SCRIPTS}")
        if metrics["nodes"] > MAX_NODES:
            failures.append(f"{path}: узлов DOM {metrics['nodes']} > {MAX_NODES}")

    assert not failures, "нарушения раскладки:\n  " + "\n  ".join(failures)
