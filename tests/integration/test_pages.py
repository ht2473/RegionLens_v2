"""Проверки публичных страниц: не менее десяти без входа, «хлебные крошки», автор в подвале."""

from __future__ import annotations

import re
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from tests.support.routes import PUBLIC_PAGES

pytestmark = [pytest.mark.integration, pytest.mark.django_db]


# Инструменты раздела «Анализ»: каждая страница со своим набором параметров.
ANALYTICS_TOOLS = [
    "analytics:inequality",
    "analytics:convergence",
    "analytics:correlation",
    "analytics:spatial",
    "analytics:index-builder",
    "analytics:revisions",
]


@pytest.fixture
def seeded(db: None, reference_seed: None) -> None:
    """Справочник территорий, доступный тесту в базе данных."""


@pytest.fixture
def catalogued(seeded: None) -> object:
    """
    Показатель с одним рядом наблюдений.

    Карточка показателя не может быть проверена на пустом каталоге, а полный каталог
    загружается только вместе с аналитическим складом, который в тестах не собирается.
    """
    from apps.catalog.models import Indicator, Section, Series, Unit

    section = Section.objects.create(slug="naselenie", source_name="Население", name_ru="Население")
    unit = Unit.objects.create(
        source_name="Тысяч человек",
        name_ru="тысяч человек",
        short_name_ru="тыс. чел.",
        kind="absolute",
    )
    indicator = Indicator.objects.create(
        code="Y000000001",
        slug="chislennost-naseleniia",
        section=section,
        name_ru="Численность населения",
        series_count=1,
    )
    return Series.objects.create(
        indicator=indicator,
        unit=unit,
        key="Y000000001:00",
        slug="osnovnoi",
        name_ru="",
        region_coverage=1.0,
        year_count=20,
        is_analysis_ready=True,
    )


