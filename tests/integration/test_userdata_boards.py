"""
Доски: создание, «На доску» с холста и инструмента, карточки видов на текущих данных,
тексты и порядок, общий год и территории, закрытая ссылка на доску и доступ.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlencode

import pytest
from django.test import Client
from django.urls import reverse

from apps.userdata import boards
from apps.userdata.models import Board
from tests.support.userdata import built as _built
from tests.support.userdata import first_record as _first
from tests.support.userdata import key_of as _key
from tests.support.userdata import userdata_dir  # noqa: F401 — приспособление модуля

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

LINK = re.compile(r'value="(http://testserver/s/[A-Za-z0-9_-]+)"')


def _board(client: Client, title: str = "К курсовой") -> Board:
    response = client.post(reverse("userdata:board-new"), {"title": title})
    assert response.status_code == 302
    return Board.objects.latest("created_at")


def _add(client: Client, board: Board, target: str, query: dict[str, Any] | list[Any]) -> Any:
    return client.post(
        reverse("userdata:board-add"),
        {
            "target": target,
            "query_string": urlencode(query, doseq=True),
            "board": str(board.public_id),
            "back": reverse("maps:choropleth"),
        },
    )


def _card(client: Client, board: Board, block: dict[str, Any], **query: Any) -> Any:
    url = reverse("userdata:board-card", args=[board.public_id, block["id"]])
    return client.get(url, query)


class TestOwner:
    """Владелец собирает доску: виды, тексты, порядок, общий выбор, удаление."""

    def test_create_and_add_view(self, member_client: Client, warehouse: Any) -> None:
        dataset = _built(member_client)
        key = _key(dataset, _first(dataset))
        board = _board(member_client)
        response = _add(member_client, board, "map", {"series": key, "year": "2020"})
        assert response.status_code == 302
        board.refresh_from_db()
        (block,) = boards.blocks_of(board)
        assert block["target"] == "map"
        assert block["parameters"]["series"] == key
        page = member_client.get(reverse("userdata:board", args=[board.public_id]))
        assert page.status_code == 200
        assert 'name="robots" content="noindex' in page.text
        card = _card(member_client, board, block)
        assert card.status_code == 200
        assert "geo-map" in card.text or "tile-map" in card.text
        assert f"b{block['id']}" in card.text
        assert "загружены пользователем" in card.text

    def test_two_maps_have_own_ids(self, member_client: Client, warehouse: Any) -> None:
        dataset = _built(member_client)
        key = _key(dataset, _first(dataset))
        board = _board(member_client)
        _add(member_client, board, "map", {"series": key, "year": "2020"})
        _add(member_client, board, "map", {"series": key, "year": "2021"})
        board.refresh_from_db()
        first, second = boards.blocks_of(board)
        ids_first = set(re.findall(r'id="([^"]+)"', _card(member_client, board, first).text))
        ids_second = set(re.findall(r'id="([^"]+)"', _card(member_client, board, second).text))
        assert ids_first
        assert not ids_first & ids_second

    def test_every_canvas_view_and_tool(self, member_client: Client, warehouse: Any) -> None:
        dataset = _built(member_client)
        key = _key(dataset, _first(dataset))
        board = _board(member_client)
        for target in boards.CANVAS_TARGETS:
            _add(member_client, board, target, {"series": key, "territory": ["RU-TA", "RU-MOW"]})
        _add(member_client, board, "correlation", {"x": key, "y": key, "year": "2020"})
        board.refresh_from_db()
        for block in boards.blocks_of(board):
            card = _card(member_client, board, block)
            assert card.status_code == 200, block["target"]
            assert "Открыть вид" in card.text, block["target"]
        tool = boards.blocks_of(board)[-1]
        assert "analytics/correlation" in _card(member_client, board, tool).text

    def test_text_order_and_remove(self, member_client: Client, warehouse: Any) -> None:
        board = _board(member_client)
        edit = reverse("userdata:board-edit", args=[board.public_id])
        member_client.post(edit, {"action": "add-text", "text": "Первый вывод"})
        member_client.post(edit, {"action": "add-text", "text": "Второй вывод"})
        board.refresh_from_db()
        first, second = boards.blocks_of(board)
        member_client.post(edit, {"action": "up", "block": second["id"]})
        board.refresh_from_db()
        assert [block["text"] for block in boards.blocks_of(board)] == [
            "Второй вывод",
            "Первый вывод",
        ]
        member_client.post(edit, {"action": "change", "block": first["id"], "text": "Иначе"})
        member_client.post(edit, {"action": "remove", "block": second["id"]})
        board.refresh_from_db()
        assert [block["text"] for block in boards.blocks_of(board)] == ["Иначе"]
        page = member_client.get(reverse("userdata:board", args=[board.public_id]))
        assert "Иначе" in page.text

    def test_common_year_and_territories(self, member_client: Client, warehouse: Any) -> None:
        dataset = _built(member_client)
        key = _key(dataset, _first(dataset))
        board = _board(member_client)
        _add(member_client, board, "table", {"series": key, "year": "2018"})
        edit = reverse("userdata:board-edit", args=[board.public_id])
        member_client.post(edit, {"action": "common", "year": "2020", "territory": ["RU-TA"]})
        board.refresh_from_db()
        assert board.year == 2020
        assert board.territories == ["RU-TA"]
        (block,) = boards.blocks_of(board)
        page = member_client.get(reverse("userdata:board", args=[board.public_id]))
        assert "year=2020" in page.text
        card = _card(member_client, board, block, common="1", year="2020", territory="RU-TA")
        assert "2020" in card.text
        assert card.text.count("<tr") == 2  # шапка и одна отмеченная территория

    def test_delete(self, member_client: Client, warehouse: Any) -> None:
        board = _board(member_client)
        edit = reverse("userdata:board-edit", args=[board.public_id])
        member_client.post(edit, {"action": "delete"})
        assert Board.objects.filter(pk=board.pk).exists()
        member_client.post(edit, {"action": "delete", "confirm": "1"})
        assert not Board.objects.filter(pk=board.pk).exists()

    def test_limits(self, member_client: Client, settings: Any, warehouse: Any) -> None:
        settings.USERDATA_MAX_BOARDS = 1
        _board(member_client)
        member_client.post(reverse("userdata:board-new"), {"title": "Вторая"})
        assert Board.objects.count() == 1
        settings.USERDATA_BOARD_BLOCKS = 1
        board = Board.objects.get()
        edit = reverse("userdata:board-edit", args=[board.public_id])
        member_client.post(edit, {"action": "add-text", "text": "раз"})
        member_client.post(edit, {"action": "add-text", "text": "два"})
        board.refresh_from_db()
        assert len(boards.blocks_of(board)) == 1

    def test_unknown_target_refused(self, member_client: Client, warehouse: Any) -> None:
        board = _board(member_client)
        _add(member_client, board, "admin", {"series": "x"})
        board.refresh_from_db()
        assert boards.blocks_of(board) == []

    def test_guest_has_no_boards(self, client: Client) -> None:
        response = client.post(reverse("userdata:board-new"), {"title": "x"})
        assert response.status_code == 302
        assert "login" in response["Location"]
        assert not Board.objects.exists()

    def test_save_popover_lists_boards(self, member_client: Client, warehouse: Any) -> None:
        board = _board(member_client, "Для редактора")
        page = member_client.get(reverse("maps:choropleth"))
        assert "Для редактора" in page.text
        assert str(board.public_id) in page.text

    def test_account_export(self, member_client: Client, member: Any, warehouse: Any) -> None:
        from apps.accounts.services import export_account

        board = _board(member_client)
        member_client.post(
            reverse("userdata:board-edit", args=[board.public_id]),
            {"action": "add-text", "text": "вывод"},
        )
        exported = export_account(member)
        assert exported["boards"][0]["title"] == "К курсовой"
        assert json.dumps(exported, ensure_ascii=False)


class TestShare:
    """Закрытая ссылка на доску: читатель видит карточки и таблицы на ней — и только их."""

    def _shared(self, client: Client, board: Board) -> str:
        client.post(reverse("userdata:board-share-create", args=[board.public_id]), {"days": "30"})
        page = client.get(reverse("userdata:board", args=[board.public_id]))
        found = LINK.search(page.text)
        assert found
        return found.group(1).removeprefix("http://testserver")

    def test_reader(self, member_client: Client, warehouse: Any) -> None:
        on_board = _built(member_client)
        off_board = _built(member_client)
        board = _board(member_client)
        key = _key(on_board, _first(on_board))
        _add(member_client, board, "map", {"series": key, "year": "2020"})
        path = self._shared(member_client, board)
        reader = Client()
        opened = reader.get(path)
        assert opened["Location"] == reverse("userdata:board", args=[board.public_id])
        page = reader.get(opened["Location"])
        assert page.status_code == 200
        assert "открыто по закрытой ссылке" in page.text
        assert reverse("userdata:board-edit", args=[board.public_id]) not in page.text
        board.refresh_from_db()
        (block,) = boards.blocks_of(board)
        card = _card(reader, board, block)
        assert card.status_code == 200
        assert "geo-map" in card.text or "tile-map" in card.text
        assert reader.get(reverse("maps:choropleth"), {"series": key}).status_code == 200
        assert reader.get(reverse("userdata:dataset", args=[on_board.public_id])).status_code == 200
        assert (
            reader.get(reverse("userdata:dataset", args=[off_board.public_id])).status_code == 404
        )
        edit = reverse("userdata:board-edit", args=[board.public_id])
        assert reader.post(edit, {"action": "add-text", "text": "x"}).status_code == 404

    def test_foreign_key_on_board_opens_nothing(
        self, member_client: Client, make_user: Any, warehouse: Any
    ) -> None:
        other = Client()
        other.force_login(make_user(email="other@example.com"))
        foreign = _built(other)
        foreign_key = _key(foreign, _first(foreign))
        board = _board(member_client)
        # Чужой ключ, вписанный в параметры вручную, ни к чему не даёт доступа.
        _add(member_client, board, "map", {"series": foreign_key})
        path = self._shared(member_client, board)
        reader = Client()
        reader.get(path)
        board.refresh_from_db()
        (block,) = boards.blocks_of(board)
        assert "Ряда этой карточки больше нет" in _card(reader, board, block).text
        assert reader.get(reverse("maps:choropleth"), {"series": foreign_key}).status_code == 404
        assert reader.get(reverse("userdata:dataset", args=[foreign.public_id])).status_code == 404

    def test_stranger(self, member_client: Client, make_user: Any, warehouse: Any) -> None:
        board = _board(member_client)
        member_client.post(
            reverse("userdata:board-edit", args=[board.public_id]),
            {"action": "add-text", "text": "вывод"},
        )
        other = Client()
        other.force_login(make_user(email="other@example.com"))
        assert other.get(reverse("userdata:board", args=[board.public_id])).status_code == 404
        response = other.post(
            reverse("userdata:board-edit", args=[board.public_id]),
            {"action": "delete", "confirm": "1"},
        )
        assert response.status_code == 404
        assert Board.objects.filter(pk=board.pk).exists()
        assert (
            other.post(
                reverse("userdata:board-share-create", args=[board.public_id]), {"days": "7"}
            ).status_code
            == 404
        )

    def test_revoked(self, member_client: Client, warehouse: Any) -> None:
        board = _board(member_client)
        path = self._shared(member_client, board)
        reader = Client()
        reader.get(path)
        share = board.shares.get()
        member_client.post(
            reverse("userdata:board-share-revoke", args=[board.public_id, share.public_id])
        )
        assert reader.get(reverse("userdata:board", args=[board.public_id])).status_code == 404
