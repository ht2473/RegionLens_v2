"""
Раздел «Источники»: внешние источники, журнал выпусков и сверка связей рядов с набором.

«Собрать сейчас» запускает ``collect`` отдельным процессом; склад пересобирается им самим.
"""

from __future__ import annotations

from typing import Any

from django.contrib import messages
from django.db.models import QuerySet
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import DetailView, TemplateView

from apps.core.navigation import Crumb
from apps.sources import collect
from apps.sources.models import Release

from .. import selectors
from ..navigation import AdminViewMixin

# Команды обслуживания сбора вручную на стенде.
COLLECT_COMMANDS: tuple[dict[str, str], ...] = (
    {
        "command": "python manage.py collect --due",
        "title": "Сбор по расписанию",
        "hint": "Проверяет источники, которым пора; так его запускает таймер",
    },
    {
        "command": "python manage.py collect rosstat_bulletin --url <адрес>",
        "title": "Выпуск по адресу",
        "hint": "Прежний выпуск, которого нет в журнале; в архив и в склад",
    },
    {
        "command": "python manage.py collect --all --reparse",
        "title": "Разобрать архив заново",
        "hint": "После правки разбора; склад пересобирается",
    },
    {
        "command": "python manage.py collect --verify",
        "title": "Сверить архив с описью",
        "hint": "Хэши файлов против MANIFEST.csv",
    },
)


class SourcesView(AdminViewMixin, TemplateView):
    """Источники, связи рядов и журнал выпусков."""

    template_name = "dashboard/sources.html"
    section_code = "sources"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Собрать состояние источников, сверку и журнал."""
        context = super().get_context_data(**kwargs)
        context["page_title"] = _("Источники данных")
        context["sources"] = selectors.sources_overview()
        context["links"] = selectors.source_link_rows()
        context["releases"] = selectors.release_journal()
        context["commands"] = COLLECT_COMMANDS
        return context


class CollectNowView(AdminViewMixin, View):
    """Запустить сбор одного источника отдельным процессом."""

    http_method_names = ["post"]
    section_code = "sources"

    def post(self, request: HttpRequest, code: str) -> HttpResponse:
        """Запустить и вернуться в раздел."""
        if code not in collect.SOURCES:
            raise Http404
        problem = collect.launch(code)
        if problem:
            messages.error(request, _("Сбор не начат: %s") % problem)
        else:
            messages.success(
                request,
                _("Сбор начат: новые выпуски появятся в журнале, склад пересоберётся сам"),
            )
        return redirect("dashboard:sources")


class ReleaseDetailView(AdminViewMixin, DetailView):
    """Карточка выпуска: файл в архиве, отчёт о разборе, изменения и ошибка."""

    model = Release
    template_name = "dashboard/release.html"
    section_code = "sources"
    context_object_name = "release"

    def get_queryset(self) -> QuerySet[Release]:
        """Выпуски вместе с источником."""
        return Release.objects.select_related("source")

    def get_crumbs(self) -> tuple[Crumb, ...]:
        """Путь к карточке выпуска."""
        return (
            Crumb(title=_("Панель управления"), url=reverse("dashboard:index")),
            Crumb(title=_("Источники"), url=reverse("dashboard:sources")),
            Crumb(title=f"{self.object.source.code}: {self.object.code}"),
        )

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Разложить отчёт о разборе по таблицам."""
        context = super().get_context_data(**kwargs)
        release = self.object
        context["page_title"] = _("Выпуск %(code)s") % {"code": release.code}
        context["tables"] = sorted(
            ((measure, info) for measure, info in (release.report or {}).get("tables", {}).items()),
            key=lambda item: item[1].get("table", item[1].get("sheet", "")),
        )
        context["latest"] = sorted(((release.changes or {}).get("latest") or {}).items())
        return context
