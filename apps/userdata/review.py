"""
Замечания шага «Проверка» словами: знаки источника, вопрос о прочерке, нули, похожие
на пропуски, и согласованность данных (правила — ``consistency``).

Прочерк у Росстата — «явление отсутствует»: вопроса нет. У таблицы другого источника
человек выбирает, что значит прочерк; подсказан тот же ответ. Замечания о согласованности
показываются только у сумм: доли и средние не складываются по регионам.
"""

from __future__ import annotations

import re
from typing import Any

from django.utils.translation import gettext as _

from apps.core.templatetags.formatting import ru_number

from . import build, consistency, semantics
from .indicators import Indicator
from .models import Dataset, DatasetVersion
from .templatetags.userdata import territory_name

# Источник таблицы — Росстат: по полю «Источник» или адресу.
_ROSSTAT_SOURCE = re.compile(
    r"росстат|федеральн\w* служб\w* государственной статистики|rosstat|gks\.ru|стат\b",
    re.IGNORECASE,
)


def review(
    dataset: Dataset, version: DatasetVersion, report: dict[str, Any], items: list[Indicator]
) -> dict[str, Any]:
    """Знаки, вопрос о прочерке, нули и замечания — для шаблона шага «Проверка»."""
    marks = report.get("marks") or {}
    titles = {item.name: item.title for item in items}
    return {
        "absent": marks.get("absent", 0),
        "hidden": marks.get("hidden", 0),
        "below_unit": marks.get("below_unit", 0),
        "ask_dashes": bool(marks.get("absent")) and not is_rosstat(dataset, report),
        "dashes": version.recipe.get("dashes") or build.DASHES_ABSENT,
        "zeros": _zeros(version, report, titles),
        "checks": _checks(report, items),
    }


def is_rosstat(dataset: Dataset, report: dict[str, Any]) -> bool:
    """Таблица Росстата или его территориального органа: по файлу, полю «Источник» или адресу."""
    return bool(report.get("rosstat")) or any(
        _ROSSTAT_SOURCE.search(text) for text in (dataset.source_title, dataset.source_url) if text
    )


def _zeros(
    version: DatasetVersion, report: dict[str, Any], titles: dict[str, str]
) -> list[dict[str, Any]]:
    """Показатели с нулями, похожими на пропуски: отмечен — по ответу или по подсказке."""
    answered = "zeros" in version.recipe
    chosen = set(version.recipe.get("zeros") or [])
    found = []
    for item in report.get("zeros") or []:
        name = item["indicator"]
        if name not in titles:
            continue
        parts = [_("Нулей: %(zeros)s из %(values)s.") % item]
        example = item.get("example")
        if example:
            parts.append(
                _(
                    "Между обычными годами: %(count)s, например %(region)s: %(before)s → 0 → "
                    "%(after)s (%(first)s–%(last)s)."
                )
                % {
                    "count": item["between"],
                    "region": territory_name(example["code"]),
                    "before": ru_number(example["before"]),
                    "after": ru_number(example["after"]),
                    "first": example["year_before"],
                    "last": example["year_after"],
                }
            )
        if item["years"]:
            parts.append(
                _("Ноль у всех регионов: %(years)s.")
                % {"years": ", ".join(str(year) for year in item["years"])}
            )
        found.append(
            {
                "name": name,
                "title": titles[name],
                "text": " ".join(str(part) for part in parts),
                "checked": name in chosen if answered else bool(item["suggested"]),
            }
        )
    return found


def _checks(report: dict[str, Any], items: list[Indicator]) -> list[str]:
    """Замечания о согласованности у сумм — словами, с местом."""
    by_name = {item.name: item for item in items}
    found = []
    for check in report.get("checks") or []:
        item = by_name.get(check["indicator"])
        if item is None or item.hidden or not semantics.is_additive(item.kind):
            continue
        text = _CHECK_TEXTS[check["kind"]](check)
        found.append(f"«{item.title}»: {text}")
    return found


def _country(check: dict[str, Any]) -> str:
    gap = check["gap"]
    text = (
        _("сумма регионов больше значения России на %(percent)s %% в %(year)s году.")
        if gap > 0
        else _("сумма регионов меньше значения России на %(percent)s %% в %(year)s году.")
    ) % {"percent": ru_number(abs(gap) * 100, 1), "year": check["year"]}
    if check["years"] > 1:
        text += " " + _("Лет с расхождением больше 1 %%: %(count)s.") % {"count": check["years"]}
    return text


def _scale(check: dict[str, Any]) -> str:
    values = {
        "step": ru_number(check["step"], 0),
        "year": check["year"],
        "before": check["year_before"],
    }
    text = (
        _("значения регионов в %(year)s году в %(step)s раз больше, чем в %(before)s.")
        if check["up"]
        else _("значения регионов в %(year)s году в %(step)s раз меньше, чем в %(before)s.")
    ) % values
    return f"{text} {_('Проверьте единицу: тысячи или миллионы.')}"


def _parts(check: dict[str, Any]) -> str:
    example = check["example"]
    return _(
        "части разреза «%(header)s» в сумме больше итога в %(count)s местах, например "
        "%(region)s, %(year)s: %(sum)s против %(total)s."
    ) % {
        "header": check["header"],
        "count": check["places"],
        "region": territory_name(example["code"]),
        "year": example["year"],
        "sum": _number(example["sum"]),
        "total": _number(example["total"]),
    }


def _number(value: float) -> str:
    """Число как в таблице: целое — без дробной части."""
    return ru_number(value, 0) if float(value).is_integer() else ru_number(value)


_CHECK_TEXTS = {
    consistency.COUNTRY: _country,
    consistency.SCALE: _scale,
    consistency.PARTS: _parts,
}
