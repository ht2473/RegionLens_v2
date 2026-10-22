"""
Виды фазы 4 на поле исследования: структура (части одного целого), «Почему изменилось» —
вклады регионов и вклады составляющих, карта значками у сумм и «Вид» карточки.

Таблица с двумя суммами одной единицы собирается из усечённого файла ЕБТ о преступлениях:
одна и та же строка региона даёт две части одного целого.
"""

from __future__ import annotations

import csv
import io
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from apps.userdata import studies
from apps.userdata.models import Dataset, Study
from tests.support.userdata import (
    FIXTURES,
    build,
    describe,
    upload,
    userdata_dir,  # noqa: F401 — приспособление модуля
)
from tests.support.userdata import built as _built
from tests.support.userdata import first_record as _first
from tests.support.userdata import key_of as _key
from tests.support.userdata import version_of as _version

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

PARTS = (("Преступления против личности", 0.3), ("Преступления против собственности", 0.7))


def _parts_table(client: Client) -> Dataset:
    """Таблица с двумя суммами в единицах — частями всех зарегистрированных преступлений."""
    source = (FIXTURES / "crime_wide.csv").read_text(encoding="utf-8")
    rows = list(csv.reader(io.StringIO(source), delimiter=";"))
    header, body = (
        rows[0],
        [row for row in rows[1:] if row[1] == "Всего зарегистрировано преступлений"],
    )
    years = [index for index, name in enumerate(header) if name.startswith("year")]
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";", lineterminator="\n")
    writer.writerow(header)
    for number, (name, share) in enumerate(PARTS, start=1):
        for row in body:
            copy = list(row)
            copy[1], copy[2] = name, f"Q11200000{number + 1}"
            for index in years:
                copy[index] = f"{float(row[index]) * share:.1f}" if row[index] else ""
            writer.writerow(copy)
    dataset = upload(client, "parts.csv", out.getvalue().encode("utf-8"))
    describe(client, dataset)
    assert build(client, dataset).status_code == 302
    dataset.refresh_from_db()
    return dataset


def _keys(dataset: Dataset) -> list[str]:
    """Ключи частей по порядку названий в ``PARTS``."""
    records = {record.title: record for record in _version(dataset).series.filter(derived="")}
    return [_key(dataset, records[name]) for name, _share in PARTS]


def _study(client: Client) -> Study:
    client.post(reverse("userdata:study-new"), {"title": "Части"})
    return Study.objects.latest("created_at")


def _edit(client: Client, study: Study, **data: Any) -> Any:
    return client.post(reverse("userdata:study-edit", args=[study.public_id]), data)


def _card(client: Client, study: Study, block: dict[str, Any]) -> Any:
    return client.get(reverse("userdata:study-card", args=[study.public_id, block["id"]]))


def _last(study: Study) -> dict[str, Any]:
    study.refresh_from_db()
    return studies.blocks_of(study)[-1]


class TestStructure:
    """Структура: выбор частей окном, карточка с долями, название — целое."""

    def test_dialog_offers_sums_of_one_unit(self, member_client: Client, warehouse: Any) -> None:
        """Окно — фрагментом без страницы; части одного начала названия отмечены заранее."""
        keys = _keys(_parts_table(member_client))
        study = _study(member_client)
        url = reverse("userdata:structure", args=[study.public_id])
        fragment = member_client.get(url, {"series": keys[0]}, HTTP_HX_REQUEST="true")
        assert fragment.status_code == 200
        assert "<html" not in fragment.text
        for key in keys:
            assert f'value="{key}" checked' in fragment.text
        page = member_client.get(url, {"series": keys[0]})
        assert "<html" in page.text and "К исследованию" in page.text

    def test_dialog_link_takes_the_whole_fragment(
        self, member_client: Client, warehouse: Any
    ) -> None:
        """Ссылка окна внутри формы «Что сделать» не наследует её выборку ответа."""
        keys = _keys(_parts_table(member_client))
        study = _study(member_client)
        page = member_client.get(
            reverse("userdata:study", args=[study.public_id]), {"series": keys[0]}
        )
        link = page.text.split('data-dialog-open="structure-dialog"', 1)[1].split(">", 1)[0]
        assert 'hx-select="unset"' in link and 'hx-select-oob="unset"' in link

    def test_card_shows_shares_under_the_name_of_the_whole(
        self, member_client: Client, warehouse: Any
    ) -> None:
        """Доли частей в сумме: Россия сверху, в названии — обе части."""
        keys = _keys(_parts_table(member_client))
        study = _study(member_client)
        _edit(member_client, study, action="add", do="answer:structure", series=keys[0], part=keys)
        block = _last(study)
        assert block["question"] == "structure" and block["parts"] == keys
        card = _card(member_client, study, block)
        assert card.status_code == 200
        assert "part-legend" in card.text
        assert "30,0" in card.text and "70,0" in card.text
        assert f"{PARTS[0][0]} и {PARTS[1][0]}" in card.text

    def test_part_from_elsewhere_is_refused(self, member_client: Client, warehouse: Any) -> None:
        """Часть не из таблицы ряда отбрасывается; одной части для структуры мало."""
        keys = _keys(_parts_table(member_client))
        other = _built(member_client)
        study = _study(member_client)
        foreign = _key(other, _first(other))
        _edit(
            member_client,
            study,
            action="add",
            do="answer:structure",
            series=keys[0],
            part=[keys[0], foreign],
        )
        study.refresh_from_db()
        assert studies.blocks_of(study) == []


