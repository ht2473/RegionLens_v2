"""Мастер своих данных: «Файл» → «Что в таблице» → «Показатели»; черновик хранится в базе."""

from __future__ import annotations

from typing import Any

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.functional import Promise
from django.utils.http import urlencode
from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView, View

from apps.core.navigation import Crumb
from apps.core.views import BreadcrumbMixin
from apps.sources import periods

from . import (
    access,
    describe,
    extract,
    indicators,
    ingest,
    jobs,
    matching,
    recognize,
    renew,
    services,
    tables,
)
from .forms import ChooseTableForm, DatasetMetaForm, PasteForm, UploadForm
from .models import Dataset, DatasetSeries, DatasetVersion
from .series import FEW_REGIONS

STEPS: tuple[tuple[str, Promise], ...] = (
    ("file", _("Файл")),
    ("table", _("Что в таблице")),
    ("series", _("Показатели")),
)


def steps(current: str, dataset: Dataset | None = None) -> list[dict[str, Any]]:
    """Шаги мастера над версией в работе: пройденные — ссылками, текущий отмечен."""
    version = renew.working_version(dataset) if dataset else None
    reachable = {
        "file": dataset is not None,
        "table": bool(version and version.recipe),
        "series": bool(version and version.recipe.get("form")),
    }
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
        return (
            Crumb(title=_("Свои данные"), url=reverse("userdata:index")),
            Crumb(title=_("Загрузить таблицу")),
        )

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        # Загрузка начата из панели исследования: собранная таблица вернётся в него.
        from .lab import remember_study

        remember_study(request, request.GET.get("study", ""))
        return super().get(request, *args, **kwargs)

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
                    services.check_limits(request)
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
                services.check_limits(request)
                dataset = services.create_from_upload(request, upload_form.cleaned_data["file"])
            except ingest.IngestError as error:
                upload_form.add_error("file", str(error))
            else:
                return redirect("userdata:file", public_id=dataset.public_id)
        return self.render_to_response(self.get_context_data(upload_form=upload_form))


class DatasetStepMixin(BreadcrumbMixin):
    """
    Шаг мастера над своим набором; чужой — 404. Шаги идут над новой версией в работе,
    если она есть, иначе над текущей.
    """

    step = ""
    dataset: Dataset
    version: DatasetVersion

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        self.dataset = access.dataset_or_404(request, kwargs["public_id"])
        version = renew.working_version(self.dataset)
        if version is None:
            return redirect("userdata:upload")
        self.version = version
        return super().dispatch(request, *args, **kwargs)  # type: ignore[misc]

    def get_crumbs(self) -> tuple[Crumb, ...]:
        return (
            Crumb(title=_("Свои данные"), url=reverse("userdata:index")),
            Crumb(title=self.dataset.title),
        )

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["dataset"] = self.dataset
        context["version"] = self.version
        context["steps"] = steps(self.step, self.dataset)
        context["renewal"] = renew.is_renewal(self.version)
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
                if renew.is_automatic(self.version):
                    # Новая версия: таблица выбрана человеком, остальное — как в прежней.
                    renew.transfer(self.version)
                    return redirect(renew.proceed(self.version, request.user))
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
        if renew.is_automatic(self.version):
            # Новая версия, ждавшая сводки большой таблицы: без вопросов — сразу к сборке.
            target = renew.proceed(self.version, request.user)
            if target != request.path:
                return redirect(target)
            self.version.refresh_from_db()
        return super().get(request, *args, **kwargs)

    def get_waiting_context(self, state: str) -> dict[str, Any]:
        context = DatasetStepMixin.get_context_data(self)
        context.update(
            page_title=_("Что в таблице"),
            waiting=state,
            status_url=reverse("userdata:table-status", args=[self.dataset.public_id]),
        )
        return context

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        loaded, result = describe.load_and_recognize(self.version, self.request.user)
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
            table=_with_sample(jobs.table_of(self.version), loaded),
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
            renewal_questions=renew.pending_questions(self.version),
        )
        return context

    def post(self, request: HttpRequest, public_id: str) -> HttpResponse:  # noqa: ARG002
        if not self.version.recipe.get("table"):
            return redirect("userdata:file", public_id=self.dataset.public_id)
        result = describe.recognition_of(self.version, request.user)
        describe.save_answers(self.version, result, _answers(request, result), request.user)
        if request.POST.get("action") == "next":
            return redirect("userdata:series", public_id=self.dataset.public_id)
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
            {
                "dataset": self.dataset,
                "waiting": state,
                "status_url": reverse("userdata:table-status", args=[self.dataset.public_id]),
            },
        )


