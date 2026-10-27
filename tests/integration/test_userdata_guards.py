"""
Ограждения анализа своих данных: сумма — через значение на жителя (окно, новая карточка,
сохранённая карточка), «С чем связано» — только с относительными показателями сайта,
резкий скачок региона — замечанием на шаге «Проверка».
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse

from apps.userdata import indicators, studies
from apps.userdata.models import Study
from tests.support.userdata import (
    built,
    describe,
    first_record,
    key_of,
    upload,
    userdata_dir,  # noqa: F401 — приспособление модуля
    version_of,
)

pytestmark = pytest.mark.integration


def _study(client: Client) -> Study:
    response = client.post(reverse("userdata:study-new"), {"title": "Ограждения"})
    assert response.status_code == 302
    return Study.objects.latest("created_at")


def _sum_table(client: Client, **options: Any) -> tuple[Any, str]:
    dataset = built(client, "crime_wide.csv", kind=indicators.SUM, **options)
    return dataset, key_of(dataset, first_record(dataset))


@pytest.mark.django_db
class TestSumThroughPerCapita:
    def test_window_offers_spread_with_recount_note(
        self, member_client: Client, warehouse: Any
    ) -> None:
        _dataset, key = _sum_table(member_client, per=())
        study = _study(member_client)
        page = member_client.get(reverse("userdata:study", args=[study.public_id]), {"series": key})
        assert 'value="answer:spread"' in page.text
        assert "Сначала посчитает значение на жителя — оно встанет в таблицу" in page.text

    def test_new_card_goes_to_recount(self, member_client: Client, warehouse: Any) -> None:
        dataset, key = _sum_table(member_client, per=())
        study = _study(member_client)
        member_client.post(
            reverse("userdata:study-edit", args=[study.public_id]),
            {"action": "add", "do": "answer:spread", "series": key},
        )
        study.refresh_from_db()
        block = studies.blocks_of(study)[-1]
        assert block["question"] == "spread"
        assert block["series"] != key
        recount = version_of(dataset).series.get(code=block["series"].rsplit(":", 1)[1])
        assert recount.base_code == first_record(dataset).code
        assert recount.derived.startswith("per")

    def test_saved_card_on_sum_uses_built_recount(
        self, member_client: Client, warehouse: Any
    ) -> None:
        _dataset, key = _sum_table(member_client, per=("per100000",))
        study = _study(member_client)
        block = studies.add_answer(study, "spread", key)
        card = member_client.get(
            reverse("userdata:study-card", args=[study.public_id, block["id"]])
        )
        assert card.status_code == 200
        assert "Посчитано по значению на жителя." in card.text
        assert "Джини" in card.text

    def test_saved_card_without_recount_explains(
        self, member_client: Client, warehouse: Any
    ) -> None:
        _dataset, key = _sum_table(member_client, per=())
        study = _study(member_client)
        block = studies.add_answer(study, "spread", key)
        card = member_client.get(
            reverse("userdata:study-card", args=[study.public_id, block["id"]])
        )
        assert "сначала пересчитайте на жителя" in card.text


@pytest.mark.django_db
class TestRelatedAndJumps:
    def test_related_skips_site_sums(self, member_client: Client, warehouse: Any) -> None:
        from apps.userdata import related, scope
        from apps.userdata.series import UserSeries

        dataset = built(member_client, kind=indicators.RELATIVE)
        record = first_record(dataset)
        with scope.reading_as(user=dataset.owner):
            found = related.related(UserSeries(record, dataset))
        links = [*found["significant"], *found["other"]]
        assert links
        assert not any(link.item.absolute for link in links)

    def test_check_step_reports_a_typo(self, member_client: Client, warehouse: Any) -> None:
        payload = json.loads((settings.REFERENCE_DIR / "territories.json").read_bytes())
        names = [record["name_ru"] for record in payload["regions"][:30]]
        lines = ["Регион;2020;2021"]
        for number, name in enumerate(names):
            later = f"{(50 + number) * 105 // 100}" if number else f"{(50 + number) * 100}"
            lines.append(f"{name};{50 + number};{later}")
        dataset = upload(member_client, "доход.csv", "\n".join(lines).encode("utf-8"))
        describe(member_client, dataset)
        page = member_client.get(reverse("userdata:series", args=[dataset.public_id]))
        assert "резкий скачок" in page.text
        assert "Белгородская область, 2020 → 2021: 50 → 5 000" in page.text
