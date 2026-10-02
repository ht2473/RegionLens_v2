"""
Набор обновляется: проверка новой версии на странице набора, отметка в панели, новость
в кабинете, пометки «публикация прекращена» при новых значениях, версия в ссылке.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from unittest.mock import patch

import duckdb
import pytest
from django.core.management import call_command
from django.test import RequestFactory
from django.utils import timezone

from apps.catalog.models import DatasetVersion
from apps.sources import dataset_watch, network

pytestmark = pytest.mark.integration

STORAGE = "https://storage.yandexcloud.net/tochno-st-catalog/Rosstat"
NEWER = "v20261120"


def _page(*versions: str) -> str:
    """Страница набора с адресами файлов названных версий."""
    return "".join(
        f'<a href="{STORAGE}/data_regions_collection_102_{code}/'
        f'data_01_socio_economic_102_{code}.zip">CSV</a>'
        for code in versions
    )


def _version(code: str, *, current: bool = True, days_ago: int = 0) -> DatasetVersion:
    """Версия набора в журнале; текущая снимает отметку с прежних."""
    if current:
        DatasetVersion.objects.filter(is_current=True).update(is_current=False)
    version = DatasetVersion.objects.create(
        code=code,
        published_on=date(int(code[1:5]), int(code[5:7]), int(code[7:9])),
        title="Социально-экономические показатели регионов России",
        publisher="Росстат",
        is_current=current,
    )
    if days_ago:
        DatasetVersion.objects.filter(pk=version.pk).update(
            created_at=timezone.now() - timedelta(days=days_ago)
        )
        version.refresh_from_db()
    return version


class TestDatasetWatch:
    """Версия на странице «Если быть точным» против загруженной."""

    def test_versions_are_read_from_file_addresses(self) -> None:
        """Версии берутся из адресов файлов, по возрастанию и без повторов."""
        page = _page(NEWER, "v20260313", NEWER)
        assert dataset_watch.versions_on_page(page) == ["v20260313", NEWER]

    def test_same_version_is_not_newer(self, db: None) -> None:
        """На сайте загруженная версия: проверка удалась, новой версии нет."""
        _version("v20260313")
        with patch.object(network, "page", return_value=_page("v20260313")):
            beat = dataset_watch.check()
        assert beat.ok
        assert beat.detail["found"] == "v20260313"
        assert dataset_watch.newer_version() == ""

    def test_newer_version_is_reported_in_panel(self, db: None) -> None:
        """Новая версия на сайте — пункт «Требует внимания» и тревога в состоянии служб."""
        from apps.dashboard.selectors import attention_items, system_health

        _version("v20260313")
        with patch.object(network, "page", return_value=_page("v20260313", NEWER)):
            dataset_watch.check()

        assert dataset_watch.newer_version() == NEWER
        titles = [str(item["title"]) for item in attention_items()]
        assert any(NEWER in title for title in titles)
        row = system_health()["dataset"]
        assert not row["ok"]
        assert NEWER in str(row["extra"])

    def test_loaded_version_clears_the_notice(self, db: None) -> None:
        """После загрузки новой версии напоминание пропадает без новой проверки."""
        _version("v20260313")
        with patch.object(network, "page", return_value=_page(NEWER)):
            dataset_watch.check()
        _version(NEWER)
        assert dataset_watch.newer_version() == ""

    def test_page_without_versions_is_a_failed_check(self, db: None) -> None:
        """Страница без адресов с версией — проверка не удалась, ложной тревоги нет."""
        from apps.dashboard.selectors import system_health

        with patch.object(network, "page", return_value="<html></html>"):
            beat = dataset_watch.check()
        assert not beat.ok
        assert dataset_watch.newer_version() == ""
        assert not system_health()["dataset"]["ok"]

    def test_network_failure_is_recorded(self, db: None) -> None:
        """Сайт не ответил — отметка с ошибкой."""
        with patch.object(network, "page", side_effect=network.FetchError("нет ответа")):
            beat = dataset_watch.check()
        assert not beat.ok
        assert "нет ответа" in beat.detail["error"]

    def test_daily_collect_checks_once_a_day(self, db: None) -> None:
        """``collect --due`` проверяет набор, повтор в те же сутки — нет."""
        from apps.sources import collect

        with (
            patch.object(collect, "run", return_value=[]),
            patch.object(network, "page", return_value=_page("v20260313")) as page,
        ):
            call_command("collect", "--due")
            call_command("collect", "--due")
        assert page.call_count == 1


class TestDiscontinuedCheck:
    """Проверка при сборке: ряды с пометкой «прекращена», у которых появились значения."""

    def test_values_after_stop_year_are_reported(self, db: None) -> None:
        """Значение с года прекращения — замечание; ряд без него и продолжаемый ряд — нет."""
        from apps.catalog.status import SeriesStatus
        from apps.warehouse.etl import quality
        from apps.warehouse.models import DataQualityCheck, EtlRun

        connection = duckdb.connect()
        connection.execute("CREATE TABLE dim_series (series_key VARCHAR, full_title_ru VARCHAR)")
        connection.execute(
            "CREATE TABLE fact_observation (series_key VARCHAR, year SMALLINT, value DOUBLE)"
        )
        connection.execute(
            "INSERT INTO dim_series VALUES ('A:00', 'Ряд А'), ('B:00', 'Ряд Б'), ('C:00', 'Ряд В')"
        )
        connection.execute(
            "INSERT INTO fact_observation VALUES "
            "('A:00', 2023, 1), ('A:00', 2025, 2), ('B:00', 2023, 1), ('B:00', 2025, NULL), "
            "('C:00', 2026, 1)"
        )
        statuses = [
            SeriesStatus(key="A:00", status="discontinued", since=2025),
            SeriesStatus(key="B:00", status="discontinued", since=2025),
            SeriesStatus(key="C:00", status="continued"),
        ]
        run = EtlRun.objects.create()

        with patch.object(quality, "all_statuses", return_value=statuses):
            found = quality._check_discontinued_series(connection, run)

        assert [(item.series_key, item.year) for item in found] == [("A:00", 2025)]
        assert found[0].check_type == DataQualityCheck.CheckType.STATUS_OUTDATED
        assert "2025" in found[0].display_message

    def test_panel_links_to_filtered_checks(self, db: None) -> None:
        """Замечание в «Требует внимания» ведёт к перечню, отобранному по виду проверки."""
        from apps.dashboard.selectors import attention_items
        from apps.warehouse.models import DataQualityCheck, EtlRun

        DataQualityCheck.objects.create(
            run=EtlRun.objects.create(),
            check_type=DataQualityCheck.CheckType.STATUS_OUTDATED,
            severity=DataQualityCheck.Severity.WARNING,
            series_key="A:00",
            message="Ряд А: значения с 2025 года",
        )

        items = [item for item in attention_items() if item["url_name"] == "dashboard:quality"]
        assert [item["query"] for item in items] == ["type=status_outdated"]


class TestCurrentVersion:
    """Версия набора в ссылках и описании — из журнала версий."""

    def test_citation_and_context_use_current_version(self, db: None) -> None:
        """Ссылка для цитирования и контекст шаблонов называют текущую версию."""
        from apps.core.citation import _dataset_part
        from apps.core.context_processors import project_metadata

        _version("v20260313", days_ago=30)
        _version(NEWER)

        assert NEWER in _dataset_part()
        context = project_metadata(RequestFactory().get("/"))
        assert str(context["data_source"]["version"]) == NEWER

    def test_without_versions_the_setting_is_used(self, db: None, settings: Any) -> None:
        """Журнал пуст — версия из настроек."""
        from apps.catalog.versions import current_version_code

        assert current_version_code() == settings.DATA_SOURCE_VERSION


class TestDatasetNews:
    """«Новое по сохранённому»: версия набора, сменившая прежнюю."""

    @staticmethod
    def _saved(make_user: Any) -> Any:
        from apps.catalog.models import Series, Territory
        from apps.workspace.models import Favorite

        user = make_user(email="reader@example.com")
        Favorite.objects.create(user=user, series=Series.objects.get(key="Y477110006:00"))
        region = Territory.objects.filter(level="region").order_by("code").first()
        Favorite.objects.create(user=user, territory=region)
        return user

    def test_replacing_version_is_news(self, warehouse: Any, make_user: Any) -> None:
        """Новая версия называет отмеченный ряд с последним годом и отмеченный регион."""
        from apps.workspace.updates import saved_updates

        DatasetVersion.objects.update(created_at=timezone.now() - timedelta(days=30))
        _version(NEWER)
        user = self._saved(make_user)

        news = saved_updates(user, timezone.now() - timedelta(days=5))
        item = next(entry for entry in news if entry.release.startswith("версия"))
        assert item.is_new
        assert NEWER in item.release
        assert item.series and item.series[0].period.endswith("год")
        assert item.regions and item.regions[0].count > 0
        assert item.regions_label

    def test_first_version_is_not_news(self, warehouse: Any, make_user: Any) -> None:
        """Единственная (первая) версия набора новостью не считается."""
        from apps.workspace.updates import saved_updates

        user = self._saved(make_user)
        news = saved_updates(user, timezone.now() - timedelta(days=5))
        assert not any(entry.release.startswith("версия") for entry in news)
