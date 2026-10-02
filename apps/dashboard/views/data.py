"""
Разделы «Загрузка данных» и «Качество данных».

Сборка запускается отдельным процессом (``etl_build --run``); склад подменяется
только после успешной сборки.
"""

from __future__ import annotations

from typing import Any

from django.contrib import messages
from django.db.models import QuerySet
from django.forms import ModelForm
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import DetailView, FormView, ListView, UpdateView

from apps.catalog.models import DatasetVersion
from apps.core.navigation import Crumb
from apps.warehouse import builds
from apps.warehouse.etl import integrity
from apps.warehouse.models import DataQualityCheck, EtlRun

from .. import selectors
from ..forms import DatasetUploadForm, DatasetVersionForm
from ..navigation import AdminViewMixin

# Команды обслуживания данных вручную на стенде.
MAINTENANCE_COMMANDS: tuple[dict[str, str], ...] = (
    {
        "command": "python manage.py etl_build --full",
        "title": "Полная сборка склада",
        "hint": "Из того же набора, что и действующий склад; при идущей сборке откажется",
    },
    {
        "command": "python manage.py etl_build --marts",
        "title": "Пересчёт витрин",
        "hint": "Быстрее полной сборки; применяется после изменения порогов покрытия",
    },
    {
        "command": "python manage.py seed_reference",
        "title": "Загрузка справочника территорий",
        "hint": "Восстанавливает справочник территорий и матрицу смежности",
    },
)


# Как часто карточка идущей сборки обновляет ход выполнения.
PROGRESS_POLL_SECONDS = 2


class DataView(AdminViewMixin, ListView):
    """Версии набора данных и история запусков загрузки."""

    template_name = "dashboard/data.html"
    context_object_name = "versions"
    section_code = "data"

    # Перечень, а не набор запросов: число запусков присоединяется в памяти.
    def get_queryset(self) -> list[DatasetVersion]:  # type: ignore[override]
        """Версии набора с числом запусков по каждой."""
        return selectors.dataset_versions()

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Дополнить контекст запусками, состоянием склада и командами обслуживания."""
        context = super().get_context_data(**kwargs)
        context["page_title"] = _("Загрузка данных и версии набора")
        context["runs"] = selectors.etl_runs()
        context["warehouse"] = selectors.warehouse_state()
        context["commands"] = MAINTENANCE_COMMANDS
        context["statuses"] = EtlRun.Status.choices
        context.setdefault("upload_form", DatasetUploadForm())
        context["active_run"] = builds.active_run()
        return context


class DatasetUploadView(AdminViewMixin, FormView):
    """Приём нового выпуска набора и запуск сборки; неверный состав отклоняется сразу."""

    form_class = DatasetUploadForm
    template_name = "dashboard/data.html"
    section_code = "data"
    http_method_names = ["post"]

    def form_valid(self, form: DatasetUploadForm) -> HttpResponse:
        """Сохранить файл, проверить состав и начать сборку."""
        if builds.active_run() is not None:
            messages.error(self.request, _("Сборка склада уже идёт — дождитесь её окончания"))
            return redirect("dashboard:data")

        path = builds.store_upload(form.cleaned_data["dataset"])
        problems = builds.check_structure(path)
        if problems:
            path.unlink(missing_ok=True)
            for problem in problems:
                form.add_error("dataset", problem)
            return self.form_invalid(form)

        run = builds.start_build(source_path=path, started_by=self.current_user)
        if run.status == EtlRun.Status.FAILED:
            messages.error(self.request, _("Сборка не начата: %s") % run.error_message)
        else:
            messages.success(self.request, _("Набор принят, сборка склада начата"))
        return redirect("dashboard:etl-run", pk=run.pk)

    def form_invalid(self, form: DatasetUploadForm) -> HttpResponse:
        """Показать страницу данных с ошибками формы."""
        view = DataView()
        view.setup(self.request)
        view.object_list = view.get_queryset()
        context = view.get_context_data(upload_form=form)
        return self.render_to_response(context, status=400)


class DatasetVersionEditView(AdminViewMixin, UpdateView):
    """Правка сведений о версии набора данных."""

    model = DatasetVersion
    form_class = DatasetVersionForm
    template_name = "dashboard/dataset_version_form.html"
    section_code = "data"
    context_object_name = "version"
    slug_field = "code"
    slug_url_kwarg = "code"

    def get_crumbs(self) -> tuple[Crumb, ...]:
        """Путь к странице правки версии."""
        return (
            Crumb(title=_("Панель управления"), url=reverse("dashboard:index")),
            Crumb(title=_("Загрузка данных"), url=reverse("dashboard:data")),
            Crumb(title=self.object.code),
        )

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Показать неизменяемые сведения о файле версии."""
        context = super().get_context_data(**kwargs)
        version = self.object
        context["page_title"] = version.code
        context["readonly_facts"] = [
            (_("Код версии"), version.code),
            (_("Дата публикации"), version.published_on),
            (_("Файл"), version.file_name or "—"),
            (
                _("Размер"),
                f"{version.file_size_bytes / 1024 / 1024:.1f} МБ"
                if version.file_size_bytes
                else "—",
            ),
            (_("Контрольная сумма"), version.file_checksum or "—"),
            (_("Наблюдений"), version.observation_count),
            (_("Период"), f"{version.first_year}–{version.last_year}"),
        ]
        return context

    def form_valid(self, form: ModelForm[Any]) -> HttpResponse:
        """Сохранить сведения, сняв отметку текущей версии с прежней (текущая — одна)."""
        if form.cleaned_data.get("is_current"):
            DatasetVersion.objects.filter(is_current=True).exclude(pk=self.object.pk).update(
                is_current=False
            )
        response = super().form_valid(form)
        messages.success(self.request, _("Сведения о версии сохранены"))
        return response

    def get_success_url(self) -> str:
        """Вернуться к перечню версий."""
        return reverse("dashboard:data")