class TestPublicPages:
    """Страницы, доступные неавторизованному посетителю."""

    @pytest.mark.parametrize("route", PUBLIC_PAGES)
    def test_page_is_available(self, client: Client, seeded: None, route: str) -> None:
        """Страница открывается без авторизации."""
        response = client.get(reverse(route))
        assert response.status_code == 200

    @pytest.mark.parametrize("route", PUBLIC_PAGES)
    def test_page_has_breadcrumbs(self, client: Client, seeded: None, route: str) -> None:
        """На странице есть «хлебные крошки»; у витрины их нет."""
        response = client.get(reverse(route))
        if route == "core:home":
            assert not response.context.get("breadcrumbs")
            assert 'id="breadcrumbs"' not in response.content.decode("utf-8")
            return
        assert "breadcrumbs" in response.context
        assert response.context["breadcrumbs"], route

    @pytest.mark.parametrize("route", PUBLIC_PAGES)
    def test_footer_contains_author(self, client: Client, seeded: None, route: str) -> None:
        """Подвал содержит фамилию, имя и отчество автора работы."""
        response = client.get(reverse(route))
        assert "Кузьмин Евгений Олегович" in response.content.decode("utf-8")

    def test_rail_is_reachable_on_a_phone(self, client: Client, seeded: None) -> None:
        """У каждого рейля есть полоса вызова: без неё на телефоне его не открыть."""
        railed, mute = [], []
        for route in PUBLIC_PAGES:
            content = client.get(reverse(route)).content.decode("utf-8")
            if 'class="filter-rail"' not in content:
                continue
            railed.append(route)
            if "data-rail-open" not in content or "data-rail-close" not in content:
                mute.append(route)

        assert railed, "ни на одной странице не нашлось панели параметров"
        assert not mute, "панель есть, а открыть её нечем: " + ", ".join(mute)

    def test_state_library_is_not_referenced(self, client: Client, seeded: None) -> None:
        """В разметке нет атрибутов убранной библиотеки состояния: они ничего бы не скрывали."""
        leftovers: list[str] = []
        for route in PUBLIC_PAGES:
            content = client.get(reverse(route)).content.decode("utf-8")
            for mark in ("x-data", "x-show", "x-cloak", "x-model", "x-transition", "@click"):
                if mark in content:
                    leftovers.append(f"{route}: {mark}")

        assert not leftovers, "остатки библиотеки состояния: " + ", ".join(leftovers)

    def test_pagination_links_carry_one_question_mark(self, client: Client, seeded: None) -> None:
        """Ссылка на страницу перечня — с одним знаком вопроса: querystring возвращает его сам."""
        broken: list[str] = []
        for route in PUBLIC_PAGES:
            content = client.get(reverse(route)).content.decode("utf-8")
            if 'href="??' in content or 'hx-get="??' in content:
                broken.append(route)

        assert not broken, "двойной знак вопроса в адресе: " + ", ".join(broken)

    def test_public_pages_meet_required_count(self) -> None:
        """Без входа доступно не менее десяти страниц — считаются страницы, а не пункты меню."""
        assert len(PUBLIC_PAGES) >= 10

    def test_navigation_addresses_resolve(self, client: Client, seeded: None) -> None:
        """
        Каждый пункт меню ведёт на существующий адрес.

        Опечатка в имени маршрута поднимает исключение при сборке меню,
        поэтому достаточно собрать все адреса: шапку, подвал и представленные разделы.
        """
        from apps.core.navigation import SITE_SECTIONS

        response = client.get(reverse("core:home"))
        items = response.context["main_navigation"]
        assert items

        addresses = [item["url"] for item in items]
        addresses += [child["url"] for item in items for child in item["children"]]
        addresses += [
            candidate.resolve()
            for section in SITE_SECTIONS
            for candidate in (section, *section.includes)
        ]
        assert all(address.startswith("/") for address in addresses)

    def test_header_holds_six_items(self, client: Client, seeded: None) -> None:
        """В шапке шесть пунктов верхнего уровня; новый пункт добавляется осознанно."""
        response = client.get(reverse("core:home"))
        titles = [str(item["title"]) for item in response.context["main_navigation"]]
        # Порядок публичный: сначала регион и показатель, затем рабочая поверхность.
        assert titles == [
            "Регионы",
            "Показатели",
            "Исследовать",
            "Анализ",
            "Свои данные",
            "Методика",
        ]

    @pytest.mark.parametrize(
        "route",
        [
            "maps:choropleth",
            "compare:index",
            "rankings:index",
            "surface:distribution",
            "surface:table",
        ],
    )
    def test_explore_is_open_on_every_view(self, client: Client, seeded: None, route: str) -> None:
        """«Исследовать» отмечен открытым на каждом из пяти представлений."""
        response = client.get(reverse(route))
        items = response.context["main_navigation"]
        explore = next(item for item in items if str(item["title"]) == "Исследовать")
        assert explore["active"] is True
        assert explore["children"] == []
        assert [item for item in items if item["active"]] == [explore]

    def test_analytics_menu_lists_section_tools(self, client: Client, seeded: None) -> None:
        """
        В панели «Анализа» — инструменты с собственной карточкой раздела.

        Конвергенция продолжает разбор неравенства и открывается с его страницы,
        а не из меню: вопрос о сближении задают, уже посмотрев на само неравенство.
        """
        response = client.get(reverse("core:home"))
        analytics = next(item for item in response.context["main_navigation"] if item["children"])
        assert [str(child["title"]) for child in analytics["children"]] == [
            "Интегральные индексы",
            "Неравенство",
            "Корреляции",
            "Пространственный анализ",
            "Пересмотры статистики",
        ]

    def test_convergence_continues_inequality(self, client: Client, seeded: None) -> None:
        """Со страницы неравенства ведёт ссылка на конвергенцию, путь к ней — через неравенство."""
        inequality = client.get(reverse("analytics:inequality")).content.decode("utf-8")
        assert f'href="{reverse("analytics:convergence")}"' in inequality

        response = client.get(reverse("analytics:convergence"))
        assert [str(crumb.title) for crumb in response.context["breadcrumbs"]][-2:] == [
            "Неравенство",
            "Конвергенция",
        ]

    def test_sections_left_the_header_but_not_the_site(self, client: Client, seeded: None) -> None:
        """«Данные», «API» и «О проекте» есть в подвале, на странице «О проекте» и в поиске."""
        from apps.core.navigation import matching_items

        home = client.get(reverse("core:home")).content.decode("utf-8")
        for route in ("catalog:dataset", "api:docs", "core:about"):
            assert f'href="{reverse(route)}"' in home, route

        about = client.get(reverse("core:about"))
        described = {str(section.title) for section in about.context["sections"]}
        assert {"Данные", "API", "О проекте"} <= described
        assert client.get("/ru/help/").url == reverse("core:about")

        # Конвергенции нет в меню, но быстрый переход её находит.
        assert reverse("analytics:convergence") in [
            item["url"] for item in matching_items("конвергенция")
        ]

        assert [item["url"] for item in matching_items("api")] == [reverse("api:docs")]
        assert reverse("rankings:index") in [item["url"] for item in matching_items("рейтинг")]

    def test_home_counts_breaks_instead_of_quoting_them(self, client: Client, seeded: None) -> None:
        """
        Число разрывов на витрине берётся из базы.

        Число, вписанное в строку перевода, расходится с базой после пересборки.
        """
        from apps.catalog.models import SeriesBreak

        response = client.get(reverse("core:home"))
        assert response.context["break_count"] == SeriesBreak.objects.count()
        assert "1409" not in response.content.decode("utf-8")

    def test_search_names_one_address_once(self, seeded: None) -> None:
        """
        Поиск не повторяет один переход под двумя именами.

        «Исследовать» и «Карта» ведут по одному адресу, и на запрос «карта» палитра
        предлагала бы одно и то же дважды.
        """
        from apps.core.navigation import matching_items

        found = matching_items("карта")
        urls = [item["url"] for item in found]
        assert len(urls) == len(set(urls))
        assert "Карта" in [str(item["title"]) for item in found]


