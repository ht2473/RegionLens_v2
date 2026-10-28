"""
Исследования: создание, страница исследования (панель данных и поле карточек), карточки
фрагментами, правка поля, «Вернуть», удаление и «В исследование» со страниц холста.

Исследование видит владелец — учётная запись или гость по ключу сеанса — и читатель его
закрытой ссылки (только чтение); чужое — 404. Правки поля ограничены по частоте.

Правка без сценариев — форма с переходом обратно на страницу. Запрос HTMX получает ту же
страницу после перехода и забирает из неё нужные части; перестановка, ширина и название
на месте приходят запросом сценария и получают пустой ответ.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote, urlencode
from uuid import UUID

from django.conf import settings
from django.contrib import messages
from django.core.files.uploadedfile import UploadedFile
from django.http import Http404, HttpRequest, HttpResponse, QueryDict
from django.http.response import HttpResponseBase
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import get_language, gettext
from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView, View

from apps.catalog.selectors import MAX_COMPARE, grouped_territories
from apps.core.navigation import Crumb
from apps.core.throttle import allow, allow_key
from apps.core.utils.redirects import safe_back
from apps.core.views import BreadcrumbMixin
from apps.warehouse.duckdb_client import WarehouseNotBuiltError
from apps.warehouse.queries import year_counts

from . import jobs, lab, shares, studies, study_cards
from .models import Study

# Окно предела правок поля, секунд.
EDIT_WINDOW = 600
# Действия с одним блоком.
BLOCK_ACTIONS = frozenset({"resize", "change", "configure", "report", "up", "down", "remove"})
# Действия, после которых сценарию не нужна разметка: он уже показал изменение.
SILENT_ACTIONS = frozenset({"resize", "change", "configure", "report", "reorder", "rename"})
# Отчётов Word и Excel за окно: сборка обходит все карточки исследования.
REPORTS_PER_WINDOW = 20
REPORT_WINDOW = 600
# Картинки отчёта со страницы: каждая и все вместе не больше этого.
REPORT_IMAGE_BYTES = 4 * 1024 * 1024
REPORT_IMAGES_BYTES = 20 * 1024 * 1024


def readable_study(request: HttpRequest, public_id: Any) -> tuple[Study, bool]:
    """Своё исследование или открытое закрытой ссылкой; вторым — владелец ли читает."""
    own = studies.owned(request).filter(public_id=public_id).first()
    if own is not None:
        return own, True
    study = Study.objects.filter(public_id=public_id, owner__isnull=False).first()
    if study is not None and shares.opened_study(request, study) is not None:
        return study, False
    raise Http404


def _common(request: HttpRequest, study: Study) -> tuple[int | None, list[str]]:
    """Общий год и регионы: из адреса (просмотр по ссылке) или сохранённые в исследовании."""
    year: int | None = study.year
    territories = list(study.territories or [])
    if "year" in request.GET:
        raw = request.GET.get("year", "")
        year = int(raw) if raw.isdigit() else None
    if "territory" in request.GET or request.GET.get("common") == "1":
        drop = request.GET.get("drop", "")
        territories = [code for code in request.GET.getlist("territory") if code and code != drop][
            :MAX_COMPARE
        ]
    return year, territories


def edit_allowed(request: HttpRequest) -> bool:
    """Правка поля в пределах частоты: с учётной записи, у гостя — с адреса."""
    limit = settings.USERDATA_STUDY_EDITS
    if request.user.is_authenticated:
        return allow_key("study-edit", f"user:{request.user.pk}", limit=limit, window=EDIT_WINDOW)
    return allow(request, "study-edit", limit=limit, window=EDIT_WINDOW)


def _url(study: Study, **params: str) -> str:
    """Адрес исследования с параметрами панели и отметками поля."""
    url = reverse("userdata:study", args=[study.public_id])
    pairs = {name: value for name, value in params.items() if value}
    return f"{url}?{urlencode(pairs)}" if pairs else url


class StudyCreateView(View):
    """Новое исследование — у учётной записи или у гостя на сутки."""

    def post(self, request: HttpRequest) -> HttpResponse:
        if not edit_allowed(request):
            messages.error(request, gettext("Слишком много изменений подряд. Повторите позже."))
            return redirect("userdata:index")
        try:
            study = studies.create(request, request.POST.get("title", ""))
        except studies.StudyError as error:
            messages.error(request, str(error))
            return redirect(f"{reverse('userdata:index')}#own-studies")
        return redirect("userdata:study", public_id=study.public_id)


class StudyView(BreadcrumbMixin, TemplateView):
    """
    Исследование: панель данных слева, поле карточек с общим годом и регионами справа;
    владельцу — «Что сделать» у показателя, правка карточек и ссылки.
    """

    template_name = "userdata/study.html"

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        self.study, self.owner = readable_study(request, kwargs["public_id"])
        return super().dispatch(request, *args, **kwargs)

    def get_crumbs(self) -> tuple[Crumb, ...]:
        if not self.owner:
            return (Crumb(title=self.study.title),)
        return (
            Crumb(title=_("Свои данные"), url=reverse("userdata:index")),
            Crumb(title=self.study.title),
        )

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        from .share_views import share_context

        context = super().get_context_data(**kwargs)
        study = self.study
        request = self.request
        year, territories = _common(request, study)
        pairs = [("common", "1")]
        if year is not None:
            pairs.append(("year", str(year)))
        pairs.extend(("territory", code) for code in territories)
        common = urlencode(pairs)
        blocks = studies.blocks_of(study)
        for block in blocks:
            if block["kind"] != studies.TEXT:
                url = reverse("userdata:study-card", args=[study.public_id, block["id"]])
                block["card_url"] = f"{url}?{common}"
                block["target_title"] = study_cards.kind_title(block)
                # Ряд карточки: брошенный на неё ряд сравнивается с ним.
                keys = studies.series_keys(block)
                block["primary_key"] = keys[0] if keys else ""
        context.update(
            page_title=study.title,
            study=study,
            owner=self.owner,
            guest=study.owner_id is None,
            blocks=blocks,
            # Только что поставленные карточки: одна или набор через запятую.
            added=[item for item in request.GET.get("added", "").split(",") if item],
            removed=request.GET.get("removed") == "1" and bool(study.undo),
            year=year,
            territories=territories,
            years=_years(blocks),
            territory_groups=grouped_territories(),
            max_compare=MAX_COMPARE,
            canvas_count=sum(1 for block in blocks if _follows_common(block)),
            card_count=sum(1 for block in blocks if block["kind"] != studies.TEXT),
            shared=shares.of(study).exists() if self.owner else True,
            selected_names=_names(territories),
            report_allowed=self.owner or _downloads(request, study),
        )
        if self.owner:
            if study.owner_id is not None:
                context.update(share_context(request, study))
            panel = lab.panel(
                request,
                study,
                request.GET.get("series", ""),
                request.GET.get("with", ""),
                picking=request.GET.get("pick") == "1",
            )
            context["panel"] = panel
            context["default_series"] = _default_series(panel, blocks)
            # Окно «Добавить карточку» открыто сразу, если показатель выбран адресом (без сценария
            # или по ссылке), но не после добавления карточки и не при выборе второго.
            context["choose_open"] = bool(
                panel["chosen"] and not panel["picking"] and not request.GET.get("added")
            )
        context["sources"] = _sources(blocks)
        return context


def _default_series(panel: dict[str, Any], blocks: list[dict[str, Any]]) -> str:
    """Показатель для «+ Карточка»: выбранный, иначе первый на поле, иначе первый в панели."""
    if panel["chosen"] is not None:
        return str(panel["chosen"].key)
    for block in blocks:
        keys = studies.series_keys(block)
        if keys:
            return keys[0]
    for source in panel["tables"]:
        for indicator in source.indicators:
            for entry in indicator.entries:
                return str(entry.key)
    return ""


def _sources(blocks: list[dict[str, Any]]) -> list[str]:
    """
    Источники данных исследования одним перечнем внизу поля, а не под каждой карточкой:
    у своей таблицы — её название и названный при загрузке источник, у ряда сайта — издатель.
    """
    from apps.core.citation import series_publisher
    from apps.warehouse import routing

    from .series import user_series_many

    keys = list(dict.fromkeys(key for block in blocks for key in studies.series_keys(block)))
    own = user_series_many([key for key in keys if routing.is_user_key(key)])
    found: list[str] = []
    for key in keys:
        if routing.is_user_key(key):
            series = own.get(key)
            if series is None:
                continue
            dataset = series.dataset
            line = (
                gettext("«%(table)s» — %(source)s")
                % {"table": dataset.title, "source": dataset.source_title}
                if dataset.source_title
                else gettext("«%(table)s» — ваша таблица") % {"table": dataset.title}
            )
        else:
            line = series_publisher(key)
        if line and line not in found:
            found.append(line)
    return found


def _follows_common(block: dict[str, Any]) -> bool:
    """Карточка следует общему году и регионам: вид холста или ответ."""
    if block["kind"] == studies.ANSWER:
        return True
    return block["kind"] == studies.VIEW and block["target"] in studies.CANVAS_TARGETS


def _names(codes: list[str]) -> list[tuple[str, str]]:
    """Коды и названия общих регионов — для чипов над полем."""
    from apps.catalog.models import Territory

    column = "name_ru" if get_language() == "ru" else "name_en"
    names = dict(Territory.objects.filter(code__in=codes).values_list("code", column))
    return [(code, names[code]) for code in codes if code in names]


def _years(blocks: list[dict[str, Any]]) -> list[int]:
    """Годы рядов карточек — для общего выбора года."""
    found: set[int] = set()
    for block in blocks:
        if not _follows_common(block):
            continue
        for key in studies.series_keys(block):
            try:
                found.update(int(year) for year in year_counts(key))
            except Http404, WarehouseNotBuiltError:
                continue
    return sorted(found, reverse=True)


class StudyCardView(View):
    """Карточка исследования фрагментом: вид строится заново на текущих данных."""

    def get(self, request: HttpRequest, public_id: str, block_id: str) -> HttpResponse:
        study, owner = readable_study(request, public_id)
        block = next(
            (
                item
                for item in studies.blocks_of(study)
                if item["id"] == block_id and item["kind"] != studies.TEXT
            ),
            None,
        )
        if block is None:
            raise Http404
        common_year, common_territories = _common(request, study)
        year, territories = studies.card_common(block, common_year, common_territories)
        card = study_cards.render(request, block, year=year, territories=territories)
        if card["template"] == "userdata/cards/_missing.html" and _building(block):
            card["template"] = "userdata/cards/_building.html"
            card["poll_url"] = request.get_full_path()
        elif owner and card["template"] not in study_cards.EMPTY_TEMPLATES:
            card["next"] = lab.next_steps(study, block)
        if owner and card["template"] != "userdata/cards/_building.html":
            card["settings"] = study_cards.settings_of(block, card, common_year=common_year)
        # Имена вида — и на верхнем уровне: карта и легенда холста включаются как есть.
        response = render(
            request,
            "userdata/cards/_card.html",
            {**card, "card": card, "study": study, "owner": owner},
        )
        response["Cache-Control"] = "private, no-store"
        return response


def _building(block: dict[str, Any]) -> bool:
    """Таблица ряда карточки пересобирается: ряд появится, когда сборка закончится."""
    from apps.warehouse import routing

    from .models import Dataset

    for key in studies.series_keys(block):
        if not routing.is_user_key(key):
            continue
        dataset = (
            Dataset.objects.select_related("current_version")
            .filter(code=routing.dataset_code(key))
            .first()
        )
        version = dataset.current_version if dataset is not None else None
        if version is not None and jobs.stage_state(version, jobs.BUILD) in {
            jobs.RUNNING,
            jobs.PENDING,
        }:
            jobs.resume(version, jobs.BUILD)
            return True
    return False


class StudyEditView(View):
    """Правка исследования владельцем: название, карточки и заметки, порядок, общий выбор."""

    def post(self, request: HttpRequest, public_id: str) -> HttpResponse:
        study = studies.own_or_404(request, public_id)
        action = request.POST.get("action", "")
        scripted = bool(request.headers.get("HX-Request") or request.headers.get("X-Study"))
        if not edit_allowed(request):
            if scripted:
                return HttpResponse(status=429)
            messages.error(request, gettext("Слишком много изменений подряд. Повторите позже."))
            return redirect(_url(study))
        try:
            target = self.apply(request, study, action)
        except studies.StudyError as error:
            if request.headers.get("X-Study"):
                return HttpResponse(str(error), status=400, content_type="text/plain")
            messages.error(request, str(error))
            target = _url(study, series=request.POST.get("series", ""))
        if request.headers.get("X-Study") and action in SILENT_ACTIONS:
            return HttpResponse(status=204)
        if request.headers.get("HX-Request") and request.POST.get("do", "").startswith(
            lab.SET_PREFIX
        ):
            # Набор: карточек несколько, у «соседей» меняются и общие регионы — страница заново.
            response = HttpResponse(status=204)
            response["HX-Redirect"] = target
            return response
        return redirect(target)

    def apply(self, request: HttpRequest, study: Study, action: str) -> str:
        """Выполнить правку; вернуть адрес, куда перейти после неё."""
        if action in BLOCK_ACTIONS:
            return _edit_block(study, action, request.POST.get("block", ""), request.POST)
        handler = EDITS.get(action)
        if handler is None:
            raise studies.StudyError(gettext("Такого действия нет."))
        return handler(request, study)


def _rename(request: HttpRequest, study: Study) -> str:
    data = request.POST
    study.title = (data.get("title", "").strip() or study.title)[: studies.TITLE_LENGTH]
    if "description" in data:
        study.description = data.get("description", "").strip()[: studies.TEXT_LENGTH]
    study.save(update_fields=["title", "description", "updated_at"])
    return _url(study)


def _add_text(request: HttpRequest, study: Study) -> str:
    block = studies.add_text(study, request.POST.get("text", ""))
    return f"{_url(study, added=block['id'])}#block-{block['id']}"


def _add(request: HttpRequest, study: Study) -> str:
    """Карточка из «Что сделать» или ряд, брошенный на поле (``at`` — место броска)."""
    data = request.POST
    key = data.get("series", "")
    before = {item["id"] for item in studies.blocks_of(study)}
    block = lab.add(
        request,
        study,
        data.get("do", ""),
        key,
        data.get("other", ""),
        parts=data.getlist("part"),
        group=data.get("group", ""),
    )
    if data.get("at", "").isdigit():
        order = [item["id"] for item in studies.blocks_of(study) if item["id"] != block["id"]]
        order.insert(int(data["at"]), block["id"])
        studies.reorder(study, order)
    added = [item["id"] for item in studies.blocks_of(study) if item["id"] not in before]
    return f"{_url(study, series=key, added=','.join(added))}#block-{block['id']}"


def _reorder(request: HttpRequest, study: Study) -> str:
    studies.reorder(study, request.POST.getlist("order"))
    return _url(study)


def _undo(request: HttpRequest, study: Study) -> str:  # noqa: ARG001 — подпись обработчика
    studies.restore(study)
    return _url(study)


def _common_choice(request: HttpRequest, study: Study) -> str:
    data = request.POST
    raw, drop = data.get("year", ""), data.get("drop", "")
    studies.set_common(
        study,
        int(raw) if raw.isdigit() else None,
        [code for code in data.getlist("territory") if code != drop],
    )
    return _url(study)


def _delete(request: HttpRequest, study: Study) -> str:
    if not request.POST.get("confirm"):
        raise studies.StudyError(gettext("Отметьте, что исследование нужно удалить."))
    title = study.title
    study.delete()
    messages.success(request, gettext("Исследование «%(title)s» удалено.") % {"title": title})
    return f"{reverse('userdata:index')}#own-studies"


# Обработчики правок поля по действию формы.
EDITS = {
    "rename": _rename,
    "add-text": _add_text,
    "add": _add,
    "reorder": _reorder,
    "undo": _undo,
    "common": _common_choice,
    "delete": _delete,
}


def _edit_block(study: Study, action: str, block_id: str, data: QueryDict) -> str:
    """
    Правка одного блока: ширина, текст и названия, настройки «Вида», порядок, удаление;
    вернуть адрес.
    """
    if action == "resize":
        studies.resize(study, block_id)
    elif action == "report":
        studies.toggle_report(study, block_id)
    elif action == "change":
        fields = {
            name: data.get(name, "")
            for name in ("text", "title", "note", "scale", "colour")
            if name in data
        }
        studies.change(study, block_id, **fields)
    elif action == "configure":
        studies.configure(study, block_id, data)
    elif action in {"up", "down"}:
        studies.move(study, block_id, -1 if action == "up" else 1)
    else:
        studies.remove(study, block_id)
        return _url(study, removed="1")
    return f"{_url(study)}#block-{block_id}"


def _own_study(request: HttpRequest, public_id: str) -> Study:
    """Своё исследование по опознавателю из формы; чужое и неверное — 404."""
    try:
        identifier = UUID(public_id)
    except ValueError as error:
        raise Http404 from error
    return studies.own_or_404(request, identifier)


class StudyAddView(View):
    """
    «В исследование» из меню «Сохранить»: вид с параметрами — карточкой, регион
    (поле ``territory``) — к регионам исследования.
    """

    def post(self, request: HttpRequest) -> HttpResponse:
        back = safe_back(request, reverse("core:home"))
        target = request.POST.get("target", "")
        territory = request.POST.get("territory", "")
        chosen = request.POST.get("study", "")
        if not edit_allowed(request):
            messages.error(request, gettext("Слишком много изменений подряд. Повторите позже."))
            return redirect(back)
        try:
            if chosen == "new" or not chosen:
                study = studies.create(request, request.POST.get("new_title", ""))
            else:
                study = _own_study(request, chosen)
            if territory:
                name = studies.add_territory(study, territory)
                done = gettext("Регион «%(name)s» добавлен в исследование «%(title)s».") % {
                    "name": name,
                    "title": study.title,
                }
            else:
                studies.add_view(study, target, request.POST.get("query_string", ""))
                done = gettext("Вид добавлен в исследование «%(title)s».") % {"title": study.title}
        except studies.StudyError as error:
            messages.error(request, str(error))
            return redirect(back)
        messages.success(
            request,
            format_html(
                '{} <a href="{}">{}</a>',
                done,
                reverse("userdata:study", args=[study.public_id]),
                gettext("Открыть"),
            ),
        )
        if request.POST.get("open") == "1":
            return redirect("userdata:study", public_id=study.public_id)
        return redirect(back)


class StudyReportView(View):
    """
    Отчёт исследования файлом: Word — карточки, заметки, расчёты и источники; Excel — данные
    карточек. Владельцу и читателю закрытой ссылки, разрешающей скачивать. Word формой
    со страницы получает картинки графиков и карт (``image-<карточка>-<номер>``); без
    сценария и по ссылке — только числа и таблицы.
    """

    def get(self, request: HttpRequest, public_id: str) -> HttpResponse:
        return self._answer(request, public_id, request.GET.get("format", ""), {})

    def post(self, request: HttpRequest, public_id: str) -> HttpResponse:
        return self._answer(
            request, public_id, request.POST.get("format", ""), _report_images(request)
        )

    def _answer(
        self, request: HttpRequest, public_id: str, kind: str, images: dict[str, list[bytes]]
    ) -> HttpResponse:
        from . import report

        study, owner = readable_study(request, public_id)
        if not (owner or _downloads(request, study)) or kind not in {"docx", "xlsx"}:
            raise Http404
        if not _report_allowed(request):
            return HttpResponse(
                gettext("Слишком много отчётов подряд. Повторите позже."),
                status=429,
                content_type="text/plain; charset=utf-8",
            )
        if kind == "xlsx":
            body = report.excel(request, study)
            content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        else:
            body = report.word(request, study, images)
            content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        response = HttpResponse(body, content_type=content_type)
        name = re.sub(r'[\\/:*?"<>|\s]+', " ", study.title).strip()[:80] or "study"
        response["Content-Disposition"] = (
            f'attachment; filename="regionlens-study.{kind}"; '
            f"filename*=UTF-8''{quote(f'{name}.{kind}')}"
        )
        response["Cache-Control"] = "private, no-store"
        return response


def _downloads(request: HttpRequest, study: Study) -> bool:
    """Читатель закрытой ссылки может скачивать: так отмечено у ссылки."""
    share = shares.opened_study(request, study)
    return share is not None and share.downloads


def _report_allowed(request: HttpRequest) -> bool:
    """Отчёты в пределах частоты: с учётной записи, у гостя и читателя — с адреса."""
    if request.user.is_authenticated:
        return allow_key(
            "study-report",
            f"user:{request.user.pk}",
            limit=REPORTS_PER_WINDOW,
            window=REPORT_WINDOW,
        )
    return allow(request, "study-report", limit=REPORTS_PER_WINDOW, window=REPORT_WINDOW)


def _report_images(request: HttpRequest) -> dict[str, list[bytes]]:
    """
    Картинки карточек из формы: поле ``image-<карточка>-<номер>``, только PNG, каждая
    и все вместе в пределах; лишнее отбрасывается молча — отчёт выйдет без этой картинки.
    """
    from apps.exports.renderers.docx import PNG_SIGNATURE

    found: dict[str, list[tuple[int, bytes]]] = {}
    total = 0
    for name in request.FILES:
        upload = request.FILES.get(name)
        matched = re.fullmatch(r"image-([0-9a-f]{8})-(\d{1,2})", name)
        if (
            matched is None
            or not isinstance(upload, UploadedFile)
            or upload.size is None
            or upload.size > REPORT_IMAGE_BYTES
        ):
            continue
        total += upload.size
        if total > REPORT_IMAGES_BYTES:
            break
        data = upload.read()
        if data.startswith(PNG_SIGNATURE):
            found.setdefault(matched.group(1), []).append((int(matched.group(2)), data))
    return {block: [data for _number, data in sorted(items)] for block, items in found.items()}


class StructureView(TemplateView):
    """
    Выбор частей структуры для ряда своей таблицы: окном на поле (фрагмент) или отдельной
    страницей без сценариев. Форма ставит карточку «Структура» обычной правкой поля.
    """

    template_name = "userdata/structure.html"

    def get_template_names(self) -> list[str]:
        if self.request.headers.get("HX-Request"):
            return ["userdata/_structure_pick.html"]
        return [self.template_name]

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        study = studies.owned(self.request).filter(public_id=kwargs["public_id"]).first()
        if study is None:
            raise Http404
        key = self.request.GET.get("series", "")
        candidates, chosen = lab.structure_choice(self.request, study, key)
        context.update(study=study, series=key, candidates=candidates, chosen=chosen)
        return context


class StudyChooseView(View):
    """
    Окно «Добавить карточку» по показателю своего исследования — фрагментом для окна на странице;
    без сценария — та же страница исследования с открытым окном.
    """

    def get(self, request: HttpRequest, public_id: str) -> HttpResponse:
        study = studies.owned(request).filter(public_id=public_id).first()
        if study is None:
            raise Http404
        series = request.GET.get("series", "")
        other = request.GET.get("with", "")
        if not request.headers.get("HX-Request"):
            query = urlencode(
                {key: value for key, value in (("series", series), ("with", other)) if value}
            )
            return redirect(f"{reverse('userdata:study', args=[study.public_id])}?{query}")
        panel = lab.panel(request, study, series, other)
        if panel["chosen"] is None:
            raise Http404
        response = render(request, "userdata/_study_choose.html", {"study": study, "panel": panel})
        response["Cache-Control"] = "private, no-store"
        return response