class SeriesStepView(DatasetStepMixin, TemplateView):
    """Шаг «Показатели»: название, единица, вид величины, направленность, пересчёт; сборка."""

    template_name = "userdata/series_step.html"
    step = "series"

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if not self.version.recipe.get("form"):
            messages.info(request, _("Сначала сохраните описание таблицы."))
            return redirect("userdata:table", public_id=self.dataset.public_id)
        stage, state = _progress(self.version)
        if state != jobs.READY:
            return self.render_to_response(self.get_waiting_context(stage, state))
        return super().get(request, *args, **kwargs)

    def get_waiting_context(self, stage: str, state: str) -> dict[str, Any]:
        context = DatasetStepMixin.get_context_data(self)
        context.update(
            page_title=_("Показатели"),
            waiting=state,
            waiting_stage=stage,
            waiting_error=jobs.stage_error(self.version, stage),
            status_url=_status_url(self.dataset, stage),
        )
        return context

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        report = self.version.report.get("extract") or {}
        items = indicators.indicators_of(self.version)
        context.update(
            page_title=_("Показатели"),
            indicators=items,
            report=report,
            meta_form=kwargs.get("meta_form") or DatasetMetaForm(instance=self.dataset),
            kinds=DatasetSeries.Kind.choices,
            polarities=DatasetSeries.Polarity.choices,
            per_choices=[
                (value, label)
                for value, label in DatasetSeries.Derived.choices
                if value in indicators.PER_CHOICES
            ],
            build_error=jobs.stage_error(self.version, jobs.BUILD)
            if jobs.stage_state(self.version, jobs.BUILD) == jobs.FAILED
            else "",
            built=self.version.state == DatasetVersion.State.BUILT,
            few_regions=max((item.regions for item in items), default=0) < FEW_REGIONS,
            alone_missing=self.version.report.get("build", {}).get("alone_missing", []),
        )
        return context

    def post(self, request: HttpRequest, public_id: str) -> HttpResponse:  # noqa: ARG002
        if request.POST.get("action") == "masks":
            # Выбор кодов-масок меняет рецепт: извлечение пройдёт заново.
            self.version.recipe = {**self.version.recipe, "masks": request.POST.getlist("mask")}
            self.version.save(update_fields=["recipe", "updated_at"])
            return redirect(f"{reverse('userdata:series', args=[self.dataset.public_id])}#masks")
        if not extract.is_current(self.version):
            return redirect("userdata:series", public_id=self.dataset.public_id)
        meta_form = DatasetMetaForm(request.POST, instance=self.dataset)
        if not meta_form.is_valid():
            return self.render_to_response(self.get_context_data(meta_form=meta_form))
        meta_form.save()
        indicators.save(self.version, _indicator_answers(request))
        state = jobs.start(self.version, jobs.BUILD)
        if state == jobs.READY:
            self.version.refresh_from_db(fields=["state"])
            if self.version.state == DatasetVersion.State.BUILT:
                return redirect(renew.after_build_url(self.version, request))
        return redirect("userdata:series", public_id=self.dataset.public_id)


class SeriesStatusView(DatasetStepMixin, View):
    """Ход извлечения или сборки: страница опрашивает его раз в секунду."""

    def get(self, request: HttpRequest, public_id: str) -> HttpResponse:  # noqa: ARG002
        waited = request.GET.get("stage", "")
        stage, state = _progress(self.version)
        if state in {jobs.READY, jobs.FAILED, ""}:
            response = HttpResponse(status=204)
            built = jobs.stage_state(self.version, jobs.BUILD) == jobs.READY
            if waited == jobs.BUILD and built:
                # Сборка закончилась, пока страница ждала: сразу на карту или к различиям.
                response["HX-Redirect"] = renew.after_build_url(self.version, request)
            else:
                response["HX-Refresh"] = "true"
            return response
        return render(
            request,
            "userdata/_waiting.html",
            {
                "dataset": self.dataset,
                "waiting": state,
                "waiting_stage": stage,
                "status_url": _status_url(self.dataset, stage),
            },
        )


