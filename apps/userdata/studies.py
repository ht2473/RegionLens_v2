"""
Исследования: рабочее поле лаборатории — заметки и карточки по порядку, общий год и регионы.

Карточка хранит, что построить, а не числа: при открытии она строится заново на текущих
данных. Блок — словарь с опознавателем ``id``; вид ``text`` несёт текст заметки, вид
``view`` — страницу ``target`` из перечня ``QUERY_TARGETS`` с параметрами, вид ``answer`` —
ответ на вопрос ``question`` о ряде ``series`` (у связи — и о ряде ``other``). У карточек —
название, пояснение и ширина; у таблицы — раскраска ``colour``. Настройки карточки
(``configure``): свой год ``year`` и свои отмеченные регионы ``territories`` поверх общих,
у карты — знаки и шкала в параметрах вида и подписи ``labels``.

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
    "notable",
    "leaders",
    "mine",
    "change",
    "growth",
    "districts",
    "multiples",
    "spread",
    "convergence",
    "neighbours",
    "related",
    "structure",
    "contributions",
    "group",
)
PAIR_QUESTIONS = ("relation", "comparison")
# Вопросы о нескольких рядах — частях: ряд карточки — первая часть, все — в ``parts``.
PARTS_QUESTIONS = ("structure",)
# Вопросы, где части по желанию: с ними вклады составляющих вместо вкладов регионов.
SPLIT_QUESTIONS = ("contributions",)
# Вопросы о своей группе регионов: в блоке — её опознаватель.
GROUP_QUESTIONS = ("group",)
# Частей структуры не больше этого.
MAX_PARTS = 30
# Ответы во всю ширину поля по умолчанию: малым графикам и сравнению тесно в половине.
WIDE_ANSWERS = ("multiples", "comparison", "structure", "contributions", "group")
# Шкала малых графиков: общая для всех клеток или своя у каждой.
MULTIPLES_SCALES = ("common", "own")
# Раскраска таблицы: без цвета или по значению — ступенью общей шкалы клеток.
TABLE_COLOURS = ("", "value")


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
    for stored in study.blocks if blocks is None else blocks:
        if not isinstance(stored, dict) or not stored.get("id"):
            continue
        block = _legacy(stored)
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


def _legacy(block: dict[str, Any]) -> dict[str, Any]:
    """
    Прежняя карточка «Тепловая таблица» — таблица значений по годам с раскраской: вид
    заменил отдельный ответ, название, пояснение и ширина сохраняются.
    """
    if block.get("kind") != ANSWER or block.get("question") != "heat":
        return block
    return {
        "id": block["id"],
        "kind": VIEW,
        "target": "table",
        "parameters": {"series": block.get("series", "")},
        "title": block.get("title", ""),
        "note": block.get("note", ""),
        "wide": block.get("wide", True),
        "colour": "value",
    }


def _answer_ok(block: dict[str, Any]) -> bool:
    if not isinstance(block.get("series"), str):
        return False
    if block.get("question") in PAIR_QUESTIONS:
        return isinstance(block.get("other"), str)
    if block.get("question") in GROUP_QUESTIONS and not isinstance(block.get("group"), str):
        return False
    if block.get("question") in PARTS_QUESTIONS or "parts" in block:
        parts = block.get("parts")
        if not (isinstance(parts, list) and all(isinstance(key, str) for key in parts)):
            return False
    return block.get("question") in QUESTIONS


def series_keys(block: dict[str, Any]) -> list[str]:
    """Ключи рядов карточки вида или ответа."""
    if block.get("kind") == ANSWER:
        keys = [key for key in (block.get("series"), block.get("other")) if key]
        return list(dict.fromkeys([*keys, *(block.get("parts") or [])]))
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


def view_block(target: str, query_string: str, *, title: str = "") -> dict[str, Any]:
    """Карточка вида страницы с её параметрами — ещё не на поле."""
    if target not in QUERY_TARGETS_BY_CODE:
        raise StudyError(_("Этот вид в исследование не ставится."))
    return {
        "kind": VIEW,
        "target": target,
        "parameters": parse_query_string(query_string),
        "title": title.strip()[:TITLE_LENGTH],
        "note": "",
        "wide": target in WIDE_TARGETS,
    }


def add_view(study: Study, target: str, query_string: str, *, title: str = "") -> dict[str, Any]:
    """Поставить карточку вида страницы с её параметрами."""
    return _append(study, view_block(target, query_string, title=title))


def add_answer(
    study: Study,
    question: str,
    series_key: str,
    other: str = "",
    *,
    parts: list[str] | None = None,
    group: str = "",
) -> dict[str, Any]:
    """
    Поставить ответ на вопрос о ряде (у связи — о двух рядах, у структуры — о частях,
    у группы регионов — о ней).
    """
    return _append(study, answer_block(question, series_key, other, parts=parts, group=group))


def answer_block(
    question: str,
    series_key: str,
    other: str = "",
    *,
    parts: list[str] | None = None,
    group: str = "",
) -> dict[str, Any]:
    """
    Ответ на вопрос о ряде — ещё не на поле: у связи — о двух рядах, у структуры —
    о частях (ряд карточки — первая из них), у группы регионов — опознаватель группы.
    """
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
    elif question in PARTS_QUESTIONS or (question in SPLIT_QUESTIONS and parts):
        wanted = list(dict.fromkeys(parts or []))[:MAX_PARTS]
        if len(wanted) < 2:  # noqa: PLR2004
            raise StudyError(
                _("Для структуры нужны хотя бы две части.")
                if question in PARTS_QUESTIONS
                else _("Для вкладов составляющих нужны хотя бы две части.")
            )
        block["series"], block["parts"] = wanted[0], wanted
    elif question in GROUP_QUESTIONS:
        if not group:
            raise StudyError(_("Выберите группу регионов."))
        block["group"] = group
    elif question not in QUESTIONS:
        raise StudyError(_("Такой карточки нет."))
    return block


def add_text(study: Study, text: str) -> dict[str, Any]:
    """Поставить заметку."""
    return _append(study, {"kind": TEXT, "text": text.strip()[:TEXT_LENGTH]})


def _append(study: Study, block: dict[str, Any]) -> dict[str, Any]:
    return append_all(study, [block])[0]


def append_all(study: Study, new: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Поставить карточки на поле одной правкой: «Вернуть» убирает их все."""
    blocks = blocks_of(study)
    if len(blocks) + len(new) > settings.USERDATA_STUDY_BLOCKS:
        raise StudyError(
            _("В исследовании уже %(count)s карточек и заметок — это предел.")
            % {"count": settings.USERDATA_STUDY_BLOCKS}
        )
    made = [{"id": secrets.token_hex(4), **block} for block in new]
    _save(study, [*blocks, *made])
    return made


