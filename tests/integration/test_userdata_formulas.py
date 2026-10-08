"""
Показатели по формулам (этап Б): формула из рядов двух таблиц и официального ряда даёт ряд
на карте, переживает новую сборку и переименование, пересчитывается после сборки таблицы,
на которую ссылается; круг таблиц и чужая таблица не допускаются.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

import duckdb
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from apps.catalog.models import Territory
from apps.userdata import formula_store, indicators
from apps.userdata.models import Dataset, DatasetSeries
from tests.support.userdata import (
    build,
    describe,
    userdata_dir,  # noqa: F401 — приспособление модуля
    version_of,
)

pytestmark = pytest.mark.integration


def _table(client: Client, name: str, header: str, values: dict[str, list[float]]) -> Dataset:
    """Таблица «регион — годы» из названий справочника, собранная как сумма."""
    lines = [f"Регион;{header}"] + [
        f"{territory};" + ";".join(str(value) for value in row) for territory, row in values.items()
    ]
    client.post(
        reverse("userdata:upload"),
        {"action": "upload", "file": SimpleUploadedFile(name, "\n".join(lines).encode("utf-8"))},
    )
    dataset = Dataset.objects.latest("created_at")
    client.post(
        reverse("userdata:file", args=[dataset.public_id]),
        {"action": "choose", "table": "", "encoding": ""},
    )
    describe(client, dataset)
    response = build(client, dataset, kind=indicators.SUM, per=(), title=name.removesuffix(".csv"))
    assert response.status_code == 302, response.content[:300]
    dataset.refresh_from_db()
    return dataset


def _regions() -> list[Territory]:
    return list(Territory.objects.comparable().order_by("code"))


def _accidents_and_cars(client: Client) -> tuple[Dataset, Dataset]:
    regions = _regions()
    accidents = _table(
        client,
        "ДТП.csv",
        "2021;2022",
        {item.name_ru: [100 + index, 110 + index] for index, item in enumerate(regions)},
    )
    cars = _table(
        client,
        "Автомобили.csv",
        "2021;2022",
        {
            item.name_ru: [10_000 + index, 0 if index == 0 else 10_500 + index]
            for index, item in enumerate(regions)
        },
    )
    return accidents, cars


def _formula_url(dataset: Dataset, code: str = "") -> str:
    if code:
        return reverse("userdata:formula", args=[dataset.public_id, code])
    return reverse("userdata:formula-new", args=[dataset.public_id])


def _values(dataset: Dataset, key: str) -> dict[tuple[str, int], float]:
    path = version_of(dataset).data_path
    assert path is not None
    connection = duckdb.connect(str(path), read_only=True)
    try:
        rows = connection.execute(
            "SELECT territory_code, year, value FROM fact_observation "
            "WHERE series_key = ? AND value IS NOT NULL",
            [key],
        ).fetchall()
    finally:
        connection.close()
    return {(code, int(year)): float(value) for code, year, value in rows}


def _first_key(dataset: Dataset) -> str:
    record = version_of(dataset).series.order_by("order").first()
    assert record is not None
    return f"u:{dataset.code}:{record.code}"


@pytest.mark.django_db
class TestFormulas:
    """Формула из двух таблиц: проверка, сохранение, карта, пересборка."""

    def test_two_tables_on_map(self, client: Client, warehouse: Any) -> None:
        accidents, cars = _accidents_and_cars(client)
        page = client.get(_formula_url(accidents))
        assert page.status_code == 200
        # В перечне вставки — обе свои таблицы; у другой — с её названием.
        assert 'data-label="Автомобили: Автомобили"' in page.text
        data = {
            "title": "ДТП на 1 000 автомобилей",
            "expression": "[ДТП] / [Автомобили: Автомобили] * 1000",
            "unit": "на 1 000 автомобилей",
            "kind": "relative",
            "polarity": "negative",
        }
        checked = client.post(_formula_url(accidents), {**data, "action": "check"})
        assert checked.status_code == 200
        # Предпросмотр: малая карта и сколько субъектов посчитано.
        assert "Посчитано для" in checked.text
        assert "geo-map" in checked.text
        assert "Субъектов с делением на ноль: 1" in checked.text
        assert not formula_store.definitions(version_of(accidents))

        saved = client.post(_formula_url(accidents), {**data, "action": "save"})
        assert saved.status_code == 302
        # Сохранённая формула — выбранной на странице таблицы.
        location = urlparse(saved["Location"])
        assert location.path == reverse("userdata:dataset", args=[accidents.public_id])
        definition = formula_store.definitions(version_of(accidents))[0]
        assert parse_qs(location.query)["show"] == [definition.code]
        key = f"u:{accidents.code}:{definition.code}"
        assert (
            definition.expression == f"{{{_first_key(accidents)}}} / {{{_first_key(cars)}}} * 1000"
        )

        values = _values(accidents, key)
        second = _regions()[1].code
        assert values[(second, 2022)] == pytest.approx(111 / 10_501 * 1000)
        # Деление на ноль — пусто, причина — на странице таблицы.
        assert (_regions()[0].code, 2022) not in values
        assert client.get(reverse("maps:choropleth"), {"series": key}).status_code == 200
        about = client.get(saved["Location"])
        assert "ДТП на 1 000 автомобилей" in about.text
        assert "[Автомобили: Автомобили]" in about.text
        assert "Субъектов с делением на ноль: 1" in about.text
        record = version_of(accidents).series.get(code=definition.code)
        assert record.derived == DatasetSeries.Derived.FORMULA
        assert record.polarity == "negative"

        # Таблица, на которую ссылается формула, пересобрана — формула пересчитана.
        old_file = version_of(accidents).data_file
        build(client, cars, kind=indicators.SUM, per=())
        assert version_of(accidents).data_file != old_file
        assert _values(accidents, key)[(second, 2022)] == pytest.approx(111 / 10_501 * 1000)

        # Переименование показателя формулу не ломает: в ней ключи.
        build(
            client,
            cars,
            kind=indicators.SUM,
            per=(),
            extra={"title-1": "Легковые автомобили"},
        )
        edit = client.get(_formula_url(accidents, definition.code))
        assert "[Автомобили: Легковые автомобили]" in edit.text
        assert _values(accidents, key)

        # Таблица удалена — формула остаётся без значений, с причиной.
        client.post(reverse("userdata:delete", args=[cars.public_id]), {"confirm": "1"})
        assert not _values(accidents, key)
        about = client.get(saved["Location"])
        assert "Нет значений показателей" in about.text

    def test_official_series_and_functions(self, client: Client, warehouse: Any) -> None:
        accidents, _cars = _accidents_and_cars(client)
        data = {
            "title": "ДТП на 1 000 жителей, место",
            "expression": "RANK([ДТП] / [Численность населения] * 1000)",
            "unit": "место",
            "kind": "relative",
            "polarity": "neutral",
            "action": "save",
        }
        response = client.post(_formula_url(accidents), data)
        assert response.status_code == 302, response.content[:500]
        definition = formula_store.definitions(version_of(accidents))[0]
        assert "{Y477110461:00}" in definition.expression
        values = _values(accidents, f"u:{accidents.code}:{definition.code}")
        ranks = sorted(value for (code, year), value in values.items() if year == 2021)
        assert ranks[0] == 1
        assert len(ranks) >= 80

    def test_formula_on_formula_and_growth(self, client: Client, warehouse: Any) -> None:
        accidents, _cars = _accidents_and_cars(client)
        first = {
            "title": "ДТП вдвое",
            "expression": "[ДТП] * 2",
            "kind": "sum",
            "polarity": "neutral",
            "action": "save",
        }
        client.post(_formula_url(accidents), first)
        second = {
            "title": "Рост",
            "expression": "[ДТП вдвое] / AGO([ДТП вдвое]; 1) * 100",
            "kind": "relative",
            "polarity": "neutral",
            "action": "save",
        }
        assert client.post(_formula_url(accidents), second).status_code == 302
        items = formula_store.definitions(version_of(accidents))
        values = _values(accidents, f"u:{accidents.code}:{items[1].code}")
        code = _regions()[2].code
        assert values[(code, 2022)] == pytest.approx(112 / 102 * 100)
        # Первая формула не может ссылаться на вторую: та считается после неё.
        edit = client.post(
            _formula_url(accidents, items[0].code),
            {**first, "expression": "[Рост] * 2", "action": "check"},
        )
        assert "Нет показателя «Рост»" in edit.text

    def test_errors_are_explained(self, client: Client, warehouse: Any) -> None:
        accidents, _cars = _accidents_and_cars(client)
        cases = [
            ({"title": "", "expression": "[ДТП]"}, "Назовите показатель"),
            ({"title": "X", "expression": "[ДТП] / [Пешеходы]"}, "Нет показателя «Пешеходы»"),
            ({"title": "X", "expression": "[ДТП] +"}, "Формула обрывается"),
            ({"title": "X", "expression": "СУММ([ДТП])"}, "Нет функции «СУММ»"),
        ]
        for data, message in cases:
            response = client.post(_formula_url(accidents), {**data, "action": "save"})
            assert response.status_code == 200
            assert message in response.text, message
        assert not formula_store.definitions(version_of(accidents))

    def test_cycle_between_tables(self, client: Client, warehouse: Any) -> None:
        accidents, cars = _accidents_and_cars(client)
        client.post(
            _formula_url(accidents),
            {"title": "Доля", "expression": "[ДТП] / [Автомобили: Автомобили]", "action": "save"},
        )
        response = client.post(
            _formula_url(cars),
            {"title": "Назад", "expression": "[ДТП: Доля] * 2", "action": "save"},
        )
        assert "Такой круг не посчитать" in response.text

    def test_delete_formula(self, client: Client, warehouse: Any) -> None:
        accidents, _cars = _accidents_and_cars(client)
        client.post(
            _formula_url(accidents),
            {"title": "ДТП вдвое", "expression": "[ДТП] * 2", "action": "save"},
        )
        code = formula_store.definitions(version_of(accidents))[0].code
        response = client.post(_formula_url(accidents, code), {"action": "delete"})
        assert response.status_code == 302
        assert not formula_store.definitions(version_of(accidents))
        assert not version_of(accidents).series.filter(code=code).exists()

    def test_other_session_and_foreign_table(self, client: Client, warehouse: Any) -> None:
        accidents, _cars = _accidents_and_cars(client)
        stranger = Client()
        assert stranger.get(_formula_url(accidents)).status_code == 404
        # Таблица другого человека в формуле не видна.
        mine = _table(
            stranger,
            "Своё.csv",
            "2021;2022",
            {item.name_ru: [1, 2] for item in _regions()},
        )
        response = stranger.post(
            _formula_url(mine),
            {"title": "X", "expression": "[Своё] / [ДТП]", "action": "check"},
        )
        assert "Нет показателя «ДТП»" in response.text
        keyed = stranger.post(
            _formula_url(mine),
            {
                "title": "X",
                "expression": f"[Своё] / {{{_first_key(accidents)}}}",
                "action": "check",
            },
        )
        assert "Нет показателя с ключом" in keyed.text


@pytest.mark.django_db
def test_new_version_keeps_derived_keys(client: Client, warehouse: Any) -> None:
    """Новая версия файла с прежним рецептом даёт те же ключи пересчётам и формулам."""
    import shutil

    from apps.userdata import jobs
    from apps.userdata.models import DatasetVersion

    accidents, _cars = _accidents_and_cars(client)
    build(
        client,
        accidents,
        kind=indicators.SUM,
        per=("per100000",),
        title="ДТП",
        extra={"recalc-1": ["share", "growth"]},
    )
    client.post(
        _formula_url(accidents),
        {"title": "Доля ДТП", "expression": "[ДТП] / [Автомобили: Автомобили]", "action": "save"},
    )
    first = version_of(accidents)
    keys = {f"u:{accidents.code}:{record.code}" for record in first.series.all()}
    assert len(keys) == 5

    second = DatasetVersion.objects.create(
        dataset=accidents,
        number=2,
        file_name=first.file_name,
        file_size=first.file_size,
        sha256=first.sha256,
        file_kind=first.file_kind,
        recipe=first.recipe,
    )
    shutil.copytree(first.directory, second.directory, dirs_exist_ok=True)
    for path in second.directory.glob("data-*.duckdb"):
        path.unlink()
    accidents.current_version = second
    accidents.save(update_fields=["current_version"])
    jobs.run(second, jobs.EXTRACT)
    jobs.run(second, jobs.BUILD)
    second.refresh_from_db()
    assert second.state == DatasetVersion.State.BUILT
    renewed = {f"u:{accidents.code}:{record.code}": record for record in second.series.all()}
    assert set(renewed) == keys
    assert all(record.values_count for record in renewed.values())