def _status_url(dataset: Dataset, stage: str) -> str:
    """Адрес опроса хода с этапом, которого ждёт страница."""
    url = reverse("userdata:series-status", args=[dataset.public_id])
    return f"{url}?{urlencode({'stage': stage})}"


def _progress(version: DatasetVersion) -> tuple[str, str]:
    """
    Этап и его состояние для шага «Показатели»: идёт сборка — она; иначе извлечение,
    начатое при необходимости (в запросе или отдельным процессом).
    """
    build_state = jobs.stage_state(version, jobs.BUILD)
    if build_state in {jobs.RUNNING, jobs.PENDING}:
        return jobs.BUILD, jobs.resume(version, jobs.BUILD)
    if extract.is_current(version):
        return jobs.EXTRACT, jobs.READY
    state = jobs.stage_state(version, jobs.EXTRACT)
    if state in {jobs.RUNNING, jobs.PENDING}:
        return jobs.EXTRACT, jobs.resume(version, jobs.EXTRACT)
    if state == jobs.FAILED:
        return jobs.EXTRACT, state
    state = jobs.start(version, jobs.EXTRACT)
    if state == jobs.READY and not extract.is_current(version):
        return jobs.EXTRACT, jobs.FAILED
    return jobs.EXTRACT, state


def first_view_url(version: DatasetVersion) -> str:
    """Карта первого ряда собранной таблицы; у суммы — её пересчёт на жителей."""
    key = first_key(version)
    if key is None:
        return reverse("userdata:dataset", args=[version.dataset.public_id])
    return f"{reverse('maps:choropleth')}?{urlencode({'series': key})}"


def first_key(version: DatasetVersion) -> str | None:
    """Первый ряд собранной таблицы; у суммы — её пересчёт на 100 000 жителей."""
    records = list(version.series.order_by("order"))
    if not records:
        return None
    first = records[0]
    preferred = next(
        (
            record
            for record in records
            if record.base_code == first.code
            and record.derived == DatasetSeries.Derived.PER_100000
            and record.values_count
        ),
        first,
    )
    return f"u:{version.dataset.code}:{preferred.code}"


def _indicator_answers(request: HttpRequest) -> dict[str, dict[str, Any]]:
    """Описание показателей из формы: поля с номером показателя."""
    data = request.POST
    answers: dict[str, dict[str, Any]] = {}
    for key in data:
        if not key.startswith("indicator-"):
            continue
        number = key.removeprefix("indicator-")
        answers[str(data.get(key, ""))] = {
            "title": data.get(f"title-{number}", ""),
            "unit": data.get(f"unit-{number}", ""),
            "kind": data.get(f"kind-{number}", ""),
            "polarity": data.get(f"polarity-{number}", ""),
            "per": data.getlist(f"per-{number}"),
            "breaks": data.get(f"breaks-{number}", ""),
            "break_note": data.get(f"break-note-{number}", ""),
            "recalc": data.getlist(f"recalc-{number}"),
            "fold_slices": data.getlist(f"fold-{number}"),
            "months": data.get(f"months-{number}", ""),
        }
    return answers


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


def _with_sample(table: ingest.TableInfo, loaded: tables.Loaded) -> ingest.TableInfo:
    """Таблица рецепта с образцом строк: в рецепте образец не хранится."""
    table.sample = ingest.sample_of(loaded.rows)
    return table


def _resolved_regions(result: recognize.Recognition) -> int:
    from apps.sources.territories import region_codes

    if result.territories is None:
        return 0
    codes = {
        match.code
        for label, match in result.territories.matches.items()
        if label in result.labels
        and match.code
        and (match.is_resolved or match.kind == matching.NESTED)
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
