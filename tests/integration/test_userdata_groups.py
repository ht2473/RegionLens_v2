"""
Свои группы регионов и готовые наборы карточек (фаза 5): страница групп, карточка «Группа
регионов против остальных» у суммы и у относительной величины, переход группы гостя
в учётную запись; наборы «Профиль показателя», «Неравенство и сближение», «Мой регион
среди соседей» и «Сравнить два показателя».
"""

from __future__ import annotations

from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from apps.analytics.selectors import neighbour_map
from apps.catalog.models import Territory
from apps.userdata import groups, studies
from apps.userdata.models import RegionGroup, Study
from tests.support.userdata import built as _built
from tests.support.userdata import first_record as _first
from tests.support.userdata import key_of as _key
from tests.support.userdata import userdata_dir  # noqa: F401 — приспособление модуля

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

SIBERIA = ["RU-KYA", "RU-KEM", "RU-IRK", "RU-NVS"]


def _group(client: Client, title: str = "Сибирь", codes: list[str] | None = None) -> Any:
    return client.post(reverse("userdata:groups"), {"title": title, "territory": codes or SIBERIA})


def _study(client: Client) -> Study:
    client.post(reverse("userdata:study-new"), {"title": "Группы"})
    return Study.objects.latest("created_at")


def _edit(client: Client, study: Study, **data: Any) -> Any:
    return client.post(reverse("userdata:study-edit", args=[study.public_id]), data)


def _card(client: Client, study: Study, block: dict[str, Any]) -> Any:
    return client.get(reverse("userdata:study-card", args=[study.public_id, block["id"]]))


def _blocks(study: Study) -> list[dict[str, Any]]:
    study.refresh_from_db()
    return studies.blocks_of(study)


class TestGroupsPage:
    """Создать, изменить, удалить; пустую, без регионов и из всех регионов — нельзя."""

    def test_create_edit_delete(self, member_client: Client, member: Any, warehouse: Any) -> None:
        assert _group(member_client).status_code == 302
        group = RegionGroup.objects.get(owner=member)
        assert group.title == "Сибирь" and set(group.codes) == set(SIBERIA)
        page = member_client.get(reverse("userdata:groups"))
        assert "Сибирь" in page.text and "регионов: 4" in page.text
        member_client.post(
            reverse("userdata:groups"),
            {"group": str(group.public_id), "title": "Восток", "territory": ["RU-PRI"]},
        )
        group.refresh_from_db()
        assert (group.title, group.codes) == ("Восток", ["RU-PRI"])
        member_client.post(
            reverse("userdata:groups"), {"action": "delete", "group": str(group.public_id)}
        )
        assert not RegionGroup.objects.filter(owner=member).exists()

    def test_refused(self, member_client: Client, member: Any, warehouse: Any) -> None:
        everything = list(Territory.objects.comparable().values_list("code", flat=True))
        for title, codes in (("", SIBERIA), ("Пусто", ["XX-NONE"]), ("Все", everything)):
            response = _group(member_client, title, codes)
            assert response.status_code == 200
        assert not RegionGroup.objects.filter(owner=member).exists()

    def test_foreign_group_is_not_found(
        self, member_client: Client, make_user: Any, warehouse: Any
    ) -> None:
        stranger = Client()
        stranger.force_login(make_user(email="other@example.com"))
        _group(stranger)
        foreign = RegionGroup.objects.get()
        response = member_client.post(
            reverse("userdata:groups"), {"group": str(foreign.public_id), "title": "Моя"}
        )
        assert response.status_code == 404

    def test_guest_group_moves_to_the_account(
        self, client: Client, member: Any, warehouse: Any
    ) -> None:
        _group(client)
        group = RegionGroup.objects.get()
        assert group.owner is None and group.expires_at is not None
        client.force_login(member)
        group.refresh_from_db()
        assert group.owner == member and group.expires_at is None


class TestGroupCard:
    """Итог группы против остальных: доля у суммы, взвешенные средние у относительной."""

    def test_sum_shows_the_share_of_the_group(
        self, member_client: Client, member: Any, warehouse: Any
    ) -> None:
        dataset = _built(member_client, "crime_wide.csv")
        key = _key(dataset, _first(dataset))
        _group(member_client)
        group = RegionGroup.objects.get(owner=member)
        study = _study(member_client)
        dialog = member_client.get(
            reverse("userdata:study-group", args=[study.public_id]),
            {"series": key},
            HTTP_HX_REQUEST="true",
        )
        assert "<html" not in dialog.text and str(group.public_id) in dialog.text
        _edit(
            member_client,
            study,
            action="add",
            do="answer:group",
            series=key,
            group=str(group.public_id),
        )
        (block,) = _blocks(study)
        assert block["question"] == "group" and block["group"] == str(group.public_id)
        card = _card(member_client, study, block)
        assert "доля группы в сумме регионов" in card.text
        assert "регионов группы со значением: 4 из 4" in card.text
        assert "Сибирь" in card.text

        group.delete()
        assert "Группа регионов удалена" in _card(member_client, study, block).text

    def test_relative_value_compares_weighted_means(
        self, member_client: Client, member: Any, warehouse: Any
    ) -> None:
        dataset = _built(member_client)
        key = _key(dataset, _first(dataset))
        _group(member_client)
        group = RegionGroup.objects.get(owner=member)
        totals = groups.totals(key, group.codes, is_sum=False)
        assert totals == [] or all(item.members <= len(group.codes) for item in totals)
        study = _study(member_client)
        _edit(
            member_client,
            study,
            action="add",
            do="answer:group",
            series=key,
            group=str(group.public_id),
        )
        card = _card(member_client, study, _blocks(study)[0])
        assert "группа против остальных" in card.text
        assert "взвешено по среднегодовой численности" in card.text

    def test_foreign_group_is_refused(
        self, member_client: Client, make_user: Any, warehouse: Any
    ) -> None:
        dataset = _built(member_client, "crime_wide.csv")
        stranger = Client()
        stranger.force_login(make_user(email="other@example.com"))
        _group(stranger)
        foreign = RegionGroup.objects.get()
        study = _study(member_client)
        _edit(
            member_client,
            study,
            action="add",
            do="answer:group",
            series=_key(dataset, _first(dataset)),
            group=str(foreign.public_id),
        )
        assert _blocks(study) == []


