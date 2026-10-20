"""
Обход действий: на основных страницах каждое действие — кнопка, ссылка с запросом фрагмента,
раскрываемый блок, вкладка — меняет то, что видно на экране, и не роняет страницу.

Снимок страницы в покое этого не ловит: результат появился ниже края окна, и нажатие
кажется пустым («Разобрать» в корреляции), ответ сервера — ошибка, сценарий упал.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import pytest
from django.urls import reverse
from playwright.sync_api import Page

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]

# Действий страницы в обходе — первые по порядку, по одному на подпись.
MAX_ACTIONS = 24

# Сколько ждать отклика после нажатия, мс: запрос фрагмента и его вставка.
SETTLE_MS = 700

# Действия на месте. Переходы по обычным ссылкам видны и так; сохранение картинкой,
# печать, копирование, отправка форм с записью и выход в обход не входят.
# Форма и поля с hx-get отвечают на изменение, а не на щелчок.
ACTIONS = (
    "main button, main [hx-get]:not(form, input, select, textarea), main summary, "
    "main [role=tab], main a[href^='#']"
)
SKIPPED = (
    ".chart-save, [data-print], [data-copy], [data-select-all], [download], "
    "[disabled], [aria-disabled=true], form[method=post] *, form[method=POST] *"
)

# Отпечаток видимого: адрес, прокрутка, тексты и места видимых элементов окна и состояния
# раскрытия. Фокус не в счёт: кнопка, которая только получила фокус, ничего не сделала.
VIEW_SCRIPT = r"""() => {
    const seen = [];
    for (const el of document.querySelectorAll('body *')) {
        const leaf = !el.children.length || ['BUTTON', 'A', 'SUMMARY'].includes(el.tagName);
        if (!leaf) continue;
        const r = el.getBoundingClientRect();
        if (r.width < 1 || r.height < 1 || r.bottom < 0 || r.top > innerHeight) continue;
        if (getComputedStyle(el).visibility === 'hidden') continue;
        const text = (el.textContent || '').trim().slice(0, 40);
        seen.push(`${el.tagName}:${text}:${Math.round(r.top)}`);
    }
    const states = [...document.querySelectorAll(
        '[aria-expanded], [aria-pressed], [aria-selected], [aria-current], '
        + 'details, dialog, [popover]'
    )].map((el) => [
        el.getAttribute('aria-expanded'), el.getAttribute('aria-pressed'),
        el.getAttribute('aria-selected'), el.getAttribute('aria-current'),
        el.open ?? '', el.matches(':popover-open'),
    ].join());
    return JSON.stringify([location.href, Math.round(scrollY), seen, states]);
}"""

# Действия страницы: селектор-номер и подпись, по одной на подпись.
LIST_SCRIPT = r"""([actions, skipped, limit]) => {
    const found = [];
    const labels = new Set();
    [...document.querySelectorAll(actions)].forEach((el, index) => {
        if (found.length >= limit || el.matches(skipped)) return;
        const r = el.getBoundingClientRect();
        if (r.width < 1 || r.height < 1 || getComputedStyle(el).visibility === 'hidden') return;
        const label = (el.getAttribute('aria-label') || el.textContent || '')
            .replace(/\s+/g, ' ').trim().slice(0, 60);
        const key = `${el.tagName}:${label}`;
        if (!label || labels.has(key)) return;
        labels.add(key);
        found.push({index, label});
    });
    return found;
}"""


def pages_to_walk() -> list[str]:
    """Страницы с действиями на холсте: поверхность, анализ, записи и свои данные."""
    from apps.catalog.models import Series, Territory

    series = Series.objects.select_related("indicator").order_by("key")
    keys = [item.key for item in series[:3]]
    paths = [reverse("core:home"), reverse("userdata:index")]
    if keys:
        one = urlencode({"series": keys[0]})
        paths += [
            f"{reverse('rankings:index')}?{one}",
            f"{reverse('surface:table')}?{one}",
            f"{reverse('analytics:inequality')}?{one}",
            f"{reverse('catalog:series-detail', kwargs={'slug': series[0].indicator.slug})}?{one}",
        ]
    if len(keys) >= 3:
        paths.append(
            f"{reverse('analytics:correlation')}?{urlencode([('series', key) for key in keys])}"
        )
    territory = Territory.objects.comparable().order_by("code").first()
    if territory is not None:
        paths.append(reverse("catalog:territory-detail", kwargs={"slug": territory.slug}))
    return paths


def walk(page: Page, url: str) -> list[str]:
    """Нажать каждое действие страницы на свежей странице; вернуть те, что ничего не дали."""
    failures: list[str] = []
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)[:120]))
    page.on(
        "response",
        lambda response: (
            errors.append(f"{response.status} {response.url}") if response.status >= 500 else None
        ),
    )
    page.goto(url)
    page.wait_for_load_state("networkidle")
    actions = page.evaluate(LIST_SCRIPT, [ACTIONS, SKIPPED, MAX_ACTIONS])
    for action in actions:
        page.goto(url)
        page.wait_for_load_state("networkidle")
        target = page.locator(ACTIONS).nth(action["index"])
        if not target.is_visible():
            continue
        target.scroll_into_view_if_needed()
        before = page.evaluate(VIEW_SCRIPT)
        errors.clear()
        target.click()
        page.wait_for_timeout(SETTLE_MS)
        page.wait_for_load_state("networkidle")
        if errors:
            failures.append(f"«{action['label']}»: {'; '.join(errors)}")
        elif page.evaluate(VIEW_SCRIPT) == before:
            failures.append(f"«{action['label']}»: на экране ничего не изменилось")
    return failures


@pytest.mark.parametrize("width", [1440, 390])
def test_every_action_gives_visible_feedback(
    page: Page, site: Any, warehouse_committed: Any, width: int
) -> None:
    """Каждое действие основных страниц что-то меняет на экране и обходится без ошибок."""
    page.set_viewport_size({"width": width, "height": 900})
    failures: list[str] = []
    for path in pages_to_walk():
        failures += [f"{path}: {problem}" for problem in walk(page, f"{site.url}{path}")]
    assert not failures, "действия без отклика:\n  " + "\n  ".join(failures)
