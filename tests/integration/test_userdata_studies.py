"""
Исследования: создание (и гостем), «В исследование» с холста и инструмента, карточки видов
и ответов на текущих данных, «Что сделать» и пересчёт из меню, заметки и порядок, «Вернуть»,
общий год и регионы, пределы частоты, закрытая ссылка на исследование и доступ.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlencode

import pytest
from django.test import Client
from django.urls import reverse

from apps.userdata import studies
from apps.userdata.models import Study
from tests.support.userdata import built as _built
from tests.support.userdata import first_record as _first
from tests.support.userdata import key_of as _key
from tests.support.userdata import userdata_dir  # noqa: F401 — приспособление модуля
from tests.support.userdata import version_of as _version

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

LINK = re.compile(r'value="(http://testserver/s/[A-Za-z0-9_-]+)"')


def _study(client: Client, title: str = "К курсовой") -> Study:
    response = client.post(reverse("userdata:study-new"), {"title": title})
    assert response.status_code == 302
    return Study.objects.latest("created_at")


def _add(client: Client, study: Study, target: str, query: dict[str, Any] | list[Any]) -> Any:
    return client.post(
        reverse("userdata:study-add"),
        {
            "target": target,
            "query_string": urlencode(query, doseq=True),
            "study": str(study.public_id),
            "back": reverse("maps:choropleth"),
        },
    )


def _card(client: Client, study: Study, block: dict[str, Any], **query: Any) -> Any:
    url = reverse("userdata:study-card", args=[study.public_id, block["id"]])
    return client.get(url, query)


class TestOwner:
    """Владелец собирает исследование: виды, заметки, порядок, общий выбор, удаление."""

    def test_create_and_add_view(self, member_client: Client, warehouse: Any) -> None:
        dataset = _built(member_client)
        key = _key(dataset, _first(dataset))
        study = _study(member_client)
        response = _add(member_client, study, "map", {"series": key, "year": "2020"})
        assert response.status_code == 302
        study.refresh_from_db()
        (block,) = studies.blocks_of(study)
        assert block["target"] == "map"
        assert block["parameters"]["series"] == key
        page = member_client.get(reverse("userdata:study", args=[study.public_id]))
        assert page.status_code == 200
        assert 'name="robots" content="noindex' in page.text
        card = _card(member_client, study, block)
        assert card.status_code == 200
        assert "geo-map" in card.text or "tile-map" in card.text
        assert f"b{block['id']}" in card.text
        assert "загружены пользователем" in card.text

    def test_two_maps_have_own_ids(self, member_client: Client, warehouse: Any) -> None:
        dataset = _built(member_client)
        key = _key(dataset, _first(dataset))
        study = _study(member_client)
        _add(member_client, study, "map", {"series": key, "year": "2020"})
        _add(member_client, study, "map", {"series": key, "year": "2021"})
        study.refresh_from_db()
        first, second = studies.blocks_of(study)
        ids_first = set(re.findall(r'id="([^"]+)"', _card(member_client, study, first).text))
        ids_second = set(re.findall(r'id="([^"]+)"', _card(member_client, study, second).text))
        assert ids_first
        assert not ids_first & ids_second

    def test_every_canvas_view_and_tool(self, member_client: Client, warehouse: Any) -> None:
        dataset = _built(member_client)
        key = _key(dataset, _first(dataset))
        study = _study(member_client)
        for target in studies.CANVAS_TARGETS:
            _add(member_client, study, target, {"series": key, "territory": ["RU-TA", "RU-MOW"]})
        _add(member_client, study, "correlation", {"x": key, "y": key, "year": "2020"})
        study.refresh_from_db()
        for block in studies.blocks_of(study):
            card = _card(member_client, study, block)
            assert card.status_code == 200, block["target"]
            assert "На отдельной странице" in card.text, block["target"]
        tool = studies.blocks_of(study)[-1]
        assert "analytics/correlation" in _card(member_client, study, tool).text

    def test_text_order_and_remove(self, member_client: Client, warehouse: Any) -> None:
        study = _study(member_client)
        edit = reverse("userdata:study-edit", args=[study.public_id])
        member_client.post(edit, {"action": "add-text", "text": "Первый вывод"})
        member_client.post(edit, {"action": "add-text", "text": "Второй вывод"})
        study.refresh_from_db()
        first, second = studies.blocks_of(study)
        member_client.post(edit, {"action": "up", "block": second["id"]})
        study.refresh_from_db()
        assert [block["text"] for block in studies.blocks_of(study)] == [
            "Второй вывод",
            "Первый вывод",
        ]
        member_client.post(edit, {"action": "change", "block": first["id"], "text": "Иначе"})
        member_client.post(edit, {"action": "remove", "block": second["id"]})
        study.refresh_from_db()
        assert [block["text"] for block in studies.blocks_of(study)] == ["Иначе"]
        page = member_client.get(reverse("userdata:study", args=[study.public_id]))
        assert "Иначе" in page.text

    def test_common_year_and_territories(self, member_client: Client, warehouse: Any) -> None:
        dataset = _built(member_client)
        key = _key(dataset, _first(dataset))
        study = _study(member_client)
        _add(member_client, study, "table", {"series": key, "year": "2018"})
        edit = reverse("userdata:study-edit", args=[study.public_id])
        member_client.post(edit, {"action": "common", "year": "2020", "territory": ["RU-TA"]})
        study.refresh_from_db()
        assert study.year == 2020
        assert study.territories == ["RU-TA"]
        (block,) = studies.blocks_of(study)
        page = member_client.get(reverse("userdata:study", args=[study.public_id]))
        assert "year=2020" in page.text
        card = _card(member_client, study, block, common="1", year="2020", territory="RU-TA")
        assert "2020" in card.text
        assert card.text.count("<tr") == 2  # шапка и одна отмеченная территория

    def test_delete(self, member_client: Client, warehouse: Any) -> None:
        study = _study(member_client)
        edit = reverse("userdata:study-edit", args=[study.public_id])
        member_client.post(edit, {"action": "delete"})
        assert Study.objects.filter(pk=study.pk).exists()
        member_client.post(edit, {"action": "delete", "confirm": "1"})
        assert not Study.objects.filter(pk=study.pk).exists()

    def test_limits(self, member_client: Client, settings: Any, warehouse: Any) -> None:
        settings.USERDATA_MAX_STUDIES = 1
        _study(member_client)
        member_client.post(reverse("userdata:study-new"), {"title": "Вторая"})
        assert Study.objects.count() == 1
        settings.USERDATA_STUDY_BLOCKS = 1
        study = Study.objects.get()
        edit = reverse("userdata:study-edit", args=[study.public_id])
        member_client.post(edit, {"action": "add-text", "text": "раз"})
        member_client.post(edit, {"action": "add-text", "text": "два"})
        study.refresh_from_db()
        assert len(studies.blocks_of(study)) == 1

    def test_unknown_target_refused(self, member_client: Client, warehouse: Any) -> None:
        study = _study(member_client)
        _add(member_client, study, "admin", {"series": "x"})
        study.refresh_from_db()
        assert studies.blocks_of(study) == []

    def test_guest_study_lives_a_day_and_moves_on_login(
        self, client: Client, member: Any, warehouse: Any
    ) -> None:
        response = client.post(reverse("userdata:study-new"), {"title": "Гостевое"})
        study = Study.objects.get()
        assert response["Location"] == reverse("userdata:study", args=[study.public_id])
        assert study.owner is None and study.guest_key and study.expires_at
        assert client.get(response["Location"]).status_code == 200
        # Чужой сеанс исследования гостя не видит.
        assert Client().get(response["Location"]).status_code == 404
        client.force_login(member)
        study.refresh_from_db()
        assert study.owner == member and not study.guest_key and study.expires_at is None

    def test_save_popover_lists_studies(self, member_client: Client, warehouse: Any) -> None:
        study = _study(member_client, "Для редактора")
        page = member_client.get(reverse("maps:choropleth"))
        assert "Для редактора" in page.text
        assert str(study.public_id) in page.text

    def test_account_export(self, member_client: Client, member: Any, warehouse: Any) -> None:
        from apps.accounts.services import export_account

        study = _study(member_client)
        member_client.post(
            reverse("userdata:study-edit", args=[study.public_id]),
            {"action": "add-text", "text": "вывод"},
        )
        exported = export_account(member)
        assert exported["studies"][0]["title"] == "К курсовой"
        assert json.dumps(exported, ensure_ascii=False)


class TestShare:
    """Закрытая ссылка на исследование: читатель видит его карточки и таблицы — и только их."""

    def _shared(self, client: Client, study: Study) -> str:
        client.post(reverse("userdata:study-share-create", args=[study.public_id]), {"days": "30"})
        page = client.get(reverse("userdata:study", args=[study.public_id]))
        found = LINK.search(page.text)
        assert found
        return found.group(1).removeprefix("http://testserver")

    def test_reader(self, member_client: Client, warehouse: Any) -> None:
        on_study = _built(member_client)
        off_study = _built(member_client)
        study = _study(member_client)
        key = _key(on_study, _first(on_study))
        _add(member_client, study, "map", {"series": key, "year": "2020"})
        path = self._shared(member_client, study)
        reader = Client()
        opened = reader.get(path)
        assert opened["Location"] == reverse("userdata:study", args=[study.public_id])
        page = reader.get(opened["Location"])
        assert page.status_code == 200
        assert "открыто по закрытой ссылке" in page.text
        assert reverse("userdata:study-edit", args=[study.public_id]) not in page.text
        study.refresh_from_db()
        (block,) = studies.blocks_of(study)
        card = _card(reader, study, block)
        assert card.status_code == 200
        assert "geo-map" in card.text or "tile-map" in card.text
        assert reader.get(reverse("maps:choropleth"), {"series": key}).status_code == 200
        assert reader.get(reverse("userdata:dataset", args=[on_study.public_id])).status_code == 200
        assert (
            reader.get(reverse("userdata:dataset", args=[off_study.public_id])).status_code == 404
        )
        edit = reverse("userdata:study-edit", args=[study.public_id])
        assert reader.post(edit, {"action": "add-text", "text": "x"}).status_code == 404

    def test_foreign_key_on_study_opens_nothing(
        self, member_client: Client, make_user: Any, warehouse: Any
    ) -> None:
        other = Client()
        other.force_login(make_user(email="other@example.com"))
        foreign = _built(other)
        foreign_key = _key(foreign, _first(foreign))
        study = _study(member_client)
        # Чужой ключ, вписанный в параметры вручную, ни к чему не даёт доступа.
        _add(member_client, study, "map", {"series": foreign_key})
        path = self._shared(member_client, study)
        reader = Client()
        reader.get(path)
        study.refresh_from_db()
        (block,) = studies.blocks_of(study)
        assert "Ряда этой карточки больше нет" in _card(reader, study, block).text
        assert reader.get(reverse("maps:choropleth"), {"series": foreign_key}).status_code == 404
        assert reader.get(reverse("userdata:dataset", args=[foreign.public_id])).status_code == 404

    def test_stranger(self, member_client: Client, make_user: Any, warehouse: Any) -> None:
        study = _study(member_client)
        member_client.post(
            reverse("userdata:study-edit", args=[study.public_id]),
            {"action": "add-text", "text": "вывод"},
        )
        other = Client()
        other.force_login(make_user(email="other@example.com"))
        assert other.get(reverse("userdata:study", args=[study.public_id])).status_code == 404
        response = other.post(
            reverse("userdata:study-edit", args=[study.public_id]),
            {"action": "delete", "confirm": "1"},
        )
        assert response.status_code == 404
        assert Study.objects.filter(pk=study.pk).exists()
        assert (
            other.post(
                reverse("userdata:study-share-create", args=[study.public_id]), {"days": "7"}
            ).status_code
            == 404
        )

    def test_revoked(self, member_client: Client, warehouse: Any) -> None:
        study = _study(member_client)
        path = self._shared(member_client, study)
        reader = Client()
        reader.get(path)
        share = study.shares.get()
        member_client.post(
            reverse("userdata:study-share-revoke", args=[study.public_id, share.public_id])
        )
        assert reader.get(reverse("userdata:study", args=[study.public_id])).status_code == 404


def _edit(client: Client, study: Study, **data: Any) -> Any:
    return client.post(reverse("userdata:study-edit", args=[study.public_id]), data)


class TestLab:
    """Поле лаборатории: «Что сделать», ответы числами, пересчёт из меню, правка на месте."""

    def test_panel_offers_actions_for_chosen_series(
        self, member_client: Client, warehouse: Any
    ) -> None:
        dataset = _built(member_client)
        key = _key(dataset, _first(dataset))
        study = _study(member_client)
        page = member_client.get(reverse("userdata:study", args=[study.public_id]), {"series": key})
        assert 'id="study-actions"' in page.text
        for value in ("view:map", "answer:leaders", "answer:spread", "answer:related"):
            assert f'value="{value}"' in page.text
        # Доля по регионам не пересчитывается: недоступное — с причиной.
        assert "только для сумм по регионам" in page.text

    def test_every_answer_renders(self, member_client: Client, warehouse: Any) -> None:
        from apps.catalog.models import Series

        dataset = _built(member_client)
        key = _key(dataset, _first(dataset))
        site = Series.objects.exclude(key__startswith="u:").order_by("key").first()
        assert site is not None
        study = _study(member_client)
        markers = {
            "leaders": "answer-list",
            "change": "answer__value",
            "spread": "Джини",
            "convergence": "изменение разброса",
            "neighbours": "индекс Морана",
            "related": "Спирмена",
        }
        for question in markers:
            _edit(member_client, study, action="add", do=f"answer:{question}", series=key)
        _edit(member_client, study, action="add", do="answer:relation", series=key, other=site.key)
        study.refresh_from_db()
        blocks = studies.blocks_of(study)
        assert [block["question"] for block in blocks] == [*markers, "relation"]
        for block in blocks:
            card = _card(member_client, study, block)
            assert card.status_code == 200
            marker = markers.get(block["question"], "rl-chart")
            assert marker in card.text or "study-card__empty" in card.text, block["question"]

    def test_new_answers_render(self, member_client: Client, member: Any, warehouse: Any) -> None:
        from apps.catalog.models import Series, Territory

        dataset = _built(member_client, "crime_wide.csv")
        key = _key(dataset, _first(dataset))
        site = Series.objects.exclude(key__startswith="u:").order_by("key").first()
        assert site is not None
        study = _study(member_client)
        markers = {
            "districts": "district-bars",
            "growth": "Изменение с",
            "mine": "Мой регион не выбран",
        }
        for question in markers:
            _edit(member_client, study, action="add", do=f"answer:{question}", series=key)
        _edit(member_client, study, action="add", do=f"compare:{site.key}", series=key)
        study.refresh_from_db()
        blocks = studies.blocks_of(study)
        assert [block["question"] for block in blocks] == [*markers, "comparison"]
        # Сравнение — во всю ширину поля.
        assert [block["wide"] for block in blocks] == [False, False, False, True]
        for block in blocks:
            card = _card(member_client, study, block)
            assert card.status_code == 200
            marker = markers.get(block["question"], "Сравнение двух показателей")
            assert marker in card.text, block["question"]
        # Мой регион выбран — его место среди регионов и соседи по рейтингу.
        member.region = Territory.objects.comparable().order_by("code").first()
        member.save(update_fields=["region"])
        mine = next(block for block in blocks if block["question"] == "mine")
        card = _card(member_client, study, mine)
        assert "Рядом в рейтинге" in card.text or "значения по моему региону нет" in card.text

    def test_section_desk(self, member_client: Client, warehouse: Any) -> None:
        """
        Рабочий стол: в первый раз — «Что получится» и примеры; затем исследования
        карточками с миниатюрой плитками и таблицы строками.
        """
        first = member_client.get(reverse("userdata:index")).text
        assert "Что получится" in first and "Начните с примера" in first
        assert "desk-drop--big" in first and "Продолжить" not in first

        dataset = _built(member_client, "crime_wide.csv")
        key = _key(dataset, _first(dataset))
        study = _study(member_client)
        _edit(member_client, study, action="add", do="view:map", series=key)
        desk = member_client.get(reverse("userdata:index")).text
        assert "Продолжить" in desk and "desk-drop--big" not in desk
        assert reverse("userdata:study", args=[study.public_id]) in desk
        assert "desk-thumb" in desk and 'data-step="' in desk
        assert reverse("userdata:dataset", args=[dataset.public_id]) in desk
        # Сборка открыла таблицу в своём исследовании, второе — с картой из неё.
        assert "исследований: 2" in desk

    def test_notable_and_next_steps(self, member_client: Client, warehouse: Any) -> None:
        """
        «Что заметно» — крайние значения числами; под первой карточкой своего ряда —
        «Дальше»: карта уже собранного пересчёта на жителя, рейтинг, сравнение с официальным.
        """
        dataset = _built(member_client, "crime_wide.csv", per=("per100000",))
        key = _key(dataset, _first(dataset))
        study = _study(member_client)
        _edit(member_client, study, action="add", do="answer:notable", series=key)
        _edit(member_client, study, action="add", do="view:map", series=key)
        study.refresh_from_db()
        notable, chosen = studies.blocks_of(study)
        card = _card(member_client, study, notable)
        assert "Больше всего" in card.text and "Меньше всего" in card.text
        assert "card-next" in card.text
        assert "Рейтинг" in card.text and "Сравнить с официальным" in card.text
        # Под второй карточкой того же ряда подсказки не повторяются.
        assert "card-next" not in _card(member_client, study, chosen).text

        per_capita = next(
            record for record in _version(dataset).series.all() if record.derived == "per100000"
        )
        per_key = f"u:{dataset.code}:{per_capita.code}"
        assert f'value="{per_key}"' in card.text
        # Пересчёт на жителя уже на поле — подсказки о нём нет.
        _edit(member_client, study, action="add", do="view:map", series=per_key)
        study.refresh_from_db()
        assert f'value="{per_key}"' not in _card(member_client, study, notable).text

    def test_table_colour(self, member_client: Client, warehouse: Any) -> None:
        """
        Таблица по годам раскрашивается по значению по выбору владельца; прежняя карточка
        «Тепловая таблица» открывается раскрашенной таблицей.
        """
        dataset = _built(member_client, "crime_wide.csv")
        key = _key(dataset, _first(dataset))
        study = _study(member_client)
        _edit(member_client, study, action="add", do="view:table", series=key)
        study.refresh_from_db()
        table = studies.blocks_of(study)[0]
        card = _card(member_client, study, table)
        assert "data-table--years" in card.text and "data-step=" not in card.text

        _edit(member_client, study, action="change", block=table["id"], colour="value")
        study.refresh_from_db()
        assert studies.blocks_of(study)[0]["colour"] == "value"
        card = _card(member_client, study, table)
        assert 'data-step="' in card.text and "scale-legend" in card.text

        legacy = {"id": "old1", "kind": "answer", "question": "heat", "series": key, "wide": True}
        study.blocks = [*study.blocks, legacy]
        study.save(update_fields=["blocks"])
        converted = studies.blocks_of(study)[-1]
        assert converted["kind"] == "view" and converted["target"] == "table"
        assert converted["colour"] == "value" and converted["wide"]
        assert 'data-step="' in _card(member_client, study, converted).text

    def test_convergence_answer_agrees_with_tool(
        self, member_client: Client, warehouse: Any
    ) -> None:
        """«Сближаются ли регионы»: вывод карточки — тот же, что в инструменте, куда она ведёт."""
        from apps.catalog.models import Series
        from tests.support import synthetic

        # Сближение — по уровню на жителя: численность населения (сумма) вывода не даёт.
        population = Series.objects.get(indicator__code=synthetic.POPULATION_CODE).key
        study = _study(member_client)
        _edit(member_client, study, action="add", do="answer:convergence", series=population)
        study.refresh_from_db()
        refused = _card(member_client, study, studies.blocks_of(study)[0])
        # Основа численности — площадь: причина ведёт к пересчёту на км².
        assert "на км²" in refused.text
        key = Series.objects.get(indicator__code=synthetic.GRP_PER_CAPITA_CODE).key
        study = _study(member_client)
        _edit(member_client, study, action="add", do="answer:convergence", series=key)
        study.refresh_from_db()
        block = studies.blocks_of(study)[0]
        card = _card(member_client, study, block)
        assert "изменение разброса" in card.text
        link = re.search(r'href="([^"]*/analytics/convergence/[^"]*)"', card.text)
        assert link is not None
        tool = member_client.get(link.group(1).replace("&amp;", "&"))
        trend = tool.context["sigma_trend"]
        expected = (
            "сокращается"
            if trend["converging"]
            else "растёт"
            if trend["diverging"]
            else "без устойчивого изменения"
        )
        assert f"<b>{expected}</b>" in card.text

    def test_settings_only_what_changes_the_card(
        self, member_client: Client, warehouse: Any
    ) -> None:
        """
        «Настроить» показывает только то, что меняет карточку: у лидеров нет отмеченных
        регионов, у сближения и связи с другими показателями нет ни года, ни регионов —
        и кнопки; у графиков по регионам регионы есть. Неуместное поле формы не сохраняется.
        """
        dataset = _built(member_client)
        key = _key(dataset, _first(dataset))
        study = _study(member_client)
        for question in ("leaders", "convergence", "related", "multiples"):
            _edit(member_client, study, action="add", do=f"answer:{question}", series=key)
        study.refresh_from_db()
        blocks = {block["question"]: block for block in studies.blocks_of(study)}

        leaders = _card(member_client, study, blocks["leaders"]).text
        assert 'name="year"' in leaders and 'name="regions"' not in leaders
        for question in ("convergence", "related"):
            assert "card-settings" not in _card(member_client, study, blocks[question]).text
        assert 'name="regions"' in _card(member_client, study, blocks["multiples"]).text

        _edit(
            member_client,
            study,
            action="configure",
            block=blocks["leaders"]["id"],
            regions="own",
            territory=["RU-MOW"],
        )
        _edit(
            member_client,
            study,
            action="configure",
            block=blocks["convergence"]["id"],
            year="2019",
        )
        study.refresh_from_db()
        saved = {block["question"]: block for block in studies.blocks_of(study)}
        assert "territories" not in saved["leaders"]
        assert "year" not in saved["convergence"]

    def test_small_multiples_by_districts(self, member_client: Client, warehouse: Any) -> None:
        """
        Малые графики: клетки по округам с полными названиями, во всю ширину поля;
        шкала — общая, по выбору своя у каждой клетки, и выбор хранится в карточке.
        """
        dataset = _built(member_client, "crime_wide.csv")
        key = _key(dataset, _first(dataset))
        study = _study(member_client)
        page = member_client.get(reverse("userdata:study", args=[study.public_id]), {"series": key})
        assert 'value="answer:multiples"' in page.text
        _edit(member_client, study, action="add", do="answer:multiples", series=key)
        study.refresh_from_db()
        block = studies.blocks_of(study)[0]
        assert block["question"] == "multiples"
        assert block["wide"] is True

        card = _card(member_client, study, block)
        assert card.status_code == 200
        assert "multiples-cell" in card.text
        assert "федеральный округ" in card.text
        # Шкала — в «Виде» карточки.
        assert 'name="scale" value="common" checked' in card.text

        _edit(member_client, study, action="configure", block=block["id"], scale="own")
        study.refresh_from_db()
        block = studies.blocks_of(study)[0]
        assert block["scale"] == "own"
        assert 'name="scale" value="own" checked' in _card(member_client, study, block).text

        # Чужое значение шкалы не принимается.
        _edit(member_client, study, action="change", block=block["id"], scale="<b>")
        study.refresh_from_db()
        assert studies.blocks_of(study)[0]["scale"] == "own"

    def test_partners_offered_for_comparison(self, member_client: Client, warehouse: Any) -> None:
        # Связь — у относительных величин: суммы сравниваются через показатель на жителя.
        dataset = _built(member_client, "crime_wide.csv", kind="relative")
        records = list(dataset.current_version.series.filter(derived="").order_by("order"))
        # Два разных показателя: пересчёт того же ряда к сравнению не предлагается.
        first = next(record for record in records if record.indicator == records[0].indicator)
        second = next(record for record in records if record.indicator != first.indicator)
        first, second = _key(dataset, first), _key(dataset, second)
        study = _study(member_client)
        _edit(member_client, study, action="add", do="view:map", series=second)
        page = member_client.get(
            reverse("userdata:study", args=[study.public_id]), {"series": first}
        )
        # Ряд карточки поля — сразу «Сравнить с …»; второй показатель можно и выбрать.
        assert f'value="compare:{second}"' in page.text
        assert "Выбрать второй показатель" in page.text
        picked = member_client.get(
            reverse("userdata:study", args=[study.public_id]), {"series": first, "with": second}
        )
        assert 'value="answer:comparison"' in picked.text
        assert 'value="answer:relation"' in picked.text
        # Окно выбора ряда сайта при выборе второго показателя ставит его вторым.
        pick = member_client.get(
            reverse("userdata:study", args=[study.public_id]), {"series": first, "pick": "1"}
        )
        picker = pick.text[pick.text.index('id="study-site-picker"') :]
        assert 'name="with"' in picker
        assert pick.text.index('id="study-site-picker"') < pick.text.index("</aside>")

    def test_drop_on_card_compares(self, member_client: Client, warehouse: Any) -> None:
        from apps.catalog.models import Series

        dataset = _built(member_client)
        key = _key(dataset, _first(dataset))
        site = Series.objects.exclude(key__startswith="u:").order_by("key").first()
        assert site is not None
        study = _study(member_client)
        _edit(member_client, study, action="add", do="view:map", series=key)
        _edit(member_client, study, action="add-text", text="Заметка")
        # Брошенный на карточку ряд — сравнение следом за ней.
        _edit(
            member_client,
            study,
            action="add",
            do="answer:comparison",
            series=key,
            other=site.key,
            at="1",
        )
        study.refresh_from_db()
        blocks = studies.blocks_of(study)
        assert [block.get("question") or block["kind"] for block in blocks] == [
            "view",
            "comparison",
            "text",
        ]
        assert blocks[1]["other"] == site.key
        page = member_client.get(reverse("userdata:study", args=[study.public_id]))
        assert f'data-series="{key}"' in page.text
        assert "data-card-drop" in page.text

    def test_relation_needs_second_series(self, member_client: Client, warehouse: Any) -> None:
        dataset = _built(member_client)
        key = _key(dataset, _first(dataset))
        study = _study(member_client)
        _edit(member_client, study, action="add", do="answer:relation", series=key)
        study.refresh_from_db()
        assert studies.blocks_of(study) == []

    def test_compute_from_menu(self, member_client: Client, warehouse: Any) -> None:
        from apps.userdata.models import DatasetSeries

        dataset = _built(member_client, "crime_wide.csv")
        record = _first(dataset)
        study = _study(member_client)
        response = _edit(
            member_client, study, action="add", do="compute:per1000", series=_key(dataset, record)
        )
        assert response.status_code == 302
        derived = DatasetSeries.objects.get(
            version__dataset=dataset, version=dataset.current_version_id, code=f"{record.code}-p1k"
        )
        assert derived.unit.endswith("на 1 000 жителей")
        study.refresh_from_db()
        (block,) = studies.blocks_of(study)
        assert block["parameters"]["series"] == f"u:{dataset.code}:{record.code}-p1k"
        card = _card(member_client, study, block)
        assert "geo-map" in card.text or "tile-map" in card.text
        # Собранный пересчёт не пересобирается: карточка ставится сразу.
        version = derived.version
        built_at = version.updated_at
        _edit(
            member_client, study, action="add", do="compute:per1000", series=_key(dataset, record)
        )
        version.refresh_from_db()
        assert version.updated_at == built_at
        assert len(studies.blocks_of(Study.objects.get(pk=study.pk))) == 2

    def test_compute_refused_for_relative(self, member_client: Client, warehouse: Any) -> None:
        dataset = _built(member_client)
        study = _study(member_client)
        _edit(
            member_client,
            study,
            action="add",
            do="compute:per100000",
            series=_key(dataset, _first(dataset)),
        )
        study.refresh_from_db()
        assert studies.blocks_of(study) == []

    def test_per_capita_unit_names_its_base(self, member_client: Client, warehouse: Any) -> None:
        from apps.userdata.models import DatasetSeries

        dataset = _built(member_client, "crime_wide.csv", per=("per100000",))
        derived = DatasetSeries.objects.filter(
            version__dataset=dataset, derived=DatasetSeries.Derived.PER_100000
        ).first()
        assert derived is not None
        assert derived.unit.endswith("на 100 000 жителей")

    def test_remove_and_undo(self, member_client: Client, warehouse: Any) -> None:
        study = _study(member_client)
        _edit(member_client, study, action="add-text", text="Первая")
        study.refresh_from_db()
        (block,) = studies.blocks_of(study)
        response = _edit(member_client, study, action="remove", block=block["id"])
        page = member_client.get(response["Location"])
        assert "Вернуть" in page.text
        _edit(member_client, study, action="undo")
        study.refresh_from_db()
        assert [item["id"] for item in studies.blocks_of(study)] == [block["id"]]

    def test_scripted_reorder_is_silent(self, member_client: Client, warehouse: Any) -> None:
        study = _study(member_client)
        for text in ("Первая", "Вторая"):
            _edit(member_client, study, action="add-text", text=text)
        study.refresh_from_db()
        first, second = (block["id"] for block in studies.blocks_of(study))
        response = member_client.post(
            reverse("userdata:study-edit", args=[study.public_id]),
            {"action": "reorder", "order": [second, first]},
            headers={"X-Study": "1"},
        )
        assert response.status_code == 204
        study.refresh_from_db()
        assert [block["id"] for block in studies.blocks_of(study)] == [second, first]

    def test_drop_at_position(self, member_client: Client, warehouse: Any) -> None:
        dataset = _built(member_client)
        study = _study(member_client)
        _edit(member_client, study, action="add-text", text="Заметка")
        _edit(
            member_client,
            study,
            action="add",
            do="view:map",
            series=_key(dataset, _first(dataset)),
            at="0",
        )
        study.refresh_from_db()
        assert [block["kind"] for block in studies.blocks_of(study)] == ["view", "text"]

    def test_edits_are_rate_limited(
        self, member_client: Client, settings: Any, warehouse: Any
    ) -> None:
        from django.core.cache import cache

        study = _study(member_client)
        settings.USERDATA_STUDY_EDITS = 2
        cache.clear()
        statuses = [
            member_client.post(
                reverse("userdata:study-edit", args=[study.public_id]),
                {"action": "rename", "title": f"Название {number}"},
                headers={"X-Study": "1"},
            ).status_code
            for number in range(3)
        ]
        assert statuses == [204, 204, 429]

    def test_guest_study_limit(self, client: Client, settings: Any, warehouse: Any) -> None:
        settings.USERDATA_GUEST_MAX_STUDIES = 1
        client.post(reverse("userdata:study-new"), {"title": "Первое"})
        client.post(reverse("userdata:study-new"), {"title": "Второе"})
        assert Study.objects.count() == 1
