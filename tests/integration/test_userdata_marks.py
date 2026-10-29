"""
Знаки вместо чисел, нули-заглушки и показатель только по России: от файла до значений
набора, шага «Проверка» и карточек.

Таблицы составлены в проверке из названий справочника: прочерк «–», «0,0», многоточие
со сноской о конфиденциальности, нулевой год.
"""

from __future__ import annotations

import json
from typing import Any

import duckdb
import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse

from apps.userdata import indicators, review
from apps.userdata.models import Dataset
from tests.support.userdata import (
    build,
    describe,
    key_of,
    upload,
    userdata_dir,  # noqa: F401 — приспособление модуля
    version_of,
)

pytestmark = pytest.mark.integration

YEARS = (2020, 2021, 2022)
SECRET_NOTE = (
    "1) Данные не публикуются в целях обеспечения конфиденциальности первичных "
    "статистических данных."
)


def _names(count: int) -> list[str]:
    payload = json.loads((settings.REFERENCE_DIR / "territories.json").read_bytes())
    return [record["name_ru"] for record in payload["regions"][:count]]


def _wide(special: dict[tuple[int, int], str], *, regions: int = 25, note: str = "") -> bytes:
    """Таблица «регион × годы»: у региона номер ``i`` в год ``y`` — особая ячейка."""
    lines = ["Регион;" + ";".join(str(year) for year in YEARS)]
    for number, name in enumerate(_names(regions)):
        cells = [special.get((number, year), f"{10 + number},{year % 10}") for year in YEARS]
        lines.append(f"{name};{';'.join(cells)}")
    if note:
        lines.append(note)
    return "\n".join(lines).encode("utf-8")


# Белгородская: прочерк в 2021; Брянская: «0,0» в 2020; Владимирская: скрыто в 2021;
# Воронежская: «0,0» в 2021 после обычного 2020.
MARKED = {(0, 2021): "–", (1, 2020): "0,0", (2, 2021): "…1)", (3, 2021): "0,0"}


def _facts(dataset: Dataset, *, derived: str = "") -> dict[tuple[str, int], tuple[Any, int, int]]:
    """(территория, год) → (значение, качество, признаки) ряда таблицы или его пересчёта."""
    version = version_of(dataset)
    record = next(item for item in version.series.order_by("order") if item.derived == derived)
    connection = duckdb.connect(str(version.data_path), read_only=True)
    try:
        rows = connection.execute(
            "SELECT territory_code, year, value, quality, flags FROM fact_observation "
            "WHERE series_key = ?",
            [key_of(dataset, record)],
        ).fetchall()
    finally:
        connection.close()
    return {
        (code, int(year)): (value, int(quality), int(flags))
        for code, year, value, quality, flags in rows
    }


def _built(
    client: Client, content: bytes, name: str = "организации.csv", **options: Any
) -> Dataset:
    dataset = upload(client, name, content)
    describe(client, dataset)
    response = build(client, dataset, **options)
    assert response.status_code == 302, response.content[:500]
    return dataset


@pytest.mark.django_db
class TestMarks:
    def test_sum_reads_dash_as_zero_and_keeps_marks(self, client: Client, warehouse: Any) -> None:
        dataset = _built(
            client,
            _wide(MARKED, note=SECRET_NOTE),
            kind=indicators.SUM,
            extra={"recalc-1": ["growth"]},
        )
        facts = _facts(dataset)
        assert facts[("RU-BEL", 2021)] == (0.0, 0, 8)
        assert facts[("RU-BRY", 2020)] == (0.0, 0, 16)
        assert facts[("RU-VLA", 2021)] == (None, 2, 0)
        marks = version_of(dataset).report["extract"]["marks"]
        assert marks == {"absent": 1, "below_unit": 2, "hidden": 1}
        # «0,0» в темп к прошлому году не идёт — ни как база, ни как значение года.
        growth = _facts(dataset, derived="growth")
        assert ("RU-VOR", 2021) not in growth
        assert ("RU-BRY", 2021) not in growth
        assert ("RU-KLU", 2021) in growth

    def test_relative_dash_has_no_value(self, client: Client, warehouse: Any) -> None:
        dataset = _built(client, _wide(MARKED), kind=indicators.RELATIVE)
        assert _facts(dataset)[("RU-BEL", 2021)] == (None, 4, 0)

    def test_answer_dash_means_no_data(self, client: Client, warehouse: Any) -> None:
        dataset = _built(client, _wide(MARKED), kind=indicators.SUM, extra={"dashes": "missing"})
        assert _facts(dataset)[("RU-BEL", 2021)] == (None, 1, 0)
        assert version_of(dataset).recipe["dashes"] == "missing"

    def test_check_step_explains_marks_and_asks_about_dash(
        self, client: Client, warehouse: Any
    ) -> None:
        dataset = upload(client, "организации.csv", _wide(MARKED, note=SECRET_NOTE))
        describe(client, dataset)
        page = client.get(reverse("userdata:series", args=[dataset.public_id]))
        assert "Что значит прочерк «–» в этой таблице?" in page.text
        assert "Скрыто источником: 1" in page.text
        assert "Значений «0,0»: 2" in page.text
        # У таблицы Росстата прочерк — «явление отсутствует» без вопроса.
        dataset.source_title = "Росстат"
        assert review.is_rosstat(dataset, {})
        assert not review.is_rosstat(Dataset(source_title="Банк России"), {})


