"""Ссылки справочников и сохранённого на ряды склада: перечень пропавших, журнал, письмо, панель."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from django.core import mail
from django.test import Client
from django.urls import reverse

from apps.warehouse import builds, duckdb_client
from apps.warehouse.etl import integrity
from apps.warehouse.models import EtlRun
from apps.workspace.models import SavedQuery
from tests.support.warehouse import accept_small_source

pytestmark = pytest.mark.integration

ADMIN = "admin@example.org"


def all_project_keys() -> set[str]:
    """Все ключи, на которые ссылаются справочники проекта."""
    return {key for keys in integrity.project_references().values() for key in keys}


def test_references_cover_reference_files(db: None) -> None:
    """Перечень ссылок берёт основной набор, связи, статусы, численность и паспорт."""
    references = integrity.project_references()
    assert set(references) == set(integrity.PROJECT_ORIGINS)
    assert all(references.values())


def test_nothing_missing_when_all_known(db: None) -> None:
    assert integrity.missing_series(all_project_keys()) == []


def test_missing_project_series(db: None) -> None:
    """Ряд основного набора, которого нет в складе, назван вместе с подписью."""
    featured = integrity.project_references()["featured"]
    key = next(iter(featured))
    missing = integrity.missing_series(all_project_keys() - {key})
    assert integrity.MissingSeries("featured", key, featured[key]) in missing


def _source_series(source: str) -> str:
    """Ключ ряда источника из справочника, на который ссылаются справочники проекта."""
    from apps.sources.registry import registry

    referenced = all_project_keys()
    return next(
        item.key for item in registry().series if item.source == source and item.key in referenced
    )


def test_source_series_wait_for_first_collection(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Ряды источника без единого собранного выпуска не пропали, а ждут сбора.

    На новом стенде архива выпусков ещё нет: письмо о «сменившихся ключах» после первой
    сборки было бы ложной тревогой.
    """
    from apps.sources import parsed

    key = _source_series("cbr")
    monkeypatch.setattr(parsed, "releases", lambda source=None: [])
    assert integrity.missing_series(all_project_keys() - {key}) == []


def test_source_series_missing_after_collection(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Выпуски источника собраны, а ряда в складе нет — это пропажа."""
    from apps.sources import parsed

    key = _source_series("cbr")
    monkeypatch.setattr(parsed, "releases", lambda source=None: [("выпуск", Path("x"))])
    missing = integrity.missing_series(all_project_keys() - {key})
    assert key in {item.key for item in missing}


def test_missing_saved_series(make_user: Any) -> None:
    """Сохранённые виды с исчезнувшим рядом считаются по числу записей."""
    user = make_user()
    for title in ("Карта", "Рейтинг"):
        SavedQuery.objects.create(
            user=user, title=title, target="map", parameters={"series": "GONE000001:00"}
        )
    missing = integrity.missing_series(all_project_keys())
    assert missing == [integrity.MissingSeries("saved", "GONE000001:00", "2")]


def test_grouped_keeps_project_first() -> None:
    items = [
        {"origin": "saved", "key": "A:00", "detail": "1"},
        {"origin": "featured", "key": "B:00", "detail": "Зарплата"},
    ]
    groups = integrity.grouped(items)
    assert [group["origin"] for group in groups] == ["featured", "saved"]
    assert groups[0]["is_project"]
    assert not groups[1]["is_project"]


@pytest.fixture
def build_target(settings: Any, tmp_path: Path, warehouse: Any) -> Any:
    """Сборка пишет в свой файл, а не в общий склад прогона."""
    target = tmp_path / "rebuilt.duckdb"
    settings.DUCKDB_PATH = target
    settings.ADMINS = [ADMIN]
    duckdb_client.close_connections()
    with accept_small_source():
        yield target
    duckdb_client.close_connections()


def test_build_records_and_reports_missing(
    admin_panel_client: Client, warehouse: Any, build_target: Path
) -> None:
    """
    В синтетическом складе нет большей части основного набора: сборка называет эти ряды.

    Письмо уходит, карточка запуска показывает перечень.
    """
    run = EtlRun.objects.create(mode=EtlRun.Mode.FULL, source_path=str(warehouse.path))
    builds.execute(run)
    run.refresh_from_db()

    known = {
        row["series_key"] for row in duckdb_client.fetch_dicts("SELECT series_key FROM dim_series")
    }
    missing = run.missing_series
    assert missing
    assert {item["origin"] for item in missing} >= {"featured"}
    assert not {item["key"] for item in missing} & known
    assert run.missing_project_series == missing

    assert [letter.subject for letter in mail.outbox] == [
        "[RegionLens] в складе нет рядов из справочников"
    ]
    assert missing[0]["key"] in mail.outbox[0].body

    page = admin_panel_client.get(reverse("dashboard:etl-run", kwargs={"pk": run.pk}))
    assert "Ссылки на ряды, которых нет в складе" in page.content.decode()


def test_series_missing_from_warehouse_leave_the_catalog(
    warehouse: Any, warehouse_file: Path
) -> None:
    """Ряд, которого нет в складе (сменился ключ), удаляется из каталога со своим избранным."""
    from apps.catalog.models import Series
    from tests.support.warehouse import sync_catalog

    kept = Series.objects.first()
    assert kept is not None
    gone = Series.objects.create(
        key=f"{kept.indicator.code}:deadbeef", indicator=kept.indicator, slug="ischeznuvshii"
    )

    sync_catalog(warehouse_file)

    assert not Series.objects.filter(pk=gone.pk).exists()
    assert Series.objects.filter(pk=kept.pk).exists()
