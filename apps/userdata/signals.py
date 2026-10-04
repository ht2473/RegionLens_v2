"""Таблицы гостя переходят в учётную запись при входе — как «Мой регион»."""

from __future__ import annotations

from typing import Any

from django.contrib.auth.signals import user_logged_in
from django.http import HttpRequest
from django.utils import timezone

from .access import GUEST_SESSION_KEY, guest_fingerprint
from .models import Dataset


def adopt_guest_datasets(
    request: HttpRequest | None = None, user: Any = None, **_kwargs: Any
) -> None:
    """Неистёкшие наборы гостевого ключа сеанса получают владельца и больше не истекают."""
    if request is None or user is None or not hasattr(request, "session"):
        return
    fingerprint = guest_fingerprint(request)
    if not fingerprint:
        return
    Dataset.objects.filter(
        owner__isnull=True, guest_key=fingerprint, expires_at__gt=timezone.now()
    ).update(owner=user, guest_key="", expires_at=None)
    request.session.pop(GUEST_SESSION_KEY, None)


user_logged_in.connect(adopt_guest_datasets, dispatch_uid="userdata-adopt-guest")