class TestCatalogFiltering:
    """Фасетные фильтры каталога."""

    def test_search_narrows_results(self, client: Client, seeded: None) -> None:
        """Поисковый запрос сокращает выборку."""
        full = client.get(reverse("catalog:indicator-list"), {"view": "all"})
        filtered = client.get(reverse("catalog:indicator-list"), {"q": "безработиц"})
        assert filtered.status_code == 200
        assert filtered.context["paginator"].count <= full.context["paginator"].count

    def test_short_query_is_ignored(self, client: Client, seeded: None) -> None:
        """
        Слишком короткий запрос не применяется.

        Поиск по одной букве вернул бы почти весь каталог и только запутал бы пользователя.
        """
        full = client.get(reverse("catalog:indicator-list"), {"view": "all"})
        short = client.get(reverse("catalog:indicator-list"), {"q": "б"})
        assert short.context["paginator"].count == full.context["paginator"].count

    def test_htmx_request_returns_fragment(self, client: Client, seeded: None) -> None:
        """
        Запрос от HTMX возвращает фрагмент, а не страницу целиком.

        Иначе таблица результатов вложилась бы сама в себя вместе с шапкой и подвалом.
        """
        response = client.get(reverse("catalog:indicator-list"), headers={"HX-Request": "true"})
        content = response.content.decode("utf-8")
        assert "<!DOCTYPE html>" not in content
        assert "app-header" not in content

    def test_quick_search_requires_minimum_length(self, client: Client, seeded: None) -> None:
        """Подсказки быстрого перехода не строятся по слишком короткому запросу."""
        response = client.get(reverse("search:suggest"), {"q": "а"})
        assert response.status_code == 200
        assert "palette__item" not in response.content.decode("utf-8")

    @pytest.mark.parametrize("query", ["численность", "Численность", "ЧИСЛЕННОСТЬ"])
    def test_search_ignores_case_of_cyrillic(
        self, client: Client, catalogued: object, query: str
    ) -> None:
        """Регистр кириллицы на выдачу не влияет: при локали базы ``C`` icontains его различает."""
        response = client.get(reverse("catalog:indicator-list"), {"q": query})
        assert response.context["paginator"].count == 1

    def test_search_ignores_word_order(self, client: Client, catalogued: object) -> None:
        """Слова запроса ищутся по отдельности, поэтому их порядок не важен."""
        response = client.get(reverse("catalog:indicator-list"), {"q": "населения численность"})
        assert response.context["paginator"].count == 1

    def test_search_pattern_symbols_are_not_special(
        self, client: Client, catalogued: object
    ) -> None:
        """Знак процента в запросе ищется как знак, а не как «любая строка»."""
        response = client.get(reverse("catalog:indicator-list"), {"q": "%%"})
        assert response.context["paginator"].count == 0

    def test_section_facet_is_labelled(self, client: Client, catalogued: object) -> None:
        """У фасета разделов есть названия."""
        response = client.get(reverse("catalog:indicator-list"), {"view": "all"})
        assert [section["name"] for section in response.context["sections"]] == ["Население"]


