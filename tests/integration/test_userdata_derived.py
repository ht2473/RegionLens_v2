"""
Пересчёты и свёртки рядов таблицы (этап Б): в ценах последнего года, Россия = 100, темп
к прошлому году, доля в сумме, сумма по разрезу, месяцы в год; коды при пересборке.

Склад — синтетический (индекс цен и численность — его ряды); таблицы — усечённые настоящие
файлы ЕБТ и таблицы, составленные в проверке из названий справочника.
"""

from __future__ import annotations

import math
from typing import Any

import duckdb
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from apps.catalog.models import Territory
from apps.userdata import build as building
from apps.userdata import indicators, jobs
from apps.userdata.models import Dataset, DatasetSeries
from apps.warehouse.queries import series_values
from tests.support.synthetic import CPI_CODE
from tests.support.userdata import (
    build,
    describe,
    key_of,
    upload,
    userdata_dir,  # noqa: F401 — приспособление модуля
    version_of,
)

pytestmark = pytest.mark.integration


def _rows(dataset: Dataset, key: str) -> dict[tuple[str, int], tuple[float, int]]:
    """Значения и признаки ряда из файла таблицы: (территория, год) → (значение, признак)."""
    path = version_of(dataset).data_path
    assert path is not None
    connection = duckdb.connect(str(path), read_only=True)
    try:
        rows = connection.execute(
            "SELECT territory_code, year, value, flags FROM fact_observation "
            "WHERE series_key = ? AND value IS NOT NULL",
            [key],
        ).fetchall()
    finally:
        connection.close()
    return {(code, int(year)): (float(value), int(flags)) for code, year, value, flags in rows}


def _records(dataset: Dataset) -> dict[tuple[str, str], DatasetSeries]:
    """Ряды таблицы по (код исходного ряда или свой код, пересчёт)."""
    return {
        (record.base_code or record.code, record.derived): record
        for record in version_of(dataset).series.all()
    }


def _upload_text(client: Client, name: str, lines: list[str]) -> Dataset:
    client.post(
        reverse("userdata:upload"),
        {"action": "upload", "file": SimpleUploadedFile(name, "\n".join(lines).encode("utf-8"))},
    )
    dataset = Dataset.objects.latest("created_at")
    client.post(
        reverse("userdata:file", args=[dataset.public_id]),
        {"action": "choose", "table": "", "encoding": ""},
    )
    return dataset


def _regions() -> list[Territory]:
    return list(Territory.objects.comparable().order_by("code"))


