"""Мастер своих данных: «Файл» → «Что в таблице» → «Показатели»; черновик хранится в базе."""

from __future__ import annotations

from typing import Any

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.functional import Promise
from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView, View

from apps.core.navigation import Crumb
from apps.core.views import BreadcrumbMixin
from apps.sources import periods

from . import access, describe, ingest, jobs, matching, recognize, services
from .forms import ChooseTableForm, PasteForm, UploadForm
from .models import Dataset, DatasetVersion

STEPS: tuple[tuple[str, Promise], ...] = (
    ("file", _("Файл")),
    ("table", _("Что в таблице")),
    ("series", _("Показатели")),
)


def steps(current: str, dataset: Dataset | None = None) -> list[dict[str, Any]]:
    """Шаги мастера: пройденные — ссылками, текущий отмечен."""
    version = dataset.current_version if dataset else None
    reachable = {"file": dataset is not None, "table": bool(version and version.recipe)}
    result = []
    for number, (code, title) in enumerate(STEPS, start=1):
        url = ""
        if dataset is not None and reachable.get(code) and code != current:
            url = reverse(f"userdata:{code}", kwargs={"public_id": dataset.public_id})
        result.append({"number": number, "title": title, "current": code == current, "url": url})
    return result


class UploadView(BreadcrumbMixin, TemplateView):
    """Начало: файл или вставка из буфера."""

    template_name = "userdata/upload.html"

    def get_crumbs(self) -> tuple[Crumb, ...]:
        return (Crumb(title=_("Свои данные")), Crumb(title=_("Загрузить таблицу")))

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context.setdefault("upload_form", UploadForm())
        context.setdefault("paste_form", PasteForm())
        context["page_title"] = _("Загрузить таблицу")
        context["steps"] = steps("file")
        context["upload_limit"] = services.upload_size_text()
        return context

    def post(self, request: HttpRequest) -> HttpResponse:
        if request.POST.get("action") == "paste":
            paste_form = PasteForm(request.POST)
            if paste_form.is_valid():
                try:
                    dataset = services.create_from_paste(
                        request, paste_form.cleaned_data["text"], paste_form.cleaned_data["markup"]
                    )
                except ingest.IngestError as error:
                    paste_form.add_error("text", str(error))
                else:
                    return redirect("userdata:file", public_id=dataset.public_id)
            return self.render_to_response(self.get_context_data(paste_form=paste_form))
        upload_form = UploadForm(request.POST, request.FILES)
        if upload_form.is_valid():
            try:
                dataset = services.create_from_upload(request, upload_form.cleaned_data["file"])
            except ingest.IngestError as error:
                upload_form.add_error("file", str(error))
            else:
                return redirect("userdata:file", public_id=dataset.public_id)
        return self.render_to_response(self.get_context_data(upload_form=upload_form))


class DatasetStepMixin(BreadcrumbMixin):
    """Шаг мастера над своим набором; чужой — 404."""

    step = ""
    dataset: Dataset
    version: DatasetVersion

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        self.dataset = access.dataset_or_404(request, kwargs["public_id"])
        version = self.dataset.current_version
        if version is None:
            return redirect("userdata:upload")
        self.version = version
        return super().dispatch(request, *args, **kwargs)  # type: ignore[misc]

    def get_crumbs(self) -> tuple[Crumb, ...]:
        return (Crumb(title=_("Свои данные")), Crumb(title=self.dataset.title))

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["dataset"] = self.dataset
        context["version"] = self.version
        context["steps"] = steps(self.step, self.dataset)
        return context


