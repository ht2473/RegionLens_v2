"""
Проверки личного кабинета: сохранённые виды и избранное; чужой объект — «не найдено».
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from django.db import IntegrityError, transaction
from django.test import Client
from django.urls import reverse

from apps.accounts.constants import CABINET_LIMITS
from apps.accounts.models import User
from apps.catalog.models import Indicator, Section, Series, Territory, Unit
from apps.workspace.models import Favorite, SavedQuery
from apps.workspace.services import parse_query_string

pytestmark = [pytest.mark.integration, pytest.mark.django_db]


@pytest.fixture
def catalogued(db: None) -> Series:
    """Показатель с одним рядом наблюдений — минимум, нужный избранному и виджетам."""
    section = Section.objects.create(slug="ekonomika", source_name="Экономика", name_ru="Экономика")
    unit = Unit.objects.create(source_name="Рублей", name_ru="рублей", short_name_ru="руб.")
    indicator = Indicator.objects.create(
        code="E000000001",
        slug="dohody-naseleniia",
        section=section,
        name_ru="Доходы населения",
        series_count=1,
    )
    return Series.objects.create(
        indicator=indicator,
        unit=unit,
        key="E000000001:00",
        slug="osnovnoi",
        name_ru="",
        region_coverage=1.0,
        year_count=20,
        is_analysis_ready=True,
    )


class TestQueryStringParsing:
    """Разбор строки запроса в параметры выборки."""

    def test_repeated_parameters_become_a_list(self) -> None:
        """Повторяющиеся имена собираются в список с сохранением порядка."""
        parsed = parse_query_string("series=A&series=B&year=2020")
        assert parsed == {"series": ["A", "B"], "year": "2020"}

    def test_service_parameters_are_dropped(self) -> None:
        """
        Служебные параметры не сохраняются.

        Они описывают способ обращения, а не то, что пользователь смотрел,
        и в восстановленной ссылке только мешают.
        """
        parsed = parse_query_string("year=2020&page=3&csrfmiddlewaretoken=abc")
        assert parsed == {"year": "2020"}


class TestSavedQueries:
    """Сохранение и восстановление состояния страниц расчёта."""

    def test_query_is_saved_from_page(self, member_client: Client, member: User) -> None:
        """Выборка сохраняется вместе со строкой запроса страницы."""
        response = member_client.post(
            reverse("workspace:query-save"),
            {
                "title": "Неравенство доходов",
                "target": "inequality",
                "query_string": "series=E000000001:00&year=2023",
                "back": reverse("analytics:inequality"),
            },
        )

        query = SavedQuery.objects.get(user=member)
        assert response.status_code == 302
        assert query.parameters == {"series": "E000000001:00", "year": "2023"}

    def test_saved_query_restores_page_address(self, member: User) -> None:
        """Адрес выборки собирается из страницы и её параметров."""
        query = SavedQuery.objects.create(
            user=member,
            title="Карта безработицы",
            target="map",
            parameters={"series": "E000000001:00", "year": "2023"},
        )

        assert query.url.startswith(reverse("maps:choropleth"))
        assert "series=E000000001%3A00" in query.url
        assert "year=2023" in query.url

    def test_unknown_target_is_rejected(self, member_client: Client, member: User) -> None:
        """Страница вне перечня сохраняемых не принимается."""
        member_client.post(
            reverse("workspace:query-save"),
            {"title": "Что-то", "target": "неизвестно", "query_string": ""},
        )
        assert not SavedQuery.objects.filter(user=member).exists()

    def test_opening_query_counts_and_redirects(self, member_client: Client, member: User) -> None:
        """Открытие выборки перенаправляет на расчёт и увеличивает счётчик."""
        query = SavedQuery.objects.create(
            user=member, title="Рейтинг", target="rankings", parameters={"year": "2023"}
        )

        response = member_client.get(
            reverse("workspace:query-open", kwargs={"public_id": query.public_id})
        )

        query.refresh_from_db()
        assert response.status_code == 302
        assert reverse("rankings:index") in response.url
        assert query.opened_count == 1

    def test_foreign_query_is_not_found(
        self, member_client: Client, make_user: Callable[..., Any]
    ) -> None:
        """Чужая выборка не открывается и не выдаёт факта своего существования."""
        stranger = make_user(email="stranger@example.com")
        query = SavedQuery.objects.create(user=stranger, title="Чужая", target="map")

        response = member_client.get(
            reverse("workspace:query-open", kwargs={"public_id": query.public_id})
        )
        assert response.status_code == 404

    def test_quota_limits_number_of_queries(
        self, member_client: Client, member: User, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        Предел кабинета ограничивает число сохранённых видов.

        Предел проверяется не только формой, но и сценарием: он защищает стенд,
        а не оформление страницы.
        """
        monkeypatch.setitem(CABINET_LIMITS, "saved_queries", 1)
        SavedQuery.objects.create(user=member, title="Первая", target="map")

        member_client.post(
            reverse("workspace:query-save"),
            {"title": "Вторая", "target": "map", "query_string": ""},
        )

        assert SavedQuery.objects.filter(user=member).count() == 1


