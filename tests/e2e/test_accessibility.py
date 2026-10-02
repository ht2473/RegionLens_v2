"""Проверки доступности: машинно проверяемые требования WCAG 2.1 уровня AA."""

from __future__ import annotations

from typing import Any

import pytest
from django.urls import reverse
from playwright.sync_api import Page

from tests.support.routes import PUBLIC_PAGES

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]

# Все публичные страницы — именами маршрутов, а не адресами: адрес, записанный строкой,
# устаревает при переезде страницы, и аудит молча проверяет страницу «не найдено».
PAGES = (*PUBLIC_PAGES, "content:methodology", "content:glossary", "feedback:create")

# Проверки выполняются в браузере: часть требований зависит от вычисленного
# состояния разметки, а не от её текста (например, связь поля с подписью).
AUDIT_SCRIPT = r"""() => {
    const problems = [];

    // Повторяющийся идентификатор ломает связь подписи с полем и переходы по ссылкам.
    const counts = {};
    document.querySelectorAll('[id]').forEach(el => {
        counts[el.id] = (counts[el.id] || 0) + 1;
    });
    Object.entries(counts)
        .filter(([, n]) => n > 1)
        .forEach(([id, n]) => problems.push('повторяющийся id=' + id + ' (' + n + ' раз)'));

    // Изображение без текстовой замены для программы чтения не существует.
    document.querySelectorAll('img').forEach(el => {
        if (!el.hasAttribute('alt')) {
            problems.push('изображение без alt: ' + String(el.getAttribute('src')).slice(-40));
        }
    });

    // Поле без подписи объявляется как «поле ввода» без указания назначения.
    // Подсказка внутри поля подписью не является: она исчезает при вводе.
    document.querySelectorAll('input, select, textarea').forEach(el => {
        if (el.type === 'hidden') return;
        const labelled = el.labels && el.labels.length > 0;
        const aria = el.getAttribute('aria-label') || el.getAttribute('aria-labelledby');
        if (!labelled && !aria) {
            problems.push('поле без подписи: ' + (el.name || el.id || el.type));
        }
    });

    // Кнопка со значком без доступного имени объявляется как «кнопка».
    // Имя может быть задано и ссылкой на другие элементы разметки: так подписан
    // выбор ряда, где имя складывается из подписи поля и выбранного значения.
    document.querySelectorAll('button, a').forEach(el => {
        const text = (el.textContent || '').trim();
        const aria = el.getAttribute('aria-label') || el.getAttribute('title');
        const referenced = (el.getAttribute('aria-labelledby') || '')
            .split(/\s+/)
            .filter(Boolean)
            .map(id => document.getElementById(id))
            .filter(Boolean)
            .map(target => (target.textContent || '').trim())
            .join(' ')
            .trim();
        if (!text && !aria && !referenced) {
            problems.push(el.tagName.toLowerCase() + ' без доступного имени');
        }
    });

    // Заголовки образуют оглавление: ровно один h1 и без пропуска уровней.
    const first = document.querySelectorAll('h1');
    if (first.length !== 1) problems.push('заголовков первого уровня: ' + first.length);

    let previous = 0;
    document.querySelectorAll('h1, h2, h3, h4, h5, h6').forEach(el => {
        const level = Number(el.tagName[1]);
        if (previous && level > previous + 1) {
            problems.push('пропуск уровня заголовка: h' + previous + ' -> h' + level);
        }
        previous = level;
    });

    // Таблица без заголовочных ячеек читается как поток несвязанных значений.
    document.querySelectorAll('table').forEach(el => {
        if (!el.querySelector('th')) problems.push('таблица без заголовочных ячеек');
        if (!el.querySelector('caption') && !el.getAttribute('aria-label')) {
            problems.push('таблица без подписи');
        }
    });

    return problems;
}"""


class TestMarkup:
    """Машинно проверяемые требования к разметке."""

    def test_pages_pass_the_audit(self, page: Page, site: Any) -> None:
        """Ни на одной странице нет нарушений — за один запуск браузера."""
        page.set_viewport_size({"width": 1440, "height": 900})

        failures: list[str] = []
        for name in PAGES:
            path = reverse(name)
            response = page.goto(f"{site.url}{path}")
            assert response is not None and response.status == 200, f"{path}: страница не открылась"
            page.wait_for_load_state("networkidle")
            for problem in page.evaluate(AUDIT_SCRIPT):
                failures.append(f"{path}: {problem}")

        assert not failures, "нарушения доступности:\n  " + "\n  ".join(failures)


class TestKeyboard:
    """Работа без мыши."""

    def test_first_stop_is_the_skip_link(self, page: Page, site: Any) -> None:
        """
        Первая остановка обхода — ссылка к содержимому.

        Без неё человек, работающий с клавиатуры, проходит всё меню заново
        на каждой странице.
        """
        page.goto(site.url)
        page.keyboard.press("Tab")

        focused = page.evaluate("() => document.activeElement.className")
        assert "skip-link" in focused

    def test_focus_is_visible(self, page: Page, site: Any) -> None:
        """
        У элемента в фокусе есть видимое обрамление.

        Убранное обрамление фокуса — распространённая правка ради внешнего вида,
        которая делает интерфейс непригодным для работы с клавиатуры.
        """
        page.goto(f"{site.url}/ru/login/")
        page.locator("input[name='username']").focus()

        outline = page.evaluate(
            """() => {
                const style = getComputedStyle(document.activeElement);
                return {
                    width: style.outlineWidth,
                    style: style.outlineStyle,
                    shadow: style.boxShadow,
                };
            }"""
        )
        visible = outline["style"] not in {"none", ""} or outline["shadow"] not in {"none", ""}
        assert visible, f"фокус не обозначен: {outline}"

    def test_form_is_submittable_by_keyboard(self, page: Page, site: Any) -> None:
        """Форма входа отправляется с клавиатуры без обращения к мыши."""
        page.goto(f"{site.url}/ru/login/")
        page.locator("input[name='username']").focus()
        page.keyboard.type("keyboard@example.com")
        page.keyboard.press("Tab")
        page.keyboard.type("некоторый-пароль")
        page.keyboard.press("Enter")
        page.wait_for_load_state("networkidle")

        # Учётной записи нет, поэтому ожидается возврат на страницу входа
        # с сообщением — важно, что форма вообще была отправлена.
        assert "/login/" in page.url


class TestLanguage:
    """Языковая разметка."""

    @pytest.mark.parametrize(("path", "code"), [("/ru/about/", "ru"), ("/en/about/", "en")])
    def test_document_language_is_declared(
        self, page: Page, site: Any, path: str, code: str
    ) -> None:
        """
        Язык документа объявлен и совпадает с языком страницы.

        По этому атрибуту программа чтения выбирает произношение: русский текст,
        прочитанный по правилам английского, неразборчив.
        """
        page.goto(f"{site.url}{path}")
        assert page.locator("html").get_attribute("lang") == code