@pytest.mark.django_db
class TestZeros:
    def test_year_of_zeros_is_asked_and_becomes_missing(
        self, client: Client, warehouse: Any
    ) -> None:
        zeros = {(number, 2021): "0" for number in range(25)}
        dataset = upload(client, "отходы.csv", _wide(zeros))
        describe(client, dataset)
        page = client.get(reverse("userdata:series", args=[dataset.public_id]))
        assert "Нули, похожие на пропуски" in page.text
        assert "Ноль у всех регионов: 2021." in page.text
        indicator = version_of(dataset).report["extract"]["zeros"][0]["indicator"]
        response = build(client, dataset, extra={"zeros-asked": "1", "zero": [indicator]})
        assert response.status_code == 302
        facts = _facts(dataset)
        assert facts[("RU-BEL", 2021)] == (None, 1, 0)
        assert facts[("RU-BEL", 2020)][0] == pytest.approx(10.0)

    def test_unticked_zeros_stay_values(self, client: Client, warehouse: Any) -> None:
        zeros = {(number, 2021): "0" for number in range(25)}
        dataset = upload(client, "отходы.csv", _wide(zeros))
        describe(client, dataset)
        build(client, dataset, extra={"zeros-asked": "1"})
        assert _facts(dataset)[("RU-BEL", 2021)] == (0.0, 0, 0)
        assert version_of(dataset).recipe["zeros"] == []


@pytest.mark.django_db
class TestCountryOnly:
    def test_indicator_without_regions_explains_itself(
        self, client: Client, warehouse: Any
    ) -> None:
        from django.contrib.auth.models import AnonymousUser
        from django.contrib.sessions.backends.cache import SessionStore
        from django.test import RequestFactory

        from apps.userdata import operations, scope, semantics, study_cards
        from apps.userdata.models import DatasetSeries

        lines = ["indicator_name;indicator_unit;object_name;year;indicator_value"]
        for year in YEARS:
            lines.append(f"Количество створов;шт.;Российская Федерация;{year};{500 + year % 10}")
            for name in _names(25):
                lines.append(f"Выбросы;тыс. т;{name};{year};12,5")
            lines.append(f"Выбросы;тыс. т;Российская Федерация;{year};312,5")
        dataset = _built(client, "\n".join(lines).encode("utf-8"), "среда.csv")
        record = DatasetSeries.objects.get(
            version=version_of(dataset), indicator="Количество створов", derived=""
        )
        key = key_of(dataset, record)
        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        request.session = SessionStore()
        with scope.reading_as(fingerprint=dataset.guest_key):
            info = operations.info_of(key)
            assert info is not None and info.value.country_only
            assert "только значение по России" in semantics.verdict("view:map", info.value).reason
            assert semantics.verdict("answer:change", info.value)
            block = {"id": "1", "kind": "answer", "question": "notable", "series": key}
            card = study_cards._answer(request, block, year=None, territories=[])
            assert card["template"].endswith("_unanswered.html")
            assert "только значение по России" in card["reason"]
            block["question"] = "change"
            card = study_cards._answer(request, block, year=None, territories=[])
        assert card["template"].endswith("_change.html"), card
        assert card["is_country"]