class TestContributions:
    """«Почему изменилось»: вклады регионов у суммы, вклады составляющих под структурой."""

    def test_regions(self, member_client: Client, warehouse: Any) -> None:
        """Сумма регионов от начального года к конечному — водопадом и таблицей."""
        keys = _keys(_parts_table(member_client))
        study = _study(member_client)
        _edit(member_client, study, action="add", do="answer:contributions", series=keys[0])
        card = _card(member_client, study, _last(study))
        assert "сумма регионов с 2017 по 2022 год" in card.text
        assert "rl-chart" in card.text and "Вклады таблицей" in card.text
        assert "остальные регионы" in card.text

    def test_parts_from_structure(self, member_client: Client, warehouse: Any) -> None:
        """Под структурой — «Почему изменилась сумма» с теми же частями; потом подсказки нет."""
        keys = _keys(_parts_table(member_client))
        study = _study(member_client)
        _edit(member_client, study, action="add", do="answer:structure", series=keys[0], part=keys)
        structure = _last(study)
        card = _card(member_client, study, structure)
        assert "Почему изменилась сумма" in card.text
        for key in keys:
            assert f'name="part" value="{key}"' in card.text
        _edit(
            member_client, study, action="add", do="answer:contributions", series=keys[0], part=keys
        )
        block = _last(study)
        assert block["question"] == "contributions" and block["parts"] == keys
        answer = _card(member_client, study, block)
        assert "сумма частей с 2017 по 2022 год" in answer.text
        assert PARTS[1][0] in answer.text
        assert "Почему изменилась сумма" not in _card(member_client, study, structure).text

    def test_relative_value_is_answered_with_a_reason(
        self, member_client: Client, warehouse: Any
    ) -> None:
        """Доли не складываются: вместо водопада — причина и куда смотреть."""
        dataset = _built(member_client)
        study = _study(member_client)
        _edit(
            member_client,
            study,
            action="add",
            do="answer:contributions",
            series=_key(dataset, _first(dataset)),
        )
        card = _card(member_client, study, _last(study))
        assert "складываются только у сумм" in card.text


class TestSymbolsAndSettings:
    """Карта суммы — значками; «Вид» карточки меняет знаки, год и вид."""

    def test_sum_is_drawn_with_circles(self, member_client: Client, warehouse: Any) -> None:
        keys = _keys(_parts_table(member_client))
        study = _study(member_client)
        _edit(member_client, study, action="add", do="view:map", series=keys[0])
        block = _last(study)
        card = _card(member_client, study, block)
        assert "geo-map__symbol" in card.text and "Площадь круга" in card.text
        assert "marks=symbols" in card.text
        assert "Знаки карты" in card.text

        _edit(
            member_client, study, action="configure", block=block["id"], marks="fill", year="2019"
        )
        block = _last(study)
        assert block["parameters"]["marks"] == "fill" and block["year"] == 2019
        card = _card(member_client, study, block)
        assert "geo-map__symbol" not in card.text
        assert "2019" in card.text

        _edit(member_client, study, action="configure", block=block["id"], target="rankings")
        block = _last(study)
        assert block["target"] == "rankings"

    def test_relative_value_keeps_the_fill(self, member_client: Client, warehouse: Any) -> None:
        dataset = _built(member_client)
        study = _study(member_client)
        _edit(
            member_client, study, action="add", do="view:map", series=_key(dataset, _first(dataset))
        )
        card = _card(member_client, study, _last(study))
        assert "geo-map__symbol" not in card.text
