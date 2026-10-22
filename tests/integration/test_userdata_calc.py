"""
Панель расчёта на поле исследования: каталог с причинами, предпросмотр расчёта, сохранение
ряда с происхождением и карты на поле, пересчёт сборки из панели, своя формула
с предупреждением, чужое исследование — 404.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from apps.userdata import formula_store, studies
from apps.userdata.models import Study
from tests.support.userdata import built as _built
from tests.support.userdata import first_record as _first
from tests.support.userdata import key_of as _key
from tests.support.userdata import userdata_dir  # noqa: F401 — приспособление модуля
from tests.support.userdata import version_of as _version

pytestmark = [pytest.mark.integration, pytest.mark.django_db]


def _study(client: Client) -> Study:
    client.post(reverse("userdata:study-new"), {"title": "Расчёты"})
    return Study.objects.latest("created_at")


def _url(study: Study) -> str:
    return reverse("userdata:calc", args=[study.public_id])


def test_panel_lists_catalog_with_reasons(member_client: Client, warehouse: Any) -> None:
    dataset = _built(member_client, "crime_wide.csv")
    key = _key(dataset, _first(dataset))
    study = _study(member_client)
    page = member_client.get(_url(study), {"series": key}, headers={"HX-Request": "true"})
    assert page.status_code == 200
    text = page.text
    assert "data-calc-form" in text and "<html" not in text
    assert "Базовый год = 100" in text and "Коэффициент локализации" in text
    # У суммы недоступны расчёты только для относительных величин — серым с причиной.
    assert "только для относительных величин" in text
    # Без сценариев — та же панель отдельной страницей.
    full = member_client.get(_url(study), {"series": key})
    assert full.status_code == 200 and "<html" in full.text


def test_preview_and_save_with_provenance(member_client: Client, warehouse: Any) -> None:
    dataset = _built(member_client, "crime_wide.csv")
    key = _key(dataset, _first(dataset))
    study = _study(member_client)
    data = {"op": "baseyear", "a": key, "table": dataset.code, "year": "2015"}
    preview = member_client.post(_url(study), data, headers={"HX-Request": "true"})
    assert preview.status_code == 200
    assert "calc-preview__count" in preview.text and "ВГОДУ" in preview.text

    saved = member_client.post(
        _url(study), {**data, "action": "save"}, headers={"HX-Request": "true"}
    )
    assert saved.status_code == 204
    assert reverse("userdata:study", args=[study.public_id]) in saved["HX-Redirect"]
    (definition,) = formula_store.definitions(_version(dataset))
    assert definition.operation == "baseyear"
    assert definition.parameters["year"] == "2015"
    assert definition.title.endswith("2015 = 100")
    assert definition.created
    study.refresh_from_db()
    (block,) = studies.blocks_of(study)
    assert block["parameters"]["series"] == f"u:{dataset.code}:{definition.code}"
    # Происхождение ряда — на странице таблицы.
    page = member_client.get(
        reverse("userdata:dataset", args=[dataset.public_id]), {"show": definition.code}
    )
    assert "Базовый год = 100" in page.text and "own-formula__origin" in page.text


def test_recount_from_panel(member_client: Client, warehouse: Any) -> None:
    dataset = _built(member_client, "crime_wide.csv", per=())
    key = _key(dataset, _first(dataset))
    study = _study(member_client)
    saved = member_client.post(_url(study), {"op": "per10000", "a": key, "action": "save"})
    assert saved.status_code == 302
    version = _version(dataset)
    assert version.series.filter(derived="per10000", values_count__gt=0).exists()


def test_free_formula_warns_about_units(member_client: Client, warehouse: Any) -> None:
    dataset = _built(member_client, "crime_wide.csv")
    record = _first(dataset)
    per = _version(dataset).series.filter(base_code=record.code).first()
    assert per is not None
    study = _study(member_client)
    expression = f"{{{_key(dataset, record)}}} + {{{_key(dataset, per)}}}"
    page = member_client.post(
        _url(study),
        {"op": "free", "expression": expression, "table": dataset.code, "title": "Сумма"},
        headers={"HX-Request": "true"},
    )
    assert "разных единицах" in page.text


def test_other_users_study_is_not_found(
    member_client: Client, make_user: Any, warehouse: Any
) -> None:
    study = _study(member_client)
    other = Client()
    other.force_login(make_user(email="other@example.com"))
    assert other.get(_url(study)).status_code == 404
    assert other.post(_url(study), {"action": "save"}).status_code == 404
