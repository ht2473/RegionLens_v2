"""
Раздел «Свои данные»: что это, загрузка и пример, «Мои таблицы»; страница таблицы
с показателями, территориями, файлом, выгрузкой и удалением.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.http import FileResponse, Http404, HttpRequest, HttpResponse, StreamingHttpResponse
from django.http.response import HttpResponseBase
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView, View

from apps.core.documents import backup_retention_days
from apps.core.navigation import Crumb
from apps.core.views import BreadcrumbMixin
from apps.warehouse import queries

from . import access, export, glance, ingest, monthly, scope, services, studies
from .models import Dataset, DatasetSeries, DatasetVersion
from .series import FEW_REGIONS, UserSeries

# Представления холста для ряда набора: код, маршрут, подпись.
VIEWS = (
    ("map", "maps:choropleth", _("Карта")),
    ("compare", "compare:index", _("Динамика")),
    ("rankings", "rankings:index", _("Рейтинг")),
    ("distribution", "surface:distribution", _("Распределение")),
    ("table", "surface:table", _("Таблица")),
)
# Вкладки страницы таблицы; «Файл и версии» и «Доступ» — только владельцу.
DATA, CHECKS, FILE, ACCESS = "data", "checks", "file", "access"
TABS = (
    (DATA, _("Данные")),
    (CHECKS, _("Проверки")),
    (FILE, _("Файл и версии")),
    (ACCESS, _("Доступ")),
)
OWNER_TABS = frozenset({FILE, ACCESS})
# Причины «вне справочника» словами.
OUTSIDE_REASONS = {
    "merged": _("прежний субъект, объединённый с другим"),
    "new": _("субъект, которого нет в справочнике сайта"),
    "district": _("федеральный округ, которого нет в справочнике сайта"),
    "baikonur": _("Байконур"),
    "composite": _("несколько территорий в одной строке"),
    "organization": _("ведомство или организация"),
}


class SectionView(BreadcrumbMixin, TemplateView):
    """Раздел: что это, загрузка, «Мои таблицы», какие таблицы подходят, кто их видит."""

    template_name = "userdata/index.html"

    def get_crumbs(self) -> tuple[Crumb, ...]:
        return (Crumb(title=_("Свои данные")),)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        mine = list(access.owned(self.request).select_related("current_version"))
        user = self.request.user
        context.update(
            page_title=_("Свои данные"),
            datasets=mine,
            used=services.used_bytes(user)
            if user.is_authenticated
            else sum(item.size_bytes for item in mine),
            quota=settings.USERDATA_QUOTA_BYTES,
            limit=settings.USERDATA_MAX_DATASETS
            if user.is_authenticated
            else settings.USERDATA_GUEST_MAX_DATASETS,
            guest_hours=settings.USERDATA_GUEST_HOURS,
            retention_days=backup_retention_days(),
            studies=list(studies.owned(self.request)),
        )
        return context


class ExampleView(View):
    """«Попробовать на примере»: таблица-пример собирается сразу и открывается в исследовании."""

    def post(self, request: HttpRequest) -> HttpResponse:
        from .renew import after_build_url

        try:
            services.check_limits(request)
            dataset = services.create_example(request)
        except ingest.IngestError as error:
            messages.error(request, str(error))
            return redirect("userdata:index")
        version = dataset.current_version
        assert version is not None
        return redirect(after_build_url(version, request))


class DatasetMixin:
    """Свой набор по опознавателю; чужой и несуществующий — 404."""

    request: HttpRequest
    dataset: Dataset

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        self.dataset = access.dataset_or_404(request, kwargs["public_id"])
        return super().dispatch(request, *args, **kwargs)  # type: ignore[misc]


class ReadableDatasetMixin:
    """
    Набор для страниц чтения: свой или открытый закрытой ссылкой (``owner`` ложно);
    прочий — 404.
    """

    request: HttpRequest
    dataset: Dataset
    owner: bool

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        self.dataset = access.readable_or_404(request, kwargs["public_id"])
        self.owner = scope.owns(self.dataset)
        return super().dispatch(request, *args, **kwargs)  # type: ignore[misc]


class DatasetView(ReadableDatasetMixin, BreadcrumbMixin, TemplateView):
    """
    Страница таблицы — вкладки «Данные» (показатели списком и выбранный показатель с картой
    ряда), «Проверки», «Файл и версии» и «Доступ»; две последние — владельцу. Читатель
    ссылки ничего не меняет. Ряды без значений к построению не предлагаются.
    """

    template_name = "userdata/dataset.html"

    def get_crumbs(self) -> tuple[Crumb, ...]:
        if not self.owner:
            return (Crumb(title=self.dataset.title),)
        return (
            Crumb(title=_("Свои данные"), url=reverse("userdata:index")),
            Crumb(title=self.dataset.title),
        )

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        dataset = self.dataset
        version = dataset.current_version
        built = version is not None and version.state == DatasetVersion.State.BUILT
        context.update(
            page_title=dataset.title,
            dataset=dataset,
            version=version,
            owner=self.owner,
            built=built,
            can_export=self.owner or scope.exportable(dataset.code),
            continue_url=_continue_url(dataset, version),
            guest=dataset.owner_id is None,
            retention_days=backup_retention_days(),
        )
        if not built:
            # Несобранная таблица: продолжить загрузку или удалить.
            context["tab"] = ""
            return context
        assert version is not None
        records = list(version.series.order_by("order"))
        everything = [UserSeries(record, dataset) for record in records]
        series = [
            item for item in everything if item.record.derived != DatasetSeries.Derived.FORMULA
        ]
        # Слой рядов сам ведёт ключи набора в его файл: набор свой.
        covered = queries.series_covered_years([item.key for item in everything])
        notices = _notices(version, series, covered)
        tab = self.request.GET.get("tab", DATA)
        available = [code for code, _title in TABS if self.owner or code not in OWNER_TABS]
        tab = tab if tab in available else DATA
        context.update(
            tab=tab,
            tabs=[
                {
                    "code": code,
                    "title": title,
                    "url": "?" + urlencode({"tab": code}) if code != DATA else "?",
                    "active": code == tab,
                    "attention": code == CHECKS and notices["attention"],
                }
                for code, title in TABS
                if code in available
            ],
            indicator_count=len({item.record.indicator for item in series}),
            xlsx_allowed=(version.values_count or 0) <= export.XLSX_ROWS,
        )
        if tab == DATA:
            context.update(self.data_context(version, series, everything, covered))
        elif tab == CHECKS:
            context.update(notices, checks=glance.checks(dataset, version))
        elif tab == FILE:
            context.update(_versions_context(dataset))
        elif tab == ACCESS and dataset.owner_id is not None:
            from .share_views import share_context

            context.update(share_context(self.request, dataset))
        return context

    def data_context(
        self,
        version: DatasetVersion,
        series: list[UserSeries],
        everything: list[UserSeries],
        covered: dict[str, tuple[int, int]],
    ) -> dict[str, Any]:
        """
        Вкладка «Данные»: показатели списком, выбранный ряд (``show`` — код ряда; по
        умолчанию — первый со значениями) и его карта.
        """
        dataset = self.dataset
        groups = _groups(series, covered, monthly.groups(version), dataset)
        formulas = _formulas(dataset, version, everything)
        for group in groups:
            # Показатель в списке открывает первый ряд со значениями.
            first = next((item for item in group["items"] if item["has_values"]), None)
            group["first_code"] = (first or group["items"][0])["record"].code
        shown = self.request.GET.get("show", "")
        pairs = [(group, item) for group in groups for item in group["items"]]
        chosen_formula = next((item for item in formulas if item["definition"].code == shown), None)
        pair = next(((group, item) for group, item in pairs if item["record"].code == shown), None)
        if pair is None and chosen_formula is None:
            # По умолчанию — первый ряд со значениями.
            pair = next(
                ((group, item) for group, item in pairs if item["has_values"]),
                pairs[0] if pairs else None,
            )
        chosen_group, chosen = pair if pair is not None else (None, None)
        target = chosen["series"] if chosen is not None else None
        if chosen_formula is not None and chosen_formula["series"] is not None:
            target = chosen_formula["series"]
        has_values = target is not None and bool(target.record.values_count)
        picked = chosen_formula if chosen_formula is not None else chosen
        return {
            "groups": groups,
            "formulas": formulas,
            "chosen_group": chosen_group,
            "chosen": chosen,
            "chosen_formula": chosen_formula,
            "chosen_series": target if has_values else None,
            "chosen_views": picked["views"] if picked is not None else [],
            "chosen_related_url": picked["related_url"] if picked is not None else "",
            "picked": bool(shown),
            "preview": _preview(self.request, target) if has_values and target else {},
        }


def _notices(
    version: DatasetVersion, series: list[UserSeries], covered: dict[str, tuple[int, int]]
) -> dict[str, Any]:
    """
    Пометки таблицы для вкладки «Проверки» — то, что считается сразу, без разбора рядов:
    суммы, коды-маски, мало субъектов, неполный последний год, вложенные области, строки
    вне справочника. ``attention`` — есть ли о чём предупредить.
    """
    report = version.report.get("extract") or {}
    build = version.report.get("build", {})
    found = {
        "sums": [item for item in series if item.is_sum],
        "per_capita": [item for item in series if item.record.derived],
        "few_regions": version.regions_count < FEW_REGIONS,
        "incomplete": _incomplete(series, covered),
        "outside": [
            (label, OUTSIDE_REASONS.get(reason, "")) for label, reason in report.get("outside", [])
        ],
        "nested": report.get("nested", []),
        "alone_missing": build.get("alone_missing", []),
        "alone_conflict": build.get("alone_conflict", []),
        "conflicts": report.get("conflicts", 0),
        "masks": [item["value"] for item in report.get("masks", []) if item.get("masked")],
    }
    found["attention"] = bool(
        (found["sums"] and not found["per_capita"])
        or found["masks"]
        or found["few_regions"]
        or found["incomplete"]
        or found["alone_conflict"]
        or found["alone_missing"]
    )
    return found


def _preview(request: HttpRequest, item: UserSeries) -> dict[str, Any]:
    """Карта выбранного ряда — та же, что у карточки исследования, — разметкой и её год."""
    from django.template.loader import render_to_string

    from . import study_cards

    block = {
        "id": "preview",
        "kind": "view",
        "target": "map",
        "parameters": {"series": item.key},
        "title": "",
        "note": "",
        "wide": True,
    }
    card = study_cards.render(request, block, year=None, territories=[])
    if card["template"] != "userdata/cards/_map.html":
        return {}
    return {
        "html": render_to_string(card["template"], {**card, "card": card}, request=request),
        "year": card.get("year"),
    }


def _formulas(
    dataset: Dataset, version: DatasetVersion, series: list[UserSeries]
) -> list[dict[str, Any]]:
    """Показатели по формулам: формула с нынешними названиями, ряд, причина пустоты."""
    from . import formula_store, formulas

    by_code = {item.record.code: item for item in series}
    errors = version.report.get("build", {}).get("formula_errors", {})
    found = []
    for item in formula_store.definitions(version):
        titles = {**item.labels, **formula_store.titles(dataset, item.keys)}
        record = by_code.get(item.code)
        found.append(
            {
                "definition": item,
                "expression": formulas.display(item.expression, titles),
                "series": record,
                "error": errors.get(item.code, ""),
                "views": _views(record)
                if record is not None and record.record.values_count
                else [],
                "related_url": f"{reverse('userdata:related', args=[dataset.public_id])}?"
                f"{urlencode({'series': record.key})}"
                if record is not None and record.record.regions_count >= FEW_REGIONS
                else "",
                "edit_url": reverse("userdata:formula", args=[dataset.public_id, item.code]),
            }
        )
    return found


def _views(item: UserSeries) -> list[dict[str, Any]]:
    """Ссылки на виды холста для ряда."""
    return [
        {
            "code": code,
            "title": title,
            "url": f"{reverse(name)}?{urlencode({'series': item.key})}",
        }
        for code, name, title in VIEWS
    ]


def _continue_url(dataset: Dataset, version: DatasetVersion | None) -> str:
    """Шаг мастера, на котором остановилась несобранная таблица или новая версия."""
    from . import renew

    if version is None or not version.recipe.get("table"):
        return reverse("userdata:file", args=[dataset.public_id])
    described = version.recipe.get("headers") if renew.is_renewal(version) else True
    if not version.recipe.get("form") or not described:
        return reverse("userdata:table", args=[dataset.public_id])
    return reverse("userdata:series", args=[dataset.public_id])


def _versions_context(dataset: Dataset) -> dict[str, Any]:
    """Версии таблицы владельцу: собранные, новая в работе и формы новой версии."""
    from . import renew
    from .forms import PasteForm, UploadForm

    draft = renew.draft_of(dataset)
    return {
        "versions": list(
            dataset.versions.filter(state=DatasetVersion.State.BUILT).order_by("-number")
        ),
        "draft": draft,
        "draft_url": _continue_url(dataset, draft) if draft is not None else "",
        "draft_questions": renew.pending_questions(draft) if draft is not None else [],
        "version_upload_form": UploadForm(),
        "version_paste_form": PasteForm(auto_id="id_version_%s"),
        "upload_limit": services.upload_size_text(),
        "keep_versions": settings.USERDATA_KEEP_VERSIONS,
    }


def _groups(
    series: list[UserSeries],
    covered: dict[str, tuple[int, int]],
    months: list[monthly.Group],
    dataset: Dataset,
) -> list[dict[str, Any]]:
    """
    Ряды по показателям: название, единица, вид величины, ссылки на виды холста и на вид
    по месяцам, если у показателя есть периоды внутри года.
    """
    month_urls: dict[str, str] = {}
    for month_group in months:
        month_urls.setdefault(
            month_group.indicator,
            f"{reverse('userdata:months', args=[dataset.public_id])}?"
            f"{urlencode({'group': month_group.code})}",
        )
    groups: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for item in series:
        record = item.record
        # Пересчёт на жителей и на км² — ряд того же показателя.
        group = groups.setdefault(
            record.indicator,
            {
                "title": record.title,
                "unit": record.unit,
                "kind": record.kind,
                "breaks": item.breaks,
                "months_url": month_urls.get(record.indicator, ""),
                "items": [],
            },
        )
        year = covered.get(item.key, (None, record.last_year))[1]
        derived = str(DatasetSeries.Derived(record.derived).label) if record.derived else ""
        group["items"].append(
            {
                "series": item,
                "record": record,
                "has_values": bool(record.values_count),
                "detail": ", ".join(part for part in (item.name, derived) if part),
                "views": _views(item),
                "related_url": f"{reverse('userdata:related', args=[dataset.public_id])}?"
                f"{urlencode({'series': item.key})}"
                if record.regions_count >= FEW_REGIONS
                else "",
                "last_full_year": year,
            }
        )
    return list(groups.values())


def _incomplete(
    series: list[UserSeries], covered: dict[str, tuple[int, int]]
) -> list[dict[str, Any]]:
    """Ряды с неполным последним годом: по умолчанию показывается последний полный."""
    found = []
    for item in series:
        full = covered.get(item.key)
        if full and item.record.last_year and full[1] < item.record.last_year:
            found.append({"series": item, "last": item.record.last_year, "full": full[1]})
    return found


class DatasetStudyView(DatasetMixin, View):
    """
    «Открыть в исследовании»: исследование, где ряды таблицы уже есть (последнее), иначе
    новое с названием таблицы; ряд выбран в панели — «Что сделать» сразу под ним.
    """

    def post(self, request: HttpRequest, public_id: str) -> HttpResponse:  # noqa: ARG002
        dataset = self.dataset
        key = request.POST.get("series", "")
        if not key.startswith(f"u:{dataset.code}:"):
            key = ""
        owned = studies.owned(request)
        study = next(
            (
                item
                for item in owned.order_by("-updated_at")
                if dataset.code in studies.dataset_codes(item)
            ),
            None,
        )
        if study is None:
            try:
                study = studies.create(request, dataset.title)
            except studies.StudyError as error:
                messages.error(request, str(error))
                return redirect("userdata:dataset", public_id=dataset.public_id)
        url = reverse("userdata:study", args=[study.public_id])
        return redirect(f"{url}?{urlencode({'series': key})}#study-actions" if key else url)


class DeleteView(DatasetMixin, View):
    """Удалить таблицу вместе с файлами."""

    def post(self, request: HttpRequest, public_id: str) -> HttpResponse:  # noqa: ARG002
        if not request.POST.get("confirm"):
            messages.error(request, gettext("Отметьте, что таблицу нужно удалить."))
            return redirect("userdata:dataset", public_id=self.dataset.public_id)
        title = self.dataset.title
        services.discard(self.dataset)
        messages.success(request, gettext("Таблица «%(title)s» удалена.") % {"title": title})
        return redirect("userdata:index")


class DownloadView(ReadableDatasetMixin, View):
    """
    Выгрузка таблицы: длинная таблица CSV или XLSX, исходный файл как загружен. Читателю
    ссылки — только если ссылка разрешает скачивать, и без исходного файла.
    """

    def get(
        self,
        request: HttpRequest,  # noqa: ARG002
        public_id: str,  # noqa: ARG002
        kind: str,
    ) -> HttpResponseBase:
        version = self.dataset.current_version
        if version is None or not (self.owner or scope.exportable(self.dataset.code)):
            raise Http404
        if kind == "source":
            if not self.owner:
                raise Http404
            return _source_file(version)
        source = scope.source_of_version(version)
        if source is None or kind not in {"csv", "xlsx"}:
            raise Http404
        plan = export.layout(version)
        name = f"regionlens-table-{self.dataset.code}.{kind}"
        if kind == "csv":
            response: HttpResponseBase = StreamingHttpResponse(
                export.csv_stream(source, plan), content_type="text/csv; charset=utf-8"
            )
        else:
            if export.count_rows(source) > export.XLSX_ROWS:
                raise Http404
            response = HttpResponse(
                export.xlsx_bytes(source, plan),
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        response["Content-Disposition"] = f'attachment; filename="{name}"'
        response["Cache-Control"] = "private, no-store"
        return response


def _source_file(version: DatasetVersion) -> HttpResponseBase:
    """Исходный файл как загружен — только вложением."""
    try:
        path = services.source_path(version)
    except ingest.IngestError as error:
        raise Http404 from error
    name = version.file_name if version.file_kind != ingest.PASTE else "table.tsv"
    response = FileResponse(path.open("rb"), as_attachment=True, filename=name)
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response