class TestServiceEndpoints:
    """Служебные проверки состояния."""

    def test_healthz(self, client: Client) -> None:
        """Проверка живости отвечает без обращения к внешним ресурсам."""
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.content == b"ok"

    def test_readyz_reports_checks(self, client: Client, seeded: None) -> None:
        """Проверка готовности перечисляет состояние зависимостей."""
        response = client.get("/readyz")
        payload = response.json()
        assert set(payload["checks"]) == {"database", "reference", "warehouse"}
        assert payload["checks"]["reference"]["ok"] is True

    def test_pages_without_prefix_follow_the_chosen_language(
        self, client: Client, seeded: None
    ) -> None:
        """Страница без языкового префикса открывается на языке остального сайта."""
        client.get("/en/analytics/")
        response = client.get(reverse("api:docs"))
        assert "Application programming interface" in response.content.decode("utf-8")


class TestDetailPages:
    """Паспорт территории и карточка показателя."""

    def test_territory_passport_opens(self, client: Client, seeded: None) -> None:
        """Паспорт субъекта доступен по адресному идентификатору."""
        response = client.get(
            reverse("catalog:territory-detail", kwargs={"slug": "respublika-tatarstan"})
        )
        assert response.status_code == 200
        assert response.context["territory"].code == "RU-TA"

    def test_territory_passport_lists_neighbours(self, client: Client, seeded: None) -> None:
        """
        В паспорте перечислены сопредельные субъекты.

        Соседство берётся из собственной матрицы смежности — той же, на которой
        строится пространственный анализ.
        """
        response = client.get(
            reverse("catalog:territory-detail", kwargs={"slug": "respublika-tatarstan"})
        )
        assert response.context["neighbours"]

    def test_unknown_territory_returns_404(self, client: Client, seeded: None) -> None:
        """Несуществующая территория не приводит к ошибке сервера."""
        response = client.get(
            reverse("catalog:territory-detail", kwargs={"slug": "nesushchestvuiushchii-region"})
        )
        assert response.status_code == 404

    def test_series_card_opens(self, client: Client, catalogued: object) -> None:
        """Карточка показателя доступна по адресному идентификатору показателя."""
        response = client.get(
            reverse("catalog:series-detail", kwargs={"slug": "chislennost-naseleniia"})
        )
        assert response.status_code == 200
        assert response.context["series"].key == "Y000000001:00"

    def test_series_card_returns_fragment_for_htmx(
        self, client: Client, catalogued: object
    ) -> None:
        """Смена разреза обновляет только рабочую область карточки."""
        response = client.get(
            reverse("catalog:series-detail", kwargs={"slug": "chislennost-naseleniia"}),
            headers={"HX-Request": "true"},
        )
        content = response.content.decode("utf-8")
        assert "<!DOCTYPE html>" not in content
        assert "app-header" not in content


# Пять представлений рабочей поверхности: у каждого свой адрес.
SURFACE_PANELS = [
    "maps:choropleth",
    "rankings:index",
    "compare:index",
    "surface:distribution",
    "surface:table",
]


