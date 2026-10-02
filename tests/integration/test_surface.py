"""
Проверки рабочей поверхности: выбор один и переносится между пятью представлениями,
у каждого из которых свой адрес.
"""

from __future__ import annotations

import html
import re
from typing import Any
from urllib.parse import quote

import pytest
from django.test import Client
from django.urls import reverse

from tests.support import synthetic

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

# Пять представлений одного экрана: код представления и его адрес.
PANELS = (
    ("map", "maps:choropleth"),
    ("compare", "compare:index"),
    ("rankings", "rankings:index"),
    ("distribution", "surface:distribution"),
    ("table", "surface:table"),
)

PANEL_ROUTES = [route for _, route in PANELS]


@pytest.fixture
def series_key(warehouse: Any) -> str:
    """Ключ ряда численности населения: значения есть по всем субъектам и годам."""
    from apps.catalog.models import Series

    return Series.objects.get(indicator__code=synthetic.POPULATION_CODE).key


@pytest.fixture
def year(series_key: str) -> int:
    """Последний год, за который у ряда есть значения."""
    from apps.warehouse.queries import available_years

    return available_years(series_key)[-1]


@pytest.fixture
def codes(series_key: str) -> list[str]:
    """Коды двух субъектов для проверки общего выбора."""
    from apps.catalog.models import Territory

    return list(Territory.objects.comparable().order_by("code").values_list("code", flat=True)[:2])


class TestPanels:
    """Каждое представление открывается по собственному адресу."""

    @pytest.mark.parametrize(("code", "route"), PANELS)
    def test_address_opens_its_own_panel(
        self, client: Client, series_key: str, code: str, route: str
    ) -> None:
        """Адрес открывает то представление, за которым закреплён."""
        response = client.get(reverse(route), {"series": series_key})
        assert response.status_code == 200
        assert response.context["panel"].code == code

    @pytest.mark.parametrize("route", PANEL_ROUTES)
    def test_unknown_parameters_are_ignored(
        self, client: Client, series_key: str, route: str
    ) -> None:
        """
        Непонятные параметры не ломают экран.

        Ссылкой делятся, и устаревший или искажённый адрес должен открывать
        работающую страницу, а не сообщение об ошибке.
        """
        response = client.get(
            reverse(route),
            {
                "series": "нет-такого-ряда",
                "year": "вчера",
                "territory": "RU-НЕТ",
                "method": "как-нибудь",
                "classes": "много",
                "mode": "иначе",
                "order": "вверх",
                "basis": "как-то",
                "sort": "по-своему",
            },
        )
        assert response.status_code == 200


class TestSharedState:
    """Выбор один на все представления и переносится между ними."""

    def test_tabs_carry_the_selection(
        self, client: Client, series_key: str, year: int, codes: list[str]
    ) -> None:
        """Переход на другое представление уносит показатель, год и территории."""
        response = client.get(
            reverse("maps:choropleth"),
            {"series": series_key, "year": year, "territory": codes},
        )
        tabs = {tab["code"]: tab["url"] for tab in response.context["tabs"]}
        assert set(tabs) == {code for code, _ in PANELS}

        for url in tabs.values():
            assert f"year={year}" in url
            for code in codes:
                assert f"territory={code}" in url

    def test_tabs_do_not_carry_foreign_parameters(self, client: Client, series_key: str) -> None:
        """
        Параметр построения переносится только туда, где он что-то значит.

        Способ разбиения шкалы понимают карта и распределение; рейтингу он
        неизвестен, и в его адресе означал бы состояние, которого нет на экране.
        """
        response = client.get(
            reverse("maps:choropleth"),
            {"series": series_key, "method": "equal", "classes": "7"},
        )
        tabs = {tab["code"]: tab["url"] for tab in response.context["tabs"]}

        assert "method=equal" in tabs["distribution"]
        assert "classes=7" in tabs["distribution"]
        assert "method" not in tabs["rankings"]
        assert "classes" not in tabs["compare"]

    def test_territories_are_limited(self, client: Client, series_key: str) -> None:
        """Число выбранных территорий ограничено тем же числом, что и в сравнении."""
        from apps.catalog.models import Territory
        from apps.catalog.selectors import MAX_COMPARE

        all_codes = list(Territory.objects.comparable().values_list("code", flat=True))
        response = client.get(
            reverse("surface:table"), {"series": series_key, "territory": all_codes}
        )
        assert len(response.context["selected_codes"]) == MAX_COMPARE

    def test_reset_link_keeps_everything_but_territories(
        self, client: Client, series_key: str, year: int, codes: list[str]
    ) -> None:
        """Снятие выбора территорий не сбрасывает ни показатель, ни год."""
        response = client.get(
            reverse("maps:choropleth"),
            {"series": series_key, "year": year, "territory": codes, "classes": "6"},
        )
        url = response.context["territories_reset_url"]
        assert "territory=" not in url
        assert f"year={year}" in url
        assert "classes=6" in url


