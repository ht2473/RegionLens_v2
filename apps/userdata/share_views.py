"""
Закрытые ссылки: открыть по токену (адрес без языка), создать и отозвать — владельцу
таблицы или доски.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import translation
from django.utils.translation import gettext as _
from django.views.generic import View

from apps.core.client_ip import client_ip
from apps.core.throttle import allow, used

from . import access, boards, shares
from .models import Board, Dataset

# Окно счёта неудачных попыток открыть ссылку, секунд.
ATTEMPTS_WINDOW = 600
ATTEMPTS_SCOPE = "userdata-share"
# Ответ на подбор токена: слишком много попыток.
TOO_MANY = 429


class ShareOpenView(View):
    """
    Ссылка ``/s/<токен>``: запомнить её в сеансе и перейти на страницу таблицы или доски
    на языке читателя. Недействующая — 404 «Ссылка больше не действует»; подбор токена
    ограничен числом неудачных попыток с адреса.
    """

    def get(self, request: HttpRequest, token: str) -> HttpResponse:
        address = client_ip(request) or "unknown"
        if used(ATTEMPTS_SCOPE, address) >= settings.USERDATA_SHARE_ATTEMPTS:
            return _gone(request, status=TOO_MANY)
        share = shares.find(token)
        if share is None:
            allow(
                request,
                ATTEMPTS_SCOPE,
                limit=settings.USERDATA_SHARE_ATTEMPTS,
                window=ATTEMPTS_WINDOW,
            )
            return _gone(request, status=404)
        shares.remember(request, share)
        language = translation.get_language_from_request(request)
        with translation.override(language):
            if share.dataset is not None:
                url = reverse("userdata:dataset", args=[share.dataset.public_id])
            else:
                assert share.board is not None
                url = reverse("userdata:board", args=[share.board.public_id])
        response = redirect(url)
        response["Cache-Control"] = "private, no-store"
        response["X-Robots-Tag"] = "noindex, nofollow"
        return response


def _gone(request: HttpRequest, *, status: int) -> HttpResponse:
    response = render(
        request, "userdata/share_gone.html", {"throttled": status == TOO_MANY}, status=status
    )
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


class _ShareTargetMixin:
    """Таблица или доска владельца, к которой относится ссылка; чужая — 404."""

    request: HttpRequest

    def target(self, public_id: str) -> Dataset | Board:
        raise NotImplementedError

    def back(self, target: Dataset | Board) -> str:
        name = "userdata:dataset" if isinstance(target, Dataset) else "userdata:board"
        return f"{reverse(name, args=[target.public_id])}#own-shares"


class ShareCreateView(_ShareTargetMixin, View):
    """Создать ссылку: срок и разрешение скачивать; адрес показывается один раз."""

    def post(self, request: HttpRequest, public_id: str) -> HttpResponse:
        target = self.target(public_id)
        if not request.user.is_authenticated or getattr(target, "owner_id", None) is None:
            messages.error(request, _("Закрытые ссылки создаются после входа."))
            return redirect(self.back(target))
        try:
            days = int(request.POST.get("days", ""))
        except ValueError:
            days = shares.DEFAULT_TERM
        try:
            _share, token = shares.create(
                target, days=days, downloads=request.POST.get("downloads") == "on"
            )
        except shares.ShareError:
            messages.error(
                request,
                _("Действующих ссылок уже %(count)s — это предел. Отзовите ненужные.")
                % {"count": settings.USERDATA_MAX_SHARES},
            )
        else:
            shares.stash_new(request, target, token)
        return redirect(self.back(target))


class ShareRevokeView(_ShareTargetMixin, View):
    """Отозвать ссылку: по ней больше ничего не открывается."""

    def post(self, request: HttpRequest, public_id: str, share_id: str) -> HttpResponse:
        target = self.target(public_id)
        share = shares.of(target).filter(public_id=share_id).first()
        if share is not None:
            shares.revoke(share)
            messages.success(request, _("Ссылка отозвана."))
        return redirect(self.back(target))


class _DatasetTarget(_ShareTargetMixin):
    def target(self, public_id: str) -> Dataset:
        return access.dataset_or_404(self.request, public_id)


class _BoardTarget(_ShareTargetMixin):
    def target(self, public_id: str) -> Board:
        from django.shortcuts import get_object_or_404

        return get_object_or_404(boards.owned(self.request.user), public_id=public_id)


class DatasetShareCreateView(_DatasetTarget, ShareCreateView):
    """Ссылка на таблицу."""


class DatasetShareRevokeView(_DatasetTarget, ShareRevokeView):
    """Отзыв ссылки на таблицу."""


class BoardShareCreateView(_BoardTarget, ShareCreateView):
    """Ссылка на доску."""


class BoardShareRevokeView(_BoardTarget, ShareRevokeView):
    """Отзыв ссылки на доску."""


def share_context(request: HttpRequest, target: Dataset | Board) -> dict[str, Any]:
    """Блок ссылок владельца: действующие ссылки, сроки на выбор и новая ссылка."""
    return {
        "shares": list(shares.of(target)),
        "share_terms": shares.TERMS,
        "share_default_term": shares.DEFAULT_TERM,
        "new_share_url": shares.pop_new(request, target),
        "share_limit": settings.USERDATA_MAX_SHARES,
    }
