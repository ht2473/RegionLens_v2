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

        assert [item.kind for item in response.context["territories"]] == ["territory"]
        assert sorted(item.kind for item in response.context["indicators"]) == [
            "indicator",
            "series",
        ]
        assert [query.title for query in response.context["queries"]] == ["Выборка"]


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