class TestSelectionIsVisibleEverywhere:
    """Один и тот же выбор опознаётся во всех представлениях."""

    def test_map_labels_every_selected_region(
        self, client: Client, series_key: str, codes: list[str]
    ) -> None:
        """Выбранные субъекты подписаны на карте, а не только первый из них."""
        response = client.get(
            reverse("maps:choropleth"),
            {"series": series_key, "mode": "geo", "territory": codes},
        )
        drawing = response.context["geo_map"]
        if drawing is None:  # pragma: no cover - зависит от наличия файла границ
            pytest.skip("файл границ недоступен")

        # На общей карте подпись — название субъекта, во врезке — его сокращение:
        # контур республики Кавказа мельче собственного названия.
        labelled = {label["name"] for label in drawing["labels"]}
        labelled |= {label["text"] for inset in drawing["insets"] for label in inset["labels"]}
        rows = {row["code"]: row for row in response.context["rows"]}
        for code in codes:
            row = rows[code]
            assert labelled & {row["name"], row["abbreviation"], code}

    def test_ranking_marks_selected_rows(
        self, client: Client, series_key: str, codes: list[str]
    ) -> None:
        """Строка выбранного субъекта отмечена в рейтинге."""
        response = client.get(reverse("rankings:index"), {"series": series_key, "territory": codes})
        marked = {row["territory_code"] for row in response.context["rows"] if row["selected"]}
        assert marked == set(codes)

    def test_table_marks_selected_rows(
        self, client: Client, series_key: str, codes: list[str]
    ) -> None:
        """Строка выбранного субъекта отмечена и в таблице значений."""
        response = client.get(reverse("surface:table"), {"series": series_key, "territory": codes})
        marked = {row["code"] for row in response.context["table_rows"] if row["selected"]}
        assert marked == set(codes)

    def test_distribution_shows_position_of_selected(
        self, client: Client, series_key: str, codes: list[str]
    ) -> None:
        """Распределение показывает долю субъектов ниже выбранного."""
        response = client.get(
            reverse("surface:distribution"), {"series": series_key, "territory": codes}
        )
        positions = response.context["positions"]
        assert [item["name"] for item in positions]
        assert all(0 <= item["share_below"] <= 100 for item in positions)

    def test_timeline_marks_the_chosen_year(
        self, client: Client, series_key: str, year: int, codes: list[str]
    ) -> None:
        """Год, выбранный в рейле, отмечен на оси времени графика динамики."""
        response = client.get(
            reverse("compare:index"),
            {"series": series_key, "year": year, "territory": codes},
        )
        option = response.context["timeline_option"]
        marks = [
            item["xAxis"]
            for entry in option["series"]
            if entry.get("markLine")
            for item in entry["markLine"]["data"]
        ]
        assert str(year) in marks


class TestDistributionPanel:
    """Разброс значений по субъектам."""

    def test_histogram_layers_account_for_every_valued_region(
        self, client: Client, series_key: str
    ) -> None:
        """
        Сумма долей столбцов равна числу субъектов со значением.

        Столбец разобран по классам шкалы, и потерянный при разборе субъект
        означал бы, что гистограмма показывает не то распределение, что карта.
        """
        response = client.get(reverse("surface:distribution"), {"series": series_key})
        option = response.context["histogram_option"]
        total = sum(sum(entry["data"]) for entry in option["series"])
        assert total == response.context["valued_count"]

    def test_histogram_uses_the_map_palette(self, client: Client, series_key: str) -> None:
        """Цвета долей — те же переменные оформления, что и у классов карты."""
        response = client.get(reverse("surface:distribution"), {"series": series_key})
        option = response.context["histogram_option"]
        colours = [entry["itemStyle"]["color"] for entry in option["series"]]
        expected = [f"var({name})" for name in response.context["palette"]]
        assert colours == expected

    def test_scale_is_shared_with_the_map(self, client: Client, series_key: str) -> None:
        """Разбиение шкалы совпадает с картой при тех же параметрах."""
        parameters = {"series": series_key, "method": "equal", "classes": "6"}
        chart = client.get(reverse("surface:distribution"), parameters)
        map_page = client.get(reverse("maps:choropleth"), parameters)
        assert chart.context["intervals"] == map_page.context["intervals"]


