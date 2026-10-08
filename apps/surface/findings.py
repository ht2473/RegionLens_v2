"""
Строка вывода под заголовком графика рабочей поверхности — по правилам, как в паспорте:
крайние регионы и разница между ними, середина распределения, движение мест, изменение
линий, направление связи. Текст не сочиняется: каждая фраза — шаблон с числами из данных.

Названия регионов стоят в именительном падеже и без согласования глаголов с ними.
"""

from __future__ import annotations

from typing import Any

from django.utils.translation import gettext

from apps.catalog.passport import RATIO_WORDING_FROM, measure_change, times_phrase
from apps.core.templatetags.formatting import ru_number


def _gap(item: Any, high: float, low: float) -> str:
    """Разница двух значений: у процентов — в пунктах, у положительных — кратностью."""
    if item.is_percentage:
        digits = max(item.precision, 1)
        return gettext("разница — %(points)s п. п.") % {"points": ru_number(high - low, digits)}
    if low > 0:
        return gettext("разница %(times)s") % {"times": times_phrase(high / low)}
    return gettext("разница — %(amount)s") % {"amount": ru_number(high - low, item.precision)}


def map_finding(rows: list[dict[str, Any]], item: Any, period: list[int] | None) -> str:
    """Карта: где больше и меньше всего; при сравнении лет — у скольких регионов рост."""
    if period:
        changed = [row for row in rows if row["mapped"] is not None]
        if not changed:
            return ""
        top = max(changed, key=lambda row: row["mapped"])
        up = sum(1 for row in changed if row["mapped"] > 0)
        return gettext(
            "С %(start)s по %(end)s год значение выросло у %(up)s из %(total)s регионов; "
            "наибольший рост — %(name)s."
        ) % {
            "start": period[0],
            "end": period[1],
            "up": up,
            "total": len(changed),
            "name": top["name"],
        }
    ranked = sorted(
        (row for row in rows if row["rank_desc"] is not None and row["value"] is not None),
        key=lambda row: row["rank_desc"],
    )
    if len(ranked) < 2:  # noqa: PLR2004 — сравнивать не с чем
        return ""
    top, bottom = ranked[0], ranked[-1]
    return gettext("Больше всего — %(top)s, меньше всего — %(bottom)s: %(gap)s.") % {
        "top": top["name"],
        "bottom": bottom["name"],
        "gap": _gap(item, top["value"], bottom["value"]),
    }


def distribution_finding(
    rows: list[dict[str, Any]], statistics: dict[str, Any] | None, item: Any
) -> str:
    """Распределение: середина регионов и где Россия (у складываемых — размах до медианы)."""
    if not statistics or statistics.get("p25_value") is None or statistics.get("p75_value") is None:
        return ""
    unit = f" {item.unit_label}" if item.unit_label else ""
    middle = gettext("Половина регионов — от %(low)s до %(high)s%(unit)s") % {
        "low": ru_number(statistics["p25_value"], item.precision),
        "high": ru_number(statistics["p75_value"], item.precision),
        "unit": unit,
    }
    values = [row["mapped"] for row in rows if row["mapped"] is not None]
    country = statistics.get("country_value")
    if country is not None and not item.absolute and values:
        below = sum(1 for value in values if value < country)
        return gettext("%(middle)s; у %(below)s из %(total)s значение ниже, чем по России.") % {
            "middle": middle,
            "below": below,
            "total": len(values),
        }
    median = statistics.get("median_value")
    if values and median and median > 0 and max(values) / median >= RATIO_WORDING_FROM:
        return gettext("%(middle)s; наибольшее значение %(times)s больше середины.") % {
            "middle": middle,
            "times": times_phrase(max(values) / median),
        }
    return f"{middle}."


def ranking_finding(movers: dict[str, list[dict[str, Any]]], period: list[int] | None) -> str:
    """Рейтинг: наибольший подъём и падение мест за период сравнения."""
    risen, fallen = movers.get("risen") or [], movers.get("fallen") or []
    if not period or not (risen or fallen):
        return ""
    parts = []
    if risen:
        parts.append(
            gettext("наибольший подъём — %(name)s (+%(places)s)")
            % {"name": risen[0]["name"], "places": risen[0]["movement"]}
        )
    if fallen:
        parts.append(
            gettext("наибольшее падение — %(name)s (−%(places)s)")
            % {"name": fallen[0]["name"], "places": abs(fallen[0]["movement"])}
        )
    return gettext("С %(start)s по %(end)s год: %(parts)s.") % {
        "start": period[0],
        "end": period[1],
        "parts": ", ".join(parts),
    }


def _change(item: Any, before: float, after: float) -> str:
    """Изменение линии: кратное — «рост в 2,4 раза», прочее — как в паспорте."""
    if not item.is_percentage and before > 0 and after > 0:
        ratio = after / before
        if ratio >= RATIO_WORDING_FROM:
            return gettext("рост %(times)s") % {"times": times_phrase(ratio)}
        if ratio <= 1 / RATIO_WORDING_FROM:
            return gettext("снижение %(times)s") % {"times": times_phrase(1 / ratio)}
    change = measure_change(item, after, before)
    return change.text if change is not None else ""


def timeline_finding(years: list[int], lines: list[dict[str, Any]], item: Any) -> str:
    """
    Динамика: изменение за годы, в которых значения есть у всех линий, — у территории
    с наибольшим и наименьшим изменением и у России.
    """
    common = [
        index
        for index in range(len(years))
        if all(line["values"][index] is not None for line in lines)
    ]
    if len(common) < 2:  # noqa: PLR2004 — изменение — это два года
        return ""
    first, last = common[0], common[-1]

    def amount(line: dict[str, Any]) -> float:
        before, after = line["values"][first], line["values"][last]
        if item.is_percentage or before <= 0:
            return after - before
        return after / before

    places = [line for line in lines if not line.get("country")]
    country = next((line for line in lines if line.get("country")), None)
    shown = []
    if places:
        ordered = sorted(places, key=amount, reverse=True)
        shown = [ordered[0]] if len(ordered) == 1 else [ordered[0], ordered[-1]]
    if country is not None:
        shown.append(country)
    parts = [
        f"{line['name']} — {_change(item, line['values'][first], line['values'][last])}"
        for line in shown
    ]
    return gettext("С %(start)s по %(end)s год: %(parts)s.") % {
        "start": years[first],
        "end": years[last],
        "parts": ", ".join(parts),
    }


def relation_finding(first: str, second: str, pair: Any) -> str:
    """Связь пары: направление словами, если она подтверждена."""
    if not pair.is_available or pair.coefficient is None:
        return ""
    if not pair.is_significant:
        return gettext("По этим данным нельзя сказать, что «%(y)s» меняется вместе с «%(x)s».") % {
            "x": first,
            "y": second,
        }
    if pair.coefficient > 0:
        text = gettext("Где больше «%(x)s», там в среднем больше и «%(y)s»; связь — %(strength)s.")
    else:
        text = gettext("Где больше «%(x)s», там в среднем меньше «%(y)s»; связь — %(strength)s.")
    return text % {"x": first, "y": second, "strength": pair.strength}
