"""
Проверки программного интерфейса для анонимного клиента.

Точки над несобранным складом отвечают 503 с объяснением, а не 500.
"""

from __future__ import annotations

import re

import pytest
from django.test import Client
from django.urls import reverse
from rest_framework.exceptions import ValidationError

from apps.api.exceptions import api_exception_handler
from apps.catalog.models import Indicator, Section, Series, Unit

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

# Точки справочников: они обслуживаются ORM и доступны без собранного склада.
CATALOG_ENDPOINTS = [
    "api:v1:territory-list",
    "api:v1:indicator-list",
    "api:v1:series-list",
]


@pytest.fixture
def catalogued(db: None) -> Series:
    """Показатель с одним рядом наблюдений."""
    section = Section.objects.create(slug="trud", source_name="Труд", name_ru="Труд")
    unit = Unit.objects.create(source_name="Процентов", name_ru="процентов", short_name_ru="%")
    indicator = Indicator.objects.create(
        code="T000000001",
        slug="uroven-bezraboticy",
        section=section,
        name_ru="Уровень безработицы",
        series_count=1,
    )
    return Series.objects.create(
        indicator=indicator,
        unit=unit,
        key="T000000001:00",
        slug="osnovnoi",
        name_ru="",
        region_coverage=0.95,
        year_count=20,
        is_analysis_ready=True,
    )


class TestPublicAccess:
    """Чтение справочников."""

    @pytest.mark.parametrize("route", CATALOG_ENDPOINTS)
    def test_endpoint_answers_anonymous_client(
        self, client: Client, catalogued: Series, route: str
    ) -> None:
        """Справочник читается анонимным клиентом."""
        response = client.get(reverse(route))
        assert response.status_code == 200
        assert "results" in response.json()

    def test_series_detail_is_addressed_by_key(self, client: Client, catalogued: Series) -> None:
        """
        Ряд адресуется своим ключом, включая двоеточие в нём.

        Ключ имеет вид «код показателя : признак разреза», и двоеточие в адресе
        не должно требовать экранирования от клиента.
        """
        response = client.get(reverse("api:v1:series-detail", kwargs={"key": catalogued.key}))

        assert response.status_code == 200
        assert response.json()["key"] == catalogued.key
        assert response.json()["indicator"] == catalogued.indicator.code

    def test_indicator_search_filters_by_name(self, client: Client, catalogued: Series) -> None:
        """Поиск по названию отбирает показатели."""
        found = client.get(reverse("api:v1:indicator-list"), {"search": "безработиц"})
        missing = client.get(reverse("api:v1:indicator-list"), {"search": "рождаемост"})

        assert found.json()["count"] == 1
        assert missing.json()["count"] == 0

    def test_schema_and_docs_are_available(self, client: Client) -> None:
        """Схема OpenAPI и страница описания открываются."""
        assert client.get(reverse("api:schema")).status_code == 200
        assert client.get(reverse("api:docs")).status_code == 200
        assert client.get(reverse("api:swagger")).status_code == 200

    def test_swagger_needs_no_inline_script(self, client: Client) -> None:
        """Оболочка Swagger запускается сценарием-файлом: встроенный CSP не пропустит."""
        page = client.get(reverse("api:swagger")).content.decode()
        assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", page), "встроенный сценарий"
        assert '<html lang="ru">' in page, "язык страницы"
        script = client.get(reverse("api:swagger") + "?script=")
        assert script.status_code == 200
        assert script["Content-Type"].startswith("application/javascript")
        assert "SwaggerUIBundle" in script.content.decode()


