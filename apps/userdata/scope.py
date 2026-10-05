"""
Кто спрашивает: учётная запись, гость по ключу в сеансе, читатель закрытой ссылки — для
слоя рядов.

``UserDataScopeMiddleware`` кладёт запрос в переменную контекста; слой рядов
(``apps.warehouse.routing``) спрашивает здесь, доступен ли набор по коду, и получает файл
его текущей сборки или ``None``, а выгрузка — можно ли его скачивать. Вне запроса (команды,
фоновые процессы) наборы недоступны.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from django.http import HttpRequest, HttpResponse
from django.utils import timezone

from apps.warehouse.duckdb_client import DataSource

from .models import Dataset, DatasetVersion


@dataclass(slots=True)
class Scope:
    """
    Читатель наборов: учётная запись, отпечаток гостевого ключа, наборы открытых закрытых
    ссылок (код → можно ли скачивать); ответы — на запрос.
    """

    user_id: int | None = None
    fingerprint: str = ""
    shared: dict[str, bool] = field(default_factory=dict)
    sources: dict[str, DataSource | None] = field(default_factory=dict)
    datasets: dict[str, Dataset | None] = field(default_factory=dict)
    request: HttpRequest | None = None
    _resolved: bool = False

    def _resolve(self) -> None:
        """Пользователь, ключ гостя и ссылки — при первом обращении: ``request.user`` ленив."""
        if self._resolved or self.request is None:
            return
        from . import shares
        from .access import guest_fingerprint

        user = getattr(self.request, "user", None)
        if user is not None and user.is_authenticated:
            self.user_id = user.pk
        if hasattr(self.request, "session"):
            self.fingerprint = guest_fingerprint(self.request)
            self.shared = shares.readable(shares.opened(self.request))
        self._resolved = True

    def owns(self, dataset: Dataset) -> bool:
        """Набор принадлежит читателю и не истёк."""
        self._resolve()
        if dataset.owner_id is not None:
            return self.user_id is not None and dataset.owner_id == self.user_id
        if not self.fingerprint or dataset.guest_key != self.fingerprint:
            return False
        return dataset.expires_at is None or dataset.expires_at > timezone.now()

    def can_read(self, dataset: Dataset) -> bool:
        """Набор свой или открыт закрытой ссылкой."""
        if self.owns(dataset):
            return True
        return dataset.owner_id is not None and dataset.code in self.shared

    def can_export(self, dataset: Dataset) -> bool:
        """Набор можно скачивать: свой или открыт ссылкой, разрешающей скачивание."""
        if self.owns(dataset):
            return True
        return dataset.owner_id is not None and self.shared.get(dataset.code, False)

    def dataset(self, code: str) -> Dataset | None:
        """Набор по коду — один запрос на запрос страницы."""
        if code not in self.datasets:
            self.datasets[code] = (
                Dataset.objects.select_related("current_version").filter(code=code).first()
            )
        return self.datasets[code]


_scope: ContextVar[Scope | None] = ContextVar("userdata_scope", default=None)


def current() -> Scope | None:
    """Читатель текущего запроса; вне запроса — ``None``."""
    return _scope.get()


@contextmanager
def reading_as(*, user: Any = None, fingerprint: str = "") -> Iterator[Scope]:
    """Читать наборы от имени учётной записи или гостя — для проверок и команд."""
    scope = Scope(
        user_id=user.pk if user is not None and user.is_authenticated else None,
        fingerprint=fingerprint,
        _resolved=True,
    )
    token = _scope.set(scope)
    try:
        yield scope
    finally:
        _scope.reset(token)


def resolve(code: str) -> DataSource | None:
    """Файл текущей сборки набора, если набор доступен читателю; иначе ``None``."""
    scope = _scope.get()
    if scope is None:
        return None
    if code in scope.sources:
        return scope.sources[code]
    source = None
    dataset = scope.dataset(code)
    if dataset is not None and scope.can_read(dataset):
        source = source_of_version(dataset.current_version)
    scope.sources[code] = source
    return source


def exportable(code: str) -> bool:
    """Ряды набора можно выгружать: набор свой или ссылка разрешает скачивание."""
    scope = _scope.get()
    if scope is None:
        return False
    dataset = scope.dataset(code)
    return dataset is not None and scope.can_export(dataset)


def owns(dataset: Dataset) -> bool:
    """Набор принадлежит тому, кто спрашивает."""
    scope = _scope.get()
    return scope is not None and scope.owns(dataset)


def source_of_version(version: DatasetVersion | None) -> DataSource | None:
    """Файл сборки версии с поколением для ключей кэша; несобранная версия — ``None``."""
    if version is None or version.state != DatasetVersion.State.BUILT:
        return None
    path = version.data_path
    if path is None or not path.exists():
        return None
    return DataSource(path=path, generation=f"u{version.pk}-{version.data_file}")


class UserDataScopeMiddleware:
    """Положить запрос в контекст слоя рядов на время его обработки."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        token = _scope.set(Scope(request=request))
        try:
            return self.get_response(request)
        finally:
            _scope.reset(token)