class TestTablePanel:
    """Полная таблица значений."""

    def test_every_region_takes_a_row(self, client: Client, series_key: str) -> None:
        """В таблице есть строка на каждый субъект справочника, включая выпавших из расчёта."""
        from apps.catalog.models import Territory

        response = client.get(reverse("surface:table"), {"series": series_key})
        codes = {row["code"] for row in response.context["table_rows"]}
        assert codes == set(Territory.objects.comparable().values_list("code", flat=True))

    def test_regions_without_values_carry_a_note(self, client: Client, warehouse: Any) -> None:
        """
        Субъект без значения занимает строку и назван причиной.

        Проверка выполняется на годе, которого у ряда нет: ближайший доступный год
        находится подстановкой, а строки без значения остаются с пометкой.
        """
        from apps.catalog.models import Series

        series = Series.objects.filter(is_analysis_ready=True).order_by("key").first()
        assert series is not None

        response = client.get(reverse("surface:table"), {"series": series.key})
        rows = response.context["table_rows"]
        missing = [row for row in rows if row["value"] is None]
        assert all(row["note"] for row in missing)
        assert response.context["missing_count"] == len(missing)

    def test_ordering_by_value_puts_missing_last(self, client: Client, series_key: str) -> None:
        """При упорядочивании по величине строки без значения уходят вниз."""
        response = client.get(reverse("surface:table"), {"series": series_key, "sort": "value"})
        values = [row["value"] for row in response.context["table_rows"]]
        known = [value for value in values if value is not None]
        assert known == sorted(known, reverse=True)
        assert values[: len(known)] == known


class TestFragments:
    """Обмен с браузером: что именно приходит в ответ."""

    @pytest.mark.parametrize("route", PANEL_ROUTES)
    def test_parameter_change_returns_only_the_canvas(
        self, client: Client, series_key: str, route: str
    ) -> None:
        """Смена параметра обновляет холст, а не страницу целиком."""
        response = client.get(
            reverse(route), {"series": series_key}, headers={"HX-Request": "true"}
        )
        content = response.content.decode("utf-8")
        assert "<!DOCTYPE html>" not in content
        assert "app-header" not in content
        assert 'id="surface-rail"' not in content

    def test_panel_switch_returns_the_whole_workspace(
        self, client: Client, series_key: str
    ) -> None:
        """Смена представления заменяет рабочую область целиком: рейль, заголовок окна и путь."""
        response = client.get(
            reverse("surface:table"),
            {"series": series_key},
            headers={"HX-Request": "true", "HX-Target": "surface"},
        )
        content = response.content.decode("utf-8")
        assert "<!DOCTYPE html>" not in content
        assert 'id="surface-rail"' in content
        assert "<title>" in content
        assert 'id="breadcrumbs" hx-swap-oob="true"' in content


class TestRankingYear:
    """Согласование года между представлениями."""

    def test_ranking_reports_a_shifted_year(self, client: Client, series_key: str) -> None:
        """
        Рейтинг называет год, если показал не тот, что выбран.

        Рейтинг рассчитан не за все годы ряда. Молчаливая подмена привела бы
        к сопоставлению позиций одного года со значениями другого.
        """
        from apps.warehouse.queries import available_years, ranked_years

        years = available_years(series_key)
        unranked = [year for year in years if year not in set(ranked_years(series_key))]
        if not unranked:
            pytest.skip("у ряда рассчитаны все годы")

        response = client.get(
            reverse("rankings:index"), {"series": series_key, "year": unranked[0]}
        )
        assert response.context["ranking_year_shifted"] is True
        assert response.context["ranking_year"] != unranked[0]


class TestCrossHighlight:
    """Один субъект опознаётся во всех частях холста."""

    @pytest.mark.parametrize("route", PANEL_ROUTES)
    def test_every_panel_names_its_territories(
        self, client: Client, series_key: str, codes: list[str], route: str
    ) -> None:
        """
        Разметка называет территорию одним и тем же способом в любом представлении.

        Связь держится на коде территории, а не на названии: на графике движения
        позиций линии подписаны сокращениями, а в таблице стоят полные названия.
        """
        response = client.get(reverse(route), {"series": series_key, "territory": codes[0]})
        content = response.content.decode("utf-8")
        assert f'data-territory="{codes[0]}"' in content

    def test_chart_lines_carry_territory_codes(
        self, client: Client, series_key: str, codes: list[str]
    ) -> None:
        """Линия динамики связана со своей территорией кодом."""
        response = client.get(
            reverse("compare:index"), {"series": series_key, **{"territory": codes[0]}}
        )
        option = response.context["timeline_option"]
        assert codes[0] in option["territories"].values()

    def test_ranking_movement_is_linked_too(
        self, client: Client, series_key: str, codes: list[str]
    ) -> None:
        """График движения позиций связан с таблицей рейтинга тем же кодом."""
        response = client.get(
            reverse("rankings:index"), {"series": series_key, "territory": codes[0]}
        )
        option = response.context["bump_option"]
        assert option is not None
        assert codes[0] in option["territories"].values()