class TestSets:
    """Готовые наборы: несколько карточек одной правкой; неподходящие — не входят."""

    def test_profile_of_a_sum(self, member_client: Client, warehouse: Any) -> None:
        dataset = _built(member_client, "crime_wide.csv")
        key = _key(dataset, _first(dataset))
        study = _study(member_client)
        response = _edit(member_client, study, action="add", do="set:profile", series=key)
        blocks = _blocks(study)
        kinds = [block.get("target") or block.get("question") for block in blocks]
        assert kinds == [
            "map",
            "notable",
            "rankings",
            "compare",
            "distribution",
            "districts",
            "contributions",
        ]
        added = response["Location"].split("added=", 1)[1].split("#", 1)[0]
        assert added.replace("%2C", ",").split(",") == [block["id"] for block in blocks]

    def test_scripted_set_reloads_the_page(self, member_client: Client, warehouse: Any) -> None:
        dataset = _built(member_client, "crime_wide.csv")
        study = _study(member_client)
        response = member_client.post(
            reverse("userdata:study-edit", args=[study.public_id]),
            {"action": "add", "do": "set:profile", "series": _key(dataset, _first(dataset))},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 204
        assert response["HX-Redirect"].startswith(reverse("userdata:study", args=[study.public_id]))

    def test_inequality(self, member_client: Client, warehouse: Any) -> None:
        # Сумме неравенство — только через показатель на жителя: набор не ставится.
        dataset = _built(member_client, "crime_wide.csv")
        study = _study(member_client)
        _edit(
            member_client,
            study,
            action="add",
            do="set:inequality",
            series=_key(dataset, _first(dataset)),
        )
        assert _blocks(study) == []
        rates = _built(member_client, "crime_wide.csv", kind="relative")
        _edit(
            member_client,
            study,
            action="add",
            do="set:inequality",
            series=_key(rates, _first(rates)),
        )
        kinds = [block.get("target") or block.get("question") for block in _blocks(study)]
        assert kinds == ["spread", "convergence", "distribution", "multiples"]

    def test_neighbours_needs_my_region(
        self, member_client: Client, member: Any, warehouse: Any
    ) -> None:
        dataset = _built(member_client, "crime_wide.csv")
        key = _key(dataset, _first(dataset))
        study = _study(member_client)
        _edit(member_client, study, action="add", do="set:neighbours", series=key)
        assert _blocks(study) == []
        member.region = Territory.objects.get(code="RU-TVE")
        member.save(update_fields=["region"])
        _edit(member_client, study, action="add", do="set:neighbours", series=key)
        kinds = [block.get("target") or block.get("question") for block in _blocks(study)]
        assert kinds[:3] == ["mine", "map", "rankings"]
        study.refresh_from_db()
        assert study.territories[0] == "RU-TVE"
        assert set(study.territories[1:]) <= set(neighbour_map("all")["RU-TVE"])

    def test_pair(self, member_client: Client, warehouse: Any) -> None:
        from apps.catalog.models import Series

        dataset = _built(member_client, "crime_wide.csv")
        key = _key(dataset, _first(dataset))
        site = Series.objects.exclude(key__startswith="u:").order_by("key").first()
        assert site is not None
        study = _study(member_client)
        _edit(member_client, study, action="add", do="set:pair", series=key, other=site.key)
        kinds = [block.get("target") or block.get("question") for block in _blocks(study)]
        # Связь суммы — только через показатель на жителя: в наборе её нет.
        assert kinds == ["comparison", "map", "map"]
        rates = _built(member_client, "crime_wide.csv", kind="relative")
        share = (
            Series.objects.exclude(key__startswith="u:")
            .filter(unit__kind__in=["share", "rate"])
            .order_by("key")
            .first()
        )
        assert share is not None
        study = _study(member_client)
        _edit(
            member_client,
            study,
            action="add",
            do="set:pair",
            series=_key(rates, _first(rates)),
            other=share.key,
        )
        kinds = [block.get("target") or block.get("question") for block in _blocks(study)]
        assert kinds == ["comparison", "relation", "map", "map"]
