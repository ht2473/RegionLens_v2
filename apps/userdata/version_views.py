"""
Версии таблицы: новая версия файла, отмена черновика, возврат к прежней и отчёт о различиях.
Всё — только владельцу; чужая таблица — 404.
"""

from __future__ import annotations

from typing import Any

from django.contrib import messages
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView, View

from apps.core.navigation import Crumb
from apps.core.views import BreadcrumbMixin

from . import changes, ingest, renew, services
from .forms import PasteForm, UploadForm
from .models import DatasetVersion
from .pages import DatasetMixin
from .series import UserSeries


def _versions_url(public_id: Any) -> str:
    """Версии — на вкладке «Файл и версии» страницы таблицы."""
    return f"{reverse('userdata:dataset', args=[public_id])}?tab=file#own-versions"


class VersionUploadView(DatasetMixin, View):
    """
    Новая версия файла: та же таблица файла, рецепт прежней версии; без вопросов —
    сборка и отчёт о различиях, с вопросами — шаги мастера над новой версией.
    """

    def post(self, request: HttpRequest, public_id: str) -> HttpResponse:  # noqa: ARG002
        dataset = self.dataset
        current = dataset.current_version
        if current is None or current.state != DatasetVersion.State.BUILT:
            messages.error(request, gettext("Сначала соберите первую версию таблицы."))
            return redirect(_versions_url(dataset.public_id))
        try:
            if request.POST.get("action") == "paste":
                paste_form = PasteForm(request.POST)
                if not paste_form.is_valid():
                    raise ingest.IngestError(_first_error(paste_form), "paste")
                version = services.create_version(
                    request,
                    dataset,
                    text=paste_form.cleaned_data["text"],
                    markup=paste_form.cleaned_data["markup"],
                )
            else:
                upload_form = UploadForm(request.POST, request.FILES)
                if not upload_form.is_valid():
                    raise ingest.IngestError(_first_error(upload_form), "upload")
                version = services.create_version(
                    request, dataset, uploaded=upload_form.cleaned_data["file"]
                )
            chosen = renew.choose_table(version)
        except ingest.IngestError as error:
            messages.error(request, str(error))
            return redirect(_versions_url(dataset.public_id))
        if not chosen:
            messages.info(
                request,
                gettext("В файле несколько таблиц: выберите ту, что в прежней версии."),
            )
            return redirect("userdata:file", public_id=dataset.public_id)
        renew.transfer(version)
        return redirect(renew.proceed(version, request.user))


def _first_error(form: Any) -> str:
    for errors in form.errors.values():
        if errors:
            return str(errors[0])
    return gettext("Файл не принят.")


class VersionDiscardView(DatasetMixin, View):
    """Отменить новую версию в работе: таблица остаётся на текущей."""

    def post(self, request: HttpRequest, public_id: str) -> HttpResponse:  # noqa: ARG002
        renew.discard_draft(self.dataset)
        messages.success(request, gettext("Новая версия отменена."))
        return redirect(_versions_url(self.dataset.public_id))


class VersionRestoreView(DatasetMixin, View):
    """Вернуться к прежней собранной версии."""

    def post(self, request: HttpRequest, public_id: str, number: int) -> HttpResponse:  # noqa: ARG002
        version = self.dataset.versions.filter(number=number).first()
        if version is None:
            raise Http404
        try:
            renew.restore(self.dataset, version)
        except ingest.IngestError as error:
            messages.error(request, str(error))
        else:
            messages.success(
                request, gettext("Таблица снова в версии %(number)s.") % {"number": number}
            )
        return redirect(_versions_url(self.dataset.public_id))


class VersionView(DatasetMixin, BreadcrumbMixin, TemplateView):
    """Что изменилось в версии по сравнению с прежней: значения, ряды, территории, годы."""

    template_name = "userdata/version.html"

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        self.number = int(kwargs["number"])
        return super().dispatch(request, *args, **kwargs)

    def get_crumbs(self) -> tuple[Crumb, ...]:
        return (
            Crumb(title=_("Свои данные"), url=reverse("userdata:index")),
            Crumb(
                title=self.dataset.title,
                url=reverse("userdata:dataset", args=[self.dataset.public_id]),
            ),
            Crumb(title=gettext("Версия %(number)s") % {"number": self.number}),
        )

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        version = self.dataset.versions.filter(number=self.number).first()
        if version is None or version.state != DatasetVersion.State.BUILT:
            raise Http404
        found = changes.report(version)
        titles = {
            UserSeries(record, self.dataset).key: UserSeries(record, self.dataset).full_title
            for record in version.series.all()
        }
        if found is not None:
            for row in found["largest"]:
                row["title"] = titles.get(row["series_key"], row["series_key"])
        context.update(
            page_title=gettext("Версия %(number)s: что изменилось") % {"number": self.number},
            dataset=self.dataset,
            version=version,
            changes=found,
            is_current=self.dataset.current_version_id == version.pk,
        )
        return context