class TestWarehouseEndpoints:
    """Точки, работающие поверх аналитического склада."""

    def test_observations_report_unavailable_warehouse(
        self, client: Client, catalogued: Series
    ) -> None:
        """
        Несобранный склад — это 503 с объяснением, а не внутренняя ошибка.

        Справочники в этот момент уже наполнены, и система работоспособна;
        ответ должен отличать «данных ещё нет» от «сломалось».
        """
        response = client.get(reverse("api:v1:observations"), {"series": catalogued.key})

        assert response.status_code == 503
        assert response.json()["code"] == "warehouse_not_built"
        # Путь к файлу склада и команда сборки остаются в журнале, клиенту не показываются.
        assert response.json()["error"] == "Данные ещё не загружены"

    def test_observations_require_series(self, client: Client) -> None:
        """Обязательный параметр проверяется до обращения к складу."""
        response = client.get(reverse("api:v1:observations"))

        payload = response.json()
        assert response.status_code == 400
        assert "series" in payload["fields"]

    def test_year_range_is_validated(self, client: Client, catalogued: Series) -> None:
        """Переставленные границы периода отклоняются с понятным сообщением."""
        response = client.get(
            reverse("api:v1:observations"),
            {"series": catalogued.key, "year_from": 2020, "year_to": 2010},
        )

        assert response.status_code == 400
        assert "year_from" in response.json()["fields"]

    def test_field_message_is_not_split_into_letters(self) -> None:
        """
        Отказ поля, заданный строкой, приходит одной строкой в перечне.

        Рейтинг за отсутствующий год отклоняется словарём ``{"year": "…"}``; перебор
        такого значения как перечня разбивал сообщение по буквам.
        """
        response = api_exception_handler(ValidationError({"year": "Нет рейтинга"}), {})

        assert response is not None
        assert response.data["fields"] == {"year": ["Нет рейтинга"]}

    def test_unknown_object_has_not_found_code(self, client: Client, catalogued: Series) -> None:
        """Объект, не найденный по коду, отвечает тем же кодом ошибки, что и пустая выборка."""
        response = client.get(reverse("api:v1:territory-detail", kwargs={"code": "XX"}))

        assert response.status_code == 404
        assert response.json()["code"] == "not_found"


class TestObservationSerialisation:
    """Представление наблюдения в ответе."""

    @pytest.mark.parametrize(
        ("code", "expected"),
        [(0, "observed"), (1, "no_data"), (2, "hidden"), (3, "not_applicable"), (None, "unknown")],
    )
    def test_quality_is_named(self, code: int | None, expected: str) -> None:
        """
        Признак качества отдаётся словом, а не числом.

        Число «2» ничего не говорит клиенту, а различать «нет данных» и «скрыто»
        он обязан: это разные основания для пропуска.
        """
        from apps.api.serializers import ObservationSerializer

        row = {
            "series_key": "T000000001:00",
            "territory_code": "RU-BEL",
            "year": 2023,
            "value": 1.5,
            "quality": code,
        }
        assert ObservationSerializer(row).data["quality"] == expected


class TestThrottling:
    """Ограничение числа обращений."""

    def test_anonymous_requests_are_limited(
        self, client: Client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        Предел обращений применяется и сообщает время ожидания.

        Предел подменяется на классе ограничителя: DRF читает пределы один раз при загрузке.
        """
        from apps.api.throttling import AnonymousHourlyThrottle

        monkeypatch.setattr(AnonymousHourlyThrottle, "THROTTLE_RATES", {"anon": "2/hour"})

        for _ in range(2):
            assert client.get(reverse("api:v1:territory-list")).status_code == 200

        response = client.get(reverse("api:v1:territory-list"))
        assert response.status_code == 429
        assert response.json()["retry_after"] > 0


@pytest.mark.parametrize("language", ["ru", "en"])
def test_docs_examples_return_data(client: Client, warehouse: object, language: str) -> None:
    """
    Каждый пример страницы описания по данным набора отвечает непустым результатом.

    Помесячному слою и журналу выпусков нужны собранные выпуски — их проверяет test_monthly.
    """
    from django.utils import translation

    from apps.api.docs import ENDPOINTS

    collected = {"api:v1:monthly", "api:v1:source-list", "api:v1:releases"}
    with translation.override(language):
        examples = [
            str(endpoint.example)
            for endpoint in ENDPOINTS
            if endpoint.example and endpoint.url_name not in collected
        ]
    assert len(examples) >= 4
    page = client.get(reverse("api:docs"), HTTP_ACCEPT_LANGUAGE=language).content.decode("utf-8")
    commands = re.findall(r'curl "https?://[^/]+(/api/v1/[^"]+)"', page.replace("&amp;", "&"))
    assert commands
    for example in [*examples, *commands]:
        response = client.get(example)
        assert response.status_code == 200, example
        payload = response.json()
        rows = payload.get("results", payload) if isinstance(payload, dict) else payload
        assert rows, example
