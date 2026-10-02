"""
Сбор выпусков и их место в складе: архив и журнал, сшивка с набором, условная связь
и разрыв на стыке, уточнение последнего года набора, признаки значений, страницы.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pytest
from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, override_settings
from django.urls import reverse

from apps.core.models import ServiceBeat
from apps.sources import archive, collect, network, parsed, rosstat_bulletin
from apps.sources.base import Candidate
from apps.sources.models import Release, Source
from tests.support.releases import (
    BASKET_KEY,
    CPI_KEY,
    UNEMPLOYMENT_KEY,
    dataset_values,
    rows_for,
    write_release,
)

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "sources"
RELEASE_URL = "https://rosstat.gov.ru/storage/mediabank/info-stat-07-2026.zip"
ADMIN = "admin@example.org"


def _bulletin_zip(*, skip: str = "") -> bytes:
    """Архив выпуска из образцов таблиц; ``skip`` — номер таблицы, которой в нём нет."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted((FIXTURES / "bulletin").iterdir()):
            if skip and path.name.startswith(skip):
                continue
            bundle.write(path, f"info-stat-07-2026/{path.name}")
    return buffer.getvalue()


# ---------------------------------------------------------------------------------------
# Сбор: архив, разбор, журнал
# ---------------------------------------------------------------------------------------


@pytest.fixture
def source_dirs(settings: Any, tmp_path: Path) -> Path:
    settings.SOURCE_ARCHIVE_DIR = tmp_path / "archive"
    settings.SOURCE_PARSED_DIR = tmp_path / "sources"
    return tmp_path


