"""
Кто владеет набором: учётная запись. Без входа раздел только рассказывает о себе.

Чужой набор везде отвечает 404: ни по опознавателю, ни по ошибке представления его не видно.
Читатель закрытой ссылки видит набор только на страницах чтения (``readable_or_404``);
шаги загрузки, формулы, ссылки и удаление — только владельцу (``dataset_or_404``).
"""

from __future__ import annotations

from uuid import UUID

from django.core.exceptions import PermissionDenied
from django.db.models import QuerySet
from django.http import Http404, HttpRequest

from .models import Dataset


def owned(request: HttpRequest) -> QuerySet[Dataset]:
    """Наборы того, кто спрашивает; без входа их нет."""
    if not request.user.is_authenticated:
        return Dataset.objects.none()
    return Dataset.objects.filter(owner=request.user)


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
    """Владелец нового набора — учётная запись; без входа набор не заводится."""
    if not request.user.is_authenticated:
        raise PermissionDenied
    dataset.owner = request.user
