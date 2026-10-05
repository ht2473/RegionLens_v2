"""
Закрытые ссылки на таблицу или доску: только чтение без входа, срок, отзыв, разрешение
скачивать.

Токен — случайные 24 знака; в базе — его отпечаток SHA-256, поэтому ссылку видно только
при создании. Открытая ссылка запоминается в сеансе читателя: слой рядов (``scope``)
пускает его к таблице ссылки, а у доски — к таблицам её владельца, ряды которых стоят
на доске. Подбор токена ограничен числом неудачных попыток с адреса.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.db.models import F, QuerySet
from django.http import HttpRequest
from django.utils import timezone

from .models import Board, Dataset, Share

SESSION_KEY = "userdata_shares"
# Ссылка, созданная последней: показывается владельцу один раз после перехода.
NEW_SESSION_KEY = "userdata_new_share"
TOKEN_BYTES = 18
# Сроки на выбор, суток; по умолчанию — месяц.
TERMS = (7, 30, 90, 365)
DEFAULT_TERM = 30
# Сколько открытых ссылок помнит сеанс читателя.
SESSION_LIMIT = 20


class ShareError(Exception):
    """Ссылку не создать: предел или гость."""


def fingerprint(token: str) -> str:
    """Отпечаток токена для хранения и поиска."""
    return hashlib.sha256(token.encode()).hexdigest()


def active() -> QuerySet[Share]:
    """Действующие ссылки: не отозваны и не истекли."""
    return Share.objects.filter(revoked_at__isnull=True, expires_at__gt=timezone.now())


def create(
    target: Dataset | Board, *, days: int = DEFAULT_TERM, downloads: bool = False
) -> tuple[Share, str]:
    """Создать ссылку; вернуть её и токен — его больше нигде не будет."""
    days = days if days in TERMS else DEFAULT_TERM
    field = "dataset" if isinstance(target, Dataset) else "board"
    if active().filter(**{field: target}).count() >= settings.USERDATA_MAX_SHARES:
        raise ShareError
    token = secrets.token_urlsafe(TOKEN_BYTES)
    share = Share.objects.create(
        **{field: target},
        token_hash=fingerprint(token),
        expires_at=timezone.now() + timedelta(days=days),
        downloads=downloads,
    )
    return share, token


def find(token: str) -> Share | None:
    """Действующая ссылка по токену."""
    if not token or len(token) > 64:  # noqa: PLR2004 — токен короче
        return None
    return active().select_related("dataset", "board").filter(token_hash=fingerprint(token)).first()


def remember(request: HttpRequest, share: Share) -> None:
    """Запомнить открытую ссылку в сеансе читателя и отметить открытие."""
    opened = [pk for pk in request.session.get(SESSION_KEY, []) if pk != share.pk]
    opened.append(share.pk)
    request.session[SESSION_KEY] = opened[-SESSION_LIMIT:]
    Share.objects.filter(pk=share.pk).update(
        opened_count=F("opened_count") + 1, last_opened_at=timezone.now()
    )


def opened(request: HttpRequest) -> list[Share]:
    """Действующие ссылки, открытые в этом сеансе."""
    if not hasattr(request, "session"):
        return []
    ids = request.session.get(SESSION_KEY) or []
    if not ids:
        return []
    return list(active().select_related("dataset", "board").filter(pk__in=ids))


def readable(shares: list[Share]) -> dict[str, bool]:
    """
    Коды таблиц, открытых ссылками, и можно ли их скачивать. Доска открывает таблицы
    своего владельца, ряды которых на ней стоят; чужой ключ на доске ничего не открывает.
    """
    from .boards import dataset_codes

    found: dict[str, bool] = {}
    for share in shares:
        if share.dataset is not None:
            codes = {share.dataset.code}
        elif share.board is not None:
            codes = set(
                Dataset.objects.filter(
                    owner_id=share.board.owner_id, code__in=dataset_codes(share.board)
                ).values_list("code", flat=True)
            )
        else:
            codes = set()
        for code in codes:
            found[code] = found.get(code, False) or share.downloads
    return found


def opened_board(request: HttpRequest, board: Board) -> Share | None:
    """Ссылка этого сеанса, по которой открыта доска."""
    return next((share for share in opened(request) if share.board_id == board.pk), None)


def opened_dataset(request: HttpRequest, dataset: Dataset) -> Share | None:
    """Ссылка этого сеанса, по которой открыта таблица: своя или доски с её рядами."""
    shares = opened(request)
    direct = next((share for share in shares if share.dataset_id == dataset.pk), None)
    if direct is not None:
        return direct
    return next(
        (share for share in shares if share.board_id and dataset.code in readable([share])),
        None,
    )


def of(target: Dataset | Board) -> QuerySet[Share]:
    """Действующие ссылки таблицы или доски — для владельца."""
    field = "dataset" if isinstance(target, Dataset) else "board"
    return active().filter(**{field: target}).order_by("-created_at")


def is_shared(dataset: Dataset) -> bool:
    """Таблицу видно по закрытой ссылке: своей или доски, на которой стоят её ряды."""
    if of(dataset).exists():
        return True
    if dataset.owner_id is None:
        return False
    from .boards import dataset_codes

    boards = Board.objects.filter(owner_id=dataset.owner_id, shares__in=active()).distinct()
    return any(dataset.code in dataset_codes(board) for board in boards)


def pop_new(request: HttpRequest, target: Dataset | Board) -> str:
    """Адрес только что созданной ссылки — один раз, для этой таблицы или доски."""
    stored: dict[str, Any] = request.session.get(NEW_SESSION_KEY) or {}
    if stored.get("target") != str(target.public_id):
        return ""
    request.session.pop(NEW_SESSION_KEY, None)
    return str(stored.get("url", ""))


def stash_new(request: HttpRequest, target: Dataset | Board, token: str) -> None:
    """Запомнить адрес новой ссылки до следующей страницы владельца."""
    from django.urls import reverse

    url = request.build_absolute_uri(reverse("share-open", args=[token]))
    request.session[NEW_SESSION_KEY] = {"target": str(target.public_id), "url": url}


def revoke(share: Share) -> None:
    """Отозвать ссылку: по ней больше ничего не открывается."""
    Share.objects.filter(pk=share.pk, revoked_at__isnull=True).update(revoked_at=timezone.now())
