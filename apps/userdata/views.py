"""
Мастер своих данных: «Файл» → «Что в таблице» → «Проверка»; черновик хранится в базе.

Шаг «Файл» пропускается, когда в файле одна таблица, шаг «Что в таблице» — когда вопросов
нет: человек сразу видит проверку — что прочитано, показатели и сборку.
"""

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
    build,
    describe,
    extract,
    indicators,
    ingest,
    jobs,
    matching,
    recognize,
    renew,
    review,
    semantics,
    services,
    tables,
)
from .forms import ChooseTableForm, DatasetMetaForm, PasteForm, UploadForm
from .models import Dataset, DatasetSeries, DatasetVersion
from .series import FEW_REGIONS

STEPS: tuple[tuple[str, Promise], ...] = (
    ("file", _("Файл")),
    ("table", _("Что в таблице")),
    ("series", _("Проверка")),
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

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        from .lab import chosen_study

        context = super().get_context_data(**kwargs)
        context.setdefault("upload_form", UploadForm())
        context.setdefault("paste_form", PasteForm())
        # Загрузка начата из панели исследования: собранная таблица вернётся в него,
        # и страница говорит об этом до загрузки.
        source = self.request.POST if self.request.method == "POST" else self.request.GET
        context["study"] = chosen_study(self.request, source.get("study", ""))
        context["page_title"] = _("Загрузить таблицу")
        context["steps"] = steps("file")
        context["upload_limit"] = services.upload_size_text()
        return context

    def created(self, request: HttpRequest, dataset: Dataset) -> HttpResponse:
        """Набор заведён: запомнить исследование, откуда начата загрузка, и к файлу."""
        from .lab import chosen_study, remember_study

        remember_study(request, dataset, chosen_study(request, request.POST.get("study", "")))
        return redirect("userdata:file", public_id=dataset.public_id)

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
                    return self.created(request, dataset)
            return self.render_to_response(self.get_context_data(paste_form=paste_form))
        upload_form = UploadForm(request.POST, request.FILES)
        if upload_form.is_valid():
            try:
                services.check_limits(request)
                dataset = services.create_from_upload(request, upload_form.cleaned_data["file"])
            except ingest.IngestError as error:
                upload_form.add_error("file", str(error))
            else:
                return self.created(request, dataset)
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

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        # В файле одна таблица и она читается — выбирать нечего: сразу дальше. Вставка —
        # тоже (продолжение таблицы добавляют по ссылке со страницы проверки).
        if not self.version.recipe.get("table") and "encoding" not in request.GET:
            target = self._single_table(request)
            if target:
                return redirect(target)
        return super().get(request, *args, **kwargs)

    def _single_table(self, request: HttpRequest) -> str:
        """Выбрать единственную таблицу файла; вернуть адрес следующего шага или пусто."""
        try:
            inspection = services.inspection_of(self.version)
        except ingest.IngestError:
            return ""
        if len(inspection.tables) != 1 or _garbled(inspection):
            return ""
        try:
            services.choose_table(self.version, inspection.tables[0].key, encoding="")
        except ingest.IngestError:
            return ""
        if renew.is_automatic(self.version):
            renew.transfer(self.version)
            return renew.proceed(self.version, request.user)
        return reverse("userdata:table", kwargs={"public_id": self.dataset.public_id})

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
            garbled=_garbled(inspection),
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
    """
    Шаг «Что в таблице»: сама таблица с ролями столбцов в шапке; вопросы — только
    о неуверенном (территории, год, вложенные области, лишние ряды); разрезы и столбцы
    периодов широкой таблицы — по требованию.
    """

    template_name = "userdata/table_step.html"
    step = "table"
    # Прочитанная таблица и распознавание: считаются в get один раз и для страницы.
    recognized: tuple[tables.Loaded, recognize.Recognition] | None = None

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
        self.recognized = describe.load_and_recognize(self.version, request.user)
        if (
            not self.version.recipe.get("form")
            and not renew.is_renewal(self.version)
            and not questions_of(self.recognized[1])
            and not warned(self.recognized[1])
        ):
            # Описание ещё не сохранено, спросить и предупредить не о чем: прочитанное
            # сохраняется как есть, проверка — на следующем шаге; разметку можно поправить,
            # вернувшись сюда.
            describe.save_answers(self.version, self.recognized[1], {}, request.user)
            return redirect("userdata:series", public_id=self.dataset.public_id)
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
        loaded, result = self.recognized or describe.load_and_recognize(
            self.version, self.request.user
        )
        columns = [column for column in result.columns if column.distinct or column.header]
        # Столбцы с периодами широкой таблицы: первые — в таблице, остальные — под раскрытием,
        # их бывает по сорок с одной ролью.
        periods = [
            column
            for column in columns
            if result.form == recognize.WIDE and column.role == recognize.VALUE and column.stamp
        ]
        hidden = periods[PERIOD_COLUMNS_SHOWN:] if len(periods) > PERIOD_COLUMNS_ALL else []
        shown = [column for column in columns if column not in hidden]
        unresolved = describe.unresolved(result)
        nested = result.territories.nested if result.territories else []
        too_many = result.too_many
        context.update(
            page_title=_("Что в таблице"),
            result=result,
            year_note=describe.year_note(
                result.year, result.period, result.year_from, result.year_text
            )
            if result.year_auto
            else "",
            columns=shown,
            preview=_preview(loaded, result, shown),
            period_columns=hidden,
            last_period_column=hidden[-1] if hidden else None,
            role_choices=ROLE_CHOICES,
            questions=questions_of(result),
            too_many=too_many,
            unresolved=unresolved,
            outside=describe.outside(result),
            territory_choices=describe.territory_choices(),
            nested=nested,
            resolved_regions=_resolved_regions(result),
            table=jobs.table_of(self.version),
            max_series=result.series_limit,
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
    """
    Шаг «Показатели»: вопросы о неуверенном (вид величины, единица, месяцы), прочее описание
    по требованию; сборка. Новые пересчёты — в исследовании («Посчитать»).
    """

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
            page_title=_("Проверка таблицы"),
            waiting=state,
            waiting_stage=stage,
            waiting_error=jobs.stage_error(self.version, stage),
            status_url=_status_url(self.dataset, stage),
        )
        return context

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        from .lab import destination

        context = super().get_context_data(**kwargs)
        report = self.version.report.get("extract") or {}
        items = indicators.indicators_of(self.version)
        context.update(
            page_title=_("Проверка таблицы"),
            summary=_review_summary(self.version, report, items),
            data_review=review.review(self.dataset, self.version, report, items),
            roles=_roles_read(self.version.recipe),
            indicators=items,
            asking=[item for item in items if item.questions],
            settled=[item for item in items if not item.questions],
            # Во что влить показатель: другие показатели таблицы, сами никуда не влитые.
            merge_choices=(
                [(item.name, item.title) for item in items if not item.merge]
                if len(items) <= MERGE_CHOICES_MAX
                else []
            ),
            titles={item.name: item.title for item in items},
            report=report,
            meta_form=kwargs.get("meta_form")
            or DatasetMetaForm(
                instance=self.dataset,
                initial=services.suggested_title(self.dataset, self.version, items),
            ),
            kinds=[
                (value, label, semantics.TYPE_HINTS[value])
                for value, label in DatasetSeries.Kind.choices
            ],
            bases=semantics.BASE_CHOICES,
            polarities=DatasetSeries.Polarity.choices,
            build_error=jobs.stage_error(self.version, jobs.BUILD)
            if jobs.stage_state(self.version, jobs.BUILD) == jobs.FAILED
            else "",
            built=self.version.state == DatasetVersion.State.BUILT,
            few_regions=max((item.regions for item in items), default=0) < FEW_REGIONS,
            alone_missing=self.version.report.get("build", {}).get("alone_missing", []),
            destination=destination(self.request, self.dataset),
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
        _save_data_answers(self.version, request)
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
    """
    Первый показатель собранной таблицы в числах человека: у показателя в разбивке — его итог
    (сумма разбивки), иначе первый ряд со значениями; пересчёт на жителей — не первым.
    """
    records = [record for record in version.series.order_by("order") if record.values_count]
    if not records:
        return None
    first = records[0]
    preferred = next(
        (
            record
            for record in records
            if record.indicator == first.indicator
            and record.derived == DatasetSeries.Derived.SLICE_SUM
        ),
        next((record for record in records if not record.derived), first),
    )
    return f"u:{version.dataset.code}:{preferred.code}"


def _save_data_answers(version: DatasetVersion, request: HttpRequest) -> None:
    """Ответы о данных: что значит прочерк и у каких показателей нули — пропуски."""
    recipe = dict(version.recipe)
    if request.POST.get("dashes") in {build.DASHES_ABSENT, build.DASHES_MISSING}:
        recipe["dashes"] = request.POST["dashes"]
    if request.POST.get("zeros-asked"):
        recipe["zeros"] = request.POST.getlist("zero")
    if recipe != version.recipe:
        version.recipe = recipe
        version.save(update_fields=["recipe", "updated_at"])


def _indicator_answers(request: HttpRequest) -> dict[str, dict[str, Any]]:
    """
    Описание показателей из формы: поля с номером показателя. Пересчёты — только если
    в форме был их перечень (recounts-N): без него остаются прежние.
    """
    data = request.POST
    answers: dict[str, dict[str, Any]] = {}
    for key in data:
        if not key.startswith("indicator-"):
            continue
        number = key.removeprefix("indicator-")
        answer: dict[str, Any] = {
            "title": data.get(f"title-{number}", ""),
            "unit": data.get(f"unit-{number}", ""),
            "kind": data.get(f"kind-{number}", ""),
            "base": data.get(f"base-{number}", ""),
            "polarity": data.get(f"polarity-{number}", ""),
            "breaks": data.get(f"breaks-{number}", ""),
            "break_note": data.get(f"break-note-{number}", ""),
            "fold_slices": data.getlist(f"fold-{number}"),
            "months": data.get(f"months-{number}", ""),
            "hidden": data.get(f"hidden-{number}") == "1",
            "scale": data.get(f"scale-{number}", "0"),
            "merge": data.get(f"merge-{number}", ""),
        }
        if data.get(f"recounts-{number}"):
            answer["per"] = data.getlist(f"per-{number}")
            answer["recalc"] = data.getlist(f"recalc-{number}")
        answers[str(data.get(key, ""))] = answer
    return answers


# Столбцов периодов широкой таблицы: до стольких — все в образце, больше — первые три.
PERIOD_COLUMNS_ALL = 6
PERIOD_COLUMNS_SHOWN = 3
# Строк образца под шапкой.
PREVIEW_ROWS = 8
# Список «Тот же показатель, что…» — у таблицы до стольких показателей: в каждом показателе
# он перечисляет все остальные, у сотен столбцов страница весила бы десятки мегабайт.
MERGE_CHOICES_MAX = 100

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


# Подписей столбцов одной роли в сводке «Как прочитана таблица» — не больше.
ROLE_HEADERS_SHOWN = 3


def questions_of(result: recognize.Recognition) -> int:
    """
    Сколько вопросов на шаге «Что в таблице»: вид таблицы не понят, вложенные области,
    год, повторы регионов, неузнанные подписи территорий, слишком много рядов.
    """
    nested = result.territories.nested if result.territories else []
    return sum(
        (
            result.form == recognize.UNKNOWN,
            len(nested),
            result.needs_year,
            result.needs_repeats,
            bool(describe.unresolved(result)),
            result.too_many,
        )
    )


def warned(result: recognize.Recognition) -> bool:
    """
    Шаг «Что в таблице» предупреждает о том, что стоит увидеть до сборки: районы вместо
    субъектов, одна территория дважды, одинаковые подписи столбцов, разрез строками под
    регионом.
    """
    return bool(result.municipal or result.duplicates or result.same_headers or result.row_parts)


def _garbled(inspection: Any) -> bool:
    """В образце таблиц есть знаки неверной кодировки."""
    return any("�" in cell for table in inspection.tables for row in table.sample for cell in row)


def _review_summary(
    version: DatasetVersion, report: dict[str, Any], items: list[indicators.Indicator]
) -> dict[str, Any]:
    """Сводка проверки: регионов из всех, годы, показатели и ряды, как устроена таблица."""
    from apps.sources.territories import region_codes

    series = report.get("series") or []
    years = [year for item in series for year in (item["first_year"], item["last_year"]) if year]
    return {
        "regions": max((item["regions"] for item in series), default=0),
        "regions_total": len(region_codes()),
        "first_year": min(years) if years else None,
        "last_year": max(years) if years else None,
        "indicators": len(items),
        "series": len(series),
        "with_unit": sum(1 for item in items if item.unit),
        "form": FORM_TEXTS.get(report.get("form") or version.recipe.get("form", ""), ""),
        "year_note": describe.year_note(*report["year_from"]) if report.get("year_from") else "",
    }


def _roles_read(recipe: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Как прочитаны столбцы: роль словами и подписи её столбцов — первые и сколько ещё;
    у широкой таблицы столбцов со значениями бывает по сорок.
    """
    labels = dict(ROLE_CHOICES)
    grouped: dict[str, list[str]] = {}
    roles = recipe.get("roles") or {}
    headers = recipe.get("headers") or {}
    for index in sorted(roles, key=int):
        role = roles[index]
        header = " ".join(str(headers.get(index) or "").split()) or str(
            _("столбец %(number)s") % {"number": int(index) + 1}
        )
        grouped.setdefault(role, []).append(header)
    return [
        {
            "role": labels.get(role, role),
            "skipped": role == recognize.SKIP,
            "headers": found[:ROLE_HEADERS_SHOWN],
            "more": max(0, len(found) - ROLE_HEADERS_SHOWN),
        }
        for role, found in grouped.items()
    ]


def _preview(
    loaded: tables.Loaded, result: recognize.Recognition, columns: list[recognize.ColumnInfo]
) -> list[list[dict[str, str]]]:
    """Первые строки данных под шапкой: клетки показанных столбцов текстом и роль столбца."""
    found: list[list[dict[str, str]]] = []
    for row in loaded.rows[result.data_start :]:
        cells = [
            {
                "text": ingest.cell_text(row[column.index]) if column.index < len(row) else "",
                "role": column.role,
            }
            for column in columns
        ]
        if any(cell["text"].strip() for cell in cells):
            found.append(cells)
        if len(found) >= PREVIEW_ROWS:
            break
    return found


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
    """Ответы формы шага: роли, год, повторы регионов, разрезы, территории, вложенные области."""
    data = request.POST
    answers: dict[str, Any] = {
        "roles": {
            int(key.removeprefix("role-")): value
            for key, value in data.items()
            if key.startswith("role-") and key.removeprefix("role-").isdigit()
        },
        # Год, подставленный из заголовка, листа или файла, и не исправленный — не ответ человека.
        "year": data.get("year", "").strip()
        if data.get("year", "").strip().isdigit()
        and data.get("year", "").strip() != data.get("year-auto", "")
        else "",
        "remember": data.get("remember") == "on",
        "repeats": data.get("repeats", ""),
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
