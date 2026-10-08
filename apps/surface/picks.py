"""
Быстрые варианты выбора регионов в рейле: мой регион, соседи, похожие, весь округ.

Опора — первый отмеченный регион, без отметок — мой регион. Вариант — ссылка с параметром
``pick``: представление дополняет выбор и переходит на адрес с регионами словами, так что
ссылка на вид остаётся обычной. Недоступный вариант показан серым с причиной.
"""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest
from django.utils.translation import gettext

from apps.catalog.models import Territory
from apps.catalog.selectors import MAX_COMPARE

PICK_MINE = "mine"
PICK_NEIGHBOURS = "neighbours"
PICK_SIMILAR = "similar"
PICK_DISTRICT = "district"
PICKS = (PICK_MINE, PICK_NEIGHBOURS, PICK_SIMILAR, PICK_DISTRICT)


def _anchor(request: HttpRequest, territories: list[Territory]) -> Territory | None:
    """Опора вариантов: первый отмеченный регион, иначе мой регион."""
    if territories:
        return territories[0]
    from apps.accounts.region import my_region

    return my_region(request)


def _district_members(anchor: Territory) -> list[str]:
    """Регионы федерального округа опоры в порядке справочника."""
    if anchor.parent_id is None:
        return []
    return list(
        Territory.objects.comparable()
        .filter(parent_id=anchor.parent_id)
        .order_by("display_order", "code")
        .values_list("code", flat=True)
    )


def _candidates(request: HttpRequest, pick: str, territories: list[Territory]) -> list[str]:
    """Коды, которые вариант добавляет к выбору (уже отмеченные тоже могут попасть)."""
    if pick == PICK_MINE:
        from apps.accounts.region import my_region

        region = my_region(request)
        return [region.code] if region is not None else []
    anchor = _anchor(request, territories)
    if anchor is None:
        return []
    if pick == PICK_NEIGHBOURS:
        from apps.analytics.selectors import neighbour_map

        return [anchor.code, *neighbour_map("land").get(anchor.code, [])]
    if pick == PICK_SIMILAR:
        from apps.analytics.similar import similar_territories

        found = similar_territories(anchor.code, limit=MAX_COMPARE - 1)
        return [anchor.code, *(item["code"] for item in found.items)] if found else []
    if pick == PICK_DISTRICT:
        return _district_members(anchor)
    return []


def apply(request: HttpRequest, pick: str, territories: list[Territory]) -> list[str]:
    """Выбор после варианта: прежние коды и новые следом, не больше допустимого."""
    codes = [territory.code for territory in territories]
    for code in _candidates(request, pick, territories):
        if code not in codes and len(codes) < MAX_COMPARE:
            codes.append(code)
    return codes


def offers(
    request: HttpRequest, territories: list[Territory], base_url: str
) -> list[dict[str, Any]]:
    """
    Варианты для рейля: подпись, подсказка, адрес и причина, если недоступен.

    Соседи и похожие считаются при нажатии, здесь — только опора и доступность.
    """
    from apps.accounts.region import my_region

    joiner = "&" if "?" in base_url else "?"
    codes = {territory.code for territory in territories}
    room = MAX_COMPARE - len(codes)
    region = my_region(request)
    anchor = _anchor(request, territories)
    full = gettext("отмечено сколько можно")
    no_anchor = gettext("отметьте регион или выберите свой в шапке")

    def offer(pick: str, title: str, hint: str, reason: str) -> dict[str, Any]:
        return {
            "pick": pick,
            "title": title,
            "hint": hint,
            "reason": reason,
            "url": "" if reason else f"{base_url}{joiner}pick={pick}",
        }

    result = []
    if region is not None and region.code not in codes:
        result.append(
            offer(
                PICK_MINE,
                gettext("мой регион"),
                region.name,
                full if room <= 0 else "",
            )
        )
    named = anchor.name if anchor is not None else ""
    for pick, title, hint in (
        (PICK_NEIGHBOURS, gettext("соседи"), gettext("Соседи по границе: %(name)s")),
        (PICK_SIMILAR, gettext("похожие"), gettext("Похожие по ключевым показателям: %(name)s")),
    ):
        reason = no_anchor if anchor is None else (full if room <= 0 else "")
        result.append(offer(pick, title, hint % {"name": named} if named else "", reason))
    if anchor is not None:
        members = _district_members(anchor)
        missing = [code for code in members if code not in codes]
        if not missing:
            reason = gettext("весь округ уже отмечен")
        elif len(missing) > room:
            reason = gettext("в округе %(count)s регионов — больше, чем можно отметить") % {
                "count": len(members)
            }
        else:
            reason = ""
        district = anchor.parent.name if anchor.parent is not None else ""
        result.append(offer(PICK_DISTRICT, gettext("весь округ"), district, reason))
    else:
        result.append(offer(PICK_DISTRICT, gettext("весь округ"), "", no_anchor))
    return result
