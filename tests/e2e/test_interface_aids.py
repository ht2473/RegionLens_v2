"""Сквозные проверки того, что делает только браузер: оглавление, отбор, поиск в списке, тема."""

from __future__ import annotations

from typing import Any

import pytest
from django.core.management import call_command
from playwright.sync_api import Page, expect

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]

# Время на отработку наблюдателя пересечений после прокрутки.
SCROLL_SETTLE_MS = 600


@pytest.fixture
def methodology_seeded(transactional_db: None) -> None:
    """Наполнить разделы методики с фиксацией в базе."""
    call_command("seed_content", "--only", "methodology", verbosity=0)


class TestTableOfContents:
    """Отметка читаемого раздела в оглавлении методологии."""

    def test_current_section_is_marked_while_scrolling(
        self,
        page: Page,
        site: Any,
        methodology_seeded: None,
    ) -> None:
        """
        При прокрутке страницы в оглавлении отмечается открытый раздел.

        Оглавление длиннее экрана и прокручивается вместе со страницей: без отметки
        по нему нельзя понять, в каком месте страницы находишься.
        """
        page.set_viewport_size({"width": 1440, "height": 900})
        page.goto(f"{site.url}/ru/methodology/")
        page.wait_for_load_state("networkidle")

        anchors = page.locator('[data-toc] a[href^="#"]')
        assert anchors.count() >= 3, "разделы методологии не наполнены"

        # Разделы из начала и середины: последний до верха области чтения не доходит.
        first_id = anchors.nth(0).get_attribute("href") or ""
        middle_id = anchors.nth(anchors.count() // 2).get_attribute("href") or ""

        for anchor in (first_id, middle_id):
            # Раздел — к верхнему краю области чтения, как при переходе по оглавлению.
            page.evaluate(
                "(id) => document.querySelector(id).scrollIntoView({block: 'start'})",
                anchor,
            )
            page.wait_for_timeout(SCROLL_SETTLE_MS)

            marked = page.locator("[data-toc] a.is-current")
            expect(marked).to_have_count(1)
            assert marked.get_attribute("href") == anchor


class TestInstantFilter:
    """Отбор в готовом перечне без обращения к серверу."""

    def test_typing_narrows_the_list_of_regions(self, page: Page, site: Any) -> None:
        """Ввод в поле поиска отбирает карточки субъектов сразу, без кнопки."""
        page.goto(f"{site.url}/ru/regions/")
        page.wait_for_load_state("networkidle")

        cards = page.locator("[data-filter-item]")
        assert cards.count() > 10

        field = page.locator("[data-filter-input]")
        field.fill("приморск")
        page.wait_for_timeout(200)

        visible = page.locator("[data-filter-item]:not([hidden])")
        assert visible.count() == 1
        expect(visible.first).to_contain_text("Приморский")

        # Пустое поле возвращает перечень целиком.
        field.fill("")
        page.wait_for_timeout(200)
        assert page.locator("[data-filter-item]:not([hidden])").count() == cards.count()

    def test_search_finds_a_region_by_its_capital(self, page: Page, site: Any) -> None:
        """Отбор идёт и по административному центру: его помнят чаще названия субъекта."""
        page.goto(f"{site.url}/ru/regions/")
        page.wait_for_load_state("networkidle")

        page.locator("[data-filter-input]").fill("владивосток")
        page.wait_for_timeout(200)

        visible = page.locator("[data-filter-item]:not([hidden])")
        assert visible.count() == 1
        expect(visible.first).to_contain_text("Приморский")

    def test_nothing_found_is_stated_explicitly(self, page: Page, site: Any) -> None:
        """Пустой результат отбора объявляется, а не показывается пустым местом."""
        page.goto(f"{site.url}/ru/regions/")
        page.wait_for_load_state("networkidle")

        page.locator("[data-filter-input]").fill("щщщ")
        page.wait_for_timeout(200)

        assert page.locator("[data-filter-item]:not([hidden])").count() == 0
        expect(page.locator("#territory-empty")).to_be_visible()


class TestSeriesCombobox:
    """Поиск в списке выбора ряда наблюдений."""

    def test_search_narrows_the_options(
        self,
        page: Page,
        site: Any,
        warehouse_committed: Any,
    ) -> None:
        """
        Поле поиска отбирает позиции списка по названию и по разделу.

        Пригодных к анализу рядов несколько сотен, и обычный выпадающий список
        открывает их сплошной полосой во весь экран.
        """
        page.goto(f"{site.url}/ru/map/")
        page.wait_for_load_state("networkidle")

        root = page.locator(".combobox").first
        total = root.locator(".combobox__option").count()
        if total == 0:
            pytest.skip("Склад не собран: список рядов пуст")

        root.locator(".combobox__trigger").click()
        root.locator(".combobox__search input").fill("населен")
        page.wait_for_timeout(200)

        visible = root.locator(".combobox__option:not([hidden])")
        assert 0 < visible.count() < total

    def test_choosing_an_option_updates_the_page(
        self,
        page: Page,
        site: Any,
        warehouse_committed: Any,
    ) -> None:
        """Выбор в списке меняет значение формы и перестраивает рабочую область."""
        page.goto(f"{site.url}/ru/map/")
        page.wait_for_load_state("networkidle")

        root = page.locator(".combobox").first
        if root.locator(".combobox__option").count() < 2:
            pytest.skip("В складе меньше двух рядов, пригодных для картограммы")

        before = root.locator("select").input_value()
        root.locator(".combobox__trigger").click()

        options = root.locator(".combobox__option:not([hidden])")
        options.nth(1).click()
        page.wait_for_timeout(600)

        assert root.locator("select").input_value() != before
        expect(root.locator(".combobox__panel")).to_be_hidden()


class TestThemeToggle:
    """Переключатель оформления."""

    def test_switch_marks_the_chosen_state(self, page: Page, site: Any) -> None:
        """Переключатель темы отмечает выбранное из трёх состояний атрибутом aria-pressed."""
        page.set_viewport_size({"width": 1600, "height": 900})
        page.goto(site.url)
        page.wait_for_load_state("networkidle")

        # Переключатель есть и в шапке, и в выдвижном меню (на смартфоне шапка
        # его не вмещает), поэтому берётся именно шапочный.
        switch = page.locator(".app-header__actions .theme-switch")
        dark = switch.locator('[data-theme-choice="dark"]')
        system = switch.locator('[data-theme-choice="system"]')

        dark.click()
        page.wait_for_timeout(200)
        assert page.locator("html").get_attribute("data-theme") == "dark"
        expect(dark).to_have_attribute("aria-pressed", "true")
        expect(system).to_have_attribute("aria-pressed", "false")

        system.click()
        page.wait_for_timeout(200)
        assert page.locator("html").get_attribute("data-theme") is None
        expect(system).to_have_attribute("aria-pressed", "true")

    def test_menus_are_not_open_on_load(self, page: Page, site: Any) -> None:
        """Выпадающие панели закрыты с самой загрузки: их держит закрытыми браузер."""
        page.goto(site.url)

        expect(page.locator("#palette")).to_be_hidden()
        assert page.locator(".main-nav__dropdown:visible").count() == 0
        assert page.locator(".mobile-nav:visible").count() == 0


class TestHeaderMenus:
    """Выпадающие панели шапки — popover, быстрый переход и выдвижное меню — <dialog>."""

    def test_state_library_is_gone(self, page: Page, site: Any) -> None:
        """Сторонняя библиотека состояния со страницы убрана целиком."""
        page.goto(site.url)

        assert page.evaluate("typeof window.Alpine") == "undefined"
        assert page.locator("[x-data]").count() == 0
        assert page.locator("[x-cloak]").count() == 0

    def test_section_menu_opens_on_click(self, page: Page, site: Any) -> None:
        """Пункт со вложенными разделами раскрывается нажатием, закрывается нажатием мимо и Esc."""
        page.set_viewport_size({"width": 1600, "height": 900})
        page.goto(site.url)

        item = page.locator(".main-nav__item[data-menu-switch]").first
        trigger = item.locator("button[popovertarget]")
        panel = page.locator("#" + (trigger.get_attribute("popovertarget") or ""))
        expect(panel).to_be_hidden()

        trigger.click()
        expect(panel).to_be_visible(timeout=100)
        assert panel.evaluate("el => el.matches(':popover-open')")
        expect(panel.locator(".main-nav__overview")).to_be_visible()

        # Панель стоит под своей кнопкой, а не посреди окна, как без сценариев.
        button_box = trigger.bounding_box() or {}
        panel_box = panel.bounding_box() or {}
        assert abs(panel_box["x"] - button_box["x"]) < 2
        assert 0 < panel_box["y"] - (button_box["y"] + button_box["height"]) < 16

        page.mouse.click(10, 800)
        expect(panel).to_be_hidden()

        trigger.click()
        page.keyboard.press("Escape")
        expect(panel).to_be_hidden()

    def test_passing_pointer_does_not_open_the_menu(self, page: Page, site: Any) -> None:
        """
        Указатель, проведённый через полосу меню, панелей не раскрывает.

        Панель раздела занимает четверть экрана и раскрывается только нажатием:
        у того, кто ведёт мышь через меню к поиску, она не распахивается.
        """
        page.set_viewport_size({"width": 1600, "height": 900})
        page.goto(site.url)

        items = page.locator(".main-nav__item[data-menu-switch]")
        for index in range(items.count()):
            items.nth(index).hover()
            page.wait_for_timeout(90)
        page.mouse.move(1500, 300)
        page.wait_for_timeout(150)

        assert self._opened(page) == 0

    def test_only_one_section_menu_is_open(self, page: Page, site: Any) -> None:
        """
        Наведение на соседний раздел при раскрытом меню переключает панель
        и не оставляет двух панелей рядом.
        """
        page.set_viewport_size({"width": 1600, "height": 900})
        page.goto(site.url)

        items = page.locator(".main-nav__item[data-menu-switch]")
        if items.count() < 2:
            pytest.skip("В меню один раскрывающийся раздел")

        items.nth(0).locator("button").click()
        assert self._opened(page) == 1

        items.nth(1).hover()
        page.wait_for_timeout(60)
        assert self._opened(page) == 1

    @staticmethod
    def _opened(page: Page) -> int:
        """Число раскрытых панелей разделов."""
        return int(
            page.evaluate(
                """() => [...document.querySelectorAll('.main-nav__dropdown')]
                    .filter(el => el.matches(':popover-open')).length"""
            )
        )

    def test_palette_opens_by_shortcut_and_searches(self, page: Page, site: Any) -> None:
        """
        Палитра открывается сочетанием клавиш, ищет и переставляет отметку стрелками.

        Подсказка внизу палитры обещала перебор стрелками с самого начала, а самого
        перебора не было: класс отметки в таблице стилей был, ставить его было некому.
        """
        page.goto(site.url)
        page.keyboard.press("Control+k")
        page.wait_for_timeout(300)

        expect(page.locator("#palette")).to_be_visible()
        assert page.evaluate("document.activeElement.className") == "palette__input"

        page.keyboard.type("Тат", delay=50)
        page.wait_for_timeout(900)
        found = page.locator(".palette__item")
        if found.count() == 0:
            pytest.skip("Справочник пуст: искать нечего")

        page.keyboard.press("ArrowDown")
        page.wait_for_timeout(150)
        assert page.locator(".palette__item.is-selected").count() == 1

        # Esc закрывает палитру с первого нажатия, хотя в поле уже набран запрос:
        # поле поиска забирает первое нажатие себе, стирая набранное.
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        expect(page.locator("#palette")).to_be_hidden()

    def test_palette_is_a_modal_window(self, page: Page, site: Any) -> None:
        """
        Палитра — модальное окно: страница под ней не прокручивается, нажатие
        по затемнению закрывает окно, а фокус возвращается на кнопку поиска.
        """
        page.set_viewport_size({"width": 1600, "height": 900})
        page.goto(site.url)

        page.click(".palette-trigger")
        page.wait_for_timeout(200)
        palette = page.locator("#palette")
        expect(palette).to_be_visible()
        assert palette.evaluate("el => el.matches(':modal')")
        assert page.evaluate("getComputedStyle(document.documentElement).overflow") == "hidden"

        page.mouse.click(40, 860)
        page.wait_for_timeout(200)
        expect(palette).to_be_hidden()
        assert "palette-trigger" in page.evaluate("document.activeElement.className")
        assert page.evaluate("getComputedStyle(document.documentElement).overflow") != "hidden"

    def test_signed_in_menu_is_a_popover(self, page: Page, site: Any, registered_user: Any) -> None:
        """Меню учётной записи раскрывается под кнопкой и закрывается нажатием мимо."""
        page.set_viewport_size({"width": 1600, "height": 900})
        page.goto(f"{site.url}/ru/login/")
        page.fill("input[name='username']", registered_user.email)
        page.fill("input[name='password']", "e2e-password-12345")
        page.click("button[type='submit']")
        page.wait_for_load_state("networkidle")

        trigger = page.locator(".user-menu__trigger")
        panel = page.locator("#user-menu-panel")
        trigger.click()
        expect(panel).to_be_visible()
        button_box = trigger.bounding_box() or {}
        panel_box = panel.bounding_box() or {}
        # Кнопка у правого края — панель выровнена по её правому краю.
        right_edge = button_box["x"] + button_box["width"]
        assert abs(panel_box["x"] + panel_box["width"] - right_edge) < 2

        page.mouse.click(10, 600)
        expect(panel).to_be_hidden()


class TestRailIsNotClosedByMenus:
    """
    Панель параметров переживает работу с меню шапки.

    Кнопки шапки и Esc не должны скрывать элементы, названные в чужих aria-controls.
    """

    def test_rail_survives_escape_and_header_buttons(self, page: Page, site: Any) -> None:
        """Рейль остаётся на месте после Esc и после вызова поиска."""
        page.set_viewport_size({"width": 1600, "height": 900})
        page.goto(f"{site.url}/ru/analytics/inequality/")
        page.wait_for_load_state("networkidle")

        rail = page.locator(".filter-rail")
        content = page.locator(".workspace__content")
        expect(rail).to_be_visible()
        width = (content.bounding_box() or {}).get("width", 0)
        assert width > 400, "холст не занял свою колонку ещё до проверки"

        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
        expect(rail).to_be_visible()

        page.click(".palette-trigger")
        page.wait_for_timeout(200)
        expect(rail).to_be_visible()

        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
        expect(rail).to_be_visible()
        assert (content.bounding_box() or {}).get("width", 0) == width

    def test_region_list_survives_escape(self, page: Page, site: Any) -> None:
        """Перечень регионов не скрывается клавишей Esc."""
        page.set_viewport_size({"width": 1600, "height": 900})
        page.goto(f"{site.url}/ru/regions/")
        page.wait_for_load_state("networkidle")

        groups = page.locator("#territory-groups")
        expect(groups).to_be_visible()

        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
        expect(groups).to_be_visible()


class TestTakeAway:
    """
    Способы унести с холста то, что на нём показано.

    Числа и картинку забирают, чтобы вставить в работу и считать дальше. До сих пор
    для этого оставался снимок экрана и заказ отчёта, доступный только вошедшему.
    """

    def test_chart_offers_an_image(self, page: Page, site: Any, warehouse_committed: Any) -> None:
        """У каждого построенного графика есть кнопка сохранения картинки."""
        page.set_viewport_size({"width": 1600, "height": 1000})
        page.goto(f"{site.url}/ru/analytics/inequality/")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(800)

        charts = page.locator("rl-chart")
        if charts.count() == 0:
            pytest.skip("Склад не собран: графиков на странице нет")

        assert page.locator(".chart-save").count() == charts.count()

        with page.expect_download() as download:
            page.locator(".chart-save").first.click()
        assert download.value.suggested_filename.endswith(".png")

    def test_map_is_saved_as_a_drawing(
        self, page: Page, site: Any, warehouse_committed: Any
    ) -> None:
        """
        Картограмма сохраняется вектором, а не картинкой.

        Она приходит с сервера чертежом, и снимок экрана вместо чертежа означал бы
        рваные края при печати.
        """
        page.set_viewport_size({"width": 1600, "height": 1000})
        page.goto(f"{site.url}/ru/map/")
        page.wait_for_load_state("networkidle")

        button = page.locator(".geo-map-wrapper .chart-save")
        if button.count() == 0:
            pytest.skip("Склад не собран: карта не построена")

        with page.expect_download() as download:
            button.click()
        assert download.value.suggested_filename.endswith(".svg")

    def test_saved_drawing_keeps_its_colours(
        self, page: Page, site: Any, warehouse_committed: Any
    ) -> None:
        """
        В сохранённом чертеже не остаётся переменных оформления.

        Цвета заливок заданы переменными, а живут они на корне документа: чертёж,
        вынутый из страницы, без подстановки значений потерял бы всякий цвет.
        """
        page.goto(f"{site.url}/ru/map/")
        page.wait_for_load_state("networkidle")

        map_node = page.locator("[data-geo-map]")
        if map_node.count() == 0:
            pytest.skip("Склад не собран: карта не построена")

        markup = page.evaluate(
            """() => {
                const map = document.querySelector('[data-geo-map]');
                const button = document.querySelector('.geo-map-wrapper .chart-save');
                if (!map || !button) return '';
                // Разметка собирается тем же кодом, что и при нажатии: проверяется
                // содержимое файла, а не то, что браузер согласился его отдать.
                let captured = '';
                const original = URL.createObjectURL;
                URL.createObjectURL = (blob) => { captured = blob; return 'blob:stub'; };
                button.click();
                URL.createObjectURL = original;
                return captured ? captured.text() : '';
            }"""
        )
        assert markup
        assert "var(--" not in markup
        assert "<svg" in markup


class TestStatusAnnouncement:
    """Объявление пересчитанной области для программ чтения с экрана."""

    def test_canvas_update_is_announced(
        self, page: Page, site: Any, warehouse_committed: Any
    ) -> None:
        """Смена параметра объявляется программе чтения с экрана (WCAG 2.2, 4.1.3)."""
        page.set_viewport_size({"width": 1600, "height": 1000})
        page.goto(f"{site.url}/ru/table/")
        page.wait_for_load_state("networkidle")

        status = page.locator("#live-status")
        expect(status).to_have_count(1)
        assert status.text_content() == ""

        districts = page.locator("select[name='district']")
        if districts.count() == 0 or districts.first.locator("option").count() < 2:
            pytest.skip("Склад не собран: отбирать не из чего")

        values = districts.first.locator("option").evaluate_all(
            "nodes => nodes.map(node => node.value)"
        )
        current = districts.first.input_value()
        other = next((value for value in values if value != current), "")
        if not other:
            pytest.skip("В складе один федеральный округ")

        districts.first.select_option(other)
        page.wait_for_timeout(1500)

        # Объявление называет предмет экрана: слушателю нужно знать не только
        # то, что расчёт выполнен, но и над каким рядом.
        assert status.text_content()


class TestPaletteMemory:
    """Палитра с пустым полем и исправление опечаток."""

    def test_recent_pages_are_offered(self, page: Page, site: Any) -> None:
        """Пустая палитра предлагает недавно открытое, хранимое в браузере."""
        page.set_viewport_size({"width": 1600, "height": 900})
        for path in ("/ru/analytics/inequality/", "/ru/regions/"):
            page.goto(f"{site.url}{path}")
            page.wait_for_load_state("domcontentloaded")

        page.keyboard.press("Control+k")
        page.wait_for_timeout(400)

        items = page.locator(".palette__item")
        assert items.count() >= 2
        titles = items.all_text_contents()
        assert any("Неравенство" in title for title in titles)

    def test_typo_is_offered_for_repair(self, page: Page, site: Any) -> None:
        """
        Опечатка предлагается к исправлению.

        Поиск отвечает вхождением подстроки, и один неверный знак означает пустую
        выдачу: «Татарстн» в наборе не встречается.
        """
        page.set_viewport_size({"width": 1600, "height": 900})
        page.goto(site.url)
        page.wait_for_load_state("networkidle")

        page.keyboard.press("Control+k")
        page.wait_for_timeout(300)
        page.fill(".palette__input", "татарстн")
        page.wait_for_timeout(900)

        suggestion = page.locator("[data-palette-suggest]")
        if suggestion.count() == 0:
            pytest.skip("Справочник пуст: исправлять не по чему")

        assert suggestion.get_attribute("data-palette-suggest") == "татарстан"

        # Нажатие подставляет исправленный запрос в поле, а не уводит по ссылке:
        # палитра остаётся раскрытой, и видно, что нашлось по исправленному запросу.
        suggestion.click()
        page.wait_for_timeout(900)
        assert page.locator(".palette__input").input_value() == "татарстан"


class TestActionPanels:
    """
    Панели «Ссылка» и «Скачать» — всплывающий слой браузера (popover).

    Открыта одна панель; она закрывается нажатием мимо и клавишей Esc.
    """

    @staticmethod
    def _open(page: Page, target: str) -> bool:
        """Открыта ли панель с этим опознавателем."""
        return bool(page.evaluate(f"document.getElementById('{target}').matches(':popover-open')"))

    def test_only_one_panel_is_open(self, page: Page, site: Any, warehouse_committed: Any) -> None:
        """Открытие второй панели закрывает первую; Esc и нажатие мимо закрывают любую."""
        page.set_viewport_size({"width": 1440, "height": 900})
        page.goto(f"{site.url}/ru/rankings/")
        page.wait_for_load_state("networkidle")
        if page.locator('[popovertarget="export-menu"]').count() == 0:
            pytest.skip("Склад не собран: выгружать нечего")

        page.locator('[popovertarget="share-menu"]').click()
        assert self._open(page, "share-menu")
        page.locator('[popovertarget="export-menu"]').click()
        assert self._open(page, "export-menu")
        assert not self._open(page, "share-menu")

        page.keyboard.press("Escape")
        assert not self._open(page, "export-menu")

        page.locator('[popovertarget="share-menu"]').click()
        page.mouse.click(700, 700)
        assert not self._open(page, "share-menu")

    def test_panel_stands_under_its_button(
        self, page: Page, site: Any, warehouse_committed: Any
    ) -> None:
        """Панель у правого края выровнена по правому краю своей кнопки и стоит под ней."""
        page.set_viewport_size({"width": 1440, "height": 900})
        page.goto(f"{site.url}/ru/map/")
        page.wait_for_load_state("networkidle")

        button = page.locator('[popovertarget="share-menu"]')
        button.click()
        panel = page.locator("#share-menu").bounding_box()
        box = button.bounding_box()
        assert panel is not None and box is not None
        assert abs((panel["x"] + panel["width"]) - (box["x"] + box["width"])) <= 1
        assert panel["y"] >= box["y"] + box["height"]


class TestStickyTableHead:
    """
    Шапка таблицы рейтинга залипает под строкой пути.

    Обёртка с overflow-x сама становится областью прокрутки и не даёт шапке залипнуть.
    """

    def test_head_stays_in_view(self, page: Page, site: Any, warehouse_committed: Any) -> None:
        """Прокрученная до середины таблица показывает шапку сразу под строкой пути."""
        page.set_viewport_size({"width": 1440, "height": 900})
        page.goto(f"{site.url}/ru/rankings/")
        page.wait_for_load_state("networkidle")
        rows = page.locator(".data-table tbody tr")
        if rows.count() < 30:
            pytest.skip("Склад не собран: таблица рейтинга короче экрана")

        rows.nth(29).scroll_into_view_if_needed()
        page.evaluate("window.scrollBy(0, 200)")
        page.wait_for_timeout(200)
        top = page.locator(".data-table thead th").first.evaluate(
            "cell => cell.getBoundingClientRect().top"
        )
        offset = page.evaluate(
            """() => { const s = getComputedStyle(document.documentElement);
                return parseFloat(s.getPropertyValue('--header-height'))
                     + parseFloat(s.getPropertyValue('--breadcrumbs-height')); }"""
        )
        assert abs(top - offset) <= 1