class TestSaveMenu:
    """Меню «Сохранить»: вид — одним нажатием, без повторов; ответ HTMX — меню целиком."""

    def test_view_gets_a_title_by_itself(self, member_client: Client, member: User) -> None:
        """Название вида — страница и параметры словами; повтор того же вида не сохраняется."""
        payload = {"target": "map", "query_string": "year=2023", "back": "/ru/map/?year=2023"}
        member_client.post(reverse("workspace:query-save"), payload)
        member_client.post(reverse("workspace:query-save"), payload)
        titles = list(SavedQuery.objects.filter(user=member).values_list("title", flat=True))
        assert titles == ["Карта · 2023 год"]

    def test_same_title_gets_a_number(self, member_client: Client, member: User) -> None:
        """Другой вид с тем же названием, данным само, получает номер."""
        SavedQuery.objects.create(user=member, title="Карта · 2023 год", target="map")
        member_client.post(
            reverse("workspace:query-save"), {"target": "map", "query_string": "year=2023"}
        )
        titles = set(SavedQuery.objects.filter(user=member).values_list("title", flat=True))
        assert titles == {"Карта · 2023 год", "Карта · 2023 год (2)"}

    def test_htmx_answer_is_the_menu(self, member_client: Client, member: User) -> None:
        """С HTMX ответ — меню в состоянии «Сохранено» со ссылкой в «Сохранённое»."""
        response = member_client.post(
            reverse("workspace:query-save"),
            {"target": "rankings", "query_string": "year=2023", "back": "/ru/rankings/"},
            headers={"HX-Request": "true"},
        )
        content = response.content.decode()
        assert response.status_code == 200
        assert 'id="save-menu-root"' in content
        assert "Сохранено" in content
        assert reverse("workspace:saved") in content

    def test_region_menu_offers_studies(
        self, member_client: Client, member: User, reference_seed: None
    ) -> None:
        """Регион — в «Сохранённое» и к регионам исследования; исследование называется в меню."""
        from apps.userdata.models import Study

        Study.objects.create(owner=member, title="К курсовой")
        territory = Territory.objects.get(code="RU-TA")
        content = member_client.get(
            reverse("catalog:territory-detail", kwargs={"slug": territory.slug})
        ).content.decode()
        assert 'name="territory" value="RU-TA"' in content
        assert "К курсовой" in content

    def test_region_goes_to_study_regions(
        self, member_client: Client, member: User, reference_seed: None
    ) -> None:
        """«В исследование» у региона добавляет его к общим регионам исследования."""
        from apps.userdata.models import Study

        study = Study.objects.create(owner=member, title="К курсовой")
        member_client.post(
            reverse("userdata:study-add"),
            {"territory": "RU-TA", "study": str(study.public_id), "back": "/ru/"},
        )
        study.refresh_from_db()
        assert study.territories == ["RU-TA"]

    def test_guest_is_sent_to_login(self, client: Client) -> None:
        """Гостю «В Сохранённое» — после входа."""
        response = client.post(
            reverse("workspace:query-save"), {"target": "map", "query_string": ""}
        )
        assert response.status_code == 302
        assert reverse("accounts:login") in response.url


