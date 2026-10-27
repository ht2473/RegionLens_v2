"""
Анализ суммы через её пересчёт на основу: «Разрыв», «Сближение», «Соседство» и «С чем
связано» у суммы своей таблицы считаются по значению на жителя (у основы «площадь» — на км²).

Таблица правил отвечает «через пересчёт» (``Verdict.via``). Окно ставит карточку на уже
собранный пересчёт, а если его нет — сначала собирает его (одно нажатие). Сохранённая
карточка на самой сумме считается по собранному пересчёту; нового она не собирает.
Множитель («на жителя», «на 100 000») для этих вопросов неважен: разрыв, сближение,
соседство и связь от него не зависят.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from apps.warehouse import routing

from . import operations, semantics
from .series import PER_CAPITA

if TYPE_CHECKING:
    from django.http import HttpRequest


def built(key: str, via: str) -> str:
    """Ключ собранного пересчёта ряда своей таблицы на основу ``via``; нет — пусто."""
    from .models import Dataset

    if not routing.is_user_key(key):
        return ""
    dataset_code, code = key.removeprefix(routing.USER_PREFIX).split(":", 1)
    dataset = Dataset.objects.filter(code=dataset_code).select_related("current_version").first()
    version = dataset.current_version if dataset is not None else None
    if version is None:
        return ""
    wanted = PER_CAPITA if via in semantics.PER_PEOPLE else (via,)
    found = {
        record.derived: record.code
        for record in version.series.filter(base_code=code, derived__in=wanted, values_count__gt=0)
    }
    chosen = next((found[item] for item in wanted if item in found), "")
    return f"{routing.USER_PREFIX}{dataset_code}:{chosen}" if chosen else ""


def through(action: str, key: str) -> tuple[str, str]:
    """
    Ряд, по которому считается действие: сам ряд или собранный пересчёт суммы. Второе —
    основа пересчёта, если он нужен (``semantics.PER_1`` или ``PER_KM2``), иначе пусто.
    """
    info = operations.info_of(key)
    if info is None or action not in semantics.RULES:
        return key, ""
    verdict = semantics.verdict(action, info.value, data=False)
    if not verdict.via or not info.is_user:
        return key, ""
    return built(key, verdict.via) or key, verdict.via


def target(request: HttpRequest, action: str, key: str) -> str:
    """Ряд для новой карточки: у суммы — пересчёт на основу; нет его — собрать."""
    from . import lab

    found, via = through(action, key)
    if not via or found != key:
        return found
    info = operations.info_of(key)
    assert info is not None
    recount = via
    if via in semantics.PER_PEOPLE:
        recount = semantics.suggested_per(info.kind, info.base, info.title, info.unit) or via
    return lab.compute(request, key, recount)


def passable(verdict: semantics.Verdict, *, is_user: bool) -> bool:
    """Недоступно только через пересчёт, а пересчёт своей таблицы можно собрать."""
    return bool(verdict.via) and is_user