class TestAnalysisPages:
    """Представления рабочей поверхности при несобранном складе."""

    @pytest.mark.parametrize("route", SURFACE_PANELS)
    def test_page_survives_missing_warehouse(
        self, client: Client, catalogued: object, route: str
    ) -> None:
        """До первой сборки склада страница говорит, что данных ещё нет, — без путей и команд."""
        response = client.get(reverse(route))
        assert response.status_code == 200
        assert response.context["warehouse_ready"] is False
        body = response.content.decode()
        assert "Данные ещё не загружены" in body
        assert "etl_build" not in body
        assert ".duckdb" not in body

    @pytest.mark.parametrize("route", SURFACE_PANELS)
    def test_htmx_request_returns_fragment(self, client: Client, seeded: None, route: str) -> None:
        """Обновление параметров возвращает фрагмент, а не страницу целиком."""
        response = client.get(reverse(route), headers={"HX-Request": "true"})
        content = response.content.decode("utf-8")
        assert "<!DOCTYPE html>" not in content
        assert "app-header" not in content

    def test_map_ignores_unknown_parameters(self, client: Client, seeded: None) -> None:
        """
        Непонятные параметры не ломают страницу.

        Ссылкой на карту делятся, и устаревший или искажённый адрес должен открывать
        работающую страницу, а не сообщение об ошибке.
        """
        response = client.get(
            reverse("maps:choropleth"),
            {"method": "непонятно", "classes": "много", "year": "вчера", "mode": "иначе"},
        )
        assert response.status_code == 200

    def test_compare_limits_territory_count(self, client: Client, seeded: None) -> None:
        """Число сравниваемых территорий ограничено."""
        from apps.catalog.models import Territory
        from apps.catalog.selectors import MAX_COMPARE, resolve_territories

        codes = list(Territory.objects.comparable().values_list("code", flat=True))
        assert len(resolve_territories(codes)) == MAX_COMPARE


class TestBoundaries:
    """Файл границ субъектов, хранящийся в репозитории."""

    def test_file_is_present(self) -> None:
        """Файл границ подготовлен и лежит в каталоге статики."""
        from apps.maps.boundaries import boundaries_available

        assert boundaries_available()

    def test_composition_matches_reference(self, seeded: None) -> None:
        """
        Состав геометрии совпадает со справочником территорий.

        Расхождение означало бы, что часть субъектов останется на карте
        незакрашенной, — и обнаружилось бы только в браузере.
        """
        import json

        from apps.catalog.models import Territory
        from apps.maps.boundaries import boundaries_path

        payload = json.loads(boundaries_path().read_text(encoding="utf-8"))
        codes = {feature["properties"]["code"] for feature in payload["features"]}
        expected = set(Territory.objects.comparable().values_list("code", flat=True))
        assert codes == expected

    def test_source_is_attributed(self) -> None:
        """Источник и лицензия границ указаны в самом файле."""
        from apps.maps.boundaries import boundaries_meta

        meta = boundaries_meta()
        assert "Natural Earth" in meta["source"]
        assert meta["licence"]

    def test_tile_layout_covers_all_regions(self, seeded: None) -> None:
        """
        Плиточная раскладка задана для всех субъектов и не содержит наложений.

        Пропущенная позиция означала бы субъект, отсутствующий на схематичной карте.
        """
        from apps.catalog.models import Territory

        placed = list(Territory.objects.comparable().values_list("code", "tile_x", "tile_y"))
        cells = [(x, y) for _, x, y in placed]
        assert all(x is not None and y is not None for _, x, y in placed)
        assert len(set(cells)) == len(cells)