def change(study: Study, block_id: str, **fields: str) -> bool:
    """
    Изменить текст заметки, название или пояснение карточки, шкалу малых графиков,
    раскраску таблицы; вернуть, нашёлся ли блок.
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
            elif name == "colour" and block.get("target") == "table" and value in TABLE_COLOURS:
                block["colour"] = value
        _save(study, blocks)
        return True
    return False


# Настройки карточки: знаки карты, подписи, выбор отмеченных регионов.
MARK_CHOICES = ("auto", "fill", "symbols")
LABEL_CHOICES = ("", "off")
REGION_CHOICES = ("common", "none", "own")
# Год карточки — в пределах этих лет.
YEARS = range(1900, 2101)
# Карточки-ответы, которые показывают отмеченный регион; у прочих настройки регионов нет.
MARKED_QUESTIONS = frozenset({"change", "multiples", "comparison"})
# Карточки-ответы без года: сближение — за весь период, связь — за свой общий год.
YEARLESS_QUESTIONS = frozenset({"convergence", "related"})


def takes_regions(block: dict[str, Any]) -> bool:
    """Меняют ли карточку отмеченные регионы."""
    return block["kind"] == VIEW or block.get("question") in MARKED_QUESTIONS


def takes_year(block: dict[str, Any]) -> bool:
    """Меняет ли карточку год."""
    return block.get("question") not in YEARLESS_QUESTIONS


def configure(study: Study, block_id: str, data: Any) -> bool:
    """
    Настройки карточки одной формой: вид (у вида холста), год, отмеченные регионы, способ
    изображения, разбиение и подписи карты, цветовая шкала таблицы, ось малых графиков.
    Неизвестное и неуместное для вида карточки отбрасывается. ``data`` — поля формы
    (``QueryDict``); вернуть, нашёлся ли блок.
    """
    blocks = blocks_of(study)
    block = next((item for item in blocks if item["id"] == block_id), None)
    if block is None or block["kind"] == TEXT:
        return False
    if block["kind"] == VIEW and block["target"] in CANVAS_TARGETS:
        target = data.get("target", block["target"])
        # Ширина остаётся выбранной: поле не перестраивается от смены вида карточки.
        if target in CANVAS_TARGETS:
            block["target"] = target
    if "year" in data and takes_year(block):
        raw = str(data.get("year", ""))
        if raw.isdigit() and int(raw) in YEARS:
            block["year"] = int(raw)
        else:
            block.pop("year", None)
    if data.get("regions") in REGION_CHOICES and takes_regions(block):
        _configure_regions(block, data.get("regions"), data.getlist("territory"))
    if block["kind"] == VIEW:
        block["parameters"] = _view_parameters(block, data)
    choices = (
        ("labels", block.get("target") == "map", LABEL_CHOICES),
        ("colour", block.get("target") == "table", TABLE_COLOURS),
        ("scale", block.get("question") == "multiples", MULTIPLES_SCALES),
    )
    for name, fits, allowed in choices:
        if fits and data.get(name) in allowed:
            block[name] = data.get(name)
    _save(study, blocks)
    return True


def _view_parameters(block: dict[str, Any], data: Any) -> dict[str, Any]:
    """Знаки и шкала — параметры вида: с ними же открывается вид целиком."""
    from apps.maps.classification import MAX_CLASSES, METHODS, MIN_CLASSES

    parameters = dict(block.get("parameters") or {})
    target = block["target"]
    if target == "map" and data.get("marks") in MARK_CHOICES:
        if data.get("marks") == "auto":
            parameters.pop("marks", None)
        else:
            parameters["marks"] = data.get("marks")
    if target in {"map", "distribution"}:
        if data.get("method") in METHODS:
            parameters["method"] = data.get("method")
        classes = str(data.get("classes", ""))
        if classes.isdigit() and MIN_CLASSES <= int(classes) <= MAX_CLASSES:
            parameters["classes"] = classes
    return parameters


def _configure_regions(block: dict[str, Any], choice: str, codes: list[str]) -> None:
    """Отмеченные регионы карточки: как у исследования, никаких или свои (не больше предела)."""
    from apps.catalog.models import Territory
    from apps.catalog.selectors import MAX_COMPARE

    if choice == "common":
        block.pop("territories", None)
        return
    if choice == "none":
        block["territories"] = []
        return
    wanted = list(dict.fromkeys(str(code) for code in codes if code))
    known = set(
        Territory.objects.comparable().filter(code__in=wanted).values_list("code", flat=True)
    )
    block["territories"] = [code for code in wanted if code in known][:MAX_COMPARE]


def card_common(
    block: dict[str, Any], year: int | None, territories: list[str]
) -> tuple[int | None, list[str]]:
    """Год и отмеченные регионы карточки: свои, если заданы в её настройках, иначе общие."""
    own_year = block.get("year")
    own_regions = block.get("territories")
    return (
        own_year if isinstance(own_year, int) else year,
        list(own_regions) if isinstance(own_regions, list) else territories,
    )


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


def toggle_report(study: Study, block_id: str) -> None:
    """Карточка или заметка в отчёте (Word, Excel, печать) или нет; по умолчанию — в отчёте."""
    blocks = blocks_of(study)
    for block in blocks:
        if block["id"] == block_id:
            if block.get("report", True):
                block["report"] = False
            else:
                block.pop("report", None)
            _save(study, blocks)
            return


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


def add_territory(study: Study, code: str) -> str:
    """Регион — к общим регионам исследования, если его там нет; вернуть его название."""
    from apps.catalog.models import Territory
    from apps.catalog.selectors import MAX_COMPARE

    territory = Territory.objects.comparable().filter(code=code).first()
    if territory is None:
        raise StudyError(_("Такого региона нет."))
    current = list(study.territories or [])
    if code not in current:
        if len(current) >= MAX_COMPARE:
            raise StudyError(
                _("В исследовании уже %(count)s регионов — это предел.") % {"count": MAX_COMPARE}
            )
        set_common(study, study.year, [*current, code])
    return territory.name


def set_common(study: Study, year: int | None, territories: Iterable[str]) -> None:
    """Общий год и регионы карточек."""
    from apps.catalog.models import Territory
    from apps.catalog.selectors import MAX_COMPARE

    wanted = list(dict.fromkeys(str(code) for code in territories))
    known = set(Territory.objects.filter(code__in=wanted).values_list("code", flat=True))
    study.year = year
    study.territories = [code for code in wanted if code in known][:MAX_COMPARE]
    study.save(update_fields=["year", "territories", "updated_at"])
