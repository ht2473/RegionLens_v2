"""Проверки описания schema.org и ссылок для цитирования: глазами их не проверить."""

from __future__ import annotations

import json

import pytest
from django.conf import settings
from django.test import RequestFactory

from apps.core import structured_data
from apps.core.citation import source_reference, view_citation
from apps.core.templatetags.seo import json_ld

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def dataset_version(monkeypatch: pytest.MonkeyPatch) -> None:
    """Версия набора без базы: модульные проверки к журналу версий не обращаются."""
    from apps.core import citation

    monkeypatch.setattr(citation, "current_version_code", lambda: settings.DATA_SOURCE_VERSION)
    monkeypatch.setattr(
        structured_data, "current_version_code", lambda: settings.DATA_SOURCE_VERSION
    )


@pytest.fixture
def request_at_home() -> object:
    """Запрос к главной странице: описание строит по нему полные адреса."""
    return RequestFactory().get("/ru/", HTTP_HOST="testserver")


class TestCollectionDescription:
    """Описание набора данных целиком."""

    def test_names_the_source_and_the_licence(self, request_at_home: object) -> None:
        """Правообладатель, обработка и лицензия названы: без них набор не переиспользуют."""
        data = structured_data.collection(request_at_home)

        assert data["@type"] == "Dataset"
        assert data["creator"]["name"] == settings.DATA_SOURCE_ORIGIN
        assert data["publisher"]["name"] == settings.DATA_SOURCE_PROCESSOR
        assert data["license"].startswith("https://")
        assert data["isAccessibleForFree"] is True

    def test_addresses_are_absolute(self, request_at_home: object) -> None:
        """
        Адреса полные.

        Описание читает не браузер, открывший страницу, а посторонняя служба:
        относительный путь в нём не значит ничего.
        """
        data = structured_data.collection(request_at_home)

        assert data["url"].startswith("http://testserver/")
        for item in data["distribution"]:
            assert item["contentUrl"].startswith("http://testserver/")

    def test_period_appears_only_when_known(self, request_at_home: object) -> None:
        """
        Охват по времени объявляется, только когда он известен.

        Склад может быть не собран. Объявить период, которого в наборе нет,
        хуже, чем не объявлять его вовсе.
        """
        empty = structured_data.collection(request_at_home)
        filled = structured_data.collection(request_at_home, first_year=2001, last_year=2025)

        assert "temporalCoverage" not in empty
        assert filled["temporalCoverage"] == "2001/2025"


class TestSiteDescription:
    """Описание системы как ресурса."""

    def test_search_action_carries_the_query_placeholder(self, request_at_home: object) -> None:
        """
        Адрес поиска содержит место для запроса.

        Без него поисковая машина не может собрать обращение и собственное поле
        поиска в выдаче не показывает.
        """
        data = structured_data.site(request_at_home)

        target = data["potentialAction"]["target"]["urlTemplate"]
        assert "{search_term_string}" in target
        assert data["potentialAction"]["query-input"].startswith("required")


class TestJsonLdTag:
    """Вывод описания блоком данных."""

    def test_output_parses_back(self, request_at_home: object) -> None:
        """Записанное разбирается обратно: иначе блок бесполезен."""
        markup = json_ld(structured_data.collection(request_at_home))
        body = markup.split(">", 1)[1].rsplit("<", 1)[0]

        assert json.loads(body.replace("\\u003C", "<").replace("\\u0026", "&"))

    def test_closing_tag_cannot_be_written_into_the_block(self) -> None:
        """
        Угловые скобки в значении не закрывают блок.

        Названия рядов приходят из источника и в разметку не вмешиваются, но правило
        общее: значение, способное закрыть блок данных, продолжилось бы разметкой.
        """
        markup = json_ld({"name": "</script><img onerror=alert(1)>"})

        assert "</script>" not in markup[:-9]
        assert "\\u003C" in markup

    def test_empty_description_gives_nothing(self) -> None:
        """Пустое описание не оставляет пустого блока в разметке."""
        assert json_ld(None) == ""
        assert json_ld({}) == ""


class TestCitation:
    """Ссылки для списка литературы."""

    def test_source_reference_names_processing(self) -> None:
        """Обработка названа: набор — не сам сборник Росстата, а его переработка."""
        assert settings.DATA_SOURCE_PROCESSOR in source_reference()
        assert settings.DATA_SOURCE_ORIGIN in source_reference()

    def test_view_citation_carries_the_date_and_the_version(self) -> None:
        """
        В ссылке на расчёт есть дата обращения и версия набора.

        Официальные оценки пересматриваются между выпусками: без этих двух
        сведений расхождение чисел через год объяснить нечем.
        """
        text = view_citation(
            title="Валовой региональный продукт на душу населения",
            url="http://testserver/ru/map/?series=X",
            year=2023,
        )

        assert "2023" in text
        assert settings.DATA_SOURCE_VERSION in text
        assert "дата обращения" in text
        assert "http://testserver/ru/map/?series=X" in text

    def test_collected_series_cites_its_publisher(self) -> None:
        """У ряда Банка России — его издатель и адрес, без версии набора Росстата."""
        text = view_citation(
            title="Число ипотечных кредитов",
            url="http://testserver/ru/map/?series=RL_MORTGAGE_COUNT:00",
            year="",
            series_key="RL_MORTGAGE_COUNT:00",
        )

        assert "[расчёт по данным: Банк России]" in text
        assert "cbr.ru" in text
        assert settings.DATA_SOURCE_VERSION not in text

    def test_year_is_optional(self) -> None:
        """Представление без года — динамика по всем годам — ссылается без года."""
        text = view_citation(title="Динамика", url="http://testserver/ru/compare/")

        assert text.startswith("Динамика :")