class TestSharing:
    """Ссылка на текущий вид и запрос к программному интерфейсу."""

    def test_link_describes_the_whole_view(
        self, client: Client, series_key: str, year: int, codes: list[str]
    ) -> None:
        """
        Адрес несёт и выбор, и параметры построения.

        Число, названное в работе, должно открываться по ссылке в том же виде,
        в каком его увидел автор.
        """
        response = client.get(
            reverse("maps:choropleth"),
            {
                "series": series_key,
                "year": year,
                "territory": codes[0],
                "method": "equal",
                "classes": "6",
            },
        )
        address = response.context["view_url"]
        assert address.startswith("http")
        # Двоеточие в ключе ряда в адресе записывается кодом: адрес собирается
        # обычным способом, а не склейкой строк.
        assert f"series={quote(series_key, safe='')}" in address
        assert f"territory={codes[0]}" in address
        assert "method=equal" in address
        assert "classes=6" in address

    def test_year_slice_panels_ask_for_observations(
        self, client: Client, series_key: str, year: int
    ) -> None:
        """Карта, распределение и таблица показывают один год — и запрашивают его же."""
        for route in ("maps:choropleth", "surface:distribution", "surface:table"):
            response = client.get(reverse(route), {"series": series_key, "year": year})
            call = response.context["api_request"]
            assert reverse("api:v1:observations") in call.url
            assert f"year_from={year}" in call.url
            assert f"year_to={year}" in call.url

    def test_ranking_asks_for_the_year_it_showed(
        self, client: Client, series_key: str, year: int
    ) -> None:
        """
        Запрос называет год, показанный на экране.

        Рейтинг рассчитан не за все годы ряда, и запрос обязан отвечать увиденному,
        а не тому, что стоит в рейле.
        """
        response = client.get(reverse("rankings:index"), {"series": series_key, "year": year})
        call = response.context["api_request"]
        assert reverse("api:v1:rankings") in call.url
        assert f"year={response.context['ranking_year']}" in call.url

    def test_single_territory_narrows_the_timeline_request(
        self, client: Client, series_key: str, codes: list[str]
    ) -> None:
        """
        Territory передаётся, только когда выбрана ровно одна.

        Точка данных принимает один код, и подставить первый из нескольких
        означало бы вернуть не то, что показано линиями.
        """
        one = client.get(reverse("compare:index"), {"series": series_key, "territory": codes[0]})
        assert f"territory={codes[0]}" in one.context["api_request"].url

        many = client.get(
            f"{reverse('compare:index')}?series={series_key}"
            f"&territory={codes[0]}&territory={codes[1]}"
        )
        assert "territory=" not in many.context["api_request"].url

    def test_table_warns_that_gaps_are_not_returned(self, client: Client, series_key: str) -> None:
        """Названо, что субъектов без строки в складе нет в ответе интерфейса."""
        response = client.get(reverse("surface:table"), {"series": series_key})
        assert str(response.context["api_request"].note)

    def test_no_series_means_no_request(self, client: Client) -> None:
        """Без выбранного ряда запроса нет: предлагать отвергаемый адрес хуже, чем ничего."""
        response = client.get(reverse("surface:table"), {"series": "нет-такого-ряда", "year": ""})
        assert response.status_code == 200

    def test_export_kind_follows_the_panel(self, client: Client, series_key: str) -> None:
        """Вид отчёта берётся у представления, а не задан на всех один."""
        from apps.exports.constants import ReportKind

        year_slice = client.get(reverse("maps:choropleth"), {"series": series_key})
        whole_series = client.get(reverse("surface:table"), {"series": series_key})
        assert year_slice.context["panel"].export_kind == ReportKind.RANKING
        assert whole_series.context["panel"].export_kind == ReportKind.SERIES