@pytest.mark.django_db
class TestRecalculations:
    """Пересчёты — отдельные ряды рядом с исходным, с пометкой «рассчитано»."""

    def test_russia_and_growth(self, client: Client, warehouse: Any) -> None:
        dataset = upload(client, "environment.csv")
        describe(client, dataset)
        response = build(
            client,
            dataset,
            kind=indicators.RELATIVE,
            extra={"recalc-1": ["russia", "growth"]},
        )
        assert response.status_code == 302
        records = _records(dataset)
        base = next(record for record in records.values() if not record.derived)
        values = {
            code_year: value
            for code_year, (value, _flags) in _rows(dataset, key_of(dataset, base)).items()
        }
        russia = _rows(dataset, key_of(dataset, records[(base.code, "russia")]))
        growth = _rows(dataset, key_of(dataset, records[(base.code, "growth")]))
        assert russia and growth
        for (code, year), (value, flags) in russia.items():
            assert value == pytest.approx(values[(code, year)] / values[("RU", year)] * 100)
            assert flags & 2
        assert russia[("RU", 2020)][0] == pytest.approx(100)
        for (code, year), (value, _flags) in growth.items():
            assert value == pytest.approx(values[(code, year)] / values[(code, year - 1)] * 100)
        assert all(year > 2014 for _code, year in growth)
        assert records[(base.code, "russia")].unit == "Россия = 100"
        assert records[(base.code, "growth")].title.endswith("% к предыдущему году")

    def test_share_and_russia_for_sums(self, client: Client, warehouse: Any) -> None:
        dataset = upload(client, "crime_wide.csv")
        describe(client, dataset)
        build(
            client,
            dataset,
            kind=indicators.SUM,
            per=(),
            extra={"recalc-1": ["share", "russia"]},
        )
        records = _records(dataset)
        base = next(record for record in records.values() if not record.derived)
        share = _rows(dataset, key_of(dataset, records[(base.code, "share")]))
        regions = {territory.code for territory in _regions()}
        by_year: dict[int, float] = {}
        for (code, year), (value, _flags) in share.items():
            if code in regions:
                by_year[year] = by_year.get(year, 0) + value
        assert by_year
        assert all(total == pytest.approx(100) for total in by_year.values())
        russia = _rows(dataset, key_of(dataset, records[(base.code, "russia")]))
        assert russia[("RU", 2020)][0] == pytest.approx(100)

    def test_russia_without_country_row(self, client: Client, warehouse: Any) -> None:
        lines = [
            line
            for line in (building.settings.BASE_DIR / "tests/fixtures/userdata/crime_wide.csv")
            .read_text(encoding="utf-8")
            .splitlines()
            if "Российская Федерация" not in line
        ]
        dataset = _upload_text(client, "crime.csv", lines)
        describe(client, dataset)
        build(client, dataset, kind=indicators.SUM, per=(), extra={"recalc-1": ["russia"]})
        records = _records(dataset)
        base = next(record for record in records.values() if not record.derived)
        values = _rows(dataset, key_of(dataset, base))
        russia = _rows(dataset, key_of(dataset, records[(base.code, "russia")]))
        regions = {territory.code for territory in _regions()}
        total = sum(
            value
            for (code, year), (value, _f) in values.items()
            if year == 2020 and code in regions
        )
        assert russia[("RU-TA", 2020)][0] == pytest.approx(values[("RU-TA", 2020)][0] / total * 100)
        # У относительной величины без строки России считать не от чего.
        build(client, dataset, kind=indicators.RELATIVE, extra={"recalc-1": ["russia"]})
        records = _records(dataset)
        base = next(record for record in records.values() if not record.derived)
        assert records[(base.code, "russia")].values_count == 0

    def test_real_terms(self, client: Client, warehouse: Any) -> None:
        regions = _regions()
        lines = ["Регион;2020;2021;2022"] + [
            f"{territory.name_ru};{1000 + index};{1100 + index};{1200 + index}"
            for index, territory in enumerate(regions)
        ]
        dataset = _upload_text(client, "зарплата.csv", lines)
        describe(client, dataset)
        response = build(
            client,
            dataset,
            kind=indicators.RELATIVE,
            extra={"unit-1": "рублей", "recalc-1": ["real"]},
        )
        assert response.status_code == 302
        records = _records(dataset)
        base = next(record for record in records.values() if not record.derived)
        real = records[(base.code, "real")]
        assert real.title.endswith("в ценах 2022 года")
        values = _rows(dataset, key_of(dataset, base))
        rows = _rows(dataset, key_of(dataset, real))
        cpi = series_values([f"{CPI_CODE}:00"], ["RU-TA"])[f"{CPI_CODE}:00"]["RU-TA"]
        expected = values[("RU-TA", 2020)][0] * cpi[2021] / 100 * cpi[2022] / 100
        assert rows[("RU-TA", 2020)][0] == pytest.approx(expected)
        assert rows[("RU-TA", 2022)][0] == pytest.approx(values[("RU-TA", 2022)][0])
        assert math.isclose(len(rows) / len(values), 1, rel_tol=0.1)

    def test_real_terms_only_for_money(self, client: Client, warehouse: Any) -> None:
        dataset = upload(client, "environment.csv")
        describe(client, dataset)
        build(client, dataset, extra={"recalc-1": ["real"]})
        assert not version_of(dataset).series.filter(derived="real").exists()
        page = client.get(reverse("userdata:series", args=[dataset.public_id]))
        assert 'value="real"' not in page.text