class EtlRunDetailView(AdminViewMixin, DetailView):
    """Карточка запуска загрузки: параметры, итоги и журнал выполнения целиком."""

    model = EtlRun
    template_name = "dashboard/etl_run.html"
    section_code = "data"
    context_object_name = "run"

    def get_queryset(self) -> QuerySet[EtlRun]:
        """Запуски со связанными версией и инициатором."""
        return EtlRun.objects.select_related("dataset_version", "started_by")

    def get_crumbs(self) -> tuple[Crumb, ...]:
        """Путь к карточке запуска."""
        return (
            Crumb(title=_("Панель управления"), url=reverse("dashboard:index")),
            Crumb(title=_("Загрузка данных"), url=reverse("dashboard:data")),
            Crumb(title=_("Запуск №%(pk)s") % {"pk": self.object.pk}),
        )

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Дополнить контекст замечаниями качества, найденными этим запуском."""
        context = super().get_context_data(**kwargs)
        run = self.object
        context["page_title"] = _("Запуск загрузки №%(pk)s") % {"pk": run.pk}
        context["checks"] = list(run.quality_checks.all()[:30])
        context["checks_total"] = run.quality_checks.count()
        context["missing_groups"] = integrity.grouped(run.missing_series)
        context["poll_seconds"] = PROGRESS_POLL_SECONDS
        return context

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """Когда сборка закончилась, запрос хода перезагружает карточку целиком."""
        response = super().get(request, *args, **kwargs)
        if request.headers.get("HX-Request") and not self.object.is_active:
            response["HX-Refresh"] = "true"
        return response

    def get_template_names(self) -> list[str]:
        """Запрос HTMX за ходом сборки получает фрагмент ``{% partialdef %}`` карточки."""
        if self.request.headers.get("HX-Request"):
            return [f"{self.template_name}#progress"]
        return [self.template_name]


class QualityView(AdminViewMixin, ListView):
    """Замечания проверок качества, найденные последней сборкой склада."""

    template_name = "dashboard/quality.html"
    context_object_name = "checks"
    section_code = "data"
    paginate_by = 50

    def get_queryset(self) -> QuerySet[DataQualityCheck]:
        """Замечания с учётом фильтров перечня."""
        return selectors.quality_checks(
            severity=self.request.GET.get("severity", "").strip(),
            check_type=self.request.GET.get("type", "").strip(),
        )

    def get_crumbs(self) -> tuple[Crumb, ...]:
        """Путь к перечню: замечания — часть раздела загрузки данных."""
        return (
            Crumb(title=_("Панель управления"), url=reverse("dashboard:index")),
            Crumb(title=_("Загрузка данных"), url=reverse("dashboard:data")),
            Crumb(title=_("Замечания проверок")),
        )

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Дополнить контекст разбивкой по типам и фильтрами."""
        context = super().get_context_data(**kwargs)
        context["page_title"] = _("Замечания проверок")
        context["breakdown"] = selectors.quality_breakdown()
        context["severities"] = DataQualityCheck.Severity.choices
        context["types"] = DataQualityCheck.CheckType.choices
        context["selected_severity"] = self.request.GET.get("severity", "")
        context["selected_type"] = self.request.GET.get("type", "")
        return context
