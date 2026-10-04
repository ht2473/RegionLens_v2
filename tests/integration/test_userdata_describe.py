"""
Шаг «Что в таблице»: распознавание выбранной таблицы, вопросы и ответы человека в рецепте.

Таблицы — усечённые настоящие файлы ЕБТ и «Регионов России» (``tests/fixtures/userdata``).
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from apps.userdata import jobs, recognize, tables
from apps.userdata.models import Dataset, DatasetVersion, TerritoryLabel

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "userdata"


@pytest.fixture(autouse=True)
def userdata_dir(settings: Any, tmp_path: Path) -> Iterator[Path]:
    settings.USERDATA_DIR = tmp_path / "userdata"
    yield settings.USERDATA_DIR
    shutil.rmtree(settings.USERDATA_DIR, ignore_errors=True)


def _chosen(client: Client, name: str, data: bytes) -> Dataset:
    """Загрузить файл и выбрать его единственную (или лучшую) таблицу."""
    client.post(
        reverse("userdata:upload"), {"action": "upload", "file": SimpleUploadedFile(name, data)}
    )
    dataset = Dataset.objects.latest("created_at")
    version = dataset.current_version
    assert version is not None
    best = next(table for table in version.report["inspection"]["tables"] if table["best"])
    client.post(
        reverse("userdata:file", args=[dataset.public_id]),
        {"action": "choose", "table": best["key"], "encoding": ""},
    )
    return dataset


def _version(dataset: Dataset) -> DatasetVersion:
    return DatasetVersion.objects.get(dataset=dataset)


@pytest.mark.django_db
class TestRecognitionPage:
    """Страница называет форму, роли, периоды и задаёт только нужные вопросы."""

    def test_long_table(self, client: Client) -> None:
        dataset = _chosen(client, "environment.csv", (FIXTURES / "environment.csv").read_bytes())
        page = client.get(reverse("userdata:table", args=[dataset.public_id]))
        assert page.status_code == 200
        text = page.text
        assert "Длинная таблица" in text
        assert "85 из 85" in text
        assert "2014–2022" in text
        assert 'name="role-5"' in text
        assert 'value="territory" selected' in text
        assert "Подписи без территории" not in text

    def test_years_in_columns_with_nested_question(self, client: Client) -> None:
        dataset = _chosen(client, "crime_wide.csv", (FIXTURES / "crime_wide.csv").read_bytes())
        text = client.get(reverse("userdata:table", args=[dataset.public_id])).text
        assert "Годы или периоды — в столбцах" in text
        assert "«Архангельская область» в таблице — вместе с автономным округом" in text
        assert "«Тюменская область» в таблице" in text

    def test_slices_and_totals(self, client: Client) -> None:
        dataset = _chosen(client, "despair.csv", (FIXTURES / "despair.csv").read_bytes())
        text = client.get(reverse("userdata:table", args=[dataset.public_id])).text
        assert "Разрезы" in text
        assert 'name="slice-6" value="Всего" checked' in text
        assert 'name="slice-7" value="T" checked' in text

    def test_pasted_two_parts(self, client: Client) -> None:
        first = (FIXTURES / "regions_population_part1.txt").read_text(encoding="utf-8")
        client.post(reverse("userdata:upload"), {"action": "paste", "text": first})
        dataset = Dataset.objects.get()
        second = (FIXTURES / "regions_population_part2.txt").read_text(encoding="utf-8")
        url = reverse("userdata:file", args=[dataset.public_id])
        client.post(url, {"action": "append", "text": second})
        client.post(url, {"action": "choose", "table": "", "encoding": ""})
        text = client.get(reverse("userdata:table", args=[dataset.public_id])).text
        assert "85 из 85" in text
        assert "2010–2023" in text


@pytest.mark.django_db
class TestAnswers:
    """Ответы человека сохраняются в рецепте и сильнее догадок."""

    def test_nested_answer_and_roles(self, client: Client) -> None:
        dataset = _chosen(client, "crime_wide.csv", (FIXTURES / "crime_wide.csv").read_bytes())
        url = reverse("userdata:table", args=[dataset.public_id])
        response = client.post(
            url,
            {
                "nested-label-1": "Архангельская область",
                "nested-1": "RU-ARK",
                "role-0": "indicator",
            },
        )
        assert response.status_code == 302
        recipe = _version(dataset).recipe
        assert recipe["nested"] == {"Архангельская область": "RU-ARK"}
        assert recipe["roles"]["0"] == "indicator"
        assert recipe["form"] == recognize.WIDE
        assert _version(dataset).state == DatasetVersion.State.DESCRIBED
        text = client.get(url).text
        assert "«Архангельская область» в таблице" not in text
        assert "«Тюменская область» в таблице" in text

    def test_unknown_label_is_chosen_and_remembered(
        self, member_client: Client, member: Any
    ) -> None:
        rows = "Регион;2023\nТатария;1,5\nТверская обл.;2\nМосква;3\nЗагадочный край;4\n"
        dataset = _chosen(member_client, "t.csv", rows.encode("utf-8"))
        url = reverse("userdata:table", args=[dataset.public_id])
        text = member_client.get(url).text
        assert "Подписи без территории: 1" in text
        assert "Загадочный край" in text
        member_client.post(
            url,
            {"label-1": "Загадочный край", "territory-1": "RU-ALT", "remember": "on"},
        )
        assert _version(dataset).recipe["territories"] == {"Загадочный край": "RU-ALT"}
        assert TerritoryLabel.objects.get(owner=member).territory_code == "RU-ALT"
        assert "Подписи без территории" not in member_client.get(url).text

    def test_year_question(self, client: Client) -> None:
        rows = (
            "Регион;Население;ВРП\nМосква;13;25\n"
            "Тверская область;1,2;0,5\nКурская область;1,1;0,6\n"
        )
        dataset = _chosen(client, "показатели_2023.csv", rows.encode("utf-8"))
        url = reverse("userdata:table", args=[dataset.public_id])
        text = client.get(url).text
        assert "Годы в таблице не найдены" in text
        assert 'value="2023"' in text
        client.post(url, {"year": "2023"})
        assert _version(dataset).recipe["year"] == 2023
        assert "Годы в таблице не найдены" not in client.get(url).text

    def test_foreign_labels_and_choices_are_ignored(self, client: Client) -> None:
        dataset = _chosen(client, "environment.csv", (FIXTURES / "environment.csv").read_bytes())
        url = reverse("userdata:table", args=[dataset.public_id])
        client.post(
            url, {"label-1": "Чужая подпись", "territory-1": "RU-ALT", "role-1": "drop table"}
        )
        recipe = _version(dataset).recipe
        assert recipe["territories"] == {}
        assert recipe["roles"]["1"] == recognize.INDICATOR

    def test_other_session_gets_404(self, client: Client) -> None:
        dataset = _chosen(client, "environment.csv", (FIXTURES / "environment.csv").read_bytes())
        stranger = Client()
        url = reverse("userdata:table", args=[dataset.public_id])
        assert stranger.get(url).status_code == 404
        assert stranger.post(url, {"year": "2020"}).status_code == 404
        status = reverse("userdata:table-status", args=[dataset.public_id])
        assert stranger.get(status).status_code == 404


@pytest.mark.django_db
class TestLargeTables:
    """Большая таблица разбирается сводкой DuckDB; очень большая — отдельным процессом."""

    def test_summary_inline(self, client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(tables, "SMALL_TABLE_BYTES", 1000)
        dataset = _chosen(client, "despair.csv", (FIXTURES / "despair.csv").read_bytes())
        text = client.get(reverse("userdata:table", args=[dataset.public_id])).text
        assert "Длинная таблица" in text
        version = _version(dataset)
        assert version.report["profile_state"] == jobs.READY
        assert version.report["profile"]["rows"] == 2257

    def test_summary_in_background(self, client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(tables, "SMALL_TABLE_BYTES", 1000)
        monkeypatch.setattr(jobs, "INLINE_BYTES", 1000)
        launched: list[int] = []
        monkeypatch.setattr(jobs, "launch", lambda version: launched.append(version.pk))
        dataset = _chosen(client, "despair.csv", (FIXTURES / "despair.csv").read_bytes())
        url = reverse("userdata:table", args=[dataset.public_id])
        page = client.get(url)
        assert "Разбираем таблицу…" in page.text
        assert 'role="status"' in page.text
        status = reverse("userdata:table-status", args=[dataset.public_id])
        assert "Разбираем таблицу…" in client.get(status).text
        version = _version(dataset)
        assert launched == [version.pk]
        jobs.summarize(version)
        done = client.get(status)
        assert done.status_code == 204
        assert done["HX-Refresh"] == "true"
        assert "Длинная таблица" in client.get(url).text
