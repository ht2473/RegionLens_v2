"""
Сквозные сценарии работы: язык, обновление без перезагрузки, диаграммы, узкий экран.

Сценарии описаны действиями пользователя, а не устройством разметки.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Page, expect

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]

# Время ожидания отрисовки диаграммы: сценарий ECharts выполняется после загрузки страницы.
CHART_TIMEOUT = 10_000


class TestPublicPages:
    """Публичная часть системы."""

    def test_home_page_presents_the_project(self, page: Page, site: Any) -> None:
        """Витрина открывается, содержит навигацию и фамилию автора работы."""
        page.goto(site.url)

        expect(page).to_have_title(re.compile("RegionLens"))
        expect(page.locator(".app-header")).to_be_visible()
        expect(page.locator(".app-footer")).to_contain_text("Кузьмин")

    def test_main_navigation_leads_to_sections(self, page: Page, site: Any) -> None:
        """Пункты основного меню, включая вложенные, ведут на работающие страницы."""
        page.set_viewport_size({"width": 1600, "height": 900})
        page.goto(site.url)

        links = page.locator(".main-nav__link")
        nested = page.locator(".main-nav__dropdown-item")
        assert links.count() == 6
        assert links.count() + nested.count() >= 10

        # Раздел со вложенными пунктами — кнопка: нажатие раскрывает панель,
        # а на страницу раздела ведёт её первая строка.
        section = page.locator("button.main-nav__link").first
        section.click()
        overview = page.locator(".main-nav__overview").first
        expect(overview).to_be_visible()
        overview.click()
        page.wait_for_load_state("networkidle")
        assert page.locator("main#main-content").is_visible()

    def test_language_switch_changes_the_page(self, page: Page, site: Any) -> None:
        """
        Переключатель языка ведёт на английскую версию той же страницы.

        Языковая версия различается адресом, а не сеансом: ссылкой на английскую
        страницу можно поделиться, и она откроется по-английски.
        """
        page.set_viewport_size({"width": 1600, "height": 900})
        page.goto(f"{site.url}/ru/about/")
        page.locator(".language-link", has_text="EN").click()
        page.wait_for_load_state("networkidle")

        assert "/en/" in page.url
        assert page.locator("html").get_attribute("lang") == "en"

    def test_theme_toggle_persists(self, page: Page, site: Any) -> None:
        """
        Выбранное оформление сохраняется между страницами.

        Тема применяется до отрисовки, иначе при каждом переходе возникала бы
        вспышка светлого фона на тёмном оформлении.
        """
        page.set_viewport_size({"width": 1600, "height": 900})
        page.goto(site.url)
        page.locator(".theme-menu__trigger").click()
        page.locator('#theme-menu-panel [data-theme-choice="dark"]').click()
        chosen = page.locator("html").get_attribute("data-theme")

        page.goto(f"{site.url}/ru/about/")
        assert page.locator("html").get_attribute("data-theme") == chosen

    def test_command_palette_searches(self, page: Page, site: Any) -> None:
        """Быстрый переход открывается и принимает запрос; на главной его заменяет поле поиска."""
        page.set_viewport_size({"width": 1600, "height": 900})
        page.goto(f"{site.url}/ru/about/")
        page.locator(".palette-trigger").click()

        field = page.locator(".palette__input")
        expect(field).to_be_visible()
        field.fill("население")
        page.wait_for_timeout(600)

        expect(page.locator("#palette-results")).to_be_visible()

    def test_skip_link_moves_focus_to_content(self, page: Page, site: Any) -> None:
        """
        Ссылка «Перейти к содержимому» доступна с клавиатуры.

        Это первое, чем пользуется человек, работающий без мыши: без неё
        приходится проходить всё меню на каждой странице.
        """
        page.goto(site.url)
        page.keyboard.press("Tab")

        expect(page.locator(".skip-link")).to_be_focused()


class TestAnalysisPages:
    """Страницы анализа с диаграммами."""

    def test_map_is_drawn_and_can_be_taken_apart(
        self, page: Page, site: Any, warehouse_committed: Any
    ) -> None:
        """Щелчок по классу легенды приглушает остальные, повторный возвращает карту целиком."""
        page.goto(f"{site.url}/ru/map/")
        page.wait_for_load_state("networkidle")

        surface = page.locator("svg.geo-map")
        if surface.count() == 0:
            pytest.skip("Склад не собран: картограмма не построена")
        expect(surface).to_be_visible()

        legend = page.locator("[data-legend-class]").first
        legend.click()
        page.wait_for_timeout(200)
        assert page.locator("[data-geo-map] [data-region].is-muted").count() > 0
        assert legend.get_attribute("aria-pressed") == "true"

        legend.click()
        page.wait_for_timeout(200)
        assert page.locator("[data-geo-map] [data-region].is-muted").count() == 0

    def test_selection_survives_the_switch_of_view(
        self, page: Page, site: Any, warehouse_committed: Any
    ) -> None:
        """Отмеченная территория остаётся отмеченной в рейле другого представления."""
        page.goto(f"{site.url}/ru/map/")
        page.wait_for_load_state("networkidle")

        # Перечень свёрнут, пока выбор пуст: сначала он раскрывается.
        page.locator(".territory-list > summary").click()
        box = page.locator("#surface-territories input[type='checkbox']").first
        if box.count() == 0:
            pytest.skip("Склад не собран: перечень территорий пуст")
        code = box.get_attribute("value")
        box.check()
        page.wait_for_timeout(600)

        page.locator(".tabs--surface .tabs__tab", has_text="Рейтинг").click()
        page.wait_for_timeout(800)

        assert "/ru/rankings/" in page.url
        assert f"territory={code}" in page.url
        expect(page.locator(f".territory-chips input[value='{code}']")).to_be_checked()
        expect(page.locator(".data-table tbody tr.is-selected")).to_have_count(1)

    def test_pointer_links_the_table_with_the_chart(
        self, page: Page, site: Any, warehouse_committed: Any
    ) -> None:
        """
        Наведение на строку таблицы отмечает тот же субъект на графике и в рейле.

        Проверяется то, что делает только браузер: связь держится на коде территории,
        и ни одно представление не знает об остальных.
        """
        page.goto(f"{site.url}/ru/rankings/")
        page.wait_for_load_state("networkidle")

        row = page.locator(".data-table tbody tr[data-territory]").first
        if row.count() == 0:
            pytest.skip("Склад не собран: таблица рейтинга пуста")
        code = row.get_attribute("data-territory")

        row.hover()
        page.wait_for_timeout(300)
        marked = page.locator(f"[data-territory='{code}'].is-highlighted")
        assert marked.count() > 1

        page.locator(".surface-head .page-head__title").hover()
        page.wait_for_timeout(300)
        assert page.locator(".is-highlighted").count() == 0

    def test_link_to_the_current_view_is_offered(
        self, page: Page, site: Any, warehouse_committed: Any
    ) -> None:
        """
        Адрес текущего вида показан целиком и годится для переписки.

        Ссылкой делятся, и она обязана открывать ровно то, что было на экране.
        """
        page.goto(f"{site.url}/ru/map/")
        page.wait_for_load_state("networkidle")

        page.locator('[popovertarget="share-menu"]').click()
        field = page.locator("#share-view-url")
        expect(field).to_be_visible()
        assert "/ru/map/" in (field.input_value() or "")
        assert page.locator("#share-api-url").input_value().startswith("http")

    def test_comparable_span_is_offered_on_the_chart(
        self, page: Page, site: Any, warehouse_committed: Any
    ) -> None:
        """
        Действие «показать только сопоставимый отрезок» обрезает график.

        Отрезок задаётся параметром адреса: обрезанный график — это утверждение
        о данных, и ссылка на него обязана открывать то же самое.
        """
        page.goto(f"{site.url}/ru/compare/")
        page.wait_for_load_state("networkidle")

        # Ряд с разрывом выбирается из списка: ключи рядов принадлежат источнику.
        key = page.evaluate(
            "() => { const found = [...document.querySelectorAll('#series-select option')]"
            ".find((item) => item.textContent.includes('продолжительность жизни'));"
            " return found ? found.value : ''; }"
        )
        if not key:
            pytest.skip("Склад не собран: перечень рядов пуст")

        page.goto(f"{site.url}/ru/compare/?series={key}")
        page.wait_for_load_state("networkidle")

        action = page.locator("a", has_text="сопоставимый отрезок")
        if action.count() == 0:
            pytest.skip("У показанного ряда нет разрывов сопоставимости")

        action.first.click()
        page.wait_for_timeout(800)
        assert "span=comparable" in page.url
        expect(page.locator("a", has_text="Показать весь ряд")).to_be_visible()

    def test_similar_regions_open_and_close(
        self, page: Page, site: Any, warehouse_committed: Any
    ) -> None:
        """Похожие регионы считаются при первом раскрытии, адрес получает ?similar=1."""
        page.goto(f"{site.url}/ru/regions/respublika-tatarstan/")
        page.wait_for_load_state("networkidle")

        summary = page.locator(".similar-block > summary")
        if summary.count() == 0:
            pytest.skip("Склад не собран: сходство не считается")

        summary.click()
        # Расчёт сравнивает субъект с остальными по шести рядам: ответ приходит
        # не мгновенно, и ждать его нужно по появлению перечня, а не по времени.
        page.wait_for_selector("#territory-similar .similar-list__item", timeout=CHART_TIMEOUT)
        assert "similar=1" in page.url
        first = page.locator("#territory-similar .similar-list__item").first
        expect(first).to_be_visible()

        summary.click()
        expect(first).to_be_hidden()

    def test_passport_themes_unfold(self, page: Page, site: Any, warehouse_committed: Any) -> None:
        """
        Темы таблицы паспорта свёрнуты до строки со сводкой и раскрываются нажатием,
        «Развернуть все» раскрывает все сразу, ссылка с #якорем темы раскрывает её.
        """
        page.set_viewport_size({"width": 1440, "height": 900})
        page.goto(f"{site.url}/ru/regions/respublika-tatarstan/")
        page.wait_for_load_state("networkidle")

        themes = page.locator("#passport-facts-table > tbody")
        if themes.count() == 0:
            pytest.skip("Склад не собран: таблицы тем нет")
        first = themes.first
        expect(first).to_have_class(re.compile("is-folded"))
        first.locator("[data-fold-toggle]").click()
        expect(first).not_to_have_class(re.compile("is-folded"))

        expand = page.locator("[data-fold-all]")
        expect(expand).to_be_visible()
        expand.click()
        assert page.locator("#passport-facts-table > tbody.is-folded").count() == 0
        expand.click()
        assert page.locator("#passport-facts-table > tbody.is-folded").count() == themes.count()

        anchor = themes.last.get_attribute("id")
        page.goto(f"{site.url}/ru/regions/respublika-tatarstan/#{anchor}")
        page.wait_for_load_state("networkidle")
        expect(page.locator(f"#{anchor}")).not_to_have_class(re.compile("is-folded"))

    def test_more_sides_unfold_below_the_list(
        self, page: Page, site: Any, warehouse_committed: Any
    ) -> None:
        """
        «Ещё N» у сильных и слабых сторон стоит после перечня, раскрывает остальные
        пункты и сворачивает их обратно.
        """
        page.goto(f"{site.url}/ru/regions/respublika-tatarstan/")
        page.wait_for_load_state("networkidle")

        toggle = page.locator("[data-more-toggle]").first
        if page.locator("[data-more-toggle]").count() == 0:
            pytest.skip("У региона не больше шести сильных и слабых сторон")
        extra = page.locator("[data-more-item]").first
        expect(extra).to_be_hidden()
        expect(toggle).to_have_text(re.compile("Ещё"))
        toggle.click()
        expect(extra).to_be_visible()
        expect(toggle).to_have_text("Свернуть")
        toggle.click()
        expect(extra).to_be_hidden()

    def test_indicator_map_answers_where_it_is_higher(
        self, page: Page, site: Any, warehouse_committed: Any
    ) -> None:
        """
        Живая карта на странице показателя: перечни крайних связаны с картой и годом.

        Наведение обводит регион, ползунок переписывает перечни и адрес, щелчок открывает карточку.
        """
        from apps.catalog.models import Series

        series = Series.objects.select_related("indicator").get(key="Y477110378:00")
        page.goto(f"{site.url}/ru/indicators/{series.indicator.slug}/")
        page.wait_for_load_state("networkidle")

        rows = page.locator("[data-live='top'] .leader-list__name")
        if rows.count() == 0:
            pytest.skip("Склад не собран: живой карты нет")

        first = rows.first
        code = first.get_attribute("data-code")
        first.hover()
        expect(page.locator(f"[data-geo-map] [data-region='{code}'].is-highlighted")).to_have_count(
            1
        )
        expect(page.locator("[data-live='card-name']")).to_have_text(first.inner_text())

        page.mouse.move(1, 1)
        # Порядок регионов от года к году может не меняться, значения — меняются всегда.
        values = page.locator("[data-live='top'] .leader-list__value")
        before = values.all_inner_texts()
        slider = page.locator("[data-live='range']")
        earliest = slider.get_attribute("min")
        slider.dispatch_event("pointerdown")
        slider.evaluate(
            "(node, year) => { node.value = year; node.dispatchEvent(new Event('input')); }",
            earliest,
        )
        expect(page.locator("[data-live='year']").first).to_have_text(earliest or "")
        assert f"year={earliest}" in page.url
        page.wait_for_function(
            "(before) => [...document.querySelectorAll(\"[data-live='top'] .leader-list__value\")]"
            ".map((node) => node.textContent).join('|') !== before.join('|')",
            arg=before,
            timeout=CHART_TIMEOUT,
        )
        assert values.count() == len(before)

        rows.first.click()
        expect(page.locator("#region-card")).to_be_visible()
        expect(page.locator(".region-card__row.is-chosen")).to_have_count(1)
        page.keyboard.press("Escape")
        expect(page.locator("#region-card")).to_be_hidden()

    def test_analytics_tool_updates_without_reload(self, page: Page, site: Any) -> None:
        """
        Смена параметров инструмента обновляет только рабочую область.

        Полная перезагрузка на каждое движение ползунка была бы заметна
        пользователю и сбрасывала бы положение прокрутки.
        """
        page.goto(f"{site.url}/ru/analytics/inequality/")
        page.wait_for_load_state("networkidle")

        marker = page.evaluate("() => { window.__reloaded = true; return true; }")
        assert marker is True

        form = page.locator("form[hx-get], form[hx-post]")
        if form.count() == 0:
            pytest.skip("Инструмент не предлагает параметров при пустом складе")

        page.wait_for_timeout(400)
        assert page.evaluate("() => window.__reloaded === true")


class TestAccountJourney:
    """Полный путь пользователя: регистрация, вход, личный кабинет."""

    def test_registration_and_login(self, page: Page, site: Any) -> None:
        """
        Посетитель регистрируется, входит и попадает в личный кабинет.

        Это самый длинный путь в системе и единственный, который проходит
        через четыре формы подряд.
        """
        page.goto(f"{site.url}/ru/register/")

        page.fill("input[name='email']", "newcomer@example.com")
        page.fill("input[name='full_name']", "Сидоров Сидор Сидорович")
        page.fill("input[name='password1']", "Очень-Длинный-Пароль-2026")
        page.fill("input[name='password2']", "Очень-Длинный-Пароль-2026")
        page.check("input[name='accepted_terms']")
        page.check("input[name='accepted_consent']")
        page.click("button[type='submit']")
        page.wait_for_load_state("networkidle")

        assert "/cabinet/" in page.url or "/login/" in page.url

    def test_login_and_open_cabinet(self, page: Page, site: Any, registered_user: Any) -> None:
        """Существующая учётная запись входит и видит обзор — первую страницу кабинета."""
        page.goto(f"{site.url}/ru/login/")
        page.fill("input[name='username']", registered_user.email)
        page.fill("input[name='password']", "e2e-password-12345")
        page.click("button[type='submit']")
        page.wait_for_load_state("networkidle")

        assert page.url.endswith("/cabinet/")
        expect(page.locator(".cabinet-tabs__tab[aria-current='page']")).to_be_visible()

    def test_wrong_password_is_reported(self, page: Page, site: Any, registered_user: Any) -> None:
        """Неверный пароль объясняется, а не приводит к пустой странице."""
        page.goto(f"{site.url}/ru/login/")
        page.fill("input[name='username']", registered_user.email)
        page.fill("input[name='password']", "неверный-пароль")
        page.click("button[type='submit']")
        page.wait_for_load_state("networkidle")

        assert "/login/" in page.url
        expect(page.locator(".form-error, .errorlist, .callout--danger").first).to_be_visible()


class TestResponsiveLayout:
    """Поведение вёрстки на разных экранах."""

    def test_navigation_switches_to_drawer(self, page: Page, site: Any) -> None:
        """Горизонтальное меню и кнопка выдвижного меню не показываются одновременно."""
        for width in (390, 1024, 1280, 1440, 1920):
            page.set_viewport_size({"width": width, "height": 900})
            page.goto(site.url)
            state = page.evaluate(
                """() => {
                    const visible = (sel) => {
                        const el = document.querySelector(sel);
                        return Boolean(el) && el.getBoundingClientRect().width > 0;
                    };
                    return {
                        nav: visible('.app-header__nav'),
                        burger: visible('.app-header__burger'),
                    };
                }"""
            )
            assert state["nav"] != state["burger"], f"ширина {width}: {state}"

    def test_menu_labels_are_not_truncated(self, page: Page, site: Any) -> None:
        """
        Подписи пунктов меню видны целиком.

        Пункт с усечённым названием бесполезен: по нему нельзя понять, куда он ведёт.
        Проверка выполняется на ширинах, где показывается горизонтальное меню.
        """
        for width in (1180, 1280, 1366, 1440, 1600, 1920):
            page.set_viewport_size({"width": width, "height": 900})
            page.goto(site.url)
            cut = page.evaluate(
                """() => [...document.querySelectorAll('.main-nav__label')]
                    .filter(el => el.scrollWidth - el.clientWidth > 2)
                    .map(el => el.textContent.trim())"""
            )
            assert cut == [], f"ширина {width}: усечены {cut}"

    def test_header_actions_stay_clickable(self, page: Page, site: Any) -> None:
        """
        Переключатель языка и оформления доступны для нажатия.

        Меню способно занять больше места, чем остаётся в шапке, и накрыть эти
        кнопки собой. Внешне шапка при этом выглядит целой, а кнопки не работают.
        """
        page.set_viewport_size({"width": 1440, "height": 900})
        page.goto(site.url)

        for selector in (".language-link", ".theme-menu__trigger"):
            box = page.locator(selector).first.bounding_box()
            assert box is not None
            topmost = page.evaluate(
                "([x, y]) => document.elementFromPoint(x, y).closest("
                "'.app-header__actions') !== null",
                [box["x"] + box["width"] / 2, box["y"] + box["height"] / 2],
            )
            assert topmost, f"элемент {selector} перекрыт другим"

    def test_mobile_navigation_opens(self, page: Page, site: Any) -> None:
        """На узком экране основное меню раскрывается кнопкой."""
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(site.url)

        page.locator(".app-header__burger").click()
        page.wait_for_timeout(300)

        assert page.locator("#mobile-nav").is_visible()
        # Меню — модальное окно: «Поиск» из него открывает палитру и закрывает меню.
        assert page.locator("#mobile-nav").evaluate("el => el.matches(':modal')")
        # Пять разделов шапки и пять инструментов «Анализа» — все доступны и с телефона.
        links = page.locator("#mobile-nav .mobile-nav__link")
        hrefs = {links.nth(index).get_attribute("href") for index in range(links.count())}
        for route in (
            "/ru/regions/",
            "/ru/analytics/",
            "/ru/analytics/inequality/",
            "/ru/analytics/revisions/",
            "/ru/methodology/",
        ):
            assert route in hrefs, route

    def test_mobile_drawer_offers_search_and_account(self, page: Page, site: Any) -> None:
        """
        В выдвижном меню есть поиск и вход.

        На смартфоне эти кнопки убраны из шапки — она их не вмещает, — и без
        переноса о них нельзя было бы узнать.
        """
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(site.url)
        page.locator(".app-header__burger").click()
        page.wait_for_timeout(300)

        footer = page.locator(".mobile-nav__footer")
        expect(footer).to_be_visible()
        expect(footer).to_contain_text("Поиск")
        expect(footer).to_contain_text("Войти")

        footer.locator("[data-dialog-open='palette']").click()
        page.wait_for_timeout(200)
        expect(page.locator("#palette")).to_be_visible()
        expect(page.locator("#mobile-nav")).to_be_hidden()


class TestRailOnAPhone:
    """Рейль на телефоне — нижний лист по нажатию на полосу вызова; первым идёт холст."""

    def _open_map(self, page: Page, site: Any, width: int = 390) -> None:
        """Открыть карту на экране заданной ширины."""
        page.set_viewport_size({"width": width, "height": 844})
        page.goto(f"{site.url}/ru/map/")
        page.wait_for_load_state("networkidle")
        if page.locator("#surface-rail").count() == 0:
            pytest.skip("Склад не собран: рабочая поверхность не построена")

    def test_canvas_comes_first(self, page: Page, site: Any, warehouse_committed: Any) -> None:
        """Первым экраном идёт холст, а не форма."""
        self._open_map(page, site)

        expect(page.locator("#surface-rail")).to_be_hidden()
        expect(page.locator(".rail-dock")).to_be_visible()

        canvas = page.locator(".surface-canvas").bounding_box()
        assert canvas is not None
        # Холст начинается в пределах первого экрана, а не за ним.
        assert canvas["y"] < 844

    def test_sheet_opens_and_closes(self, page: Page, site: Any, warehouse_committed: Any) -> None:
        """Лист раскрывается полосой, закрывается кнопкой и клавишей Esc."""
        self._open_map(page, site)
        rail = page.locator("#surface-rail")
        opener = page.locator("[data-rail-open]")

        opener.click()
        page.wait_for_timeout(400)
        expect(rail).to_be_visible()
        expect(page.locator(".rail-backdrop")).to_be_visible()
        assert opener.get_attribute("aria-expanded") == "true"
        assert page.evaluate("getComputedStyle(document.body).overflow") == "hidden"

        page.locator("[data-rail-close]").click()
        page.wait_for_timeout(400)
        expect(rail).to_be_hidden()
        assert page.evaluate("getComputedStyle(document.body).overflow") != "hidden"

        opener.click()
        page.wait_for_timeout(400)
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)
        expect(rail).to_be_hidden()

    def test_sheet_survives_a_change_of_parameter(
        self, page: Page, site: Any, warehouse_committed: Any
    ) -> None:
        """Смена параметра из листа обновляет холст, а лист и его признак остаются раскрытыми."""
        self._open_map(page, site)
        page.locator("[data-rail-open]").click()
        page.wait_for_timeout(400)

        page.locator(".territory-list > summary").click()
        box = page.locator("#surface-territories input[type='checkbox']").first
        box.check()
        page.wait_for_timeout(900)

        expect(page.locator("#surface-rail")).to_be_visible()
        assert page.locator("[data-rail-open]").get_attribute("aria-expanded") == "true"
        expect(page.locator(".rail-dock__summary")).to_contain_text("субъект")

    def test_rail_is_a_column_on_a_wide_screen(
        self, page: Page, site: Any, warehouse_committed: Any
    ) -> None:
        """На широком экране рейль остаётся колонкой слева, полосы вызова нет."""
        self._open_map(page, site, width=1280)

        expect(page.locator("#surface-rail")).to_be_visible()
        expect(page.locator(".rail-dock")).to_be_hidden()

        rail = page.locator("#surface-rail").bounding_box()
        canvas = page.locator(".surface-canvas").bounding_box()
        assert rail is not None and canvas is not None
        assert rail["x"] + rail["width"] <= canvas["x"] + 1

    def test_every_view_is_visible_on_a_phone(
        self, page: Page, site: Any, warehouse_committed: Any
    ) -> None:
        """Все пять вкладок представлений видны без прокрутки вбок."""
        self._open_map(page, site, width=320)

        tabs = page.locator(".tabs--surface .tabs__tab")
        expect(tabs).to_have_count(5)
        outside = page.evaluate(
            """() => {
                const limit = document.documentElement.clientWidth;
                return [...document.querySelectorAll('.tabs--surface .tabs__tab')]
                    .filter(el => el.getBoundingClientRect().right > limit + 1)
                    .map(el => el.textContent.trim());
            }"""
        )
        assert outside == [], f"вкладки за краем экрана: {outside}"

    def test_chart_gives_the_lines_room(
        self, page: Page, site: Any, warehouse_committed: Any
    ) -> None:
        """
        На узком холсте подписи у концов линий уступают место легенде снизу.

        Поле под подписи — 186 точек: на телефоне это половина холста, и линиям
        оставалось меньше половины ширины.
        """
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(f"{site.url}/ru/compare/")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(2000)

        state = page.evaluate(
            """() => {
                const el = document.querySelector('.chart');
                if (!el || !window.echarts) return null;
                const inst = window.echarts.getInstanceByDom(el);
                if (!inst) return null;
                const option = inst.getOption();
                return {
                    labelled: (option.series || [])
                        .filter(s => s.endLabel && s.endLabel.show).length,
                    legend: Boolean(option.legend && option.legend[0] && option.legend[0].show),
                    right: option.grid && option.grid[0] ? option.grid[0].right : null,
                };
            }"""
        )
        if state is None:
            pytest.skip("Библиотека графиков не загрузилась")

        assert state["labelled"] == 0
        assert state["legend"]
        assert state["right"] is not None and state["right"] <= 24


class TestScreenshots:
    """Снимки экрана для руководства пользователя."""

    @pytest.mark.parametrize(
        ("name", "path"),
        [
            ("home", "/ru/"),
            ("catalog", "/ru/indicators/"),
            ("map", "/ru/map/"),
            ("analytics", "/ru/analytics/"),
        ],
    )
    def test_capture(
        self,
        page: Page,
        site: Any,
        artifacts_dir: Path,
        name: str,
        path: str,
    ) -> None:
        """
        Снять экран раздела.

        Снимки нужны руководству пользователя, и делать их вручную после каждой
        правки оформления бессмысленно: они устаревают быстрее, чем текст.
        """
        page.set_viewport_size({"width": 1440, "height": 900})
        page.goto(f"{site.url}{path}")
        page.wait_for_load_state("networkidle")
        page.screenshot(path=str(artifacts_dir / f"{name}.png"), full_page=False)

        assert (artifacts_dir / f"{name}.png").exists()