class TestSavedEditing:
    """Правка на месте и «Убрать» с «Вернуть»."""

    def test_rename_in_place(self, member_client: Client, member: User) -> None:
        """Название меняется запросом правки на месте; занятое — отказ с причиной."""
        query = SavedQuery.objects.create(user=member, title="Первая", target="map")
        SavedQuery.objects.create(user=member, title="Занято", target="map")
        url = reverse("workspace:query-change", kwargs={"public_id": query.public_id})

        response = member_client.post(
            url, {"title": "Новое имя"}, headers={"Accept": "application/json"}
        )
        assert response.json() == {"value": "Новое имя"}

        response = member_client.post(
            url, {"title": "Занято"}, headers={"Accept": "application/json"}
        )
        assert response.status_code == 400
        assert "уже сохранён" in response.json()["error"]
        query.refresh_from_db()
        assert query.title == "Новое имя"

    def test_note_in_place(self, member_client: Client, member: User, catalogued: Series) -> None:
        """Пометка вида и отметки — тем же способом; ответ — новое значение."""
        query = SavedQuery.objects.create(user=member, title="Вид", target="map")
        favorite = Favorite.objects.create(user=member, series=catalogued)
        answer = member_client.post(
            reverse("workspace:query-change", kwargs={"public_id": query.public_id}),
            {"description": "к главе 2"},
            headers={"Accept": "application/json"},
        )
        assert answer.json() == {"value": "к главе 2"}
        answer = member_client.post(
            reverse("workspace:favorite-note", kwargs={"pk": favorite.pk}),
            {"note": "сравнить с соседями"},
            headers={"Accept": "application/json"},
        )
        assert answer.json() == {"value": "сравнить с соседями"}

    def test_removed_view_comes_back_the_same(self, member_client: Client, member: User) -> None:
        """Убранный вид возвращается с тем же адресом, параметрами и счётчиком открытий."""
        query = SavedQuery.objects.create(
            user=member, title="Вид", target="map", parameters={"year": "2023"}, opened_count=4
        )
        public_id = query.public_id
        response = member_client.post(
            reverse("workspace:query-delete", kwargs={"public_id": public_id})
        )
        assert not SavedQuery.objects.filter(user=member).exists()
        page = member_client.get(response.url)
        assert "убран из Сохранённого" in page.content.decode()

        member_client.post(reverse("workspace:restore"))
        restored = SavedQuery.objects.get(user=member)
        assert restored.public_id == public_id
        assert restored.parameters == {"year": "2023"}
        assert restored.opened_count == 4

    def test_removed_mark_comes_back(
        self, member_client: Client, member: User, catalogued: Series
    ) -> None:
        """Снятая отметка возвращается с пометкой."""
        favorite = Favorite.objects.create(user=member, series=catalogued, note="зачем")
        member_client.post(reverse("workspace:favorite-delete", kwargs={"pk": favorite.pk}))
        assert not Favorite.objects.filter(user=member).exists()
        member_client.post(reverse("workspace:restore"))
        assert Favorite.objects.get(user=member).note == "зачем"

    def test_restore_is_one_time(self, member_client: Client, member: User) -> None:
        """«Вернуть» срабатывает один раз: второй раз возвращать нечего."""
        query = SavedQuery.objects.create(user=member, title="Вид", target="map")
        member_client.post(reverse("workspace:query-delete", kwargs={"public_id": query.public_id}))
        member_client.post(reverse("workspace:restore"))
        member_client.post(reverse("workspace:restore"))
        assert SavedQuery.objects.filter(user=member).count() == 1

    def test_foreign_view_is_not_changed(
        self, member_client: Client, make_user: Callable[..., Any]
    ) -> None:
        """Чужой вид не переименовать и не убрать."""
        stranger = make_user(email="stranger@example.com")
        query = SavedQuery.objects.create(user=stranger, title="Чужая", target="map")
        change = reverse("workspace:query-change", kwargs={"public_id": query.public_id})
        delete = reverse("workspace:query-delete", kwargs={"public_id": query.public_id})
        assert member_client.post(change, {"title": "Моя"}).status_code == 404
        assert member_client.post(delete).status_code == 404
        assert SavedQuery.objects.get(pk=query.pk).title == "Чужая"