class TestWorkspaceMarkup:
    """Устройство разметки рабочей области."""

    def test_form_covers_the_rail_only(self, client: Client, series_key: str) -> None:
        """Форма рейля не охватывает холст: вложенные формы отбрасываются разбором."""
        response = client.get(reverse("maps:choropleth"), {"series": series_key})
        content = response.content.decode("utf-8")
        rail = content.index('id="surface-rail"')
        canvas = content.index('id="surface-main"')
        assert content.index("</form>", rail) < canvas


class TestRailLayout:
    """Порядок и свёртка рейля: параметры построения видны без прокрутки."""

    @staticmethod
    def _opened(content: str, css_class: str) -> bool:
        """Раскрыт ли блок details с указанным классом."""
        match = re.search(rf'<details class="{css_class}"\s*(open)?\s*>', content)
        assert match, f"блок {css_class} не найден"
        return match.group(1) is not None

    @pytest.mark.parametrize("route", ["rankings:index", "surface:table"])
    def test_district_stands_with_territories(
        self, client: Client, series_key: str, route: str
    ) -> None:
        """Отбор по округу стоит в группе территорий, выше перечня, а не в «Построении»."""
        content = client.get(reverse(route), {"series": series_key}).content.decode("utf-8")
        district = content.index('id="surface-district"')
        title = content.index('id="surface-territories-title"')
        assert title < district < content.index('id="surface-territories"')
        assert district < content.index("Построение")
        assert content.count('name="district"') == 1

    @pytest.mark.parametrize("route", ["maps:choropleth", "compare:index", "surface:distribution"])
    def test_district_is_offered_only_where_understood(
        self, client: Client, series_key: str, route: str
    ) -> None:
        """Представления, которые округ не отбирают, поля округа не показывают."""
        content = client.get(reverse(route), {"series": series_key}).content.decode("utf-8")
        assert 'name="district"' not in content

    def test_region_list_is_folded_until_chosen(
        self, client: Client, series_key: str, codes: list[str]
    ) -> None:
        """
        Перечень субъектов свёрнут при пустом выборе и раскрыт, когда выбор есть.

        Поле поиска стоит над перечнем в любом случае: субъект чаще находят
        набором названия.
        """
        empty = client.get(reverse("maps:choropleth"), {"series": series_key})
        content = empty.content.decode("utf-8")
        assert not self._opened(content, "territory-list")
        assert content.index('data-filter-input="#surface-territories"') < content.index(
            '<details class="territory-list"'
        )
        assert f">{empty.context['territory_count']}<" in content

        chosen = client.get(
            reverse("maps:choropleth"), {"series": series_key, "territory": codes}
        ).content.decode("utf-8")
        assert self._opened(chosen, "territory-list")

    def test_dynamics_opens_the_list_at_once(self, client: Client, series_key: str) -> None:
        """Динамика без выбора не строится, и перечень у неё раскрыт сразу."""
        content = client.get(reverse("compare:index"), {"series": series_key}).content.decode(
            "utf-8"
        )
        assert self._opened(content, "territory-list")

    def test_rare_parameter_is_folded_with_its_value(
        self, client: Client, series_key: str, year: int
    ) -> None:
        """
        Сравнение лет на карте свёрнуто и называет своё значение в заголовке.

        Включённое сравнение меняет всю карту и потому раскрывает блок само.
        """
        plain = client.get(
            reverse("maps:choropleth"), {"series": series_key, "year": year}
        ).content.decode("utf-8")
        assert not self._opened(plain, "rail-more")
        assert re.search(r'data-summary-of="map-compare">\s*не сравнивать\s*<', plain)

        compared = client.get(
            reverse("maps:choropleth"),
            {"series": series_key, "year": year, "compare": year - 1},
        ).content.decode("utf-8")
        assert self._opened(compared, "rail-more")
        assert re.search(rf'data-summary-of="map-compare">\s*{year - 1}\s*<', compared)

    def test_build_parameters_precede_the_rare_one(self, client: Client, series_key: str) -> None:
        """В рейтинге порядок стоит выше свёрнутого года сравнения позиций."""
        content = client.get(reverse("rankings:index"), {"series": series_key}).content.decode(
            "utf-8"
        )
        assert content.index('name="order"') < content.index('<details class="rail-more"')
        assert content.index('<details class="rail-more"') < content.index('name="base"')


