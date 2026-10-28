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
    dataset = _built(member_client, "crime_wide.csv", per=("per100000",))
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


def test_concentration_warns_when_parts_miss_the_total(
    member_client: Client, warehouse: Any
) -> None:
    from tests.support.userdata import build, describe, upload

    regions = ["Москва", "Тверская область", "Курская область", "Белгородская область"]
    lines = ["Регион;Показатель;Единица;2022;2023"]
    for region in regions:
        lines += [
            f"{region};Отгружено всего;млн руб.;100;120",
            f"{region};Промышленность;млн руб.;50;60",
            f"{region};Торговля;млн руб.;30;36",
            f"{region};Прочее;млн руб.;20;24",
        ]
    dataset = upload(member_client, "отрасли.csv", "\n".join(lines).encode("utf-8"))
    describe(member_client, dataset)
    assert build(member_client, dataset).status_code == 302
    keys = {record.title: _key(dataset, record) for record in _version(dataset).series.all()}
    total = next(key for title, key in keys.items() if "всего" in title)
    industry = next(key for title, key in keys.items() if "Промышленность" in title)
    trade = next(key for title, key in keys.items() if "Торговля" in title)
    study = _study(member_client)
    data = {"op": "hhi", "a": total, "b": total, "parts": [industry, trade]}
    page = member_client.post(_url(study), {**data, "table": dataset.code})
    # Промышленность и торговля — 80 % итога: индекс выйдет неточным.
    assert "покрывают около 80 % итога" in page.text
    other = next(key for title, key in keys.items() if "Прочее" in title)
    full = member_client.post(
        _url(study), {**data, "parts": [industry, trade, other], "table": dataset.code}
    )
    assert "покрывают около" not in full.text


def test_calc_from_table_page_saves_into_table(member_client: Client, warehouse: Any) -> None:
    # Со страницы таблицы — то же окно без исследования: показатель встаёт в таблицу.
    dataset = _built(member_client, "crime_wide.csv")
    url = reverse("userdata:dataset-calc", args=[dataset.public_id])
    page = member_client.get(url)
    assert page.status_code == 200
    assert "Расчёт показателя" in page.text and "Сохранить в таблицу" in page.text
    assert "К таблице" in page.text
    key = _key(dataset, _first(dataset))
    data = {"op": "baseyear", "a": key, "table": dataset.code, "year": "2015", "action": "save"}
    saved = member_client.post(url, data)
    (definition,) = formula_store.definitions(_version(dataset))
    assert saved["Location"] == (
        f"{reverse('userdata:dataset', args=[dataset.public_id])}?show={definition.code}"
    )
    assert definition.operation == "baseyear"


def test_calc_from_table_needs_own_built_table(member_client: Client, warehouse: Any) -> None:
    dataset = _built(member_client, "crime_wide.csv")
    url = reverse("userdata:dataset-calc", args=[dataset.public_id])
    # Чужой человек — новый сеанс без входа.
    assert Client().get(url).status_code == 404


def test_series_pick_like_my_tables(member_client: Client, warehouse: Any) -> None:
    """
    Показатель в окне — перечнем, как «Мои таблицы»: таблица выбранного раскрыта, у таблиц
    с одинаковым названием — когда загружена; не выпадающий список во весь экран.
    """
    first = _built(member_client, "crime_wide.csv")
    _built(member_client, "crime_wide.csv")
    key = _key(first, _first(first))
    study = _study(member_client)
    text = member_client.get(_url(study), {"series": key}, headers={"HX-Request": "true"}).text
    assert 'id="calc-a-pick"' in text and '<select class="select" id="calc-a"' not in text
    assert text.count('class="series-pick__group" data-filter-group open') == 1
    assert text.count("series-pick__note") == 2
    assert "загружена" in text
    # Та же пометка — в панели «Мои таблицы» исследования.
    page = member_client.get(reverse("userdata:study", args=[study.public_id])).text
    assert page.count("study-source__note") == 2


def test_window_opens_on_series_from_field(member_client: Client, warehouse: Any) -> None:
    """
    «Рассчитать показатель» без выбранного в панели — на первом показателе поля, а не на
    случайном ряду другой таблицы.
    """
    first = _built(member_client, "crime_wide.csv")
    other = _built(member_client)
    key = _key(other, _first(other))
    study = _study(member_client)
    member_client.post(
        reverse("userdata:study-edit", args=[study.public_id]),
        {"action": "add", "do": "view:map", "series": key},
    )
    page = member_client.get(reverse("userdata:study", args=[study.public_id])).text
    assert f"{_url(study)}?series={key.replace(':', '%3A')}" in page
    panel = member_client.get(_url(study), {"series": key}, headers={"HX-Request": "true"}).text
    assert f'value="{key}" checked' in panel
    del first
