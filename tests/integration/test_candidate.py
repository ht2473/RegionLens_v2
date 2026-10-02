"""
Проверка новой версии набора: сборка рядом с рабочим складом, отчёт о различиях,
решение в панели, скачивание новой версии при сборе.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any
from unittest.mock import patch

import duckdb
import pytest
from django.core import mail
from django.test import Client
from django.urls import reverse

from apps.catalog.models import DatasetVersion, Series
from apps.core.models import ServiceBeat
from apps.sources import dataset_watch, network
from apps.warehouse import builds, duckdb_client
from apps.warehouse.etl import compare
from apps.warehouse.etl.classify import make_series_key
from apps.warehouse.models import EtlRun
from tests.support.synthetic import LABOUR_FORCE_CODE, LIFE_EXPECTANCY_CODE, POPULATION_CODE
from tests.support.warehouse import accept_small_source

pytestmark = pytest.mark.integration

ADMIN = "admin@example.org"
NEWER = "v20270115"
NOTE = (
    "С 2020 года численность рассчитывается с учётом итогов Всероссийской переписи "
    "населения 2020 года, поэтому данные за предшествующие годы несопоставимы с последующими"
)


@pytest.fixture(autouse=True)
def inline_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """Сборка, начатая из панели, выполняется в процессе теста."""
    monkeypatch.setattr(builds, "launch", lambda run: builds.run_queued(run.pk))


@pytest.fixture
def working(settings: Any, tmp_path: Path, warehouse: Any, warehouse_file: Path) -> Any:
    """Рабочий склад — копия тестового; наборы и сборки — во временном каталоге."""
    target = tmp_path / "regionlens.duckdb"
    shutil.copy(warehouse_file, target)
    settings.DUCKDB_PATH = target
    settings.DATASET_UPLOAD_DIR = tmp_path / "sources"
    settings.SOURCE_PARQUET_PATH = warehouse.path
    settings.ADMINS = [ADMIN]
    duckdb_client.close_connections()
    with accept_small_source():
        yield target
    duckdb_client.close_connections()


@pytest.fixture
def changed_dataset(tmp_path: Path, warehouse: Any) -> Path:
    """
    Новая версия синтетического набора.

    Значения продолжительности жизни выше на 10 %, разрез «Мужчины» записан с переносом,
    у численности населения — новое примечание.
    """
    target = tmp_path / f"data_regions_collection_102_{NEWER}.parquet"
    source = warehouse.path.as_posix()
    duckdb.sql(
        f"""
        COPY (
            SELECT * REPLACE (
                CASE WHEN indicator_code = '{LIFE_EXPECTANCY_CODE}' AND indicator_value > 0
                     THEN indicator_value * 1.1 ELSE indicator_value END AS indicator_value,
                CASE WHEN indicator_code = '{LABOUR_FORCE_CODE}' AND subsection = 'Мужчины'
                     THEN 'Муж-чины' ELSE subsection END AS subsection,
                CASE WHEN indicator_code = '{POPULATION_CODE}' AND year >= 2020
                     THEN '{NOTE}' ELSE comment END AS comment,
                '{NEWER}' AS version_date
            )
            FROM read_parquet('{source}')
        ) TO '{target.as_posix()}' (FORMAT parquet)
        """
    )
    return target


def check(path: Path, *, offered: dict[str, str] | None = None) -> EtlRun:
    """Проверить набор, как это делают панель и сбор."""
    return builds.start_candidate(source_path=path, started_by=None, offered=offered, inline=True)


class TestReport:
    """Отчёт о различиях; рабочий склад и справочники не меняются."""

    def test_differences(self, working: Path, changed_dataset: Path) -> None:
        stamp = working.stat().st_mtime_ns
        series_before = Series.objects.count()

        run = check(changed_dataset)

        assert run.status == EtlRun.Status.SUCCESS, run.error_message
        assert run.awaits_decision
        assert working.stat().st_mtime_ns == stamp
        assert not working.with_suffix(".candidate.duckdb").exists()
        assert Series.objects.count() == series_before
        assert DatasetVersion.objects.get(is_current=True).code != NEWER

        report = run.report
        assert report["current"]["version"] != NEWER
        assert report["candidate"]["version"] == NEWER
        renamed = report["series"]["renamed"]
        assert [(item["before"], item["after"], item["same_text"]) for item in renamed] == [
            (
                make_series_key(LABOUR_FORCE_CODE, "Мужчины"),
                make_series_key(LABOUR_FORCE_CODE, "Муж-чины"),
                True,
            )
        ]
        assert report["values"]["changed"] > 0
        assert report["values"]["top"][0]["key"].startswith(LIFE_EXPECTANCY_CODE)
        assert report["notes"] == {
            "count": 1,
            "breaks": 1,
            "sample": [{"text": NOTE, "years": [2020]}],
        }

    def test_summary_lines(self, working: Path, changed_dataset: Path) -> None:
        lines = compare.summary_lines(check(changed_dataset).report)
        assert lines[0].endswith(f"→ {NEWER}")
        assert any("переименовано 1" in line for line in lines)
        assert any("отмечают разрыв: 1" in line for line in lines)

    def test_without_working_warehouse(self, working: Path, changed_dataset: Path) -> None:
        working.unlink()
        report = check(changed_dataset).report
        assert report["current"] is None
        assert compare.summary_lines(report)[0].startswith(f"Версия {NEWER}")


class TestDecision:
    """Решение в панели: принятая версия собирается в рабочий склад, отклонённая — нет."""

    def test_accept(
        self, admin_panel_client: Client, administrator: Any, working: Path, changed_dataset: Path
    ) -> None:
        run = check(builds.store_download(changed_dataset.read_bytes(), changed_dataset.name))
        page = admin_panel_client.get(reverse("dashboard:etl-run", kwargs={"pk": run.pk}))
        assert "Что изменится, если принять версию" in page.content.decode()

        response = admin_panel_client.post(
            reverse("dashboard:candidate-accept", kwargs={"pk": run.pk})
        )

        build = EtlRun.objects.filter(mode=EtlRun.Mode.FULL).latest("started_at")
        assert response["Location"] == reverse("dashboard:etl-run", kwargs={"pk": build.pk})
        assert build.status == EtlRun.Status.SUCCESS, build.error_message
        assert build.started_by == administrator
        assert build.source_path == run.source_path
        assert DatasetVersion.objects.get(is_current=True).code == NEWER
        run.refresh_from_db()
        assert run.decision["accepted"]
        assert run.decision["build"] == build.pk
        assert not run.awaits_decision

    def test_reject(self, admin_panel_client: Client, working: Path, changed_dataset: Path) -> None:
        run = check(builds.store_download(changed_dataset.read_bytes(), changed_dataset.name))
        stamp = working.stat().st_mtime_ns

        response = admin_panel_client.post(
            reverse("dashboard:candidate-reject", kwargs={"pk": run.pk})
        )

        assert response["Location"] == reverse("dashboard:data")
        run.refresh_from_db()
        assert run.decision["accepted"] is False
        assert not Path(run.source_path).exists()
        assert working.stat().st_mtime_ns == stamp
        assert not EtlRun.objects.filter(mode=EtlRun.Mode.FULL).exists()

    def test_decision_is_taken_once(
        self, admin_panel_client: Client, working: Path, changed_dataset: Path
    ) -> None:
        run = check(builds.store_download(changed_dataset.read_bytes(), changed_dataset.name))
        admin_panel_client.post(reverse("dashboard:candidate-reject", kwargs={"pk": run.pk}))
        admin_panel_client.post(reverse("dashboard:candidate-accept", kwargs={"pk": run.pk}))
        assert not EtlRun.objects.filter(mode=EtlRun.Mode.FULL).exists()

    def test_members_are_refused(
        self, member_client: Client, working: Path, changed_dataset: Path
    ) -> None:
        run = check(changed_dataset)
        response = member_client.post(reverse("dashboard:candidate-accept", kwargs={"pk": run.pk}))
        assert response.status_code == 403
        run.refresh_from_db()
        assert run.awaits_decision

    def test_awaiting_version_is_shown(
        self, admin_panel_client: Client, working: Path, changed_dataset: Path
    ) -> None:
        from apps.dashboard.selectors import attention_items

        run = check(changed_dataset)
        page = admin_panel_client.get(reverse("dashboard:data"))
        assert reverse("dashboard:etl-run", kwargs={"pk": run.pk}) in page.content.decode()
        titles = [str(item["title"]) for item in attention_items()]
        assert any("ждёт решения" in title for title in titles)

    def test_awaiting_file_survives_pruning(
        self, working: Path, changed_dataset: Path, settings: Any
    ) -> None:
        run = check(builds.store_download(changed_dataset.read_bytes(), changed_dataset.name))
        builds.prune_sources()
        assert Path(run.source_path).exists()


class TestOfferNewer:
    """Сбор находит новую версию на странице набора, скачивает и проверяет её один раз."""

    @staticmethod
    def page(version: str) -> str:
        folder = f"https://storage.example/data_regions_collection_102_{version}"
        return f'<a href="{folder}/data_regions_collection_102_{version}.parquet">parquet</a>'

    def test_new_version_is_checked_once(self, working: Path, changed_dataset: Path) -> None:
        downloaded = (changed_dataset.read_bytes(), None)
        with (
            patch.object(network, "page", return_value=self.page(NEWER)),
            patch.object(network, "download", return_value=downloaded) as download,
        ):
            beat = dataset_watch.check()
            run = dataset_watch.offer_newer(lambda message: None)
            again = dataset_watch.offer_newer(lambda message: None)

        assert beat.detail["url"].endswith(f"{NEWER}.parquet")
        assert run is not None
        assert run.status == EtlRun.Status.SUCCESS, run.error_message
        assert run.offered_version == NEWER
        assert run.report["candidate"]["version"] == NEWER
        assert again is None
        assert download.call_count == 1
        assert [letter.subject for letter in mail.outbox] == [
            f"[RegionLens] новая версия набора {NEWER}: проверка готова"
        ]
        body = mail.outbox[0].body
        assert f"→ {NEWER}" in body
        assert f"/ru/manage/data/runs/{run.pk}/" in body

    def test_foreign_file_is_reported(self, working: Path, tmp_path: Path) -> None:
        foreign = tmp_path / "foreign.parquet"
        duckdb.sql(f"COPY (SELECT 1 AS id) TO '{foreign.as_posix()}' (FORMAT parquet)")
        with (
            patch.object(network, "page", return_value=self.page(NEWER)),
            patch.object(network, "download", return_value=(foreign.read_bytes(), None)),
        ):
            dataset_watch.check()
            assert dataset_watch.offer_newer(lambda message: None) is None

        assert not EtlRun.objects.exists()
        assert "другой состав атрибутов" in mail.outbox[0].subject
        assert not list(Path(working.parent / "sources").glob("*.parquet"))

    def test_current_version_is_not_offered(self, working: Path) -> None:
        current = DatasetVersion.objects.get(is_current=True).code
        with patch.object(network, "page", return_value=self.page(current)):
            dataset_watch.check()
        assert ServiceBeat.objects.get(service=ServiceBeat.Service.DATASET).ok
        assert dataset_watch.offer_newer(lambda message: None) is None