class TestAnalyticsSection:
    """Раздел «Анализ»: инструменты и обзорная страница."""

    def test_section_lists_every_tool(self, client: Client, seeded: None) -> None:
        """
        Обзорная страница перечисляет все инструменты раздела.

        Кроме конвергенции: она продолжает разбор неравенства и открывается с его страницы.
        """
        response = client.get(reverse("analytics:index"))
        listed = {f"analytics:{tool.url_name}" for tool in response.context["tools"]}
        assert listed == set(ANALYTICS_TOOLS) - {"analytics:convergence"}

    @pytest.mark.parametrize("route", ANALYTICS_TOOLS)
    def test_tool_opens_without_warehouse(self, client: Client, seeded: None, route: str) -> None:
        """Инструмент при несобранном складе объясняет, чего не хватает, а не отвечает ошибкой."""
        response = client.get(reverse(route))
        content = response.content.decode("utf-8")
        assert response.status_code == 200
        assert "callout--warning" in content or "empty-state" in content

    @pytest.mark.parametrize("route", ANALYTICS_TOOLS)
    def test_tool_returns_fragment_to_htmx(self, client: Client, seeded: None, route: str) -> None:
        """Смена параметров обновляет только рабочую область, а не страницу целиком."""
        response = client.get(reverse(route), headers={"HX-Request": "true"})
        content = response.content.decode("utf-8")
        assert response.status_code == 200
        assert "<!DOCTYPE html>" not in content
        assert "app-header" not in content

    @pytest.mark.parametrize("route", ANALYTICS_TOOLS)
    def test_tool_has_breadcrumbs_to_section(
        self, client: Client, seeded: None, route: str
    ) -> None:
        """Путь к инструменту проходит через раздел, а не ведёт прямо с главной."""
        response = client.get(reverse(route))
        titles = [str(crumb.title) for crumb in response.context["breadcrumbs"]]
        assert "Анализ" in titles

    @pytest.mark.parametrize("route", ANALYTICS_TOOLS)
    def test_malformed_parameters_are_ignored(
        self, client: Client, seeded: None, route: str
    ) -> None:
        """
        Непонятный параметр заменяется значением по умолчанию.

        Ссылка, скопированная из другого контекста или устаревшая после пересборки
        склада, должна открывать работающую страницу, а не сообщение об ошибке.
        """
        response = client.get(
            reverse(route),
            {
                "series": "нет-такого-ряда",
                "year": "позапрошлый",
                "method": "выдуманный",
                "clusters": "много",
                "horizon": "-5",
            },
        )
        assert response.status_code == 200


