"""
Шаг «Что в таблице»: распознавание выбранной таблицы с решениями человека из рецепта,
перечни для вопросов и запись ответов обратно в рецепт.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from django.db import transaction
from django.utils.translation import get_language

from . import jobs, matching, recognize, tables
from .models import DatasetVersion, TerritoryLabel

NOT_TERRITORY = recognize.NOT_TERRITORY


@dataclass(frozen=True, slots=True)
class Choice:
    """Вариант выбора территории."""

    code: str
    name: str


def recognition_of(version: DatasetVersion, user: Any = None) -> recognize.Recognition:
    """Распознать таблицу версии с решениями человека и его запомненными подписями."""
    table = jobs.table_of(version)
    path = version.directory / version.recipe["file"]
    profile = version.report.get("profile") if tables.is_large(path, table) else None
    loaded = tables.load(path, table, profile)
    return recognize.recognize(
        loaded,
        table,
        version.recipe,
        file_name=version.file_name,
        remembered=remembered_labels(user),
    )


def remembered_labels(user: Any) -> dict[str, str]:
    """Запомненные сопоставления учётной записи: ключ подписи → код."""
    if user is None or not getattr(user, "is_authenticated", False):
        return {}
    return dict(
        TerritoryLabel.objects.filter(owner=user).values_list("label_key", "territory_code")
    )


def unresolved(result: recognize.Recognition) -> list[tuple[str, matching.Match, int]]:
    """Подписи, о которых нужно спросить: не узнаны, неоднозначны, похожи на город."""
    if result.territories is None:
        return []
    found = []
    for label, count in result.labels.items():
        match = result.territories.matches[label]
        if match.rule != "chosen" and match.kind in {
            matching.ASK,
            matching.NONE,
            matching.MUNICIPAL,
        }:
            found.append((label, match, count))
    return sorted(found, key=lambda item: (-item[2], item[0]))


def outside(result: recognize.Recognition) -> list[tuple[str, matching.Match]]:
    """Строки вне справочника: хранятся в наборе, на карту не попадают."""
    if result.territories is None:
        return []
    return sorted(
        (label, match)
        for label, match in result.territories.matches.items()
        if match.kind == matching.OUTSIDE and label in result.labels
    )


def territory_choices() -> list[tuple[str, list[Choice]]]:
    """Территории справочника для выбора: страна, округа, субъекты по округам."""
    reference = matching._reference()
    field = "name_en" if get_language() == "en" else "name_ru"
    districts = {record["code"]: record[field] for record in reference["federal_districts"]}
    groups: list[tuple[str, list[Choice]]] = [
        ("", [Choice(reference["country"]["code"], reference["country"][field])]),
        ("", [Choice(code, name) for code, name in districts.items()]),
    ]
    for code, name in districts.items():
        members = [
            Choice(record["code"], record[field])
            for record in reference["regions"]
            if record.get("district") == code
        ]
        members += [
            Choice(record["code"], record[field])
            for record in reference["aggregates"]
            if record.get("district") == code
        ]
        groups.append((name, sorted(members, key=lambda item: item.name)))
    return groups


def save_answers(
    version: DatasetVersion,
    result: recognize.Recognition,
    answers: Mapping[str, Any],
    user: Any = None,
) -> None:
    """
    Записать ответы человека в рецепт версии.

    ``answers``: ``roles`` (номер столбца → роль), ``year``, ``slices`` (номер → значения),
    ``territories`` и ``nested`` (подпись → код, «outside» или «none»), ``remember``.
    """
    recipe = dict(version.recipe)
    roles = {str(column.index): column.role for column in result.columns}
    for index, role in (answers.get("roles") or {}).items():
        if str(index) in roles and role in recognize.ROLES:
            roles[str(index)] = role
    recipe["roles"] = roles
    recipe["form"] = result.form
    year = answers.get("year")
    recipe["year"] = int(year) if year else None
    slices = {
        str(index): [
            value
            for value in values
            if value in result.slices.get(int(index), {}).get("values", {})
        ]
        for index, values in (answers.get("slices") or {}).items()
    }
    recipe["slices"] = {index: values for index, values in slices.items() if values}
    valid = _valid_codes() | {matching.OUTSIDE, NOT_TERRITORY}
    choices = {
        label: code
        for label, code in (answers.get("territories") or {}).items()
        if label in result.labels and code in valid
    }
    recipe["territories"] = {**(recipe.get("territories") or {}), **choices}
    nested = {
        label: code
        for label, code in (answers.get("nested") or {}).items()
        if code in matching.NESTED_PARENTS or code in matching.NESTED_PARENTS.values()
    }
    recipe["nested"] = {**(recipe.get("nested") or {}), **nested}
    with transaction.atomic():
        version.recipe = recipe
        version.state = DatasetVersion.State.DESCRIBED
        version.save(update_fields=["recipe", "state", "updated_at"])
        if answers.get("remember") and user is not None and user.is_authenticated:
            sorter = matching.matcher()
            for label, code in choices.items():
                if code == NOT_TERRITORY:
                    continue
                TerritoryLabel.objects.update_or_create(
                    owner=user, label_key=sorter.key(label)[:300], defaults={"territory_code": code}
                )


def _valid_codes() -> set[str]:
    reference = matching._reference()
    records = [
        reference["country"],
        *reference["federal_districts"],
        *reference["regions"],
        *reference["aggregates"],
    ]
    return {record["code"] for record in records}
