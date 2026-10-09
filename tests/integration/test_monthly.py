"""
Банк России, ФНС и помесячный слой: сбор с подменённой сетью, склад с синтетическими
выпусками, «Что сейчас» в паспорте, помесячный блок страницы показателя, API и документы.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import patch

import duckdb
import pytest
from django.test import Client, override_settings
from django.urls import reverse

from apps.sources import cbr, collect, fns_sme, parsed
from apps.sources.base import Candidate
from apps.sources.models import Release
from apps.sources.territories import region_codes
from tests.support.releases import rows_for, write_release

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "sources"
FNS_DATES = ("2016-09-10", "2022-12-10", "2026-09-10")
WAGE_KEY = "Y477110378:00"
MORTGAGE_KEY = "RL_MORTGAGE_COUNT:00"
MORTGAGE_PC_KEY = "RL_MORTGAGE_COUNT_PC:00"
SME_PC_KEY = "RL_SME_COUNT_PC:00"
REGION = "RU-TOM"


@pytest.fixture
def source_dirs(settings: Any, tmp_path: Path) -> Path:
    settings.SOURCE_ARCHIVE_DIR = tmp_path / "archive"
    settings.SOURCE_PARSED_DIR = tmp_path / "sources"
    return tmp_path


# ---------------------------------------------------------------------------------------
# Сбор
# ---------------------------------------------------------------------------------------


@pytest.mark.django_db
class TestCollect:
    """Выпуск программного интерфейса собирается функцией модуля; история ФНС — целиком."""

    def test_cbr_snapshot(
        self, reference_seed: None, source_dirs: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fetched: list[str] = []
        candidate = cbr.identify("cbr-dataservice-2026-09-08.zip")
        monkeypatch.setattr(cbr, "discover", lambda: [candidate])

        def fetch(item: Candidate) -> bytes:
            fetched.append(item.release_code)
            return (FIXTURES / "cbr" / "cbr-dataservice-2026-09-08.zip").read_bytes()

        monkeypatch.setattr(cbr, "fetch", fetch)
        outcomes = collect.run(["cbr"])
        assert [(item.fetched, item.parsed, item.ok) for item in outcomes] == [
            (["2026-09-08"], ["2026-09-08"], True)
        ]
        release = Release.objects.get(source__code="cbr")
        assert release.report["tables"]["mortgage_count"]["regions"] == len(region_codes())
        # Тот же код — тот же выпуск: сервис не опрашивается второй раз.
        collect.run(["cbr"])
        assert fetched == ["2026-09-08"]

    def test_fns_history(
        self, reference_seed: None, source_dirs: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        candidates = [fns_sme.identify(f"rmsp-statistics-{day}.json") for day in FNS_DATES]
        monkeypatch.setattr(fns_sme, "discover", lambda: list(reversed(candidates)))
        monkeypatch.setattr(
            fns_sme,
            "fetch",
            lambda item: (
                FIXTURES / "fns" / f"rmsp-statistics-{item.release_code}.json"
            ).read_bytes(),
        )
        outcomes = collect.run(["fns_sme"])
        # Первый сбор берёт все даты страницы, а не одну последнюю.
        assert sorted(outcomes[0].parsed) == sorted(FNS_DATES)
        assert [info.code for info, _path in parsed.releases("fns_sme")] == list(FNS_DATES)
        early = Release.objects.get(source__code="fns_sme", code="2016-09-10")
        assert list(early.report["tables"]) == ["sme_count"]


# ---------------------------------------------------------------------------------------
# Склад с выпусками Банка России, ФНС и бюллетеня
# ---------------------------------------------------------------------------------------


def _months(first_year: int, last_year: int, last_month: int = 12) -> list[tuple[int, int]]:
    return [
        (year, month)
        for year in range(first_year, last_year + 1)
        for month in range(1, 13)
        if year < last_year or month <= last_month
    ]


def _cbr_rows(revision: float = 1.0) -> list[dict[str, Any]]:
    """Все показатели Банка России по всем субъектам и России, январь 2019 — июль 2026."""
    periods = _months(2019, 2026, 7)
    codes = sorted(region_codes())
    rows: list[dict[str, Any]] = []
    base = {
        "mortgage_count": 100.0,
        "mortgage_volume": 300.0,
        "mortgage_debt": 5_000.0,
        "mortgage_overdue": 50.0,
        "mortgage_term": 200.0,
        "mortgage_rate": 8.0,
        "retail_volume": 900.0,
        "retail_debt": 20_000.0,
        "retail_overdue": 800.0,
        "sme_volume": 700.0,
        "sme_debt": 9_000.0,
        "sme_overdue": 400.0,
    }
    for measure, start in base.items():
        values = {}
        for index, code in enumerate(codes):
            for step, (year, month) in enumerate(periods):
                values[(code, year, month)] = (
                    start * (1 + index / 100) * (1 + step / 200) * revision
                )
        for step, (year, month) in enumerate(periods):
            values[("RU", year, month)] = (
                sum(values[(code, year, month)] for code in codes)
                if measure not in {"mortgage_term", "mortgage_rate"}
                else start * (1 + step / 200)
            )
        rows += [
            {
                "measure": measure,
                "territory_code": code,
                "year": year,
                "period_kind": "month",
                "period": month,
                "value": value,
                "hidden": False,
                "preliminary": False,
            }
            for (code, year, month), value in values.items()
        ]
    return rows


def _fns_rows(day: date) -> list[dict[str, Any]]:
    codes = sorted(region_codes())
    rows = []
    for measure, start in (("sme_count", 1000.0), ("sme_workers", 3000.0)):
        for index, code in enumerate(codes):
            rows.append(
                {
                    "measure": measure,
                    "territory_code": code,
                    "year": day.year,
                    "period_kind": "month",
                    "period": day.month,
                    "value": start * (1 + index / 50) * (1 + (day.year - 2020) / 20),
                    "hidden": False,
                    "preliminary": False,
                }
            )
        rows.append({**rows[-1], "territory_code": "ext:запорожская область", "value": 5.0})
        rows.append({**rows[-1], "territory_code": "RU", "value": 1.0})
    return rows


@pytest.fixture(scope="module")
def monthly_warehouse(
    synthetic_dataset: Any,
    reference_seed: None,
    django_db_blocker: Any,
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[str, Any]:
    """Склад из синтетического набора и выпусков Банка России (два снимка), ФНС и бюллетеня."""
    from apps.warehouse.etl import monthly
    from tests.support.warehouse import build_warehouse

    root = tmp_path_factory.mktemp("monthly-sources")
    write_release(
        root,
        source="cbr",
        code="2026-08-31",
        reference_year=2026,
        published_on=date(2026, 8, 31),
        rows=_cbr_rows(revision=0.98),
    )
    write_release(
        root,
        source="cbr",
        code="2026-09-08",
        reference_year=2026,
        published_on=date(2026, 9, 8),
        rows=_cbr_rows(),
    )
    for day in (date(2025, 1, 10), date(2026, 1, 10), date(2026, 9, 10)):
        write_release(
            root,
            source="fns_sme",
            code=day.isoformat(),
            reference_year=day.year,
            published_on=day,
            rows=_fns_rows(day),
        )
    # Зарплата за июнь: 56 000 в 2025 году и 61 000 в 2026-м.
    wage_rows = []
    for code in ("RU", REGION):
        for year, month in _months(2025, 2026, 6):
            value = 50_000.0 + 1_000 * month + (year - 2025) * 5_000
            wage_rows += rows_for("wage", {(code, year): value}, kind="month", period=month)
    unemployment = rows_for(
        "unemployment_3m", {("RU", 2026): 2.2, (REGION, 2026): 3.1}, kind="month", period=7
    )
    write_release(
        root,
        code="07-2026",
        reference_year=2026,
        published_on=date(2026, 9, 2),
        rows=[*wage_rows, *unemployment],
    )

    target = tmp_path_factory.mktemp("monthly-warehouse") / "regionlens.duckdb"
    # Синтетические ВРП и ВРП на душу не согласованы: численность из них не похожа на настоящую.
    with (
        django_db_blocker.unblock(),
        override_settings(SOURCE_PARSED_DIR=root),
        patch.object(monthly, "POPULATION_RANGE", (0.0, float("inf"))),
    ):
        build_warehouse(synthetic_dataset, target)
    return {"path": target}


def _query(path: Path, sql: str, params: list[Any] | None = None) -> list[tuple[Any, ...]]:
    connection = duckdb.connect(str(path), read_only=True)
    try:
        return connection.execute(sql, params or []).fetchall()
    finally:
        connection.close()


class TestWarehouse:
    """Измерения рядов проекта, помесячный слой, годовые версии и пересмотры."""

    def test_project_series_dimensions(self, monthly_warehouse: dict[str, Any]) -> None:
        rows = dict(
            _query(
                monthly_warehouse["path"],
                "SELECT series_key, source_code FROM dim_series WHERE source_code IS NOT NULL",
            )
        )
        assert rows[MORTGAGE_KEY] == "cbr"
        assert rows[SME_PC_KEY] == "fns_sme"
        assert len(rows) == 23

    def test_month_kinds_and_latest_edition(self, monthly_warehouse: dict[str, Any]) -> None:
        path = monthly_warehouse["path"]
        kinds = dict(
            _query(
                path,
                "SELECT kind, count(*) FROM fact_month WHERE series_key = ? GROUP BY 1",
                [MORTGAGE_KEY],
            )
        )
        assert set(kinds) == {"level", "ytd"}
        editions = {
            row[0]
            for row in _query(
                path,
                "SELECT DISTINCT edition_code FROM fact_month WHERE series_key = ?",
                [MORTGAGE_KEY],
            )
        }
        # Оба снимка несут все месяцы: остаётся поздний.
        assert len(editions) == 1
        assert editions.pop().startswith("rel_cbr_2026-09-08")
        layer = {row[0] for row in _query(path, "SELECT DISTINCT series_key FROM fact_month")}
        assert {WAGE_KEY, "Y477110418:00", SME_PC_KEY} <= layer

    def test_annual_values_and_revisions(self, monthly_warehouse: dict[str, Any]) -> None:
        path = monthly_warehouse["path"]
        years = [
            row[0]
            for row in _query(
                path,
                "SELECT year FROM fact_observation WHERE series_key = ? AND territory_code = ? "
                "AND value IS NOT NULL ORDER BY year",
                [MORTGAGE_KEY, REGION],
            )
        ]
        # 2026 год неполный — годового значения нет.
        assert years == list(range(2019, 2026))
        revisions = _query(
            path,
            "SELECT count(*) FROM mart_revision WHERE series_key = ?",
            [MORTGAGE_KEY],
        )[0][0]
        assert revisions > 0

    def test_flags_of_computed_values(self, monthly_warehouse: dict[str, Any]) -> None:
        path = monthly_warehouse["path"]
        flags = dict(
            _query(
                path,
                "SELECT year, flags FROM fact_observation WHERE series_key = ? "
                "AND territory_code = ? AND value IS NOT NULL",
                [MORTGAGE_PC_KEY, REGION],
            )
        )
        # Численность субъекта известна за годы, где есть и ВРП, и ВРП на душу.
        last_population = _query(
            path,
            "SELECT max(year) FROM (SELECT year FROM fact_observation "
            "WHERE series_key IN ('Y477110005:00', 'Y477110006:00') AND territory_code = ? "
            "AND value IS NOT NULL GROUP BY year HAVING count(*) = 2)",
            [REGION],
        )[0][0]
        assert all(value & 2 for value in flags.values())
        for year, value in flags.items():
            assert bool(value & 4) == (year > last_population), year

    def test_register_country_is_the_sum_of_regions(
        self, monthly_warehouse: dict[str, Any]
    ) -> None:
        path = monthly_warehouse["path"]
        country, regions = _query(
            path,
            """
            SELECT
                max(value) FILTER (WHERE territory_code = 'RU'),
                sum(value) FILTER (WHERE territory_code <> 'RU' AND territory_code LIKE 'RU-%'
                                   AND territory_code NOT LIKE '%-AGG')
            FROM fact_month
            WHERE series_key = 'RL_SME_COUNT:00' AND year = 2026 AND month = 9 AND kind = 'level'
            """,
        )[0]
        assert country == pytest.approx(regions)
        outside = _query(path, "SELECT count(*) FROM fact_month WHERE territory_code LIKE 'ext:%'")
        assert outside[0][0] == 0

    def test_readiness_of_short_series(self, monthly_warehouse: dict[str, Any]) -> None:
        ready = dict(
            _query(
                monthly_warehouse["path"],
                "SELECT series_key, is_analysis_ready FROM mart_series_coverage "
                "WHERE series_key IN (?, ?)",
                [MORTGAGE_KEY, SME_PC_KEY],
            )
        )
        # Семь лет Банка России — достаточно; у ФНС в складе два года — мало.
        assert ready[MORTGAGE_KEY] is True
        assert ready[SME_PC_KEY] is False


@pytest.fixture
def monthly_catalog(
    db: None, settings: Any, monthly_warehouse: dict[str, Any]
) -> Iterator[dict[str, Any]]:
    """Склад с выпусками — рабочий, каталог синхронизирован в транзакции теста."""
    from apps.warehouse import duckdb_client
    from tests.support.warehouse import sync_catalog

    settings.DUCKDB_PATH = monthly_warehouse["path"]
    duckdb_client.close_connections()
    sync_catalog(monthly_warehouse["path"])
    yield monthly_warehouse
    duckdb_client.close_connections()


def _slug(series_key: str) -> str:
    from apps.catalog.models import Series

    return Series.objects.select_related("indicator").get(key=series_key).indicator.slug


class TestPages:
    """«Что сейчас», помесячный блок, страница источников, API и документы."""

    def test_catalog_names(self, monthly_catalog: dict[str, Any]) -> None:
        from apps.catalog.models import Series

        series = Series.objects.select_related("indicator__section").get(key=MORTGAGE_PC_KEY)
        assert series.indicator.name_en.startswith("Residential mortgage loans")
        assert series.indicator.section.name_ru == "Кредиты и ипотека"
        assert series.indicator.section.name_en == "Credit and mortgages"

    def test_now_block(self, monthly_catalog: dict[str, Any], client: Client) -> None:
        from apps.catalog.models import Territory

        territory = Territory.objects.get(code=REGION)
        page = client.get(
            reverse("catalog:territory-detail", kwargs={"slug": territory.slug})
        ).content.decode()
        assert 'id="passport-now"' in page
        assert "Июнь 2026" in page
        assert "Январь — июль 2026" in page
        assert "в среднем" in page
        english = client.get(f"/en/regions/{territory.slug}/")
        assert english.status_code == 200

    def test_now_rows(self, monthly_catalog: dict[str, Any]) -> None:
        from django.utils import translation

        from apps.catalog.monthly import now_rows

        with translation.override("ru"):
            rows = {row.key: row for row in now_rows(REGION)}
        wage = rows[WAGE_KEY]
        assert wage.period == "июнь 2026"
        assert wage.change == "+8,9 % за год"
        assert wage.tone == "neutral"
        assert wage.country.startswith("по России")
        mortgage = rows[MORTGAGE_KEY]
        assert mortgage.period == "январь — июль 2026"
        assert "к тому же периоду прошлого года" in mortgage.change
        assert "Банк России" in mortgage.source
        assert "08.09.2026" in mortgage.source

    def test_indicator_month_block(self, monthly_catalog: dict[str, Any], client: Client) -> None:
        page = client.get(
            reverse("catalog:series-detail", kwargs={"slug": _slug(MORTGAGE_PC_KEY)}),
            {"series": MORTGAGE_PC_KEY},
        ).content.decode()
        assert 'id="indicator-month-title"' in page
        assert "indicator-month-option" in page
        assert "rl-month-" in page
        assert "Полное название" in page
        assert "не меньше пяти лет" in page

    def test_structured_data_names_the_publisher(self, monthly_catalog: dict[str, Any]) -> None:
        from django.test import RequestFactory
        from django.utils import translation

        from apps.catalog.models import Series
        from apps.core.structured_data import series_dataset

        with translation.override("ru"):
            data = series_dataset(
                RequestFactory().get("/"), Series.objects.get(key=MORTGAGE_PC_KEY), "/x/"
            )
        assert data["creator"]["name"] == "Банк России"
        assert "license" not in data
        dataset = series_dataset(
            RequestFactory().get("/"), Series.objects.get(key="Y477110006:00"), "/x/"
        )
        assert "license" in dataset

    def test_series_without_months_has_no_block(
        self, monthly_catalog: dict[str, Any], client: Client
    ) -> None:
        page = client.get(
            reverse("catalog:series-detail", kwargs={"slug": _slug("Y477110006:00")}),
            {"series": "Y477110006:00"},
        ).content.decode()
        assert "indicator-month-title" not in page

    def test_sources_page(self, monthly_catalog: dict[str, Any], client: Client) -> None:
        page = client.get(reverse("catalog:sources")).content.decode()
        assert 'id="sources-collected"' in page
        assert "Сумма двенадцати месяцев" in page

    def test_api(self, monthly_catalog: dict[str, Any], client: Client) -> None:
        response = client.get(
            reverse("api:v1:monthly"),
            {"series": MORTGAGE_KEY, "territory": "RU", "kind": "ytd", "year_from": 2026},
        )
        payload = response.json()
        assert response.status_code == 200
        assert payload["count"] == 7
        first = payload["results"][0]
        assert (first["month"], first["kind"], first["source"]) == (1, "ytd", "cbr")
        assert first["release"] == "2026-09-08"

        observations = client.get(
            reverse("api:v1:observations"), {"series": MORTGAGE_PC_KEY, "territory": REGION}
        ).json()["results"]
        assert all(row["source"] == "cbr" and "computed" in row["flags"] for row in observations)
        dataset = client.get(
            reverse("api:v1:observations"), {"series": "Y477110006:00", "territory": REGION}
        ).json()["results"]
        assert dataset[0]["source"] == "dataset"
        assert dataset[0]["flags"] == []

        missing = client.get(reverse("api:v1:monthly"), {"series": "Y477110006:00"})
        assert missing.status_code == 404

    def test_series_document_names_sources(self, monthly_catalog: dict[str, Any]) -> None:
        from apps.exports.reports.builders import build_series_report

        document = build_series_report({"series": MORTGAGE_PC_KEY}, max_rows=100)
        meta = dict(document.meta)
        assert meta["Источник данных"].startswith("Банк России")
        assert "Версия набора" not in meta
        paragraphs = " ".join(document.sections[0].paragraphs)
        assert "Источники значений: Банк России" in paragraphs
        assert "2019–2025" in paragraphs


@pytest.mark.django_db
class TestApiSources:
    """Источники и журнал выпусков в API."""

    def test_sources_and_releases(self, reference_seed: None, client: Client) -> None:
        source = collect.sync_source("cbr")
        Release.objects.create(
            source=source,
            code="2026-09-08",
            title="по состоянию на 08.09.2026",
            reference_year=2026,
            published_on=date(2026, 9, 8),
            file_name="cbr-dataservice-2026-09-08.zip",
            archive_path="cbr/2026-09-08_cbr-dataservice-2026-09-08.zip",
            sha256="a" * 64,
            size_bytes=10,
            fetched_at="2026-09-08T10:00:00+03:00",
            status=Release.Status.PARSED,
        )
        sources = client.get(reverse("api:v1:source-list")).json()["results"]
        assert [(item["code"], item["latest_release"]) for item in sources] == [
            ("cbr", "2026-09-08")
        ]
        releases = client.get(reverse("api:v1:releases"), {"source__code": "cbr"}).json()
        assert releases["results"][0]["sha256"] == "a" * 64

    def test_journal_keeps_every_source(self, reference_seed: None) -> None:
        from apps.dashboard.selectors import release_journal

        register = collect.sync_source("fns_sme")
        bulletin = collect.sync_source("rosstat_bulletin")
        for index in range(15):
            day = date(2025, 1 + index % 12, 10).replace(year=2025 + index // 12)
            Release.objects.create(
                source=register,
                code=day.isoformat(),
                title=f"на {day:%d.%m.%Y}",
                reference_year=day.year,
                published_on=day,
                file_name=f"rmsp-statistics-{day.isoformat()}.json",
                archive_path=f"fns_sme/{day.isoformat()}.json",
                sha256=f"{index:064d}",
                size_bytes=10,
                fetched_at="2026-09-25T10:00:00+03:00",
                status=Release.Status.PARSED,
            )
        Release.objects.create(
            source=bulletin,
            code="07-2026",
            title="январь — июль 2026 г.",
            reference_year=2026,
            published_on=date(2026, 9, 2),
            file_name="info-stat-07-2026.zip",
            archive_path="rosstat_bulletin/info-stat-07-2026.zip",
            sha256="b" * 64,
            size_bytes=10,
            fetched_at="2026-09-01T10:00:00+03:00",
            status=Release.Status.PARSED,
        )
        journal = release_journal(per_source=12)
        codes = [(item.source.code, item.code) for item in journal]
        assert ("rosstat_bulletin", "07-2026") in codes
        assert sum(1 for source, _code in codes if source == "fns_sme") == 12
        assert codes[0] == ("fns_sme", "2026-03-10")


class TestSavedUpdates:
    """«Новое по сохранённому» в обзоре кабинета: выпуски источников с 90 дней."""

    @staticmethod
    def _journal(days_ago: dict[str, int]) -> None:
        """Выпуски Банка России в журнале — те же, что в складе, полученные N дней назад."""
        from datetime import timedelta

        from django.utils import timezone

        source = collect.sync_source("cbr")
        for code, days in days_ago.items():
            Release.objects.create(
                source=source,
                code=code,
                title=f"по состоянию на {code}",
                reference_year=2026,
                published_on=date.fromisoformat(code),
                file_name=f"cbr-{code}.zip",
                archive_path=f"cbr/{code}.zip",
                sha256=(code.replace("-", "") + "0" * 64)[:64],
                size_bytes=1,
                fetched_at=timezone.now() - timedelta(days=days),
                status=Release.Status.PARSED,
            )

    def test_new_release_names_saved_series_and_regions(
        self, monthly_catalog: dict[str, Any], make_user: Any
    ) -> None:
        """Новый выпуск называет отмеченный ряд с последним месяцем и отмеченный регион."""
        from datetime import timedelta

        from django.utils import timezone

        from apps.catalog.models import Series, Territory
        from apps.workspace.models import Favorite
        from apps.workspace.updates import saved_updates

        user = make_user(email="reader@example.com")
        Favorite.objects.create(user=user, series=Series.objects.get(key=MORTGAGE_PC_KEY))
        Favorite.objects.create(user=user, territory=Territory.objects.get(code=REGION))
        self._journal({"2026-08-31": 30, "2026-09-08": 1})

        news = saved_updates(user, timezone.now() - timedelta(days=5))
        assert len(news) == 1
        item = news[0]
        assert item.is_new
        assert item.releases == 1
        assert [entry.period for entry in item.series] == ["июль 2026"]
        assert [entry.name for entry in item.regions] == [Territory.objects.get(code=REGION).name]
        assert item.regions[0].count > 0

    def test_old_releases_are_shown_without_mark(
        self, monthly_catalog: dict[str, Any], make_user: Any
    ) -> None:
        """Выпуски до прошлого визита показываются без отметки «новое»."""
        from django.utils import timezone

        from apps.catalog.models import Series
        from apps.workspace.models import Favorite
        from apps.workspace.updates import saved_updates

        user = make_user(email="reader@example.com")
        Favorite.objects.create(user=user, series=Series.objects.get(key=MORTGAGE_PC_KEY))
        self._journal({"2026-08-31": 30, "2026-09-08": 10})

        news = saved_updates(user, timezone.now())
        assert [(item.is_new, item.releases) for item in news] == [(False, 2)]

    def test_release_outside_warehouse_is_skipped(
        self, monthly_catalog: dict[str, Any], make_user: Any
    ) -> None:
        """Выпуск, ещё не попавший в склад, в перечень не входит."""
        from datetime import timedelta

        from django.utils import timezone

        from apps.catalog.models import Series
        from apps.workspace.models import Favorite
        from apps.workspace.updates import saved_updates

        user = make_user(email="reader@example.com")
        Favorite.objects.create(user=user, series=Series.objects.get(key=MORTGAGE_PC_KEY))
        self._journal({"2026-09-20": 1})

        assert saved_updates(user, timezone.now() - timedelta(days=5)) == []

    def test_overview_lists_news(
        self, monthly_catalog: dict[str, Any], make_user: Any, client: Client
    ) -> None:
        """Обзор кабинета показывает выпуск с отметкой «новое»."""
        from apps.catalog.models import Series
        from apps.workspace.models import Favorite

        user = make_user(email="reader@example.com")
        Favorite.objects.create(user=user, series=Series.objects.get(key=MORTGAGE_PC_KEY))
        self._journal({"2026-09-08": 0})
        client.force_login(user)
        # Прошлый визит — до получения выпуска.
        user.updates_seen_at = user.created_at.replace(year=2026, month=1, day=1)
        user.save(update_fields=["updates_seen_at"])

        page = client.get(reverse("accounts:dashboard")).content.decode()
        assert 'class="news-row is-new"' in page
        assert "июль 2026" in page