@pytest.fixture
def fake_site(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Сайт Росстата из образцов: один выпуск, содержимое можно подменить."""
    site: dict[str, Any] = {"content": _bulletin_zip(), "downloads": 0}

    def discover() -> list[Candidate]:
        return [
            Candidate(
                url=RELEASE_URL,
                release_code="07-2026",
                title="январь — июль 2026 г.",
                reference_year=2026,
                published_on=date(2026, 9, 2),
                file_name="info-stat-07-2026.zip",
            )
        ]

    def download(url: str) -> tuple[bytes, network.RemoteFile]:
        site["downloads"] += 1
        return site["content"], head(url)

    def head(url: str) -> network.RemoteFile:
        return network.RemoteFile(
            url=url, status=200, size=len(site["content"]), modified=None, etag=""
        )

    monkeypatch.setattr(rosstat_bulletin, "discover", discover)
    monkeypatch.setattr(network, "download", download)
    monkeypatch.setattr(network, "head", head)
    return site


@pytest.mark.django_db
class TestCollect:
    """Новый выпуск попадает в архив, разбирается и записывается в журнал один раз."""

    def test_new_release(self, reference_seed: None, source_dirs: Path, fake_site: Any) -> None:
        outcomes = collect.run(["rosstat_bulletin"])
        assert [(item.fetched, item.parsed, item.ok) for item in outcomes] == [
            (["07-2026"], ["07-2026"], True)
        ]
        release = Release.objects.get()
        assert release.status == Release.Status.PARSED
        assert release.published_on == date(2026, 9, 2)
        assert release.report["tables"]["wage"]["regions"] >= 85
        assert release.changes["latest"]["wage"] == "2026-06"
        assert (archive.archive_root() / release.archive_path).exists()
        assert [info.code for info, _path in parsed.releases()] == ["07-2026"]
        beat = ServiceBeat.objects.get(service=ServiceBeat.Service.COLLECT)
        assert beat.ok
        assert beat.detail["parsed"] == ["rosstat_bulletin:07-2026"]
        source = Source.objects.get(code="rosstat_bulletin")
        assert source.check_ok
        assert source.running_since is None

    def test_same_release_is_not_fetched_again(
        self, reference_seed: None, source_dirs: Path, fake_site: Any
    ) -> None:
        collect.run(["rosstat_bulletin"])
        outcomes = collect.run(["rosstat_bulletin"])
        assert outcomes[0].fetched == []
        assert fake_site["downloads"] == 1
        assert Release.objects.count() == 1

    def test_replaced_file_is_a_new_release(
        self, reference_seed: None, source_dirs: Path, fake_site: Any
    ) -> None:
        collect.run(["rosstat_bulletin"])
        fake_site["content"] = _bulletin_zip() + b"\0"
        collect.run(["rosstat_bulletin"])
        assert Release.objects.filter(code="07-2026").count() == 2
        assert len(archive.entries("rosstat_bulletin")) == 2

    def test_changed_layout_keeps_data(
        self, reference_seed: None, source_dirs: Path, fake_site: Any
    ) -> None:
        fake_site["content"] = _bulletin_zip(skip="12-01")
        outcomes = collect.run(["rosstat_bulletin"])
        assert outcomes[0].failed == ["07-2026"]
        release = Release.objects.get()
        assert release.status == Release.Status.FAILED
        assert "нет таблицы 12-01" in release.error
        assert parsed.releases() == []
        assert not ServiceBeat.objects.get(service=ServiceBeat.Service.COLLECT).ok

    @override_settings(ADMINS=[ADMIN])
    def test_changed_layout_is_reported_once(
        self, reference_seed: None, source_dirs: Path, fake_site: Any
    ) -> None:
        fake_site["content"] = _bulletin_zip(skip="12-01")
        collect.run(["rosstat_bulletin"])
        # Следующий сбор снова пробует разобрать выпуск — письмо не повторяется.
        collect.run(["rosstat_bulletin"])
        assert len(mail.outbox) == 1
        letter = mail.outbox[0]
        assert "07-2026" in letter.subject
        assert "нет таблицы 12-01" in letter.body
        assert f"/ru/manage/sources/releases/{Release.objects.get().pk}/" in letter.body

    @override_settings(ADMINS=[ADMIN])
    def test_unreachable_source_is_reported_on_second_failure(
        self,
        reference_seed: None,
        source_dirs: Path,
        fake_site: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def refuse() -> list[Candidate]:
            raise network.FetchError("сайт не ответил")

        monkeypatch.setattr(rosstat_bulletin, "discover", refuse)
        collect.run(["rosstat_bulletin"])
        assert mail.outbox == []
        collect.run(["rosstat_bulletin"])
        assert len(mail.outbox) == 1
        assert "не отвечает" in mail.outbox[0].subject
        assert "сайт не ответил" in mail.outbox[0].body

    def test_due_skips_recently_checked(
        self, reference_seed: None, source_dirs: Path, fake_site: Any
    ) -> None:
        collect.run(["rosstat_bulletin"])
        assert collect.run(["rosstat_bulletin"], due_only=True) == []

    def test_command(
        self, reference_seed: None, source_dirs: Path, fake_site: Any, capsys: Any
    ) -> None:
        call_command("collect", "rosstat_bulletin", "--no-build")
        call_command("collect", "--verify")
        output = capsys.readouterr().out
        assert "разобран" in output
        assert "Архив сходится с описью: 1" in output
        with pytest.raises(CommandError, match="Укажите источник"):
            call_command("collect")
        fake_site["content"] = _bulletin_zip(skip="12-01")
        with pytest.raises(CommandError, match="с ошибками"):
            call_command("collect", "rosstat_bulletin", "--no-build")

    def test_file_option(self, reference_seed: None, source_dirs: Path, tmp_path: Path) -> None:
        path = tmp_path / "info-stat-07-2026.zip"
        path.write_bytes(_bulletin_zip())
        call_command("collect", "rosstat_bulletin", "--file", str(path), "--no-build")
        release = Release.objects.get()
        assert (release.code, release.status) == ("07-2026", Release.Status.PARSED)

    def test_new_release_rebuilds_warehouse(
        self,
        reference_seed: None,
        source_dirs: Path,
        fake_site: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from apps.warehouse import builds

        started: list[Any] = []
        monkeypatch.setattr(builds, "execute", lambda run, progress=None: started.append(run))
        call_command("collect", "rosstat_bulletin")
        assert len(started) == 1
        assert "после сбора" in started[0].log

    def test_launch(self, reference_seed: None, monkeypatch: pytest.MonkeyPatch) -> None:
        commands: list[list[str]] = []
        monkeypatch.setattr(
            collect.subprocess, "Popen", lambda command, **kwargs: commands.append(command)
        )
        assert collect.launch("rosstat_grp") == ""
        assert commands[0][-2:] == ["collect", "rosstat_grp"]
        assert Source.objects.get(code="rosstat_grp").is_running
        assert "уже идёт" in collect.launch("rosstat_grp")


# ---------------------------------------------------------------------------------------
# Склад с выпусками: сшивка, признаки, разрывы
# ---------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def release_warehouse(
    synthetic_dataset: Any,
    warehouse_file: Path,
    reference_seed: None,
    django_db_blocker: Any,
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[str, Any]:
    """
    Склад из синтетического набора и двух выпусков бюллетеня.

    ИПЦ совпадает с набором и продолжается 2026 годом; стоимость набора на 10 % выше
    набора (условная связь); безработицу выпуск уточняет за последний год набора
    у одного субъекта.
    """
    from tests.support.warehouse import build_warehouse

    root = tmp_path_factory.mktemp("release-sources")
    cpi = dataset_values(warehouse_file, CPI_KEY)
    basket = dataset_values(warehouse_file, BASKET_KEY)
    unemployment = dataset_values(warehouse_file, UNEMPLOYMENT_KEY)
    last = max(year for _code, year in cpi)
    recent = {key: value for key, value in cpi.items() if key[1] >= last - 3}
    extended = {
        **recent,
        **{
            (code, last + 1): value * 1.05 for (code, year), value in recent.items() if year == last
        },
    }
    early = {(code, year): value for (code, year), value in extended.items() if year <= last}
    revised_code = "RU-TOM"
    labour = {key: value for key, value in unemployment.items() if key[1] >= last - 3}
    labour[(revised_code, last)] = labour[(revised_code, last)] + 1.0

    write_release(
        root,
        code="12-2025",
        reference_year=last,
        published_on=date(last + 1, 2, 6),
        rows=rows_for("cpi_december", early, kind="month", period=12),
    )
    write_release(
        root,
        code="07-2026",
        reference_year=last + 1,
        published_on=date(last + 1, 9, 2),
        rows=[
            *rows_for("cpi_december", extended, kind="month", period=12),
            *rows_for(
                "basket",
                {
                    **{key: value * 1.1 for key, value in basket.items() if key[1] >= last - 3},
                    **{
                        (code, last + 1): value * 1.15
                        for (code, year), value in basket.items()
                        if year == last
                    },
                },
                kind="month",
                period=12,
            ),
            *rows_for("unemployment_rate", labour, kind="annual", period=12),
        ],
    )
    target = tmp_path_factory.mktemp("release-warehouse") / "regionlens.duckdb"
    with django_db_blocker.unblock(), override_settings(SOURCE_PARSED_DIR=root):
        build_warehouse(synthetic_dataset, target)
    return {
        "path": target,
        "last": last,
        "cpi": extended,
        "revised_code": revised_code,
        "revised_value": labour[(revised_code, last)],
    }


def _query(path: Path, sql: str, params: list[Any] | None = None) -> list[tuple[Any, ...]]:
    connection = duckdb.connect(str(path), read_only=True)
    try:
        return connection.execute(sql, params or []).fetchall()
    finally:
        connection.close()


class TestStitching:
    """Витрина связей, признаки значений и порядок выпусков."""

    def test_links(self, release_warehouse: dict[str, Any]) -> None:
        rows = {
            key: (link, junction, last_year, revised)
            for key, link, junction, last_year, revised in _query(
                release_warehouse["path"],
                "SELECT series_key, link, junction_year, last_year, revised_count "
                "FROM mart_source_link",
            )
        }
        last = release_warehouse["last"]
        assert rows[CPI_KEY][:3] == ("full", last + 1, last + 1)
        assert rows[BASKET_KEY][:2] == ("conditional", last + 1)
        assert rows[UNEMPLOYMENT_KEY][0] == "full"
        assert rows[UNEMPLOYMENT_KEY][3] == 1

    def test_continuation_is_preliminary(self, release_warehouse: dict[str, Any]) -> None:
        last = release_warehouse["last"]
        value, flags, edition = _query(
            release_warehouse["path"],
            "SELECT value, flags, edition_code FROM fact_observation "
            "WHERE series_key = ? AND territory_code = 'RU' AND year = ?",
            [CPI_KEY, last + 1],
        )[0]
        assert value == pytest.approx(release_warehouse["cpi"][("RU", last + 1)])
        assert flags & 1
        assert edition.startswith("rel_rosstat_bulletin_07-2026")

    def test_matching_years_are_not_revisions(self, release_warehouse: dict[str, Any]) -> None:
        loaded = _query(
            release_warehouse["path"],
            "SELECT count(*) FROM fact_vintage AS v JOIN dim_edition AS e USING (edition_code) "
            "WHERE v.series_key = ? AND e.source_code IS NOT NULL AND v.year <= ?",
            [CPI_KEY, release_warehouse["last"]],
        )[0][0]
        assert loaded == 0

    def test_last_dataset_year_is_revised(self, release_warehouse: dict[str, Any]) -> None:
        last = release_warehouse["last"]
        code = release_warehouse["revised_code"]
        value, edition, revisions = _query(
            release_warehouse["path"],
            "SELECT value, edition_code, revision_count FROM fact_observation "
            "WHERE series_key = ? AND territory_code = ? AND year = ?",
            [UNEMPLOYMENT_KEY, code, last],
        )[0]
        assert value == pytest.approx(release_warehouse["revised_value"])
        assert edition.startswith("rel_")
        assert revisions == 1
        assert (
            _query(
                release_warehouse["path"],
                "SELECT count(*) FROM mart_revision WHERE series_key = ? AND territory_code = ? "
                "AND year = ?",
                [UNEMPLOYMENT_KEY, code, last],
            )[0][0]
            == 1
        )

    def test_editions_in_release_order(self, release_warehouse: dict[str, Any]) -> None:
        rows = _query(
            release_warehouse["path"],
            "SELECT edition_label, source_code FROM dim_edition ORDER BY edition_rank DESC LIMIT 2",
        )
        assert rows == [("07-2026", "rosstat_bulletin"), ("12-2025", "rosstat_bulletin")]


GRP_PER_CAPITA_KEY = "Y477110006:00"
GRP_FOOTNOTE = (
    "Данные динамического ряда, начиная с 2016 года, содержат изменения, связанные с внедрением "
    "международной методологии оценки жилищных услуг, производимых и потребляемых собственниками "
    "жилья; оценкой потребления основного капитала, исходя из его текущей рыночной стоимости."
)


@pytest.fixture(scope="module")
def grp_warehouse(
    synthetic_dataset: Any,
    warehouse_file: Path,
    reference_seed: None,
    django_db_blocker: Any,
    tmp_path_factory: pytest.TempPathFactory,
) -> Path:
    """Склад с выпуском таблицы ВРП: ВРП на душу как в наборе и сноски двух показателей."""
    from tests.support.warehouse import build_warehouse

    root = tmp_path_factory.mktemp("grp-sources")
    write_release(
        root,
        code="2026-03-06",
        reference_year=2026,
        published_on=date(2026, 3, 6),
        rows=rows_for(
            "grp_per_capita",
            dataset_values(warehouse_file, GRP_PER_CAPITA_KEY),
            kind="annual",
            period=12,
        ),
        source="rosstat_grp",
        notes=[("grp_per_capita", GRP_FOOTNOTE), ("grp_index", "Сноска другого показателя")],
    )
    target = tmp_path_factory.mktemp("grp-warehouse") / "regionlens.duckdb"
    with django_db_blocker.unblock(), override_settings(SOURCE_PARSED_DIR=root):
        build_warehouse(synthetic_dataset, target)
    return target


class TestSourceNotes:
    """Сноски таблицы источника доходят до склада вместе с её связями."""

    def test_notes_of_the_linked_measure(self, grp_warehouse: Path) -> None:
        rows = _query(
            grp_warehouse,
            "SELECT l.mode, n.series_key, n.position, n.note_text FROM mart_source_note AS n "
            "JOIN mart_source_link AS l USING (series_key)",
        )
        assert rows == [("supersede", GRP_PER_CAPITA_KEY, 0, GRP_FOOTNOTE)]


@pytest.fixture
def release_catalog(
    db: None, settings: Any, release_warehouse: dict[str, Any]
) -> Iterator[dict[str, Any]]:
    """Склад с выпусками — рабочий, каталог синхронизирован в транзакции теста."""
    from apps.warehouse import duckdb_client
    from tests.support.warehouse import sync_catalog

    settings.DUCKDB_PATH = release_warehouse["path"]
    duckdb_client.close_connections()
    sync_catalog(release_warehouse["path"])
    yield release_warehouse
    duckdb_client.close_connections()


class TestCatalogAndPages:
    """Разрыв на стыке, происхождение значений и страницы."""

    def test_conditional_link_breaks_series(self, release_catalog: dict[str, Any]) -> None:
        from apps.catalog.constants import BreakKind
        from apps.catalog.models import SeriesBreak

        junction = release_catalog["last"] + 1
        found = SeriesBreak.objects.filter(series__key=BASKET_KEY, kind=BreakKind.SOURCE)
        assert [item.year for item in found] == [junction]
        assert "несопоставимы" in found[0].description
        assert not SeriesBreak.objects.filter(series__key=CPI_KEY, kind=BreakKind.SOURCE).exists()

    def test_origin_and_status(self, release_catalog: dict[str, Any]) -> None:
        from apps.catalog.provenance import origin_of_year, series_link
        from apps.catalog.status import series_status

        last = release_catalog["last"]
        origin = origin_of_year(CPI_KEY, last + 1)
        assert origin is not None
        assert origin.is_preliminary
        assert origin.mark == "предв."
        assert "07-2026" in origin.text
        assert origin_of_year(CPI_KEY, last - 1) is None
        link = series_link(BASKET_KEY)
        assert link is not None
        assert link.is_conditional
        assert "несопоставимы" in link.summary
        status = series_status(CPI_KEY)
        assert status is not None
        assert status.label == "обновляется"

    def test_sources_page(self, release_catalog: dict[str, Any], client: Client) -> None:
        for language in ("ru", "en"):
            response = client.get(f"/{language}/datasets/sources/")
            assert response.status_code == 200
        page = client.get("/ru/datasets/sources/").content.decode()
        assert "условная" in page
        assert "полная" in page
        assert "прекращена" in page or "Прекращён" in page

    def test_surface_names_origin(self, release_catalog: dict[str, Any], client: Client) -> None:
        year = release_catalog["last"] + 1
        page = client.get(reverse("maps:choropleth"), {"series": CPI_KEY, "year": year}).content
        assert "surface-head__origin" in page.decode()

    def test_country_tile_marks_value(
        self, release_catalog: dict[str, Any], client: Client
    ) -> None:
        page = client.get(
            reverse("catalog:series-detail", kwargs={"slug": _indicator_slug(CPI_KEY)}),
            {"series": CPI_KEY},
        ).content.decode()
        assert 'class="origin-mark"' in page
        assert "Обновляется" in page

    def test_ranking_document_names_origin(self, release_catalog: dict[str, Any]) -> None:
        from apps.exports.reports.builders import build_ranking_report

        document = build_ranking_report(
            {"series": CPI_KEY, "year": release_catalog["last"] + 1}, max_rows=100
        )
        meta = dict(document.meta)
        assert "07-2026" in meta["Значения года"]
        assert meta["Публикация"].startswith("Обновляется.")


def _indicator_slug(series_key: str) -> str:
    from apps.catalog.models import Series

    return Series.objects.select_related("indicator").get(key=series_key).indicator.slug


# ---------------------------------------------------------------------------------------
# Панель управления
# ---------------------------------------------------------------------------------------


@pytest.mark.django_db
class TestPanel:
    """Раздел «Источники»: доступ, журнал, «Собрать сейчас»."""

    def _release(self) -> Release:
        source = collect.sync_source("rosstat_bulletin")
        return Release.objects.create(
            source=source,
            code="07-2026",
            title="январь — июль 2026 г.",
            reference_year=2026,
            published_on=date(2026, 9, 2),
            url=RELEASE_URL,
            file_name="info-stat-07-2026.zip",
            archive_path="rosstat_bulletin/2026-09-24_info-stat-07-2026.zip",
            sha256="a" * 64,
            size_bytes=8_531_019,
            fetched_at=datetime(2026, 9, 24, tzinfo=UTC),
            status=Release.Status.FAILED,
            error="таблица 12-01: нет листа «рублей»",
        )

    def test_page_and_attention(self, admin_panel_client: Client, reference_seed: None) -> None:
        release = self._release()
        page = admin_panel_client.get(reverse("dashboard:sources"))
        assert page.status_code == 200
        text = page.content.decode()
        assert "Журнал выпусков" in text
        assert "нет листа" in text
        detail = admin_panel_client.get(reverse("dashboard:release", kwargs={"pk": release.pk}))
        assert detail.status_code == 200
        assert "склад остался прежним" in detail.content.decode()
        overview = admin_panel_client.get(reverse("dashboard:index")).content.decode()
        assert "Выпусков источников не разобрано: 1" in overview
        assert "Сбор источников" in overview

    def test_collect_now(
        self, admin_panel_client: Client, reference_seed: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        started: list[str] = []
        monkeypatch.setattr(collect, "launch", lambda code: started.append(code) or "")
        response = admin_panel_client.post(
            reverse("dashboard:source-collect", kwargs={"code": "rosstat_grp"})
        )
        assert response.status_code == 302
        assert started == ["rosstat_grp"]
        assert (
            admin_panel_client.post(
                reverse("dashboard:source-collect", kwargs={"code": "unknown"})
            ).status_code
            == 404
        )

    def test_members_are_refused(self, member_client: Client) -> None:
        assert member_client.get(reverse("dashboard:sources")).status_code == 403
        assert (
            member_client.post(
                reverse("dashboard:source-collect", kwargs={"code": "rosstat_grp"})
            ).status_code
            == 403
        )
