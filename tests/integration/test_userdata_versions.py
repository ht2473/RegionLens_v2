"""
Новая версия таблицы: рецепт прежней переносится, без новых подписей вопросов нет, ключи
рядов те же, отчёт о различиях — средствами «Пересмотров», возврат к прежней версии,
отмена черновика, предел хранимых версий и доступ.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from apps.userdata import renew
from apps.userdata.models import Dataset, DatasetVersion
from apps.warehouse import queries
from tests.support.userdata import build as _build
from tests.support.userdata import describe as _describe
from tests.support.userdata import key_of as _key
from tests.support.userdata import upload as _upload
from tests.support.userdata import userdata_dir  # noqa: F401 — приспособление модуля
from tests.support.userdata import version_of as _version

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

REGIONS = (
    "Москва",
    "Санкт-Петербург",
    "Республика Татарстан",
    "Свердловская область",
    "Новосибирская область",
    "Краснодарский край",
    "Ростовская область",
    "Самарская область",
    "Нижегородская область",
    "Челябинская область",
    "Пермский край",
    "Красноярский край",
    "Омская область",
    "Воронежская область",
    "Волгоградская область",
    "Саратовская область",
    "Тюменская область",
    "Иркутская область",
    "Хабаровский край",
    "Приморский край",
    "Республика Башкортостан",
    "Алтайский край",
    "Кемеровская область",
    "Ярославская область",
)


def _table(
    *,
    years: tuple[int, ...] = (2021, 2022),
    bump: dict[tuple[str, int], float] | None = None,
    extra: tuple[str, ...] = (),
) -> bytes:
    """Длинная таблица ДТП: значение — номер региона × 10 + год; ``bump`` меняет числа."""
    lines = ["region;year;indicator;unit;value"]
    for number, name in enumerate((*REGIONS, *extra), start=1):
        for year in years:
            value = (bump or {}).get((name, year), number * 10 + year - 2000)
            lines.append(f"{name};{year};ДТП;единиц;{value}")
    return "\n".join(lines).encode("utf-8")


def _first_version(client: Client) -> Dataset:
    dataset = _upload(client, "accidents.csv", _table())
    _describe(client, dataset)
    response = _build(client, dataset, kind="relative", per=())
    assert response.status_code == 302, response.content[:400]
    dataset.refresh_from_db()
    return dataset


def _new_version(
    client: Client, dataset: Dataset, content: bytes, name: str = "accidents.csv"
) -> Any:
    return client.post(
        reverse("userdata:version-new", args=[dataset.public_id]),
        {"action": "upload", "file": SimpleUploadedFile(name, content)},
    )


def _keys(dataset: Dataset) -> set[str]:
    return {_key(dataset, record) for record in _version(dataset).series.all()}


class TestRenewal:
    """Тот же файл в новой редакции собирается без вопросов, ключи рядов те же."""

    def test_without_questions(self, member_client: Client, warehouse: Any) -> None:
        dataset = _first_version(member_client)
        keys = _keys(dataset)
        response = _new_version(
            member_client,
            dataset,
            _table(years=(2021, 2022, 2023), bump={("Москва", 2022): 999.0}),
            name="accidents_2023.csv",
        )
        assert response.status_code == 302
        dataset.refresh_from_db()
        version = _version(dataset)
        assert version.number == 2
        assert response["Location"] == reverse("userdata:version", args=[dataset.public_id, 2])
        assert _keys(dataset) == keys
        changes = version.report["changes"]
        assert changes["base"] == 1
        assert changes["changed"] == 1
        assert changes["added"] == len(REGIONS)
        assert changes["removed"] == 0
        assert changes["new_years"] == [2023]
        assert not changes["new_series"]
        assert not changes["gone_series"]
        key = next(iter(keys))
        assert queries.year_counts(key) == {}  # без читателя слой рядов ничего не видит
        page = member_client.get(response["Location"])
        assert page.status_code == 200
        assert "Изменённых значений" in page.text
        assert "999" in page.text

    def test_value_history(self, member_client: Client, warehouse: Any) -> None:
        dataset = _first_version(member_client)
        _new_version(member_client, dataset, _table(bump={("Москва", 2022): 999.0}))
        dataset.refresh_from_db()
        key = next(iter(_keys(dataset)))
        trace = member_client.get(
            reverse("analytics:revision-trace"),
            {"series": key, "territory": "RU-MOW", "year": "2022"},
            HTTP_HX_REQUEST="true",
        )
        assert trace.status_code == 200
        assert "999" in trace.text
        assert "32" in trace.text  # прежнее значение: регион № 1, 2022 год

    def test_new_label_is_asked(self, member_client: Client, warehouse: Any) -> None:
        dataset = _first_version(member_client)
        first = _version(dataset)
        response = _new_version(member_client, dataset, _table(extra=("Тридевятое царство",)))
        assert response["Location"] == reverse("userdata:table", args=[dataset.public_id])
        dataset.refresh_from_db()
        assert dataset.current_version_id == first.pk  # пока новая не собрана
        draft = renew.draft_of(dataset)
        assert draft is not None
        assert renew.pending_questions(draft)
        page = member_client.get(response["Location"])
        assert "новые подписи территорий" in page.text
        # Ответ — и сборка: новая версия становится текущей.
        _describe(member_client, dataset)
        built = _build(member_client, dataset, kind="relative", per=())
        assert built.status_code == 302
        assert built["Location"] == reverse("userdata:version", args=[dataset.public_id, 2])
        dataset.refresh_from_db()
        assert dataset.current_version_id == draft.pk

    def test_same_file_refused(self, member_client: Client, warehouse: Any) -> None:
        dataset = _first_version(member_client)
        response = _new_version(member_client, dataset, _table())
        assert response.status_code == 302
        assert dataset.versions.count() == 1
        page = member_client.get(reverse("userdata:dataset", args=[dataset.public_id]))
        assert "совпадает с текущей версией" in page.text

    def test_restore_and_discard(self, member_client: Client, warehouse: Any) -> None:
        dataset = _first_version(member_client)
        first = _version(dataset)
        _new_version(member_client, dataset, _table(bump={("Москва", 2022): 999.0}))
        dataset.refresh_from_db()
        second = _version(dataset)
        key = next(iter(_keys(dataset)))
        member_client.post(reverse("userdata:version-restore", args=[dataset.public_id, 1]))
        dataset.refresh_from_db()
        assert dataset.current_version_id == first.pk
        table = member_client.get(reverse("surface:table"), {"series": key, "year": "2022"})
        assert table.status_code == 200
        assert "999" not in table.text
        # Черновик новой версии отменяется, текущая остаётся.
        _new_version(member_client, dataset, _table(extra=("Тридевятое царство",)))
        assert renew.draft_of(dataset) is not None
        member_client.post(reverse("userdata:version-discard", args=[dataset.public_id]))
        dataset.refresh_from_db()
        assert renew.draft_of(dataset) is None
        assert dataset.current_version_id == first.pk
        assert DatasetVersion.objects.filter(pk=second.pk).exists()

    def test_keeps_last_versions(
        self, member_client: Client, settings: Any, warehouse: Any
    ) -> None:
        settings.USERDATA_KEEP_VERSIONS = 2
        dataset = _first_version(member_client)
        for value in (500.0, 600.0):
            _new_version(member_client, dataset, _table(bump={("Москва", 2022): value}))
            dataset.refresh_from_db()
        assert sorted(dataset.versions.values_list("number", flat=True)) == [2, 3]
        assert not (dataset.directory / "1").exists()
        # Значения удалённой версии остались в выпусках файла текущей.
        key = next(iter(_keys(dataset)))
        trace = member_client.get(
            reverse("analytics:revision-trace"),
            {"series": key, "territory": "RU-MOW", "year": "2022"},
            HTTP_HX_REQUEST="true",
        )
        assert "500" in trace.text and "600" in trace.text and "32" in trace.text


class TestAccess:
    """Версии — только владельцу; читатель ссылки видит новую версию по той же ссылке."""

    def test_stranger(self, member_client: Client, make_user: Any, warehouse: Any) -> None:
        dataset = _first_version(member_client)
        other = Client()
        other.force_login(make_user(email="other@example.com"))
        response = _new_version(other, dataset, _table(bump={("Москва", 2022): 1.0}))
        assert response.status_code == 404
        assert (
            other.get(reverse("userdata:version", args=[dataset.public_id, 1])).status_code == 404
        )
        assert (
            other.post(reverse("userdata:version-restore", args=[dataset.public_id, 1])).status_code
            == 404
        )

    def test_share_follows_new_version(self, member_client: Client, warehouse: Any) -> None:
        dataset = _first_version(member_client)
        member_client.post(
            reverse("userdata:share-create", args=[dataset.public_id]), {"days": "7"}
        )
        page = member_client.get(reverse("userdata:dataset", args=[dataset.public_id]))
        link = page.text.split('value="http://testserver', 1)[1].split('"', 1)[0]
        _new_version(member_client, dataset, _table(bump={("Москва", 2022): 999.0}))
        reader = Client()
        reader.get(link)
        key = next(iter(_keys(Dataset.objects.get(pk=dataset.pk))))
        table = reader.get(reverse("surface:table"), {"series": key, "year": "2022"})
        assert table.status_code == 200
        assert "999" in table.text
        # Отчёт о различиях — только владельцу.
        assert (
            reader.get(reverse("userdata:version", args=[dataset.public_id, 2])).status_code == 404
        )