class FileStepView(DatasetStepMixin, TemplateView):
    """Шаг «Файл»: таблицы в файле с образцом строк, выбор таблицы и кодировки."""

    template_name = "userdata/file_step.html"
    step = "file"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        encoding = self.request.GET.get("encoding", "")
        encoding = encoding if encoding in ingest.ENCODINGS else ""
        try:
            inspection = services.inspection_of(self.version, encoding=encoding)
        except ingest.IngestError as error:
            context["error"] = str(error)
            inspection = services.inspection_of(self.version)
        chosen = self.version.recipe.get("table", {}).get("key")
        keys = [table.key for table in inspection.tables]
        selected = chosen if chosen in keys else next(t.key for t in inspection.tables if t.best)
        context.update(
            page_title=_("Файл: %(name)s") % {"name": self.version.file_name},
            inspection=inspection,
            selected=selected,
            garbled=any(
                "�" in cell for table in inspection.tables for row in table.sample for cell in row
            ),
            is_text=self.version.file_kind != ingest.PASTE
            and any(table.encoding for table in inspection.tables),
            choose_form=kwargs.get("choose_form")
            or ChooseTableForm(initial={"encoding": encoding}),
            append_form=kwargs.get("append_form") or PasteForm(),
            is_paste=self.version.file_kind == ingest.PASTE,
        )
        return context

    def post(self, request: HttpRequest, public_id: str) -> HttpResponse:  # noqa: ARG002
        if request.POST.get("action") == "append":
            return self._append(request)
        form = ChooseTableForm(request.POST)
        if form.is_valid():
            try:
                services.choose_table(
                    self.version,
                    form.cleaned_data["table"],
                    encoding=form.cleaned_data["encoding"],
                )
            except ingest.IngestError as error:
                form.add_error(None, str(error))
            else:
                return redirect("userdata:table", public_id=self.dataset.public_id)
        return self.render_to_response(self.get_context_data(choose_form=form))

    def _append(self, request: HttpRequest) -> HttpResponse:
        form = PasteForm(request.POST)
        if self.version.file_kind != ingest.PASTE:
            return redirect("userdata:file", public_id=self.dataset.public_id)
        if form.is_valid():
            added = services.append_paste(
                self.version, form.cleaned_data["text"], form.cleaned_data["markup"]
            )
            if added:
                messages.success(request, _("Строк добавлено: %(count)s.") % {"count": added})
            else:
                messages.info(request, _("Новых строк во вставке нет."))
            return redirect("userdata:file", public_id=self.dataset.public_id)
        return self.render_to_response(self.get_context_data(append_form=form))


class TableStepView(DatasetStepMixin, TemplateView):
    """Шаг «Что в таблице»: форма, роли столбцов, периоды, разрезы, территории и вопросы."""

    template_name = "userdata/table_step.html"
    step = "table"

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if not self.version.recipe.get("table"):
            return redirect("userdata:file", public_id=self.dataset.public_id)
        state = jobs.ensure_summary(self.version)
        if state in {jobs.RUNNING, jobs.FAILED}:
            return self.render_to_response(self.get_waiting_context(state))
        return super().get(request, *args, **kwargs)

    def get_waiting_context(self, state: str) -> dict[str, Any]:
        context = DatasetStepMixin.get_context_data(self)
        context.update(page_title=_("Что в таблице"), waiting=state)
        return context

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        result = describe.recognition_of(self.version, self.request.user)
        columns = [column for column in result.columns if column.distinct or column.header]
        # Столбцы с периодами широкой таблицы — под раскрытием: их бывает по сорок с одной ролью.
        periods = [
            column
            for column in columns
            if result.form == recognize.WIDE and column.role == recognize.VALUE and column.stamp
        ]
        if len(periods) <= PERIOD_COLUMNS_SHOWN:
            periods = []
        context.update(
            page_title=_("Что в таблице"),
            result=result,
            columns=[column for column in columns if column not in periods],
            period_columns=periods,
            last_period_column=periods[-1] if periods else None,
            role_choices=ROLE_CHOICES,
            unresolved=describe.unresolved(result),
            outside=describe.outside(result),
            territory_choices=describe.territory_choices(),
            nested=result.territories.nested if result.territories else [],
            resolved_regions=_resolved_regions(result),
            table=jobs.table_of(self.version),
            max_series=recognize.MAX_SERIES,
            repeated={number - 1 for item in result.same_headers for number in item["columns"]},
            form_text=FORM_TEXTS.get(result.form, ""),
            period_kinds=[
                PERIOD_TEXTS[kind]
                for kind in dict.fromkeys(
                    key.split(":")[0] for key in result.periods.get("kinds", {})
                )
                if kind in PERIOD_TEXTS
            ],
            is_authenticated=self.request.user.is_authenticated,
        )
        return context

    def post(self, request: HttpRequest, public_id: str) -> HttpResponse:  # noqa: ARG002
        if not self.version.recipe.get("table"):
            return redirect("userdata:file", public_id=self.dataset.public_id)
        result = describe.recognition_of(self.version, request.user)
        describe.save_answers(self.version, result, _answers(request, result), request.user)
        messages.success(request, _("Описание таблицы сохранено."))
        return redirect("userdata:table", public_id=self.dataset.public_id)


