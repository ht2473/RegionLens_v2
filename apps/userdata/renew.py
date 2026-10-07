"""
Новая версия таблицы: рецепт прежней версии переносится на новый файл, спрашивается только
новое, ключи рядов сохраняются, после сборки — отчёт о различиях и возврат к прежней.

Пока новая версия не собрана, она — черновик: таблица на холсте, ссылки и исследования работают
на текущей. Перенос: та же таблица файла (по ключу, по похожему имени или единственная),
роли столбцов — по тексту шапки (новый столбец получает роль по догадке), ответы о
территориях и вложенных, отбор разрезов, выбор кодов-масок, описание показателей
с пересчётами, формулы.
Год таблицы «показатели в столбцах» спрашивается заново: новый файл — обычно новый год.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from django.conf import settings
from django.http import HttpRequest
from django.urls import reverse
from django.utils.translation import gettext as _

from . import describe, extract, ingest, jobs, recognize, services
from .models import Dataset, DatasetVersion

logger = logging.getLogger(__name__)

# Состояние переноса в отчёте версии: без вопросов — сборка сама, с вопросами — шаги мастера.
AUTO = "auto"
ASKED = "asked"
# Части рецепта, которые новая версия получает как есть.
CARRIED = ("territories", "nested", "indicators", "formulas", "seen", "masks")
_DIGITS = re.compile(r"[\d_\-\s.]+")
_SPACES = re.compile(r"\s+")


def draft_of(dataset: Dataset) -> DatasetVersion | None:
    """Новая версия в работе: новее текущей и ещё не собрана."""
    current = dataset.current_version
    return (
        dataset.versions.filter(number__gt=current.number if current else 0)
        .exclude(state=DatasetVersion.State.BUILT)
        .order_by("-number")
        .first()
    )


def working_version(dataset: Dataset) -> DatasetVersion | None:
    """Версия, над которой идут шаги мастера: черновик новой или текущая."""
    return draft_of(dataset) or dataset.current_version


def discard_draft(dataset: Dataset) -> None:
    """Отменить новую версию в работе."""
    draft = draft_of(dataset)
    if draft is not None and draft.pk != dataset.current_version_id:
        services.drop_version(draft)


def is_renewal(version: DatasetVersion) -> bool:
    """Версия — новая версия собранной таблицы."""
    return version.previous_id is not None


# --- Перенос --------------------------------------------------------------------------------------


def choose_table(version: DatasetVersion) -> bool:
    """
    Выбрать в новом файле ту же таблицу, что в прежней версии; ``False`` — неясно какую,
    спросить на шаге «Файл».
    """
    previous = version.previous
    inspection = services.inspection_of(version)
    if not inspection.tables:
        return False
    old = (previous.recipe.get("table") or {}) if previous else {}
    key = _same_table(old.get("key", ""), [table.key for table in inspection.tables])
    if key is None:
        return False
    services.choose_table(version, key)
    return True


def _same_table(old: str, keys: list[str]) -> str | None:
    """Та же таблица: тот же ключ, ключ без чисел (дата в имени файла) или единственная."""
    if old in keys:
        return old
    similar = [key for key in keys if _DIGITS.sub("", key.lower()) == _DIGITS.sub("", old.lower())]
    if len(similar) == 1:
        return similar[0]
    return keys[0] if len(keys) == 1 else None


def transfer(version: DatasetVersion) -> None:
    """Перенести рецепт прежней версии на выбранную таблицу нового файла."""
    previous = version.previous
    if previous is None:
        return
    old = previous.recipe
    recipe = {
        "table": version.recipe["table"],
        "file": version.recipe["file"],
        **{name: old[name] for name in CARRIED if name in old},
    }
    if old.get("form") in {recognize.LONG, recognize.WIDE}:
        recipe["form"] = old["form"]
    version.recipe = recipe
    # Роли — по тексту шапки нового файла: догадка распознавателя без прежних ролей.
    _loaded, guess = describe.load_and_recognize(version, version.dataset.owner)
    roles, moved = _roles(old, guess)
    recipe["roles"] = roles
    recipe["slices"] = {
        moved[index]: values
        for index, values in (old.get("slices") or {}).items()
        if index in moved
    }
    version.recipe = recipe
    version.save(update_fields=["recipe", "updated_at"])


def _roles(
    old: dict[str, Any], guess: recognize.Recognition
) -> tuple[dict[str, str], dict[str, str]]:
    """
    Роли столбцов нового файла и соответствие номеров «прежний → новый»: столбец с той же
    шапкой получает прежнюю роль, новый — роль по догадке.
    """
    old_roles: dict[str, str] = old.get("roles") or {}
    old_headers: dict[str, str] = old.get("headers") or {}
    by_header: dict[str, list[str]] = {}
    for index in old_roles:
        by_header.setdefault(_header_key(old_headers.get(index, ""), index), []).append(index)
    roles: dict[str, str] = {}
    moved: dict[str, str] = {}
    for column in guess.columns:
        found = by_header.get(_header_key(column.header, str(column.index)), [])
        if len(found) == 1:
            roles[str(column.index)] = old_roles[found[0]]
            moved[found[0]] = str(column.index)
        else:
            roles[str(column.index)] = column.role
    return roles, moved


def _header_key(header: str, index: str) -> str:
    """Шапка для сравнения; у столбца без шапки — его номер."""
    text = _SPACES.sub(" ", header or "").strip().lower()
    return text or f"#{index}"


# --- Вопросы --------------------------------------------------------------------------------------


def questions(version: DatasetVersion, result: recognize.Recognition) -> list[str]:
    """О чём спросить в новой версии: только то, чего прежняя не знала."""
    found: list[str] = []
    if result.form == recognize.UNKNOWN or not result.territory_columns:
        found.append(_("столбец с регионами не найден"))
        return found
    if not result.value_columns:
        found.append(_("столбцы со значениями не найдены"))
    seen = version.recipe.get("seen") or {}
    labels = set(seen.get("labels") or [])
    fresh = [label for label, _match, _count in describe.unresolved(result) if label not in labels]
    if fresh:
        found.append(
            _("новые подписи территорий без сопоставления: %(count)s") % {"count": len(fresh)}
        )
    nested = set(seen.get("nested") or [])
    if result.territories and any(
        question.label not in nested for question in result.territories.nested
    ):
        found.append(_("область с автономными округами, о которой ещё не спрашивали"))
    if result.needs_year:
        found.append(_("год значений"))
    if result.series > recognize.MAX_SERIES:
        found.append(
            _("рядов получается %(count)s — больше %(limit)s")
            % {"count": result.series, "limit": recognize.MAX_SERIES}
        )
    return found


def proceed(version: DatasetVersion, user: Any) -> str:
    """
    Новая версия без вопросов собирается сама. Вернуть адрес следующего шага: опрос сборки,
    отчёт о различиях — или шаг «Что в таблице» с вопросами.
    """
    dataset = version.dataset
    table_url = reverse("userdata:table", args=[dataset.public_id])
    state = jobs.ensure_summary(version)
    if state in {jobs.RUNNING, jobs.FAILED}:
        return table_url
    _loaded, result = describe.load_and_recognize(version, user)
    asked = questions(version, result)
    if asked:
        _mark(version, ASKED, asked)
        return table_url
    describe.save_answers(version, result, {}, user)
    version.refresh_from_db()
    _mark(version, AUTO, [])
    if jobs._inline(version, jobs.EXTRACT):
        jobs.run(version, jobs.EXTRACT)
        version.refresh_from_db()
        if not extract.is_current(version):
            return reverse("userdata:series", args=[dataset.public_id])
    if jobs.start(version, jobs.BUILD) == jobs.READY:
        version.refresh_from_db()
        if version.state == DatasetVersion.State.BUILT:
            return changes_url(version)
    return reverse("userdata:series", args=[dataset.public_id])


def _mark(version: DatasetVersion, state: str, asked: list[str]) -> None:
    jobs.mark(version, renewal={"state": state, "questions": asked})


def pending_questions(version: DatasetVersion) -> list[str]:
    """Вопросы новой версии, показанные на шаге «Что в таблице»."""
    renewal = version.report.get("renewal") or {}
    return list(renewal.get("questions") or []) if renewal.get("state") == ASKED else []


def is_automatic(version: DatasetVersion) -> bool:
    """Новая версия ещё не описана и не спрашивала — её можно собрать без вопросов."""
    renewal = version.report.get("renewal") or {}
    return (
        is_renewal(version)
        and renewal.get("state") == AUTO
        and version.state != DatasetVersion.State.BUILT
        and not version.recipe.get("headers")
    )


# --- После сборки ---------------------------------------------------------------------------------


def changes_url(version: DatasetVersion) -> str:
    """Отчёт о различиях версии."""
    return reverse("userdata:version", args=[version.dataset.public_id, version.number])


def after_build_url(version: DatasetVersion, request: HttpRequest | None = None) -> str:
    """
    Куда вести после сборки: новая версия — к отчёту о различиях, первая — в исследование
    с картой первого ряда; если исследования не завести (предел), — на карту.
    """
    if is_renewal(version):
        return changes_url(version)
    from .lab import open_in_study
    from .views import first_view_url

    opened = open_in_study(request, version) if request is not None else None
    return opened or first_view_url(version)


def promote(version: DatasetVersion) -> bool:
    """
    Собранная новая версия становится текущей; старые версии сверх
    ``USERDATA_KEEP_VERSIONS`` удаляются с диска и из базы. Вернуть, сменилась ли текущая.
    """
    dataset = Dataset.objects.select_related("current_version").get(pk=version.dataset_id)
    current = dataset.current_version
    if current is not None and current.number >= version.number:
        return False
    Dataset.objects.filter(pk=dataset.pk).update(current_version=version)
    keep = max(int(settings.USERDATA_KEEP_VERSIONS), 1)
    built = list(
        dataset.versions.filter(state=DatasetVersion.State.BUILT)
        .exclude(pk=version.pk)
        .order_by("-number")
    )
    for old in built[keep - 1 :]:
        services.drop_version(old)
    return True


def restore(dataset: Dataset, version: DatasetVersion) -> None:
    """Вернуться к прежней собранной версии: она снова текущая, ключи рядов те же."""
    from . import formula_store

    if version.state != DatasetVersion.State.BUILT or version.data_path is None:
        raise ingest.IngestError(_("Эта версия не собрана."), "version")
    if not version.data_path.exists():
        raise ingest.IngestError(_("Файл этой версии больше не хранится."), "version")
    discard_draft(dataset)
    Dataset.objects.filter(pk=dataset.pk).update(current_version=version)
    dataset.current_version = version
    logger.info("userdata: набор %s возвращён к версии %s", dataset.public_id, version.number)
    formula_store.rebuild_dependents(dataset)