@pytest.mark.django_db
class TestFolds:
    """Свёртки: сумма по разрезу без итога и месяцы в год."""

    def test_slice_sum(self, client: Client, warehouse: Any) -> None:
        dataset = upload(client, "despair.csv")
        describe(client, dataset)
        page = client.get(reverse("userdata:series", args=[dataset.public_id]))
        assert 'name="fold-1"' in page.text
        build(
            client,
            dataset,
            kind=indicators.SUM,
            per=(),
            extra={"fold-1": ["settlement_type", "gender"]},
        )
        version = version_of(dataset)
        folds = list(version.series.filter(derived=DatasetSeries.Derived.SLICE_SUM))
        assert folds
        records = list(version.series.filter(derived=""))

        def find(**wanted: str) -> DatasetSeries:
            return next(
                record
                for record in records
                if all(dict(record.slices).get(header) == value for header, value in wanted.items())
            )

        reason = "Случайное отравление (воздействие) алкоголем"
        fold = next(
            record
            for record in folds
            if dict(record.slices).get("gender") == "M"
            and dict(record.slices).get("reason_name") == reason
            and dict(record.slices).get("age") == "Всего"
            and "settlement_type" in dict(record.slices).get("settlement_type", "settlement_type")
        )
        urban = _rows(
            dataset,
            key_of(dataset, find(age="Всего", settlement_type="U", gender="M", reason_name=reason)),
        )
        rural = _rows(
            dataset,
            key_of(dataset, find(age="Всего", settlement_type="R", gender="M", reason_name=reason)),
        )
        summed = _rows(dataset, key_of(dataset, fold))
        assert summed[("RU", 2020)][0] == pytest.approx(
            urban[("RU", 2020)][0] + rural[("RU", 2020)][0]
        )
        assert summed[("RU", 2020)][1] & 2
        assert fold.code.endswith("-sum")
        assert "сумма по разрезу «settlement_type»" in fold.slices[-1][1]

    def test_month_fold(self, client: Client, warehouse: Any) -> None:
        months = (
            "январь",
            "февраль",
            "март",
            "апрель",
            "май",
            "июнь",
            "июль",
            "август",
            "сентябрь",
            "октябрь",
            "ноябрь",
            "декабрь",
        )
        header = [f"{month} 2024" for month in months] + [f"{month} 2025" for month in months[:7]]
        regions = _regions()
        lines = ["Регион;" + ";".join(header)] + [
            f"{territory.name_ru};" + ";".join(str(index + column) for column in range(len(header)))
            for index, territory in enumerate(regions, start=1)
        ]
        dataset = _upload_text(client, "месяцы.csv", lines)
        describe(client, dataset)
        page = client.get(reverse("userdata:series", args=[dataset.public_id]))
        assert 'name="months-1"' in page.text
        build(client, dataset, kind=indicators.SUM, per=(), extra={"months-1": "sum"})
        version = version_of(dataset)
        fold = version.series.get(derived=DatasetSeries.Derived.MONTHS)
        assert fold.period == "year:12"
        assert fold.title.endswith("за год (сумма месяцев)")
        rows = _rows(dataset, key_of(dataset, fold))
        first = regions[0].code
        assert rows[(first, 2024)][0] == pytest.approx(sum(1 + column for column in range(12)))
        # Год без всех месяцев остаётся пустым.
        assert (first, 2025) not in rows

        # У относительной величины месяцы не складываются: только среднее.
        build(client, dataset, kind=indicators.RELATIVE, extra={"months-1": "sum"})
        assert not version_of(dataset).series.filter(derived=DatasetSeries.Derived.MONTHS).exists()
        build(client, dataset, kind=indicators.RELATIVE, extra={"months-1": "mean"})
        fold = version_of(dataset).series.get(derived=DatasetSeries.Derived.MONTHS)
        rows = _rows(dataset, key_of(dataset, fold))
        assert rows[(first, 2024)][0] == pytest.approx(sum(1 + column for column in range(12)) / 12)


