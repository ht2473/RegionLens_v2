"""
Свои данные в инструментах анализа (этап Б): ряды таблиц в перечнях и расчётах, чужая
таблица — 404, смена методики, отмеченная человеком, малое число субъектов, суммы.

Склад — синтетический; таблицы — усечённые настоящие файлы ЕБТ.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from apps.userdata import indicators
from apps.userdata.models import Dataset
from tests.support.userdata import (
    build,
    built,
    describe,
    first_record,
    key_of,
    upload,
    userdata_dir,  # noqa: F401 — приспособление модуля
    version_of,
)

pytestmark = pytest.mark.integration

SINGLE_TOOLS = ("analytics:inequality", "analytics:convergence", "analytics:spatial")
MULTI_TOOLS = ("analytics:correlation", "analytics:index-builder")


def _warehouse_key(warehouse: Any) -> str:
    from apps.catalog.models import Series

    series = Series.objects.filter(is_analysis_ready=True).order_by("key").first()
    assert series is not None
    return series.key


@pytest.mark.django_db
class TestTools:
    """Инструменты считают ряды своих таблиц так же, как ряды сайта."""

    def test_single_series_tools(self, client: Client, warehouse: Any) -> None:
        dataset = built(client)
        key = key_of(dataset, first_record(dataset))
        for name in SINGLE_TOOLS:
            page = client.get(reverse(name), {"series": key})
            assert page.status_code == 200, name
            assert page.context["series"].key == key, name
            # Свои таблицы — в перечне выбора, выбранный ряд отмечен.
            assert "data-own" in page.text, name
            assert f'value="{key}"' in page.text, name
        inequality = client.get(reverse("analytics:inequality"), {"series": key})
        assert inequality.context["snapshot"]["available"]
        assert inequality.context["snapshot"]["count"] >= 80

    def test_mixed_series_tools(self, client: Client, warehouse: Any) -> None:
        dataset = built(client)
        own = key_of(dataset, first_record(dataset))
        official = _warehouse_key(warehouse)
        for name in MULTI_TOOLS:
            page = client.get(reverse(name), {"series": [own, official]})
            assert page.status_code == 200, name
            # В индекс ряд без направленности не входит, но остаётся в форме.
            assert page.context["selected_keys"][:2] == [own, official], name
            # Отмеченный ряд таблицы остаётся в свёрнутом перечне.
            assert f'value="{own}"' in page.text, name
        correlation = client.get(reverse("analytics:correlation"), {"series": [own, official]})
        assert correlation.context["matrix"]["size"] == 2
        assert f"/own-data/{dataset.public_id}/#series-" in correlation.text
        # Направленность из описания таблицы: ряд входит в индекс.
        index = client.get(
            reverse("analytics:index-builder"),
            {"series": [own, official], "direction": [f"{own}:negative", f"{official}:positive"]},
        )
        assert [item.key for item in index.context["selected"]] == [own, official]
        assert index.context["scores"]

    def test_picker_lists_own_tables(self, client: Client, warehouse: Any) -> None:
        dataset = built(client)
        page = client.get(reverse("catalog:series-picker"), {"picker": "1"})
        assert f"u:{dataset.code}:" in page.text
        assert "Мои таблицы" in page.text

    def test_other_session_gets_404(self, client: Client, warehouse: Any) -> None:
        dataset = built(client)
        key = key_of(dataset, first_record(dataset))
        stranger = Client()
        for name in SINGLE_TOOLS:
            assert stranger.get(reverse(name), {"series": key}).status_code == 404, name
        for name in MULTI_TOOLS:
            response = stranger.get(reverse(name), {"series": [key, _warehouse_key(warehouse)]})
            assert response.status_code == 404, name


@pytest.mark.django_db
class TestBreaks:
    """Смена методики, отмеченная у показателя: динамика и инструменты её учитывают."""

    def test_break_reaches_tools_and_canvas(self, client: Client, warehouse: Any) -> None:
        dataset = upload(client, "environment.csv")
        describe(client, dataset)
        response = build(
            client,
            dataset,
            extra={"breaks-1": "2019, 1066", "break-note-1": "Другой порог загрязнения"},
        )
        assert response.status_code == 302
        version = version_of(dataset)
        name = indicators.indicators_of(version)[0].name
        assert indicators.breaks_of(version.recipe, name) == [
            {"year": 2019, "note": "Другой порог загрязнения"}
        ]
        key = key_of(dataset, first_record(dataset))

        page = client.get(
            reverse("analytics:inequality"), {"series": key, "first": "2014", "last": "2022"}
        )
        assert [item["year"] for item in page.context["methodological_breaks"]] == [2019]
        assert page.context["comparable_from"] == 2019

        dynamics = client.get(reverse("compare:index"), {"series": key})
        assert [item["year"] for item in dynamics.context["timeline_breaks"]] == [2019]

        about = client.get(reverse("userdata:dataset", args=[dataset.public_id]))
        assert "Смена методики" in about.text
        assert "Другой порог загрязнения" in about.text

        # Описание открывается с отмеченными годами.
        step = client.get(reverse("userdata:series", args=[dataset.public_id]))
        assert 'value="2019"' in step.text

    def test_years_are_parsed_strictly(self) -> None:
        assert indicators.parse_years("2019, 2022; 2019 и 1800, 20 г.") == [2019, 2022]
        assert indicators.parse_years("") == []


@pytest.mark.django_db
class TestLimits:
    """Малое число субъектов и суммы."""

    def test_few_regions(self, client: Client, warehouse: Any) -> None:
        rows = ["Регион;2021;2022;2023"] + [
            f"{name};{index};{index + 1};{index + 2}"
            for index, name in enumerate(
                ("Татарстан", "Башкортостан", "Москва", "Санкт-Петербург", "Пермский край"),
                start=10,
            )
        ]
        client.post(
            reverse("userdata:upload"),
            {
                "action": "upload",
                "file": SimpleUploadedFile("мало.csv", "\n".join(rows).encode("utf-8")),
            },
        )
        dataset = Dataset.objects.latest("created_at")
        client.post(
            reverse("userdata:file", args=[dataset.public_id]),
            {"action": "choose", "table": "", "encoding": ""},
        )
        describe(client, dataset)
        assert build(client, dataset, kind=indicators.RELATIVE).status_code == 302
        key = key_of(dataset, first_record(dataset))
        for name in SINGLE_TOOLS:
            page = client.get(reverse(name), {"series": key})
            assert page.status_code == 200, name
            assert page.context["too_few"], name
            assert "В таблице меньше 20 субъектов" in page.text, name

    def test_sum_offers_per_capita(self, client: Client, warehouse: Any) -> None:
        dataset = built(client, kind=indicators.SUM, per=("per100000",))
        base = first_record(dataset)
        key = key_of(dataset, base)
        page = client.get(reverse("analytics:inequality"), {"series": key})
        per_capita = page.context["per_capita"]
        assert per_capita is not None
        assert per_capita.record.base_code == base.code
        assert "Считать на жителя" in page.text

    def test_sum_without_per_capita_links_to_description(
        self, client: Client, warehouse: Any
    ) -> None:
        dataset = built(client, kind=indicators.SUM, per=())
        key = key_of(dataset, first_record(dataset))
        page = client.get(reverse("analytics:inequality"), {"series": key})
        assert page.context["per_capita"] is None
        assert reverse("userdata:series", args=[dataset.public_id]) in page.text


@pytest.mark.django_db
class TestGlance:
    """Первый взгляд: вид величины по данным, охват и состав, что открыть первым."""

    def _shares(self, client: Client, kind: str) -> Dataset:
        """Доля по регионам 10–94 % за два года и Россия — 52 %: относительная величина."""
        from apps.catalog.models import Territory

        regions = list(Territory.objects.comparable().order_by("code"))
        lines = ["Регион;2022;2023", "Российская Федерация;52;53"] + [
            f"{territory.name_ru};{10 + index};{11 + index}"
            for index, territory in enumerate(regions)
        ]
        client.post(
            reverse("userdata:upload"),
            {
                "action": "upload",
                "file": SimpleUploadedFile("доли.csv", "\n".join(lines).encode("utf-8")),
            },
        )
        dataset = Dataset.objects.latest("created_at")
        client.post(
            reverse("userdata:file", args=[dataset.public_id]),
            {"action": "choose", "table": "", "encoding": ""},
        )
        describe(client, dataset)
        assert build(client, dataset, kind=kind, per=()).status_code == 302
        return dataset

    def test_relative_described_as_sum(self, client: Client, warehouse: Any) -> None:
        dataset = self._shares(client, indicators.SUM)
        page = client.get(reverse("userdata:dataset", args=[dataset.public_id]))
        look = page.context["glance"]["looks"][0]
        assert look.data_kind == "relative"
        assert look.regions >= 80
        assert look.gini is not None and look.moran is not None
        assert "похоже на долю или среднее" in page.text
        assert "Первый взгляд" in page.text

    def test_inconsistent_country_gives_no_verdict(self, client: Client, warehouse: Any) -> None:
        # «Окружающая среда»: Россия — 49, сумма регионов — десятки тысяч: ни сумма, ни доля.
        dataset = built(client, kind=indicators.SUM, per=())
        page = client.get(reverse("userdata:dataset", args=[dataset.public_id]))
        look = page.context["glance"]["looks"][0]
        assert look.country is not None
        assert look.data_kind == ""
        assert not look.kind_note

    def test_sum_described_as_relative(self, client: Client, warehouse: Any) -> None:
        dataset = upload(client, "crime_wide.csv")
        describe(client, dataset)
        build(client, dataset, kind=indicators.RELATIVE)
        page = client.get(reverse("userdata:dataset", args=[dataset.public_id]))
        looks = page.context["glance"]["looks"]
        assert len(looks) == 2
        assert all(look.data_kind == "sum" for look in looks)
        assert "величина складывается по регионам" in page.text
        assert 0.85 < looks[0].subjects_sum / looks[0].country < 1
        # Два показателя — облако точек.
        titles = [str(item["title"]) for item in page.context["glance"]["open_as"]]
        assert "Облако точек двух показателей" in titles
        assert "Динамика" in titles

    def test_no_note_when_description_agrees(self, client: Client, warehouse: Any) -> None:
        dataset = self._shares(client, indicators.RELATIVE)
        page = client.get(reverse("userdata:dataset", args=[dataset.public_id]))
        look = page.context["glance"]["looks"][0]
        assert look.data_kind == "relative"
        assert not look.kind_note


@pytest.mark.django_db
class TestRelated:
    """«С чем связан показатель»: связи с основным набором с поправкой на множественность."""

    def test_related(self, client: Client, warehouse: Any) -> None:
        dataset = built(client)
        record = first_record(dataset)
        about = client.get(reverse("userdata:dataset", args=[dataset.public_id]))
        url = reverse("userdata:related", args=[dataset.public_id])
        assert url in about.text
        page = client.get(url, {"series": key_of(dataset, record)})
        assert page.status_code == 200
        links = page.context["links"]
        assert links["year"] == 2022
        found = links["significant"] + links["other"]
        assert found
        assert all(link.result.p_adjusted is not None for link in found)
        assert all(link.result.p_adjusted >= link.result.p_value for link in found)
        coefficients = [abs(link.result.coefficient) for link in links["significant"]]
        assert coefficients == sorted(coefficients, reverse=True)
        assert "analytics/correlation/" in page.text
        assert Client().get(url).status_code == 404

    def test_sum_points_to_per_capita(self, client: Client, warehouse: Any) -> None:
        dataset = built(client, kind=indicators.SUM, per=("per100000",))
        page = client.get(
            reverse("userdata:related", args=[dataset.public_id]),
            {"series": key_of(dataset, first_record(dataset))},
        )
        assert "Смотреть ряд на жителя" in page.text


ALL_TOOLS = (
    "analytics:inequality",
    "analytics:convergence",
    "analytics:spatial",
    "analytics:correlation",
    "analytics:index-builder",
)


@pytest.mark.django_db
class TestReadiness:
    """Условие этапа Б: на «Преступности» и таблице-примере работают все инструменты."""

    def _check(self, client: Client, dataset: Dataset) -> None:
        records = list(version_of(dataset).series.filter(values_count__gt=0).order_by("order"))
        keys = [key_of(dataset, record) for record in records][:3]
        for name in ALL_TOOLS:
            query: dict[str, Any] = {
                "series": keys if "correlation" in name or "index" in name else keys[0]
            }
            page = client.get(reverse(name), query)
            assert page.status_code == 200, name
            assert not page.context.get("too_few"), name
        inequality = client.get(reverse("analytics:inequality"), {"series": keys[0]})
        assert inequality.context["snapshot"]["available"]
        spatial = client.get(reverse("analytics:spatial"), {"series": keys[0]})
        assert spatial.context["global_moran"] is not None
        convergence = client.get(reverse("analytics:convergence"), {"series": keys[0]})
        assert convergence.context["sigma"]

    def test_crime(self, client: Client, warehouse: Any) -> None:
        dataset = upload(client, "crime_wide.csv")
        describe(client, dataset)
        build(client, dataset, kind=indicators.SUM, per=("per100000",))
        self._check(client, dataset)
        correlation = client.get(
            reverse("analytics:correlation"),
            {
                "series": [key_of(dataset, record) for record in version_of(dataset).series.all()][
                    :3
                ]
            },
        )
        assert correlation.context["matrix"]["size"] == 3

    def test_example(self, client: Client, warehouse: Any) -> None:
        client.post(reverse("userdata:example"))
        dataset = Dataset.objects.get()
        self._check(client, dataset)
