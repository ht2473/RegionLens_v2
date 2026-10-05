"""
Доски: тексты и карточки видов холста и инструментов по порядку, общий год и территории.

Карточка хранит страницу и её параметры, как сохранённый вид кабинета (``SavedQuery``):
при открытии доски вид строится заново на текущих данных. Блок — словарь с опознавателем
``id``; вид ``text`` несёт текст, вид ``view`` — страницу ``target`` из перечня
``QUERY_TARGETS``, параметры, название и пояснение.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterable
from typing import Any

from django.conf import settings
from django.db.models import QuerySet
from django.utils.translation import gettext as _

from apps.warehouse import routing
from apps.workspace.constants import QUERY_TARGETS_BY_CODE
from apps.workspace.models import parameter_series_keys
from apps.workspace.services import parse_query_string

from .models import Board

TEXT = "text"
VIEW = "view"
TITLE_LENGTH = 200
TEXT_LENGTH = 4000
NOTE_LENGTH = 500
# Представления холста: у них общий год и территории доски.
CANVAS_TARGETS = ("map", "compare", "rankings", "distribution", "table")


class BoardError(Exception):
    """Доску не изменить: предел или неизвестная страница."""


def owned(user: Any) -> QuerySet[Board]:
    """Доски учётной записи; у гостя досок нет."""
    if user is None or not getattr(user, "is_authenticated", False):
        return Board.objects.none()
    return Board.objects.filter(owner=user)


def create(user: Any, title: str) -> Board:
    """Новая доска учётной записи."""
    if owned(user).count() >= settings.USERDATA_MAX_BOARDS:
        raise BoardError(
            _("Досок уже %(count)s — это предел. Удалите ненужные доски.")
            % {"count": settings.USERDATA_MAX_BOARDS}
        )
    return Board.objects.create(owner=user, title=(title.strip() or _("Доска"))[:TITLE_LENGTH])


def blocks_of(board: Board) -> list[dict[str, Any]]:
    """Блоки доски по порядку; повреждённые и с неизвестной страницей пропускаются."""
    found = []
    for block in board.blocks or []:
        if not isinstance(block, dict) or not block.get("id"):
            continue
        if block.get("kind") == TEXT and isinstance(block.get("text"), str):
            found.append(block)
        elif block.get("kind") == VIEW and block.get("target") in QUERY_TARGETS_BY_CODE:
            block.setdefault("parameters", {})
            found.append(block)
    return found


def series_keys(block: dict[str, Any]) -> list[str]:
    """Ключи рядов карточки вида."""
    if block.get("kind") != VIEW:
        return []
    return parameter_series_keys(block.get("parameters"))


def dataset_codes(board: Board) -> set[str]:
    """Коды таблиц, ряды которых стоят на доске."""
    return {
        routing.dataset_code(key)
        for block in blocks_of(board)
        for key in series_keys(block)
        if routing.is_user_key(key)
    }


def add_view(board: Board, target: str, query_string: str, *, title: str = "") -> dict[str, Any]:
    """Поставить на доску карточку вида страницы с её параметрами."""
    if target not in QUERY_TARGETS_BY_CODE:
        raise BoardError(_("Этот вид на доску не ставится."))
    return _append(
        board,
        {
            "kind": VIEW,
            "target": target,
            "parameters": parse_query_string(query_string),
            "title": title.strip()[:TITLE_LENGTH],
            "note": "",
        },
    )


def add_text(board: Board, text: str) -> dict[str, Any]:
    """Поставить на доску текстовый блок."""
    return _append(board, {"kind": TEXT, "text": text.strip()[:TEXT_LENGTH]})


def _append(board: Board, block: dict[str, Any]) -> dict[str, Any]:
    blocks = blocks_of(board)
    if len(blocks) >= settings.USERDATA_BOARD_BLOCKS:
        raise BoardError(
            _("На доске уже %(count)s блоков — это предел.")
            % {"count": settings.USERDATA_BOARD_BLOCKS}
        )
    block = {"id": secrets.token_hex(4), **block}
    board.blocks = [*blocks, block]
    board.save(update_fields=["blocks", "updated_at"])
    return block


def change(board: Board, block_id: str, **fields: str) -> bool:
    """Изменить текст, название или пояснение блока; вернуть, нашёлся ли блок."""
    limits = {"text": TEXT_LENGTH, "title": TITLE_LENGTH, "note": NOTE_LENGTH}
    blocks = blocks_of(board)
    for block in blocks:
        if block["id"] != block_id:
            continue
        for name, value in fields.items():
            if name in limits and (name != "text" or block["kind"] == TEXT):
                block[name] = str(value).strip()[: limits[name]]
        board.blocks = blocks
        board.save(update_fields=["blocks", "updated_at"])
        return True
    return False


def move(board: Board, block_id: str, step: int) -> None:
    """Сдвинуть блок выше (−1) или ниже (+1)."""
    blocks = blocks_of(board)
    index = next((at for at, block in enumerate(blocks) if block["id"] == block_id), None)
    if index is None:
        return
    target = index + (1 if step > 0 else -1)
    if 0 <= target < len(blocks):
        blocks[index], blocks[target] = blocks[target], blocks[index]
        board.blocks = blocks
        board.save(update_fields=["blocks", "updated_at"])


def remove(board: Board, block_id: str) -> None:
    """Убрать блок с доски."""
    board.blocks = [block for block in blocks_of(board) if block["id"] != block_id]
    board.save(update_fields=["blocks", "updated_at"])


def set_common(board: Board, year: int | None, territories: Iterable[str]) -> None:
    """Общий год и территории для карточек холста."""
    from apps.catalog.models import Territory
    from apps.catalog.selectors import MAX_COMPARE

    wanted = list(dict.fromkeys(str(code) for code in territories))
    known = set(Territory.objects.filter(code__in=wanted).values_list("code", flat=True))
    board.year = year
    board.territories = [code for code in wanted if code in known][:MAX_COMPARE]
    board.save(update_fields=["year", "territories", "updated_at"])