@pytest.mark.django_db
class TestStability:
    """Коды пересчётов и свёрток не меняются при новой сборке; предел пересчётов."""

    def test_codes_survive_rebuild(self, client: Client, warehouse: Any) -> None:
        dataset = upload(client, "despair.csv")
        describe(client, dataset)
        options = {"recalc-1": ["share", "russia"], "fold-1": ["gender"]}
        build(client, dataset, kind=indicators.SUM, extra=options)
        first = {(record.code, record.derived) for record in version_of(dataset).series.all()}
        build(client, dataset, kind=indicators.SUM, extra=options)
        assert {
            (record.code, record.derived) for record in version_of(dataset).series.all()
        } == first

    def test_too_many_derived(
        self, client: Client, warehouse: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(building, "MAX_DERIVED", 3)
        dataset = upload(client, "despair.csv")
        describe(client, dataset)
        build(client, dataset, kind=indicators.SUM, extra={"recalc-1": ["share", "russia"]})
        version = version_of(dataset)
        assert jobs.stage_state(version, jobs.BUILD) == jobs.FAILED
        page = client.get(reverse("userdata:series", args=[dataset.public_id]))
        assert "Пересчётов и свёрток получается" in page.text


@pytest.mark.django_db
class TestMonths:
    """Вид «По месяцам»: слой месяцев файла таблицы, график и карта последнего периода."""

    def test_months_view(self, client: Client, warehouse: Any) -> None:
        months = (
            "январь",
            "февраль",
            "март",
            "апрель",
            "май",
            "июнь",
            "июль",
            "август",
            "сентябрь",
            "октябрь",
            "ноябрь",
            "декабрь",
        )
        header = [f"{month} 2024" for month in months] + [f"{month} 2025" for month in months[:7]]
        regions = _regions()
        lines = ["Регион;" + ";".join(header)] + [
            f"{territory.name_ru};" + ";".join(str(index + column) for column in range(len(header)))
            for index, territory in enumerate(regions, start=1)
        ]
        dataset = _upload_text(client, "месяцы.csv", lines)
        describe(client, dataset)
        build(client, dataset, kind=indicators.SUM, per=())
        about = client.get(reverse("userdata:dataset", args=[dataset.public_id]))
        url = reverse("userdata:months", args=[dataset.public_id])
        assert url in about.text
        page = client.get(url)
        assert page.status_code == 200
        group = page.context["group"]
        assert group.key.startswith(f"u:{dataset.code}:")
        # России в таблице нет: по умолчанию — субъект со значениями.
        assert page.context["territory"] in {territory.code for territory in regions}
        assert page.context["chart"]["years"] == [2024, 2025]
        assert page.context["map"]["period"] == "июль 2025"
        assert page.context["current"]["period"] == "июль 2025"
        assert "к тому же периоду прошлого года" in page.context["current"]["change"]
        chosen = client.get(url, {"territory": regions[0].code, "group": group.code})
        assert chosen.context["current"]["value"] == pytest.approx(1 + 18)
        # Чужой сеанс — 404; таблица без месяцев — 404.
        assert Client().get(url).status_code == 404
        yearly = upload(client, "environment.csv")
        describe(client, yearly)
        build(client, yearly)
        assert client.get(reverse("userdata:months", args=[yearly.public_id])).status_code == 404


@pytest.mark.django_db
def test_oblast_already_without_okrugs(client: Client, warehouse: Any) -> None:
    """
    «Тюменская область» в таблице МВД — уже без округов: итог меньше суммы округов,
    вычитать нельзя; отрицательной области без округов нет, причина названа.
    """
    lines = (building.settings.BASE_DIR / "tests/fixtures/userdata/crime_wide.csv").read_text(
        encoding="utf-8"
    )
    tyumen = [line for line in lines.splitlines() if ";Тюменская область;" in line]
    khanty = [line for line in lines.splitlines() if "Ханты-Мансийский" in line]
    assert tyumen and khanty
    dataset = upload(client, "crime_wide.csv")
    describe(client, dataset)
    build(client, dataset, kind=indicators.SUM, per=())
    version = version_of(dataset)
    base = version.series.filter(derived="").order_by("order").first()
    assert base is not None
    rows = _rows(dataset, key_of(dataset, base))
    assert all(value >= 0 for value, _flags in rows.values())
    assert "RU-TYU" in version.report["build"]["alone_conflict"]
    page = client.get(reverse("userdata:dataset", args=[dataset.public_id]), {"tab": "checks"})
    assert "Область, похоже, уже без автономных округов" in page.text


@pytest.mark.django_db
def test_slices_reselected_after_build(client: Client, warehouse: Any) -> None:
    """Отбор разрезов меняется и после сборки: со страницы таблицы — на шаг «Что в таблице»."""
    import re

    dataset = upload(client, "despair.csv")
    describe(client, dataset)
    build(client, dataset, kind=indicators.SUM, per=())
    before = version_of(dataset).series_count
    about = client.get(reverse("userdata:dataset", args=[dataset.public_id]))
    table_url = reverse("userdata:table", args=[dataset.public_id])
    assert table_url in about.text
    page = client.get(table_url).text
    chosen: dict[str, list[str]] = {}
    for name, value in re.findall(r'name="(slice-\d+)" value="([^"]*)" checked', page):
        chosen.setdefault(name, []).append(value)
    # Оставить один пол: рядов становится меньше.
    gender = next(name for name, values in chosen.items() if set(values) >= {"M", "F"})
    chosen[gender] = ["M"]
    response = client.post(table_url, {"action": "next", "slices-present": "1", **chosen})
    assert response.status_code == 302
    build(client, dataset, kind=indicators.SUM, per=())
    after = version_of(dataset)
    assert after.state == "built"
    assert after.series_count < before
    assert all(dict(record.slices).get("gender") == "M" for record in after.series.all())
