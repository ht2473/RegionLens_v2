"""Проверки доступности: машинно проверяемые требования WCAG 2.1 уровня AA."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import pytest
from django.test import Client
from django.urls import reverse
from playwright.sync_api import Page, expect

from tests.conftest import login_with_code
from tests.support.routes import PUBLIC_PAGES

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]

# Все публичные страницы — именами маршрутов, а не адресами: адрес, записанный строкой,
# устаревает при переезде страницы, и аудит молча проверяет страницу «не найдено».
PAGES = (
    *PUBLIC_PAGES,
    "content:methodology",
    "content:glossary",
    "feedback:create",
)

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

    // График рисуется на холсте, а холст программа чтения не читает: перед ним — абзац
    // с подписью и описанием по данным (RegionChart.describe в static/js/elements/chart.js).
    document.querySelectorAll('rl-chart').forEach(el => {
        const before = el.previousElementSibling;
        const hidden = before && before.classList.contains('visually-hidden');
        const text = hidden ? before.textContent : '';
        if (!text.trim()) {
            problems.push('график без текстовой замены: ' + (el.dataset.options || ''));
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
        // Дубль ссылки, спрятанный от программы чтения и от клавиатуры (миниатюра плитки):
        // его не объявляют, имя ему не нужно.
        if (el.closest('[aria-hidden="true"]') && el.tabIndex < 0) return;
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
        # Страница результатов — и с ответом: чипы «понято как» и группы найденного.
        search = reverse("search:results") + "?" + urlencode({"q": "зарплата в Татарстане"})
        for path in [*(reverse(name) for name in PAGES), search]:
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


# Цель нажатия меньше 24 px допустима, только если круг диаметром 24 px вокруг её
# середины не задевает соседние цели (WCAG 2.5.8). Исключения критерия: ссылка внутри
# предложения, флажок с подписью (подпись — часть цели) и субъект на карте — у каждого
# есть та же ссылка в списке регионов и в поиске.
TARGET_SCRIPT = r"""() => {
    const selector = 'a[href], button, input:not([type=hidden]), select, textarea, summary, '
        + '[role=button], [role=tab], [role=option], [tabindex]:not([tabindex="-1"])';
    const targets = [...document.querySelectorAll(selector)]
        .filter(el => !el.closest('[hidden], [inert], dialog:not([open])'))
        .map(el => ({el, box: el.getBoundingClientRect()}))
        .filter(({el, box}) => box.width > 0 && box.height > 0
            && getComputedStyle(el).visibility !== 'hidden');

    const inSentence = el => {
        if (getComputedStyle(el).display !== 'inline' || !el.parentElement) return false;
        return [...el.parentElement.childNodes]
            .some(node => node !== el && node.textContent.trim());
    };
    const labelled = el => ['checkbox', 'radio'].includes(el.type) && el.labels.length > 0;
    const small = targets.filter(({el, box}) => (box.width < 24 || box.height < 24)
        && !inSentence(el) && !labelled(el) && !el.closest('.geo-map, .tile-map'));

    const centre = box => [(box.left + box.right) / 2, (box.top + box.bottom) / 2];
    const reach = ([x, y], box) => Math.hypot(
        Math.max(box.left - x, 0, x - box.right), Math.max(box.top - y, 0, y - box.bottom));

    return small.filter(({el, box}) => {
        const point = centre(box);
        return targets.some(other => other.el !== el
            && !other.el.contains(el) && !el.contains(other.el)
            && (reach(point, other.box) < 12
                || (small.includes(other)
                    && Math.hypot(...centre(other.box).map((v, i) => v - point[i])) < 24)));
    }).map(({el, box}) => el.tagName.toLowerCase() + '.' + (el.classList[0] || '')
        + ' ' + Math.round(box.width) + 'x' + Math.round(box.height)
        + ' «' + (el.textContent || el.getAttribute('aria-label') || '').trim().slice(0, 25) + '»');
}"""

# Страницы кабинета и панели управления: видны только после входа с кодом.
SIGNED_IN_PAGES = (
    "accounts:dashboard",
    "accounts:settings",
    "accounts:profile",
    "accounts:data",
    "workspace:saved",
    "feedback:mine",
    "userdata:index",
    "userdata:upload",
    "userdata:groups",
    "dashboard:index",
    "dashboard:user-list",
    "dashboard:data",
    "dashboard:sources",
    "dashboard:search",
    "dashboard:visits",
    "dashboard:ticket-list",
    "dashboard:content",
)

# Интервалы, которые читатель вправе задать своим стилем (WCAG 1.4.12).
TEXT_SPACING = (
    "* { line-height: 1.5 !important; letter-spacing: 0.12em !important;"
    " word-spacing: 0.16em !important; } p { margin-bottom: 2em !important; }"
)

# Страница шире окна, и текст, срезанный рамкой с overflow: hidden. Не в счёт: скрытые
# подписи, индикатор загрузки и формулы — у формулы своя прокрутка.
REFLOW_SCRIPT = r"""() => {
    const problems = [];
    const extra = document.documentElement.scrollWidth - document.documentElement.clientWidth;
    if (extra > 1) problems.push('страница шире окна на ' + extra + ' px');
    document.querySelectorAll('body *').forEach(el => {
        const style = getComputedStyle(el);
        if (![style.overflowX, style.overflowY].includes('hidden')) return;
        if (style.textOverflow === 'ellipsis' || !el.textContent.trim()) return;
        if (el.closest('.visually-hidden, [hidden], svg, math, .htmx-indicator')) return;
        // Подпись для программы чтения, спрятанная тем же приёмом, что .visually-hidden.
        if (style.clipPath === 'inset(50%)') return;
        if (el.scrollHeight > el.clientHeight + 2 || el.scrollWidth > el.clientWidth + 2) {
            const name = el.tagName.toLowerCase() + '.' + (el.classList[0] || '');
            problems.push('срезан текст: ' + name);
        }
    });
    return problems;
}"""

# Элемент в фокусе, целиком ушедший под прилипшие шапку и строку пути или под закреплённую
# внизу полосу вызова панели на телефоне (WCAG 2.4.11). Сначала ждёт конца прокрутки: страница
# едет плавно, и элемент за краем окна в середине движения ещё ни о чём не говорит.
OBSCURED_SCRIPT = r"""async () => {
    const deadline = performance.now() + 3000;
    let last = scrollY;
    for (let still = 0; still < 5 && performance.now() < deadline;) {
        await new Promise(requestAnimationFrame);
        still = scrollY === last ? still + 1 : 0;
        last = scrollY;
    }
    const el = document.activeElement;
    if (!el || el === document.body || el.closest('.app-header, .breadcrumbs-bar, .rail-dock')) {
        return null;
    }
    if (el.classList.contains('skip-link')) return null;
    const box = el.getBoundingClientRect();
    if (!box.height) return null;
    const shown = bar => {
        const style = getComputedStyle(bar);
        return ['sticky', 'fixed'].includes(style.position) && style.display !== 'none'
            && style.visibility !== 'hidden';
    };
    const top = [...document.querySelectorAll('.app-header, .breadcrumbs-bar')]
        .filter(shown)
        .reduce((edge, bar) => Math.max(edge, bar.getBoundingClientRect().bottom), 0);
    const bottom = [...document.querySelectorAll('.rail-dock')]
        .filter(shown)
        .reduce((edge, bar) => Math.min(edge, bar.getBoundingClientRect().top), innerHeight);
    const name = el.tagName.toLowerCase() + ' «'
        + (el.textContent || el.name || '').trim().slice(0, 30) + '»';
    if (box.bottom <= top + 1) return 'под шапкой: ' + name;
    if (box.top >= bottom - 1) return 'под полосой внизу: ' + name;
    return null;
}"""


class TestTargetSize:
    """Размер целей нажатия (WCAG 2.5.8)."""

    @pytest.mark.parametrize(("width", "language"), [(1440, "ru"), (390, "ru"), (390, "en")])
    def test_targets_are_large_or_spaced(
        self, page: Page, site: Any, warehouse_committed: Any, width: int, language: str
    ) -> None:
        """
        На всех публичных страницах цель не меньше 24 px или отстоит от соседних.

        Склад наполнен: ссылки-названия в строках таблиц — самые частые мелкие цели.
        Английские подписи длиннее русских и на узком экране переносятся иначе.
        """
        page.set_viewport_size({"width": width, "height": 900})
        failures: list[str] = []
        for path in (reverse(name).replace("/ru/", f"/{language}/", 1) for name in PAGES):
            page.goto(f"{site.url}{path}")
            page.wait_for_load_state("networkidle")
            failures.extend(f"{path}: {problem}" for problem in page.evaluate(TARGET_SCRIPT))

        assert not failures, "мелкие цели:\n  " + "\n  ".join(failures)

    def test_signed_in_pages_too(self, page: Page, site: Any, administrator: Any) -> None:
        """Кабинет и панель управления — те же требования к разметке и целям."""
        client = login_with_code(Client(), administrator)
        page.context.add_cookies(
            [
                {
                    "name": "regionlens_sessionid",
                    "value": client.cookies["regionlens_sessionid"].value,
                    "url": site.url,
                }
            ]
        )
        failures: list[str] = []
        for width in (1440, 390):
            page.set_viewport_size({"width": width, "height": 900})
            for path in (reverse(name) for name in SIGNED_IN_PAGES):
                response = page.goto(f"{site.url}{path}")
                assert response is not None and response.status == 200, f"{path}: не открылась"
                page.wait_for_load_state("networkidle")
                failures.extend(
                    f"{width} {path}: {problem}" for problem in page.evaluate(TARGET_SCRIPT)
                )
                failures.extend(
                    f"{width} {path}: {problem}" for problem in page.evaluate(AUDIT_SCRIPT)
                )

        assert not failures, "мелкие цели:\n  " + "\n  ".join(failures)


class TestReflow:
    """Узкое окно и увеличенные интервалы текста (WCAG 1.4.10, 1.4.12)."""

    def test_pages_fit_320_with_wide_spacing(
        self, page: Page, site: Any, warehouse_committed: Any
    ) -> None:
        """
        В окне 320 px — это 1280 px при масштабе 400 % — страница не шире окна и текст
        не срезан даже при увеличенных интервалах.
        """
        page.set_viewport_size({"width": 320, "height": 256})
        failures: list[str] = []
        for path in (reverse(name) for name in PAGES):
            page.goto(f"{site.url}{path}")
            page.wait_for_load_state("networkidle")
            page.add_style_tag(content=TEXT_SPACING)
            failures.extend(f"{path}: {problem}" for problem in page.evaluate(REFLOW_SCRIPT))

        assert not failures, "не помещается:\n  " + "\n  ".join(failures)


class TestFocusNotObscured:
    """Фокус не прячется под прилипшими шапкой и нижней полосой (WCAG 2.4.11)."""

    @pytest.mark.parametrize("width", [1440, 390])
    def test_focus_stays_below_the_header(self, page: Page, site: Any, width: int) -> None:
        """
        Обход назад от подвала не уводит элемент в фокусе под шапку.

        Назад — худший случай: элемент выше видимой части прокручивается к верху окна,
        где стоят шапка и строка пути.
        """
        page.set_viewport_size({"width": width, "height": 800})
        failures: list[str] = []
        for name in ("content:glossary", "feedback:create", "catalog:territory-list"):
            page.goto(f"{site.url}{reverse(name)}")
            page.wait_for_load_state("networkidle")
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.locator("footer a").last.focus()
            for _ in range(40):
                page.keyboard.press("Shift+Tab")
                problem = page.evaluate(OBSCURED_SCRIPT)
                if problem:
                    failures.append(f"{name}: {problem}")

        assert not failures, "фокус под шапкой:\n  " + "\n  ".join(failures)

    def test_focus_stays_above_the_rail_dock(
        self, page: Page, site: Any, warehouse_committed: Any
    ) -> None:
        """
        На телефоне обход вперёд не уводит элемент в фокусе под полосу вызова панели.

        Вперёд — худший случай для нижней полосы: элемент ниже видимой части прокручивается
        к низу окна, где закреплена полоса.
        """
        page.set_viewport_size({"width": 390, "height": 700})
        failures: list[str] = []
        for name in ("rankings:index", "surface:table", "analytics:inequality"):
            page.goto(f"{site.url}{reverse(name)}")
            page.wait_for_load_state("networkidle")
            page.keyboard.press("Tab")
            for _ in range(60):
                page.keyboard.press("Tab")
                problem = page.evaluate(OBSCURED_SCRIPT)
                if problem:
                    failures.append(f"{name}: {problem}")

        assert not failures, "фокус под полосой:\n  " + "\n  ".join(failures)


class TestForcedColors:
    """Режим высокой контрастности Windows."""

    def test_field_focus_keeps_a_ring(self, page: Page, site: Any) -> None:
        """
        У поля в фокусе остаётся кольцо.

        Поле показывает фокус тенью, а тень в этом режиме браузер убирает.
        """
        page.emulate_media(forced_colors="active")
        page.goto(f"{site.url}{reverse('accounts:login')}")
        page.locator("input[name='username']").focus()

        style = page.evaluate("getComputedStyle(document.activeElement).outlineStyle")
        assert style not in {"none", ""}, f"фокус не обозначен: {style}"

    def test_legend_swatch_keeps_its_colour(self, page: Page, site: Any) -> None:
        """
        Образец легенды сохраняет свой цвет.

        Иначе браузер заливает все образцы фоном и ступени шкалы не различить.
        """
        page.emulate_media(forced_colors="active")
        page.goto(f"{site.url}{reverse('core:about')}")
        adjust = page.evaluate(
            """() => {
                const swatch = document.createElement('span');
                swatch.className = 'map-legend__swatch';
                document.body.append(swatch);
                return getComputedStyle(swatch).forcedColorAdjust;
            }"""
        )
        assert adjust == "none"


class TestSuggestions:
    """Подсказки поиска для программы чтения (WCAG 4.1.2)."""

    @pytest.mark.parametrize("opener", ["field", "palette"])
    def test_arrow_choice_is_announced(self, page: Page, site: Any, opener: str) -> None:
        """
        Строка, отмеченная стрелкой, — option, и поле указывает на неё.

        Отметка одним классом видна глазу, но программа чтения после нажатия стрелки
        молчит: о выборе ей сообщает только aria-activedescendant.
        """
        page.set_viewport_size({"width": 1440, "height": 900})
        # Поле с подсказками есть на странице результатов при любом наполнении склада.
        page.goto(f"{site.url}{reverse('search:results')}")
        if opener == "field":
            page.locator("input[aria-controls='find-suggest']").first.click()
        else:
            page.keyboard.press("Control+k")
        page.keyboard.type("Татар", delay=50)
        page.wait_for_selector(".palette__item:visible")
        page.keyboard.press("ArrowDown")

        chosen = page.evaluate(
            """() => {
                const field = document.activeElement;
                const option = document.getElementById(
                    field.getAttribute('aria-activedescendant') || '');
                return option && [
                    field.getAttribute('role'),
                    option.getAttribute('role'),
                    option.getAttribute('aria-selected'),
                    Boolean(option.closest('[role=listbox]')),
                ];
            }"""
        )
        assert chosen == ["combobox", "option", "true", True]


class TestCodeStepTimeLimit:
    """Срок ввода кода входа (WCAG 2.2.1)."""

    def test_warning_extends_and_keeps_the_digits(
        self, page: Page, site: Any, make_user: Any
    ) -> None:
        """
        За две минуты до конца — предупреждение с кнопкой; продление не стирает набранное,
        а по истечении срока остаётся ссылка на вход заново.

        Часы страницы подменены: десять минут проходят перемоткой.
        """
        from tests.conftest import TEST_PASSWORD

        user = make_user(email="timer@example.com", two_factor=True)
        page.clock.install()
        page.goto(f"{site.url}{reverse('accounts:login')}")
        page.fill("input[name='username']", user.email)
        page.fill("input[name='password']", TEST_PASSWORD)
        page.click("form button[type='submit']")
        page.wait_for_url("**/login/code/")

        box = page.locator("[data-login-timer]")
        page.fill("input[name='code']", "123")
        page.clock.run_for("07:00")
        assert box.is_hidden()

        page.clock.run_for("01:05")
        assert box.is_visible()
        page.clock.run_for(500)
        assert "скоро выйдет" in (page.locator("#live-status").text_content() or "")

        page.get_by_role("button", name="Продлить на 10 минут").click()
        expect(box).to_be_hidden()
        assert page.input_value("input[name='code']") == "123"

        page.clock.run_for("10:01")
        assert page.locator("[data-login-timer-over]").is_visible()
        assert page.get_by_role("link", name="Войти заново").first.is_visible()
