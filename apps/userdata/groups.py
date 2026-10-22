"""
Свои группы регионов: создание, правка, удаление и итог группы по годам против остальных
регионов. У суммы итог — сумма по регионам со значением; у относительной величины —
среднее, взвешенное по среднегодовой численности населения (как значение «по России»
у долей и средних — приближение: точнее взвешивать по знаменателю показателя).

Группа гостя, как исследование, привязана к отпечатку ключа из сеанса и хранится сутки;
после входа переходит в учётную запись.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.db.models import QuerySet
from django.http import Http404, HttpRequest
from django.utils import timezone
from django.utils.translation import gettext as _

from .access import guest_fingerprint
from .models import RegionGroup

# Групп у учётной записи и у гостя не больше этого.
MAX_GROUPS = 30
TITLE_LENGTH = 120


class GroupError(Exception):
    """Группу не сохранить: пустая, без регионов, со всеми регионами или предел."""


def owned(request: HttpRequest) -> QuerySet[RegionGroup]:
    """Группы того, кто спрашивает: учётной записи или гостя по ключу сеанса."""
    if request.user.is_authenticated:
        return RegionGroup.objects.filter(owner=request.user)
    fingerprint = guest_fingerprint(request)
    if not fingerprint:
        return RegionGroup.objects.none()
    return RegionGroup.objects.filter(
        owner__isnull=True, guest_key=fingerprint, expires_at__gt=timezone.now()
    )


def own_or_404(request: HttpRequest, public_id: Any) -> RegionGroup:
    """Своя группа; чужая и несуществующая — 404."""
    group = owned(request).filter(public_id=public_id).first()
    if group is None:
        raise Http404
    return group


def save(
    request: HttpRequest, group: RegionGroup | None, title: str, codes: list[str]
) -> RegionGroup:
    """
    Создать или изменить группу: название и субъекты из справочника, хотя бы один и не все —
    иначе не с кем сравнивать.
    """
    from apps.catalog.models import Territory

    comparable = list(Territory.objects.comparable().values_list("code", flat=True))
    wanted = [code for code in dict.fromkeys(codes) if code in set(comparable)]
    name = " ".join((title or "").split())[:TITLE_LENGTH]
    if not name:
        raise GroupError(_("Назовите группу."))
    if not wanted:
        raise GroupError(_("Отметьте регионы группы."))
    if len(wanted) == len(comparable):
        raise GroupError(_("В группе все регионы — сравнивать её не с чем."))
    if group is None:
        if owned(request).count() >= MAX_GROUPS:
            raise GroupError(
                _("Групп уже %(count)s — это предел. Удалите ненужные.") % {"count": MAX_GROUPS}
            )
        group = RegionGroup()
        if request.user.is_authenticated:
            group.owner = request.user
        else:
            group.guest_key = guest_fingerprint(request, create=True)
            group.expires_at = timezone.now() + timedelta(hours=settings.USERDATA_GUEST_HOURS)
    group.title = name
    # Порядок показа — как в справочнике, а не порядок отметок.
    group.codes = [code for code in comparable if code in set(wanted)]
    group.save()
    return group


@dataclass(frozen=True, slots=True)
class Year:
    """Итог года: группы и остальных регионов, сколько регионов группы со значением."""

    year: int
    group: float | None
    rest: float | None
    members: int


def totals(key: str, codes: list[str], *, is_sum: bool) -> list[Year]:
    """
    Итог группы и остальных регионов по годам ряда: сумма у сумм, среднее, взвешенное
    по численности, у относительных. Регион без значения в году — вне итога этого года.
    """
    from apps.warehouse.queries import region_panel
    from apps.warehouse.queries.sources import population_table

    panel = region_panel(key)
    people = {} if is_sum else population_table()
    inside = set(codes)
    found = []
    for year in sorted(panel):
        values = panel[year]
        group = {code: value for code, value in values.items() if code in inside}
        rest = {code: value for code, value in values.items() if code not in inside}
        found.append(
            Year(
                year=year,
                group=_total(group, people, year, is_sum=is_sum),
                rest=_total(rest, people, year, is_sum=is_sum),
                members=len(group),
            )
        )
    return found


def _total(
    values: dict[str, float],
    people: dict[str, dict[int, float]],
    year: int,
    *,
    is_sum: bool,
) -> float | None:
    """Сумма значений или среднее, взвешенное численностью года (после последнего — последней)."""
    if not values:
        return None
    if is_sum:
        return sum(values.values())
    weighted = weight = 0.0
    for code, value in values.items():
        years = people.get(code) or {}
        known = [item for item in years if item <= year] or sorted(years)[:1]
        if not known:
            continue
        population = years[max(known)]
        weighted += value * population
        weight += population
    return weighted / weight if weight > 0 else None