class TestRailOnANarrowScreen:
    """Полоса вызова рейля на телефоне и сводка выбора на ней."""

    @pytest.mark.parametrize("route", PANEL_ROUTES)
    def test_every_panel_offers_the_dock(self, client: Client, series_key: str, route: str) -> None:
        """Полоса вызова есть на каждом представлении и указывает на рейль."""
        response = client.get(reverse(route), {"series": series_key})
        content = response.content.decode("utf-8")
        assert "data-rail-open" in content
        assert 'aria-controls="surface-rail"' in content
        assert 'id="surface-rail"' in content

    def test_dock_names_the_series_and_the_selection(
        self, client: Client, series_key: str, year: int, codes: list[str]
    ) -> None:
        """Полоса называет показатель, как заголовок страницы, а сводка — год и число субъектов."""
        response = client.get(
            reverse("maps:choropleth"),
            {"series": series_key, "year": year, "territory": codes},
        )
        assert response.context["dock_title"] == response.context["subject"].title
        summary = response.context["dock_summary"]
        assert str(year) in summary
        assert str(len(codes)) in summary

    def test_dock_says_when_the_whole_country_is_shown(
        self, client: Client, series_key: str, year: int
    ) -> None:
        """При пустом выборе сводка говорит, что показаны все субъекты."""
        response = client.get(reverse("maps:choropleth"), {"series": series_key, "year": year})
        assert "все субъекты" in response.context["dock_summary"]

    def test_dock_comes_with_the_canvas(self, client: Client, series_key: str, year: int) -> None:
        """Полоса приходит вместе с холстом: рейль при смене параметра не пересобирается."""
        response = client.get(
            reverse("maps:choropleth"),
            {"series": series_key, "year": year},
            headers={"HX-Request": "true", "HX-Target": "surface-main"},
        )
        content = response.content.decode("utf-8")
        assert "data-rail-open" in content
        assert 'id="surface-rail"' not in content

    def test_sheet_has_a_way_out(self, client: Client, series_key: str) -> None:
        """У листа есть кнопка выхода: она же закрывает его на смартфоне."""
        response = client.get(reverse("maps:choropleth"), {"series": series_key})
        content = response.content.decode("utf-8")
        assert "data-rail-close" in content
        assert content.index("data-rail-close") > content.index('id="surface-rail"')


class TestComparabilityBreaks:
    """Предупреждение о разрыве сопоставимости и сопоставимый отрезок."""

    @pytest.fixture
    def broken_series(self, warehouse: Any) -> str:
        """Ключ ряда, у которого источник заявил смену методики."""
        from apps.catalog.models import Series

        return Series.objects.get(indicator__code=warehouse.break_code).key

    def test_breaks_are_named_above_the_chart(
        self, client: Client, broken_series: str, codes: list[str]
    ) -> None:
        """
        Разрыв назван на самом холсте, а не в примечании под графиком.

        Линия, проведённая через год смены методики, показала бы движение, которого
        могло не быть: значения по обе стороны получены по разной методике.
        """
        response = client.get(
            reverse("compare:index"), {"series": broken_series, "territory": codes[0]}
        )
        breaks = response.context["timeline_breaks"]
        assert breaks
        assert "Ряд пересекают разрывы сопоставимости" in response.content.decode("utf-8")

    def test_breaks_are_marked_on_the_time_axis(
        self, client: Client, broken_series: str, codes: list[str]
    ) -> None:
        """Отметка стоит на графике: примечание читают после того, как выводы сделаны."""
        response = client.get(
            reverse("compare:index"), {"series": broken_series, "territory": codes[0]}
        )
        option = response.context["timeline_option"]
        marks = option["series"][0]["markLine"]["data"]
        years = {item["xAxis"] for item in marks}
        assert {str(item["year"]) for item in response.context["timeline_breaks"]} & years

    def test_comparable_span_trims_the_series(
        self, client: Client, broken_series: str, codes: list[str]
    ) -> None:
        """
        Действие оставляет часть ряда после последнего разрыва.

        Внутри неё значения получены по одной методике и сравнимы между собой.
        """
        whole = client.get(
            reverse("compare:index"), {"series": broken_series, "territory": codes[0]}
        )
        start = whole.context["comparable_from"]
        assert start is not None

        trimmed = client.get(
            reverse("compare:index"),
            {"series": broken_series, "territory": codes[0], "span": "comparable"},
        )
        assert trimmed.context["timeline_trimmed"] is True
        assert min(trimmed.context["timeline_years"]) == start
        assert min(whole.context["timeline_years"]) < start

    def test_trimmed_view_offers_the_whole_series_back(
        self, client: Client, broken_series: str, codes: list[str]
    ) -> None:
        """Обратное действие снимает ограничение, а не задаёт его значением."""
        response = client.get(
            reverse("compare:index"),
            {"series": broken_series, "territory": codes[0], "span": "comparable"},
        )
        assert "span" not in response.context["span_all_url"]
        assert "span=comparable" in response.context["span_comparable_url"]

    def test_span_survives_a_change_in_the_rail(
        self, client: Client, broken_series: str, codes: list[str]
    ) -> None:
        """Сопоставимый отрезок уходит вместе с формой рейля."""
        response = client.get(
            reverse("compare:index"),
            {"series": broken_series, "territory": codes[0], "span": "comparable"},
            headers={"HX-Request": "true", "HX-Target": "surface"},
        )
        assert '<input type="hidden" name="span" value="comparable">' in response.content.decode(
            "utf-8"
        )

    def test_series_without_breaks_says_nothing(self, client: Client, series_key: str) -> None:
        """Ряд без разрывов предупреждения не получает."""
        response = client.get(reverse("compare:index"), {"series": series_key})
        assert not response.context["timeline_breaks"]