class TestSimilarRegions:
    """Похожие регионы в паспорте субъекта."""

    ADDRESS = "catalog:territory-detail"
    SLUG = "respublika-tatarstan"

    def test_calculation_waits_for_the_button(self, client: Client, warehouse: Any) -> None:
        """
        При открытии паспорта сходство не считается.

        Расчёт обращается к складу за шестью рядами и разбивает восемьдесят пять
        субъектов на группы, а вопрос о сходстве задают не всегда.
        """
        response = client.get(reverse(self.ADDRESS, kwargs={"slug": self.SLUG}))
        assert response.context["similar_requested"] is False
        assert "similar" not in response.context

    def test_button_leads_to_the_same_address(self, client: Client, warehouse: Any) -> None:
        """Кнопка — обычная ссылка: ответ на неё адресуем и работает без сценариев."""
        response = client.get(reverse(self.ADDRESS, kwargs={"slug": self.SLUG}))
        assert "similar=1" in response.context["similar_url"]

    def test_closed_block_loads_on_first_opening(self, client: Client, warehouse: Any) -> None:
        """Блок закрыт и догружает перечень при первом раскрытии."""
        content = client.get(reverse(self.ADDRESS, kwargs={"slug": self.SLUG})).content.decode()
        block = re.search(r'<details class="similar-block"[^>]*>', content)
        assert block
        assert " open" not in block.group(0)
        assert 'hx-trigger="toggle once"' in block.group(0)
        # Пока идёт расчёт, видна строка «Расчёт…», а запасная ссылка скрыта: обе
        # названы в hx-indicator, и htmx на время запроса меняет их местами.
        assert "similar-block__wait" in block.group(0)
        assert "similar-block__fallback" in block.group(0)
        assert "similar-block__wait" in content
        assert "similar-block__fallback" in content

    def test_fragment_does_not_build_the_whole_page(self, client: Client, warehouse: Any) -> None:
        """Фрагмент похожих регионов собирает только перечень, а не весь паспорт."""
        response = client.get(
            reverse(self.ADDRESS, kwargs={"slug": self.SLUG}),
            {"similar": "1"},
            headers={"HX-Request": "true", "HX-Target": "territory-similar"},
        )
        assert response.context["similar"] is not None
        for absent in ("passport", "metrics", "locator", "neighbours", "timeline_option"):
            assert absent not in response.context
        assert len(response.content) < len(
            client.get(reverse(self.ADDRESS, kwargs={"slug": self.SLUG})).content
        )

    def test_timeline_fragment_builds_only_the_chart(self, client: Client, warehouse: Any) -> None:
        """Фрагмент графика тоже собирает только своё: паспорт ему не нужен."""
        response = client.get(
            reverse(self.ADDRESS, kwargs={"slug": self.SLUG}),
            headers={"HX-Request": "true", "HX-Target": "territory-timeline"},
        )
        assert "timeline_series" in response.context
        assert "passport" not in response.context
        assert "metrics" not in response.context

    def test_address_opens_the_block(self, client: Client, warehouse: Any) -> None:
        """Адрес с ?similar=1 открывает блок сразу и второй раз не запрашивает перечень."""
        content = client.get(
            reverse(self.ADDRESS, kwargs={"slug": self.SLUG}), {"similar": "1"}
        ).content.decode()
        block = re.search(r'<details class="similar-block"[^>]*>', content)
        assert block
        assert " open" in block.group(0)
        assert "hx-get" not in block.group(0)
        assert "расстояние: меньше — ближе" in content

    def test_features_are_named_briefly(self, client: Client, warehouse: Any) -> None:
        """«Сильнее всего расходятся» называет показатель коротким названием набора."""
        from apps.warehouse.queries import featured_set

        short = {item.key: item.short_title for item in featured_set().series}
        response = client.get(reverse(self.ADDRESS, kwargs={"slug": self.SLUG}), {"similar": "1"})
        result = response.context["similar"]
        for item in result.items:
            column = result.columns[item["apart"]]
            assert item["apart_label"] == short.get(column["key"], column["short"])

    def test_similar_regions_are_found(self, client: Client, warehouse: Any) -> None:
        """
        Перечень похожих субъектов упорядочен по расстоянию.

        Сходство определяется сочетанием ключевых показателей, а не расположением
        на карте: соседство по границе сходства не означает.
        """
        response = client.get(reverse(self.ADDRESS, kwargs={"slug": self.SLUG}), {"similar": "1"})
        result = response.context["similar"]
        assert result is not None
        assert result.items
        distances = [item["distance"] for item in result.items]
        assert distances == sorted(distances)
        assert all(item["code"] != "RU-TA" for item in result.items)

    def test_features_and_their_years_are_named(self, client: Client, warehouse: Any) -> None:
        """
        Названо, по каким показателям и за какие годы считалось сходство.

        Последний доступный год у разных рядов не совпадает, и умолчать об этом
        нельзя: сопоставление величин разных лет без пометки было бы подлогом.
        """
        response = client.get(reverse(self.ADDRESS, kwargs={"slug": self.SLUG}), {"similar": "1"})
        columns = response.context["similar"].columns
        assert columns
        assert all(column["label"] for column in columns)

    def test_fragment_answers_the_button(self, client: Client, warehouse: Any) -> None:
        """Нажатие обновляет только свой блок, а не страницу целиком."""
        response = client.get(
            reverse(self.ADDRESS, kwargs={"slug": self.SLUG}),
            {"similar": "1"},
            headers={"HX-Request": "true", "HX-Target": "territory-similar"},
        )
        content = response.content.decode("utf-8")
        assert "<!DOCTYPE html>" not in content
        assert 'id="territory-similar"' in content

    def test_timeline_fragment_is_not_confused_with_it(
        self, client: Client, warehouse: Any
    ) -> None:
        """Смена показателя на графике обновляет график — второй фрагмент страницы."""
        response = client.get(
            reverse(self.ADDRESS, kwargs={"slug": self.SLUG}),
            headers={"HX-Request": "true", "HX-Target": "territory-timeline"},
        )
        content = response.content.decode("utf-8")
        assert 'id="territory-similar"' not in content
