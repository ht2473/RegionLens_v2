"""
Исследования: рабочее поле лаборатории — заметки и карточки по порядку, общий год и регионы.

Карточка хранит, что построить, а не числа: при открытии она строится заново на текущих
данных. Блок — словарь с опознавателем ``id``; вид ``text`` несёт текст заметки, вид
``view`` — страницу ``target`` из перечня ``QUERY_TARGETS`` с параметрами, вид ``answer`` —
ответ на вопрос ``question`` о ряде ``series`` (у связи — и о ряде ``other``). У карточек —
название, пояснение и ширина.

Каждая правка поля запоминает прежние блоки: «Вернуть» меняет их местами с нынешними.
Исследование гостя, как его таблица, привязано к ключу сеанса и хранится сутки.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterable
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.db.models import QuerySet
from django.http import Http404, HttpRequest
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.warehouse import routing
from apps.workspace.constants import QUERY_TARGETS_BY_CODE
from apps.workspace.models import parameter_series_keys
from apps.workspace.services import parse_query_string

from .access import guest_fingerprint
from .models import Study

TEXT = "text"
VIEW = "view"
ANSWER = "answer"
TITLE_LENGTH = 200
TEXT_LENGTH = 4000
NOTE_LENGTH = 500
# Представления холста: у них общий год и регионы исследования.
CANVAS_TARGETS = ("map", "compare", "rankings", "distribution", "table")
# Виды во всю ширину поля по умолчанию: карте и линиям тесно в половине.
WIDE_TARGETS = ("map", "compare")
# Вопросы карточек-ответов о ряде; у связи и сравнения — о двух рядах.
QUESTIONS = (
    "leaders",
    "mine",
    "change",
    "growth",
    "districts",
    "heat",
    "multiples",
    "spread",
    "neighbours",
    "related",
)
PAIR_QUESTIONS = ("relation", "comparison")
# Ответы во всю ширину поля по умолчанию: тепловой таблице, малым графикам и сравнению
# тесно в половине.
WIDE_ANSWERS = ("heat", "multiples", "comparison")
# Шкала малых графиков: общая для всех клеток или своя у каждой.
MULTIPLES_SCALES = ("common", "own")


class StudyError(Exception):
    """Исследование не изменить: предел или неизвестная карточка."""


def owned(request: HttpRequest) -> QuerySet[Study]:
    """Исследования того, кто спрашивает: учётной записи или гостя по ключу сеанса."""
    if request.user.is_authenticated:
        return Study.objects.filter(owner=request.user)
    fingerprint = guest_fingerprint(request)
    if not fingerprint:
        return Study.objects.none()
    # Истёкшее исследование гостя удалит очистка; до неё его уже нет.
    return Study.objects.filter(
        owner__isnull=True, guest_key=fingerprint, expires_at__gt=timezone.now()
    )


def own_or_404(request: HttpRequest, public_id: Any) -> Study:
    """Своё исследование; чужое и несуществующее — 404."""
    study = owned(request).filter(public_id=public_id).first()
    if study is None:
        raise Http404
    return study


def create(request: HttpRequest, title: str) -> Study:
    """Новое исследование учётной записи или гостя (на сутки)."""
    user = request.user
    limit = (
        settings.USERDATA_MAX_STUDIES
        if user.is_authenticated
        else settings.USERDATA_GUEST_MAX_STUDIES
    )
    if owned(request).count() >= limit:
        raise StudyError(
            _("Исследований уже %(count)s — это предел. Удалите ненужные.") % {"count": limit}
        )
    study = Study(title=(title.strip() or _("Исследование"))[:TITLE_LENGTH])
    if user.is_authenticated:
        study.owner = user
    else:
        study.guest_key = guest_fingerprint(request, create=True)
        study.expires_at = timezone.now() + timedelta(hours=settings.USERDATA_GUEST_HOURS)
    study.save()
    return study


def blocks_of(study: Study, blocks: Iterable[Any] | None = None) -> list[dict[str, Any]]:
    """Блоки исследования по порядку; повреждённые и с неизвестной карточкой пропускаются."""
    found = []
    for block in study.blocks if blocks is None else blocks:
        if not isinstance(block, dict) or not block.get("id"):
            continue
        kind = block.get("kind")
        if kind == TEXT and isinstance(block.get("text"), str):
            found.append(block)
        elif kind == VIEW and block.get("target") in QUERY_TARGETS_BY_CODE:
            block.setdefault("parameters", {})
            block.setdefault("wide", block["target"] in WIDE_TARGETS)
            found.append(block)
        elif kind == ANSWER and _answer_ok(block):
            block.setdefault("wide", False)
            found.append(block)
    return found


def _answer_ok(block: dict[str, Any]) -> bool:
    if not isinstance(block.get("series"), str):
        return False
    if block.get("question") in PAIR_QUESTIONS:
        return isinstance(block.get("other"), str)
    return block.get("question") in QUESTIONS


def series_keys(block: dict[str, Any]) -> list[str]:
    """Ключи рядов карточки вида или ответа."""
    if block.get("kind") == ANSWER:
        return [key for key in (block.get("series"), block.get("other")) if key]
    if block.get("kind") != VIEW:
        return []
    return parameter_series_keys(block.get("parameters"))


def dataset_codes(study: Study) -> set[str]:
    """Коды таблиц, ряды которых есть в исследовании."""
    return {
        routing.dataset_code(key)
        for block in blocks_of(study)
        for key in series_keys(block)
        if routing.is_user_key(key)
    }


# --- Правка поля --------------------------------------------------------------------------


def _save(study: Study, blocks: list[dict[str, Any]]) -> None:
    """Новые блоки; прежние — для «Вернуть»."""
    study.undo = list(study.blocks or [])
    study.blocks = blocks
    study.save(update_fields=["blocks", "undo", "updated_at"])


def add_view(study: Study, target: str, query_string: str, *, title: str = "") -> dict[str, Any]:
    """Поставить карточку вида страницы с её параметрами."""
    if target not in QUERY_TARGETS_BY_CODE:
        raise StudyError(_("Этот вид в исследование не ставится."))
    return _append(
        study,
        {
            "kind": VIEW,
            "target": target,
            "parameters": parse_query_string(query_string),
            "title": title.strip()[:TITLE_LENGTH],
            "note": "",
            "wide": target in WIDE_TARGETS,
        },
    )


def add_answer(study: Study, question: str, series_key: str, other: str = "") -> dict[str, Any]:
    """Поставить ответ на вопрос о ряде (у связи — о двух рядах)."""
    block: dict[str, Any] = {
        "kind": ANSWER,
        "question": question,
        "series": series_key,
        "title": "",
        "note": "",
        "wide": question in WIDE_ANSWERS,
    }
    if question in PAIR_QUESTIONS:
        if not other or other == series_key:
            raise StudyError(_("Нужен второй показатель."))
        block["other"] = other
    elif question not in QUESTIONS:
        raise StudyError(_("Такой карточки нет."))
    return _append(study, block)


def add_text(study: Study, text: str) -> dict[str, Any]:
    """Поставить заметку."""
    return _append(study, {"kind": TEXT, "text": text.strip()[:TEXT_LENGTH]})


def _append(study: Study, block: dict[str, Any]) -> dict[str, Any]:
    blocks = blocks_of(study)
    if len(blocks) >= settings.USERDATA_STUDY_BLOCKS:
        raise StudyError(
            _("В исследовании уже %(count)s карточек и заметок — это предел.")
            % {"count": settings.USERDATA_STUDY_BLOCKS}
        )
    block = {"id": secrets.token_hex(4), **block}
    _save(study, [*blocks, block])
    return block


def change(study: Study, block_id: str, **fields: str) -> bool:
    """
    Изменить текст заметки, название или пояснение карточки, шкалу малых графиков; вернуть,
    нашёлся ли блок.
    """
    limits = {"text": TEXT_LENGTH, "title": TITLE_LENGTH, "note": NOTE_LENGTH}
    blocks = blocks_of(study)
    for block in blocks:
        if block["id"] != block_id:
            continue
        for name, value in fields.items():
            if name in limits and (name == "text") == (block["kind"] == TEXT):
                block[name] = str(value).strip()[: limits[name]]
            elif (
                name == "scale"
                and block.get("question") == "multiples"
                and value in MULTIPLES_SCALES
            ):
                block["scale"] = value
        _save(study, blocks)
        return True
    return False


def move(study: Study, block_id: str, step: int) -> None:
    """Сдвинуть блок выше (−1) или ниже (+1)."""
    blocks = blocks_of(study)
    index = next((at for at, block in enumerate(blocks) if block["id"] == block_id), None)
    if index is None:
        return
    target = index + (1 if step > 0 else -1)
    if 0 <= target < len(blocks):
        blocks[index], blocks[target] = blocks[target], blocks[index]
        _save(study, blocks)


def reorder(study: Study, order: list[str]) -> None:
    """Порядок блоков после перетаскивания: названные — в этом порядке, прочие — следом."""
    blocks = blocks_of(study)
    by_id = {block["id"]: block for block in blocks}
    placed = [by_id[block_id] for block_id in dict.fromkeys(order) if block_id in by_id]
    rest = [block for block in blocks if block["id"] not in {item["id"] for item in placed}]
    arranged = placed + rest
    if [block["id"] for block in arranged] != [block["id"] for block in blocks]:
        _save(study, arranged)


def resize(study: Study, block_id: str) -> None:
    """Карточка во всю ширину поля или в половину."""
    blocks = blocks_of(study)
    for block in blocks:
        if block["id"] == block_id and block["kind"] != TEXT:
            block["wide"] = not block.get("wide")
            _save(study, blocks)
            return


def remove(study: Study, block_id: str) -> dict[str, Any] | None:
    """Убрать блок с поля; вернуть убранный."""
    blocks = blocks_of(study)
    gone = next((block for block in blocks if block["id"] == block_id), None)
    if gone is not None:
        _save(study, [block for block in blocks if block["id"] != block_id])
    return gone


def restore(study: Study) -> bool:
    """«Вернуть»: прежние блоки на поле, нынешние — в запас; вернуть, было ли что вернуть."""
    previous = blocks_of(study, study.undo or [])
    if not study.undo:
        return False
    study.undo, study.blocks = list(study.blocks or []), previous
    study.save(update_fields=["blocks", "undo", "updated_at"])
    return True


def set_common(study: Study, year: int | None, territories: Iterable[str]) -> None:
    """Общий год и регионы карточек."""
    from apps.catalog.models import Territory
    from apps.catalog.selectors import MAX_COMPARE

    wanted = list(dict.fromkeys(str(code) for code in territories))
    known = set(Territory.objects.filter(code__in=wanted).values_list("code", flat=True))
    study.year = year
    study.territories = [code for code in wanted if code in known][:MAX_COMPARE]
    study.save(update_fields=["year", "territories", "updated_at"])