class TestExportFromTheCanvas:
    """Меню «Скачать» над холстом выгружает то, что на нём показано."""

    @staticmethod
    def _links(content: str) -> list[str]:
        """Адреса файлов из меню выгрузки."""
        menu = content[content.index('id="export-menu"') :]
        menu = menu[: menu.index("</ul>")]
        links = [html.unescape(item) for item in re.findall(r'href="([^"]*)" download', menu)]
        assert links, "в меню выгрузки нет ни одного файла"
        return links

    def test_links_carry_the_kind_and_the_order(
        self, client: Client, series_key: str, year: int
    ) -> None:
        """
        Вид документа и направление рейтинга уезжают вместе со ссылкой.

        Документ, упорядоченный не так, как таблица на экране, отвечал бы не на тот
        вопрос, который задан.
        """
        response = client.get(
            reverse("rankings:index"), {"series": series_key, "year": year, "order": "asc"}
        )
        links = self._links(response.content.decode("utf-8"))
        assert all("kind=ranking" in link for link in links)
        assert all("ascending=1" in link for link in links)
        assert all(f"year={year}" in link for link in links)

    def test_menu_offers_every_format_at_once(
        self, client: Client, series_key: str, year: int
    ) -> None:
        """
        В одном меню числа и документы: CSV, Excel и PDF.

        Вход не нужен ни для одного из них: документ собирается в ответ на запрос.
        """
        response = client.get(reverse("rankings:index"), {"series": series_key, "year": year})
        content = response.content.decode("utf-8")
        formats = {link.split("format=")[1].split("&")[0] for link in self._links(content)}
        assert formats == {"csv", "xlsx", "pdf"}
        assert "после входа" not in content

    def test_guest_downloads_numbers(self, client: Client, series_key: str, year: int) -> None:
        """Гость забирает числа таблицей прямо из меню."""
        response = client.get(
            reverse("rankings:index"), {"series": series_key, "year": year, "order": "asc"}
        )
        csv_link = next(
            link for link in self._links(response.content.decode("utf-8")) if "format=csv" in link
        )
        numbers = client.get(csv_link)
        assert numbers.status_code == 200
        assert numbers["Content-Type"].startswith("text/csv")

    def test_whole_series_export_leaves_the_order_out(
        self, client: Client, series_key: str
    ) -> None:
        """У таблицы «субъекты × годы» направления рейтинга нет вовсе."""
        response = client.get(reverse("surface:table"), {"series": series_key})
        links = self._links(response.content.decode("utf-8"))
        assert all("kind=series" in link for link in links)
        assert not any("ascending" in link for link in links)


@pytest.fixture
def wage_key(warehouse: Any) -> str:
    """Ключ ряда средней зарплаты: основной набор, не абсолютная величина."""
    from apps.catalog.models import Series

    return Series.objects.get(indicator__code=synthetic.WAGE_CODE).key


def _heading(content: str) -> str:
    """Текст единственного заголовка первого уровня без разметки."""
    assert content.count("<h1") == 1
    match = re.search(r"<h1[^>]*>(.*?)</h1>", content, re.S)
    assert match
    return " ".join(re.sub(r"<[^>]+>", "", match.group(1)).split())


