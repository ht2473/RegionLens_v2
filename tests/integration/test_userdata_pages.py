"""
Страница таблицы, мастер и формула (этап 4 плана улучшения): вкладки и выбранный ряд,
«Открыть в исследовании», таблица с ролями столбцов, вопросы только о неуверенном,
пересчёты в мастере — только снять, шаблоны формулы, предпросмотр и возврат в исследование.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from apps.catalog.models import Territory
from apps.userdata import formula_store, indicators, studies
from apps.userdata.models import Dataset, DatasetSeries, Study
from tests.support.userdata import built as _built
from tests.support.userdata import (
    describe,
    upload,
    userdata_dir,  # noqa: F401 — приспособление модуля
    version_of,
)
from tests.support.userdata import first_record as _first
from tests.support.userdata import key_of as _key

pytestmark = [pytest.mark.integration, pytest.mark.django_db]


def _page(client: Client, dataset: Dataset, **query: Any) -> Any:
    return client.get(reverse("userdata:dataset", args=[dataset.public_id]), query)


class TestDatasetPage:
    """Вкладки страницы таблицы и выбранный ряд."""

    def test_data_tab_shows_chosen_series_with_map(self, client: Client, warehouse: Any) -> None:
        dataset = _built(client)
        page = _page(client, dataset)
        assert page.status_code == 200
        assert 'id="dataset-detail"' in page.text
        assert "geo-map" in page.text or "tile-map" in page.text
        assert reverse("userdata:dataset-study", args=[dataset.public_id]) in page.text
        # Вкладки владельца — все четыре.
        for title in ("Данные", "Проверки", "Файл и версии", "Доступ"):
            assert title in page.text

    def test_show_selects_series(self, client: Client, warehouse: Any) -> None:
        dataset = _built(client, "crime_wide.csv")
        records = list(version_of(dataset).series.order_by("order"))
        chosen = records[-1]
        page = _page(client, dataset, show=chosen.code)
        assert f'value="{_key(dataset, chosen)}"' in page.text
        # Ряд из старой ссылки «#series-…» — по адресу «?show=…».
        assert f"?show={chosen.code}" in page.text

    def test_series_without_values_not_offered(self, client: Client, warehouse: Any) -> None:
        dataset = _built(client, "crime_wide.csv")
        record = _first(dataset)
        DatasetSeries.objects.filter(pk=record.pk).update(values_count=0)
        page = _page(client, dataset, show=record.code)
        assert "нет значений" in page.text
        assert f'value="{_key(dataset, record)}"' not in page.text

    def test_checks_tab_has_notices(self, client: Client, warehouse: Any) -> None:
        dataset = _built(client, "crime_wide.csv", kind=indicators.RELATIVE)
        page = _page(client, dataset, tab="checks")
        assert "Для долей и средних область без округов не вычислить" in page.text
        assert "Территории" in page.text

    def test_file_and_access_tabs(self, member_client: Client, warehouse: Any) -> None:
        dataset = _built(member_client)
        files = _page(member_client, dataset, tab="file")
        assert 'id="own-versions"' in files.text
        assert "Удалить таблицу" in files.text
        access = _page(member_client, dataset, tab="access")
        assert 'id="own-shares"' in access.text

    def test_reader_sees_only_data_and_checks(
        self, member_client: Client, client: Client, warehouse: Any
    ) -> None:
        from apps.userdata import shares

        dataset = _built(member_client)
        share, token = shares.create(dataset, days=7, downloads=False)
        reader = Client()
        reader.get(reverse("share-open", args=[token]))
        page = reader.get(reverse("userdata:dataset", args=[dataset.public_id]), {"tab": "file"})
        assert page.status_code == 200
        assert "Файл и версии" not in page.text
        assert 'id="own-versions"' not in page.text
        assert reverse("userdata:dataset-study", args=[dataset.public_id]) not in page.text
        assert share.pk


class TestOpenInStudy:
    """«Открыть в исследовании» со страницы таблицы."""

    def test_creates_study_and_selects_series(self, client: Client, warehouse: Any) -> None:
        dataset = _built(client)
        key = _key(dataset, _first(dataset))
        response = client.post(
            reverse("userdata:dataset-study", args=[dataset.public_id]), {"series": key}
        )
        assert response.status_code == 302
        study = Study.objects.get()
        location = urlparse(response["Location"])
        assert location.path == reverse("userdata:study", args=[study.public_id])
        assert parse_qs(location.query)["series"] == [key]

    def test_reuses_study_with_table(self, client: Client, warehouse: Any) -> None:
        dataset = _built(client)
        key = _key(dataset, _first(dataset))
        study = Study.objects.get() if Study.objects.exists() else None
        if study is None:
            client.post(reverse("userdata:study-new"), {"title": "Своё"})
            study = Study.objects.get()
        studies.add_view(study, "map", f"series={key}")
        response = client.post(
            reverse("userdata:dataset-study", args=[dataset.public_id]), {"series": "u:x:y"}
        )
        # Чужой ключ отбрасывается; исследование — то, где таблица уже есть.
        assert response["Location"] == reverse("userdata:study", args=[study.public_id])
        assert Study.objects.count() == 1


class TestWizard:
    """Мастер: таблица с ролями в шапке, вопросы о неуверенном, пересчёты."""

    def test_table_step_shows_roles_over_columns(self, client: Client, warehouse: Any) -> None:
        dataset = upload(client, "crime_wide.csv")
        page = client.get(reverse("userdata:table", args=[dataset.public_id]))
        assert page.status_code == 200
        assert "role-table" in page.text
        assert 'name="role-0"' in page.text
        # Первые строки данных — под шапкой таблицы.
        assert "Алтайский край" in page.text or "Республика Адыгея" in page.text

    def test_series_step_asks_only_about_unclear(self, client: Client, warehouse: Any) -> None:
        regions = list(Territory.objects.comparable().order_by("code"))
        lines = ["Регион;Показатель 7"] + [
            f"{item.name_ru};{index + 1}" for index, item in enumerate(regions)
        ]
        client.post(
            reverse("userdata:upload"),
            {
                "action": "upload",
                "file": SimpleUploadedFile("t.csv", "\n".join(lines).encode("utf-8")),
            },
        )
        dataset = Dataset.objects.latest("created_at")
        client.post(
            reverse("userdata:file", args=[dataset.public_id]),
            {"action": "choose", "table": "", "encoding": ""},
        )
        describe(client, dataset, {"year": "2023"})
        page = client.get(reverse("userdata:series", args=[dataset.public_id]))
        assert "Показателей с вопросами: 1" in page.text
        assert "Складывается ли по регионам?" in page.text
        # Новые пересчёты в мастере не предлагаются.
        assert 'name="recalc-1" value="russia"' not in page.text

    def test_recounts_kept_unless_unchecked(self, client: Client, warehouse: Any) -> None:
        dataset = _built(client, "crime_wide.csv", kind=indicators.SUM)
        version = version_of(dataset)
        assert indicators.indicators_of(version)[0].per == indicators.DEFAULT_PER
        # Форма без перечня пересчётов (старая или без поля) их не трогает.
        answers = {
            item.name: {"title": item.title, "unit": item.unit, "kind": item.kind}
            for item in indicators.indicators_of(version)
        }
        indicators.save(version, answers)
        version.refresh_from_db()
        assert indicators.indicators_of(version)[0].per == indicators.DEFAULT_PER
        # Снятая отметка убирает пересчёт.
        answers = {name: {**answer, "per": [], "recalc": []} for name, answer in answers.items()}
        indicators.save(version, answers)
        version.refresh_from_db()
        assert indicators.indicators_of(version)[0].per == ()


class TestFormulaTemplates:
    """Шаблоны формулы, предпросмотр и возврат в исследование."""

    def test_template_builds_expression_and_title(self, client: Client, warehouse: Any) -> None:
        from apps.catalog.models import Series

        dataset = _built(client, "crime_wide.csv", kind=indicators.SUM, per=())
        key = _key(dataset, _first(dataset))
        site = Series.objects.get(key="Y477110461:00")
        url = reverse("userdata:formula-new", args=[dataset.public_id])
        page = client.get(url)
        assert 'value="per1000" checked' in page.text
        data = {"template": "per1000", "a": key, "b": site.key, "kind": "relative"}
        checked = client.post(url, {**data, "action": "check"})
        assert "Посчитано для" in checked.text
        assert "geo-map" in checked.text
        saved = client.post(url, {**data, "action": "save"})
        assert saved.status_code == 302
        (definition,) = formula_store.definitions(version_of(dataset))
        assert definition.expression == f"{{{key}}} / {{{site.key}}} * 1000"
        assert "на 1 000" in definition.title
        assert definition.unit == "на 1 000"
        location = urlparse(saved["Location"])
        assert location.path == reverse("userdata:dataset", args=[dataset.public_id])
        assert parse_qs(location.query)["show"] == [definition.code]
        # Правка формулы по шаблону открывается шаблоном.
        edit = client.get(reverse("userdata:formula", args=[dataset.public_id, definition.code]))
        assert 'value="per1000" checked' in edit.text

    def test_template_needs_both_series(self, client: Client, warehouse: Any) -> None:
        dataset = _built(client)
        url = reverse("userdata:formula-new", args=[dataset.public_id])
        response = client.post(url, {"template": "diff", "a": "", "b": "", "action": "check"})
        assert "Выберите оба показателя" in response.text

    def test_from_study_returns_with_map(self, client: Client, warehouse: Any) -> None:
        dataset = _built(client, "crime_wide.csv", kind=indicators.SUM, per=())
        key = _key(dataset, _first(dataset))
        client.post(reverse("userdata:study-new"), {"title": "К курсовой"})
        study = Study.objects.latest("created_at")
        url = reverse("userdata:formula-new", args=[dataset.public_id])
        page = client.get(url, {"study": str(study.public_id)})
        assert f'name="study" value="{study.public_id}"' in page.text
        saved = client.post(
            url,
            {
                "template": "free",
                "title": "Вдвое",
                "expression": f"{{{key}}} * 2",
                "kind": "sum",
                "study": str(study.public_id),
                "action": "save",
            },
        )
        assert urlparse(saved["Location"]).path == reverse("userdata:study", args=[study.public_id])
        study.refresh_from_db()
        (block,) = studies.blocks_of(study)
        assert block["target"] == "map"

    def test_previews_are_rate_limited(self, client: Client, settings: Any, warehouse: Any) -> None:
        from django.core.cache import cache

        dataset = _built(client)
        key = _key(dataset, _first(dataset))
        settings.USERDATA_FORMULA_PREVIEWS = 1
        cache.clear()
        url = reverse("userdata:formula-new", args=[dataset.public_id])
        data = {"template": "free", "title": "X", "expression": f"{{{key}}} * 2"}
        statuses = [
            client.post(url, data, headers={"HX-Request": "true"}).status_code for _ in range(2)
        ]
        assert statuses == [200, 429]