class TableStatusView(DatasetStepMixin, View):
    """Ход сводки большой таблицы: страница опрашивает его раз в секунду."""

    def get(self, request: HttpRequest, public_id: str) -> HttpResponse:  # noqa: ARG002
        state = jobs.summary_state(self.version)
        if state in {"", jobs.READY}:
            response = HttpResponse(status=204)
            response["HX-Refresh"] = "true"
            return response
        return render(
            request,
            "userdata/_waiting.html",
            {"dataset": self.dataset, "waiting": state},
        )


PERIOD_COLUMNS_SHOWN = 6

ROLE_CHOICES = (
    (recognize.TERRITORY, _("регион")),
    (recognize.TERRITORY_CODE, _("код региона")),
    (recognize.LEVEL, _("уровень территории")),
    (recognize.PERIOD, _("период")),
    (recognize.INDICATOR, _("показатель")),
    (recognize.INDICATOR_CODE, _("код показателя")),
    (recognize.UNIT, _("единица")),
    (recognize.SLICE, _("разрез")),
    (recognize.VALUE, _("значение")),
    (recognize.MISSING_REASON, _("причина пропуска")),
    (recognize.NOTE, _("примечание")),
    (recognize.SKIP, _("пропустить")),
)
PERIOD_TEXTS = {
    periods.YEAR: _("годы"),
    periods.MONTH: _("месяцы"),
    periods.QUARTER: _("кварталы"),
    periods.HALF: _("полугодия"),
    periods.YTD: _("с начала года"),
    periods.WINDOW: _("скользящие месяцы"),
    periods.POINT: _("на начало месяца"),
}
FORM_TEXTS = {
    recognize.LONG: _("Длинная таблица: регион, период и значение — в своих столбцах."),
    recognize.WIDE: _("Годы или периоды — в столбцах, регионы — в строках."),
    recognize.INDICATORS: _("Показатели — в столбцах, регионы — в строках; года в таблице нет."),
    recognize.TRANSPOSED: _("Регионы — в столбцах, годы — в строках."),
    recognize.UNKNOWN: _("Регионы в таблице не найдены."),
}


def _resolved_regions(result: recognize.Recognition) -> int:
    from apps.sources.territories import region_codes

    if result.territories is None:
        return 0
    codes = {
        match.code
        for label, match in result.territories.matches.items()
        if label in result.labels and match.is_resolved and match.code
    }
    for code in list(codes):
        codes.add(matching.NESTED_PARENTS.get(code, code))
    return len(codes & region_codes())


def _answers(request: HttpRequest, result: recognize.Recognition) -> dict[str, Any]:
    """Ответы формы шага: роли, год, разрезы, территории, вложенные области."""
    data = request.POST
    answers: dict[str, Any] = {
        "roles": {
            int(key.removeprefix("role-")): value
            for key, value in data.items()
            if key.startswith("role-") and key.removeprefix("role-").isdigit()
        },
        "year": data.get("year", "").strip() if data.get("year", "").strip().isdigit() else "",
        "remember": data.get("remember") == "on",
        "territories": {},
        "nested": {},
        "slices": {},
    }
    if data.get("slices-present"):
        answers["slices"] = {index: data.getlist(f"slice-{index}") for index in result.slices}
    for key, label in data.items():
        if key.startswith("label-"):
            number = key.removeprefix("label-")
            answers["territories"][label] = data.get(f"territory-{number}", "")
        elif key.startswith("nested-label-"):
            number = key.removeprefix("nested-label-")
            answers["nested"][label] = data.get(f"nested-{number}", "")
    return answers