class TestSubject:
    """Заголовок страницы — сам предмет: показатель и год, рядом Россия и середина регионов."""

    @pytest.mark.parametrize("route", PANEL_ROUTES)
    def test_heading_names_the_indicator(self, client: Client, wage_key: str, route: str) -> None:
        """В каждом представлении заголовок — короткое название показателя."""
        from apps.warehouse.queries import featured_set

        response = client.get(reverse(route), {"series": wage_key})
        short_title = featured_set().by_key()[wage_key].short_title
        assert response.context["subject"].title == short_title
        assert _heading(response.content.decode("utf-8")).startswith(short_title)

    def test_year_follows_the_choice(self, client: Client, wage_key: str) -> None:
        """На карте в заголовке — выбранный год, в динамике — показанный отрезок лет."""
        from apps.warehouse.queries import available_years

        years = available_years(wage_key)
        shown = client.get(reverse("maps:choropleth"), {"series": wage_key, "year": years[0]})
        assert _heading(shown.content.decode("utf-8")).endswith(str(years[0]))
        dynamics = client.get(reverse("compare:index"), {"series": wage_key})
        assert re.search(r"\d{4}–\d{4}$", _heading(dynamics.content.decode("utf-8")))

    def test_country_and_middle_are_named(self, client: Client, wage_key: str) -> None:
        """Рядом с заголовком — значение по России и середина регионов за тот же год."""
        response = client.get(reverse("maps:choropleth"), {"series": wage_key})
        subject = response.context["subject"]
        assert subject.country and subject.median
        content = response.content.decode("utf-8")
        assert subject.country in content
        assert "середина регионов" in content

    def test_heading_comes_with_the_canvas(self, client: Client, wage_key: str, year: int) -> None:
        """Смена параметра в рейле приносит новый заголовок: он стоит в самом холсте."""
        response = client.get(
            reverse("maps:choropleth"),
            {"series": wage_key, "year": year},
            headers={"HX-Request": "true", "HX-Target": "surface-main"},
        )
        assert _heading(response.content.decode("utf-8")).endswith(str(year))


class TestMapLeaders:
    """Перечни «Больше всего / Меньше всего» под картой."""

    def test_leaders_follow_the_ranking(self, client: Client, wage_key: str) -> None:
        """Сверху — места с первого, снизу — с последнего; у каждой строки ссылка на паспорт."""
        context = client.get(reverse("maps:choropleth"), {"series": wage_key}).context
        top, bottom = context["leaders_top"], context["leaders_bottom"]
        assert [row["rank"] for row in top] == [1, 2, 3, 4, 5]
        ranks = [row["rank"] for row in bottom]
        assert ranks == sorted(ranks, reverse=True)
        assert ranks[0] == max(row["rank_desc"] for row in context["rows"] if row["rank_desc"])
        assert all(row["href"].startswith("/ru/regions/") for row in (*top, *bottom))
        assert all(row["ratio"] for row in top)

    def test_additive_value_is_not_compared_with_the_country(
        self, client: Client, series_key: str
    ) -> None:
        """У численности значение страны — сумма: отношение к ней не показывается."""
        context = client.get(reverse("maps:choropleth"), {"series": series_key}).context
        assert all(row["ratio"] is None for row in context["leaders_top"])

    def test_boundaries_are_named_in_words(self, client: Client, wage_key: str) -> None:
        """В подписи карты — источник границ словами, а не имя файла."""
        content = client.get(reverse("maps:choropleth"), {"series": wage_key}).content
        assert b"ne_10m" not in content


class TestRankingShare:
    """Доля в сумме по субъектам — только у величин, которые складываются."""

    def test_share_for_additive_values(self, client: Client, series_key: str) -> None:
        """У численности доля в сумме по стране осмысленна."""
        content = client.get(reverse("rankings:index"), {"series": series_key}).content
        assert "Доля субъекта в сумме по всем субъектам".encode() in content

    def test_no_share_for_averages(self, client: Client, wage_key: str) -> None:
        """Доля «средней зарплаты» в сумме зарплат регионов — число без смысла."""
        content = client.get(reverse("rankings:index"), {"series": wage_key}).content
        assert "Доля субъекта в сумме по всем субъектам".encode() not in content


class TestActionPanels:
    """«Сохранить», «Ссылка» и «Скачать» — всплывающий слой браузера, а не <details>."""

    def test_panels_are_popovers(self, client: Client, wage_key: str) -> None:
        """Кнопка называет свою панель, панель помечена атрибутом popover."""
        content = client.get(reverse("rankings:index"), {"series": wage_key}).content.decode()
        for target in ("share-menu", "export-menu"):
            assert f'popovertarget="{target}"' in content
            assert re.search(rf'id="{target}" popover\b', content)
        assert '<details class="share-menu' not in content
        assert '<details class="export-menu' not in content

    def test_no_promise_of_api_keys(self, client: Client, wage_key: str) -> None:
        """Панель ссылки не обещает ключей API: их нет."""
        content = client.get(reverse("maps:choropleth"), {"series": wage_key}).content.decode()
        assert "ключ из личного кабинета" not in content
