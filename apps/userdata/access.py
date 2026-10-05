"""
Кто владеет набором: учётная запись или гость по случайному ключу в сеансе.

Чужой набор везде отвечает 404: ни по опознавателю, ни по ошибке представления его не видно.
Читатель закрытой ссылки видит набор только на страницах чтения (``readable_or_404``);
шаги загрузки, формулы, ссылки и удаление — только владельцу (``dataset_or_404``).
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta
from uuid import UUID

from django.conf import settings
from django.db.models import QuerySet
from django.http import Http404, HttpRequest
from django.utils import timezone

from .models import Dataset

GUEST_SESSION_KEY = "userdata_guest"


def guest_fingerprint(request: HttpRequest, *, create: bool = False) -> str:
    """Отпечаток гостевого ключа сеанса; ``create`` — завести ключ, если его нет."""
    key = request.session.get(GUEST_SESSION_KEY, "")
    if not key and create:
        key = secrets.token_urlsafe(32)
        request.session[GUEST_SESSION_KEY] = key
    return hashlib.sha256(key.encode()).hexdigest() if key else ""


def owned(request: HttpRequest) -> QuerySet[Dataset]:
    """Наборы того, кто спрашивает."""
    if request.user.is_authenticated:
        return Dataset.objects.filter(owner=request.user)
    fingerprint = guest_fingerprint(request)
    if not fingerprint:
        return Dataset.objects.none()
    # Истёкший набор гостя удалит очистка; до неё его уже нет.
    return Dataset.objects.filter(
        owner__isnull=True, guest_key=fingerprint, expires_at__gt=timezone.now()
    )


def dataset_or_404(request: HttpRequest, public_id: UUID | str) -> Dataset:
    """Свой набор по опознавателю; чужой и несуществующий — 404."""
    dataset = owned(request).filter(public_id=public_id).first()
    if dataset is None:
        raise Http404
    return dataset


def readable_or_404(request: HttpRequest, public_id: UUID | str) -> Dataset:
    """Набор владельца или открытый закрытой ссылкой; прочий — 404."""
    from . import scope

    dataset = owned(request).filter(public_id=public_id).first()
    if dataset is not None:
        return dataset
    dataset = Dataset.objects.select_related("current_version").filter(public_id=public_id).first()
    reader = scope.current()
    if dataset is None or reader is None or not reader.can_read(dataset):
        raise Http404
    return dataset


def assign_owner(dataset: Dataset, request: HttpRequest) -> None:
    """Владелец нового набора: учётная запись или гость со сроком хранения."""
    if request.user.is_authenticated:
        dataset.owner = request.user
        return
    dataset.guest_key = guest_fingerprint(request, create=True)
    dataset.expires_at = timezone.now() + timedelta(hours=settings.USERDATA_GUEST_HOURS)
