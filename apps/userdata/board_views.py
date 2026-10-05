"""
Доски: создание, страница доски с общим годом и территориями, карточки фрагментами,
правка блоков, удаление и «На доску» со страниц холста и инструментов.

Доску видит владелец и читатель её закрытой ссылки (только чтение); чужая — 404.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.http import Http404, HttpRequest, HttpResponse
from django.http.response import HttpResponseBase
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView, View

from apps.catalog.selectors import MAX_COMPARE, grouped_territories
from apps.core.navigation import Crumb
from apps.core.utils.redirects import safe_back
from apps.core.views import BreadcrumbMixin
from apps.warehouse.duckdb_client import WarehouseNotBuiltError
from apps.warehouse.queries import year_counts
from apps.workspace.constants import QUERY_TARGETS_BY_CODE

from . import board_cards, boards, shares
from .models import Board


def readable_board(request: HttpRequest, public_id: Any) -> tuple[Board, bool]:
    """Доска владельца или открытая закрытой ссылкой; вторым — владелец ли читает."""
    board = Board.objects.filter(public_id=public_id).first()
    if board is None:
        raise Http404
    if request.user.is_authenticated and board.owner_id == request.user.pk:
        return board, True
    if shares.opened_board(request, board) is not None:
        return board, False
    raise Http404


def _common(request: HttpRequest, board: Board) -> tuple[int | None, list[str]]:
    """Общий год и территории: из адреса (просмотр) или сохранённые на доске."""
    year: int | None = board.year
    territories = list(board.territories or [])
    if "year" in request.GET:
        raw = request.GET.get("year", "")
        year = int(raw) if raw.isdigit() else None
    if "territory" in request.GET or request.GET.get("common") == "1":
        territories = [code for code in request.GET.getlist("territory") if code][:MAX_COMPARE]
    return year, territories


class BoardCreateView(View):
    """Новая доска учётной записи."""

    def post(self, request: HttpRequest) -> HttpResponse:
        if not request.user.is_authenticated:
            return redirect_to_login(reverse("userdata:index"))
        try:
            board = boards.create(request.user, request.POST.get("title", ""))
        except boards.BoardError as error:
            messages.error(request, str(error))
            return redirect(f"{reverse('userdata:index')}#own-boards")
        return redirect("userdata:board", public_id=board.public_id)


class BoardView(BreadcrumbMixin, TemplateView):
    """Доска: тексты и карточки по порядку; владельцу — правка, общий выбор и ссылки."""

    template_name = "userdata/board.html"

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        self.board, self.owner = readable_board(request, kwargs["public_id"])
        return super().dispatch(request, *args, **kwargs)

    def get_crumbs(self) -> tuple[Crumb, ...]:
        if not self.owner:
            return (Crumb(title=self.board.title),)
        return (
            Crumb(title=_("Свои данные"), url=f"{reverse('userdata:index')}#own-boards"),
            Crumb(title=self.board.title),
        )

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        from .share_views import share_context

        context = super().get_context_data(**kwargs)
        board = self.board
        year, territories = _common(self.request, board)
        pairs = [("common", "1")]
        if year is not None:
            pairs.append(("year", str(year)))
        pairs.extend(("territory", code) for code in territories)
        common = urlencode(pairs)
        blocks = boards.blocks_of(board)
        for block in blocks:
            if block["kind"] == boards.VIEW:
                url = reverse("userdata:board-card", args=[board.public_id, block["id"]])
                block["card_url"] = f"{url}?{common}"
                block["target_title"] = QUERY_TARGETS_BY_CODE[block["target"]].title
        context.update(
            page_title=board.title,
            board=board,
            owner=self.owner,
            blocks=blocks,
            year=year,
            territories=territories,
            years=_years(blocks),
            territory_groups=grouped_territories(),
            max_compare=MAX_COMPARE,
            canvas_count=sum(
                1
                for block in blocks
                if block["kind"] == boards.VIEW and block["target"] in boards.CANVAS_TARGETS
            ),
            shared=shares.of(board).exists() if self.owner else True,
        )
        if self.owner:
            context.update(share_context(self.request, board))
        return context


def _years(blocks: list[dict[str, Any]]) -> list[int]:
    """Годы рядов карточек холста — для общего выбора года."""
    found: set[int] = set()
    for block in blocks:
        if block["kind"] != boards.VIEW or block["target"] not in boards.CANVAS_TARGETS:
            continue
        for key in boards.series_keys(block):
            try:
                found.update(int(year) for year in year_counts(key))
            except WarehouseNotBuiltError:
                return []
    return sorted(found, reverse=True)


class BoardCardView(View):
    """Карточка доски фрагментом: вид строится заново на текущих данных."""

    def get(self, request: HttpRequest, public_id: str, block_id: str) -> HttpResponse:
        board, _owner = readable_board(request, public_id)
        block = next(
            (
                item
                for item in boards.blocks_of(board)
                if item["id"] == block_id and item["kind"] == boards.VIEW
            ),
            None,
        )
        if block is None:
            raise Http404
        year, territories = _common(request, board)
        card = board_cards.render(request, block, year=year, territories=territories)
        # Имена вида — и на верхнем уровне: карта и легенда холста включаются как есть.
        response = render(
            request, "userdata/cards/_card.html", {**card, "card": card, "board": board}
        )
        response["Cache-Control"] = "private, no-store"
        return response


class BoardEditView(View):
    """Правка доски владельцем: название, блоки, порядок, общий выбор, удаление."""

    def post(self, request: HttpRequest, public_id: str) -> HttpResponse:
        board = boards.owned(request.user).filter(public_id=public_id).first()
        if board is None:
            raise Http404
        action = request.POST.get("action", "")
        block_id = request.POST.get("block", "")
        anchor = f"#block-{block_id}" if block_id else ""
        try:
            if action == "rename":
                board.title = (request.POST.get("title", "").strip() or board.title)[
                    : boards.TITLE_LENGTH
                ]
                board.description = request.POST.get("description", "").strip()[
                    : boards.TEXT_LENGTH
                ]
                board.save(update_fields=["title", "description", "updated_at"])
            elif action == "add-text":
                block = boards.add_text(board, request.POST.get("text", ""))
                anchor = f"#block-{block['id']}"
            elif action == "change":
                boards.change(
                    board,
                    block_id,
                    **{
                        name: request.POST[name]
                        for name in ("text", "title", "note")
                        if name in request.POST
                    },
                )
            elif action in {"up", "down"}:
                boards.move(board, block_id, -1 if action == "up" else 1)
            elif action == "remove":
                boards.remove(board, block_id)
                anchor = ""
            elif action == "common":
                raw = request.POST.get("year", "")
                boards.set_common(
                    board, int(raw) if raw.isdigit() else None, request.POST.getlist("territory")
                )
                messages.success(request, gettext("Общий выбор сохранён на доске."))
            elif action == "delete":
                if not request.POST.get("confirm"):
                    messages.error(request, gettext("Отметьте, что доску нужно удалить."))
                else:
                    title = board.title
                    board.delete()
                    messages.success(
                        request, gettext("Доска «%(title)s» удалена.") % {"title": title}
                    )
                    return redirect(f"{reverse('userdata:index')}#own-boards")
        except boards.BoardError as error:
            messages.error(request, str(error))
        return redirect(f"{reverse('userdata:board', args=[board.public_id])}{anchor}")


def _own_board(request: HttpRequest, public_id: str) -> Board:
    """Своя доска по опознавателю из формы; чужая и неверная — 404."""
    try:
        identifier = UUID(public_id)
    except ValueError as error:
        raise Http404 from error
    found = boards.owned(request.user).filter(public_id=identifier).first()
    if found is None:
        raise Http404
    return found


class BoardAddView(View):
    """«На доску» со страницы холста или инструмента: вид с параметрами — новой карточкой."""

    def post(self, request: HttpRequest) -> HttpResponse:
        back = safe_back(request, reverse("core:home"))
        if not request.user.is_authenticated:
            return redirect_to_login(back)
        target = request.POST.get("target", "")
        chosen = request.POST.get("board", "")
        try:
            if chosen == "new" or not chosen:
                board = boards.create(request.user, request.POST.get("new_title", ""))
            else:
                board = _own_board(request, chosen)
            boards.add_view(board, target, request.POST.get("query_string", ""))
        except boards.BoardError as error:
            messages.error(request, str(error))
            return redirect(back)
        url = reverse("userdata:board", args=[board.public_id])
        messages.success(
            request,
            gettext("Вид добавлен на доску «%(title)s».") % {"title": board.title},
        )
        if request.POST.get("open") == "1":
            return redirect(url)
        return redirect(back)
