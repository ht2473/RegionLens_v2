"""
Подготовка своей таблицы (фаза 5): скрыть показатель, сменить масштаб, влить дубль в другой
показатель, исключить регион и год из расчётов — с пометкой в выгрузке.

Таблицы — усечённый файл ЕБТ о преступлениях: два показателя по годам в столбцах.
"""

from __future__ import annotations

import csv
import io
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from apps.userdata import scope
from apps.userdata.models import Dataset
from apps.warehouse import queries
from tests.support.userdata import (
    FIXTURES,
    build,
    describe,
    upload,
    userdata_dir,  # noqa: F401 — приспособление модуля
)
from tests.support.userdata import key_of as _key
from tests.support.userdata import version_of as _version

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

TOTAL = "Всего зарегистрировано преступлений"
PEOPLE = "Выявлено лиц, совершивших преступления"
RENAMED = "Зарегистрировано преступлений всего"


def _built(client: Client, content: bytes | None = None, **extra: Any) -> Dataset:
    """Таблица о преступлениях с описанием по умолчанию и полями ``extra`` шага «Показатели»."""
    dataset = upload(client, "crime_wide.csv", content)
    describe(client, dataset)
    assert build(client, dataset, per=(), extra=extra).status_code == 302
    dataset.refresh_from_db()
    return dataset


def _record(dataset: Dataset, title: str) -> Any:
    return _version(dataset).series.filter(derived="", title=title).first()


def _panel(dataset: Dataset, key: str) -> dict[int, dict[str, float]]:
    with scope.reading_as(fingerprint=dataset.guest_key):
        return queries.region_panel(key)


def _renamed_halves() -> bytes:
    """
    Тот же показатель под двумя названиями: до 2016 года — «Всего зарегистрировано…»,
    с 2017-го — переименован. Разреза нет, годы в столбцах.
    """
    source = (FIXTURES / "crime_wide.csv").read_text(encoding="utf-8")
    rows = list(csv.reader(io.StringIO(source), delimiter=";"))
    header = rows[0]
    years = {name: index for index, name in enumerate(header) if name.startswith("year")}
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";", lineterminator="\n")
    writer.writerow(header)
    for row in rows[1:]:
        if row[1] != TOTAL:
            continue
        early, late = list(row), list(row)
        late[1], late[2] = RENAMED, "Q112000099"
        for name, index in years.items():
            if int(name.removeprefix("year")) >= 2017:
                early[index] = ""
            else:
                late[index] = ""
        writer.writerow(early)
        writer.writerow(late)
    return out.getvalue().encode("utf-8")


class TestIndicators:
    """Скрыть, масштаб и влитый дубль — ответы шага «Показатели», применяются при сборке."""

    def test_hidden_indicator_is_not_built(self, client: Client, warehouse: Any) -> None:
        dataset = _built(client, **{"hidden-2": "1"})
        titles = set(_version(dataset).series.values_list("title", flat=True))
        assert TOTAL in titles and PEOPLE not in titles
        page = client.get(reverse("userdata:series", args=[dataset.public_id]))
        assert "скрыт" in page.text

    def test_scale_divides_values_and_moves_the_unit(self, client: Client, warehouse: Any) -> None:
        plain = _built(client)
        scaled = _built(client, **{"scale-1": "-1"})
        before, after = _record(plain, TOTAL), _record(scaled, TOTAL)
        assert after.unit == "тыс. единиц"
        old = _panel(plain, _key(plain, before))
        new = _panel(scaled, _key(scaled, after))
        year = max(old)
        region = next(iter(old[year]))
        assert new[year][region] == pytest.approx(old[year][region] / 1000)

    def test_duplicate_is_merged_into_the_main_indicator(
        self, client: Client, warehouse: Any
    ) -> None:
        """Переименованный показатель дополняет годы основного: один ряд на все годы."""
        separate = _built(client, _renamed_halves())
        assert _record(separate, RENAMED) is not None
        merged = _built(client, _renamed_halves(), **{"merge-2": TOTAL})
        assert _record(merged, RENAMED) is None
        record = _record(merged, TOTAL)
        assert (record.first_year, record.last_year) == (2011, 2022)
        page = client.get(reverse("userdata:series", args=[merged.public_id]))
        assert f"влит в «{TOTAL}»" in page.text

    def test_merge_without_chains_or_into_itself(self, client: Client, warehouse: Any) -> None:
        dataset = _built(client, _renamed_halves(), **{"merge-1": RENAMED, "merge-2": TOTAL})
        answers = _version(dataset).recipe["indicators"]
        # Влиты друг в друга — остаётся одно вливание: в того, кто сам никуда не влит.
        merged = {name: answer["merge"] for name, answer in answers.items() if answer["merge"]}
        assert len(merged) == 1
        assert all(answers[target]["merge"] == "" for target in merged.values())


class TestExclusions:
    """Регион и год вне расчётов: нет в рядах, есть в выгрузке с пометкой."""

    def test_excluded_region_and_year(self, client: Client, warehouse: Any) -> None:
        dataset = _built(client)
        record = _record(dataset, TOTAL)
        key = _key(dataset, record)
        assert "RU-MOW" in _panel(dataset, key)[2020]
        response = client.post(
            reverse("userdata:exclusions", args=[dataset.public_id]),
            {"territory": ["RU-MOW", "XX-NOPE"], "year": ["2020", "1066"]},
        )
        assert response.status_code == 302
        dataset.refresh_from_db()
        version = _version(dataset)
        assert version.recipe["exclusions"] == {"territories": ["RU-MOW"], "years": [2020]}
        record = _record(dataset, TOTAL)
        panel = _panel(dataset, _key(dataset, record))
        assert 2020 not in panel
        assert all("RU-MOW" not in values for values in panel.values())
        assert version.report["build"]["excluded"] > 0
        page = client.get(reverse("userdata:dataset", args=[dataset.public_id]))
        assert "Значений вне расчётов" in page.text

        download = client.get(reverse("userdata:download", args=[dataset.public_id, "csv"]))
        text = b"".join(download.streaming_content).decode("utf-8-sig")
        marked = [line for line in text.splitlines() if "исключено из расчётов" in line]
        assert any("RU-MOW" in line for line in marked)
        assert any(",2020," in line for line in marked)

    def test_clearing_returns_the_values(self, client: Client, warehouse: Any) -> None:
        dataset = _built(client)
        url = reverse("userdata:exclusions", args=[dataset.public_id])
        client.post(url, {"territory": ["RU-MOW"]})
        client.post(url, {})
        dataset.refresh_from_db()
        record = _record(dataset, TOTAL)
        assert "RU-MOW" in _panel(dataset, _key(dataset, record))[2020]

    def test_only_owner(self, client: Client, warehouse: Any) -> None:
        dataset = _built(client)
        url = reverse("userdata:exclusions", args=[dataset.public_id])
        assert Client().post(url, {"territory": ["RU-MOW"]}).status_code == 404