class TestFavorites:
    """Отметки избранного."""

    def test_toggle_adds_and_removes(
        self, member_client: Client, member: User, catalogued: Series
    ) -> None:
        """Повторное нажатие снимает отметку."""
        payload = {
            "kind": "indicator",
            "identifier": catalogued.indicator.slug,
            "back": reverse("catalog:indicator-list"),
        }

        member_client.post(reverse("workspace:favorite-toggle"), payload)
        assert Favorite.objects.filter(user=member).count() == 1

        member_client.post(reverse("workspace:favorite-toggle"), payload)
        assert Favorite.objects.filter(user=member).count() == 0

    def test_duplicate_favorite_is_rejected_by_database(
        self, member: User, catalogued: Series
    ) -> None:
        """Ограничение целостности не допускает двух отметок на один объект."""
        Favorite.objects.create(user=member, indicator=catalogued.indicator)

        with pytest.raises(IntegrityError), transaction.atomic():
            Favorite.objects.create(user=member, indicator=catalogued.indicator)

    def test_favorite_requires_exactly_one_object(self, member: User, catalogued: Series) -> None:
        """
        Отметка ссылается ровно на один объект.

        Проверка выполняется базой данных: запись, ссылающаяся сразу на показатель
        и на территорию, не имеет смысла ни в одном разделе интерфейса.
        """
        territory = Territory.objects.create(
            code="RU-TST", slug="test", name_ru="Тестовая", source_name="Тестовая"
        )

        with pytest.raises(IntegrityError), transaction.atomic():
            Favorite.objects.create(
                user=member, indicator=catalogued.indicator, territory=territory
            )

    def test_saved_page_groups_everything(
        self, member_client: Client, member: User, catalogued: Series
    ) -> None:
        """
        Регионы, показатели и выборки — на одной странице «Сохранённое».

        Ряд показателя стоит среди показателей: для читателя это одна и та же карточка.
        """
        territory = Territory.objects.create(
            code="RU-TST", slug="test", name_ru="Тестовая", source_name="Тестовая"
        )
        Favorite.objects.create(user=member, indicator=catalogued.indicator)
        Favorite.objects.create(user=member, series=catalogued)
        Favorite.objects.create(user=member, territory=territory)
        SavedQuery.objects.create(user=member, title="Выборка", target="map")

        response = member_client.get(reverse("workspace:saved"))

        kinds = sorted(tile.kind for tile in response.context["tiles"])
        assert kinds == ["indicator", "indicator", "map", "territory"]
        assert {item["code"]: item["count"] for item in response.context["filters"]} == {
            "territory": 1,
            "indicator": 2,
            "map": 1,
        }
        chosen = member_client.get(reverse("workspace:saved"), {"kind": "territory"})
        assert [tile.title for tile in chosen.context["tiles"]] == ["Тестовая"]


class TestSavedInWords:
    """
    Сохранённое описано словами, без строки запроса.

    Строка запроса как есть («method: jenks», «series: Y477110463:00») читателю ничего не говорит.
    """

    def test_parameters_are_described(
        self, member_client: Client, member: User, catalogued: Series
    ) -> None:
        """Показатель — названием, год — годом, шкала — подписью со страницы карты."""
        SavedQuery.objects.create(
            user=member,
            title="Карта доходов",
            target="map",
            parameters={
                "series": catalogued.key,
                "year": "2024",
                "method": "jenks",
                "classes": "6",
                "kind": "absolute",
            },
        )
        content = member_client.get(reverse("workspace:saved")).content.decode()
        assert "Доходы населения" in content
        assert "2024 год" in content
        assert "по разрывам, классов: 6" in content
        for code in ("method:", "series:", "classes:", catalogued.key, "absolute"):
            assert code not in content

    def test_saved_variant_opens_itself(self, member: User, catalogued: Series) -> None:
        """Сохранённый разрез открывает страницу показателя на нём же, а не по умолчанию."""
        favorite = Favorite.objects.create(user=member, series=catalogued)
        assert favorite.url.endswith(f"?series={catalogued.key.replace(':', '%3A')}")

    def test_cabinet_opens_with_overview(self, member_client: Client) -> None:
        """Первая страница кабинета — обзор; «Сохранённое» — вкладка рядом."""
        response = member_client.get(reverse("accounts:dashboard"))
        assert response.status_code == 200
        assert response.context["cabinet_section"] == "overview"
