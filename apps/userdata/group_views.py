"""
Свои группы регионов: страница групп (создать, изменить, удалить) и окно выбора группы
для карточки «Группа регионов против остальных» на поле исследования.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from django.contrib import messages
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView

from apps.catalog.selectors import grouped_territories
from apps.core.navigation import Crumb
from apps.core.views import BreadcrumbMixin

from . import groups, studies
from .study_views import edit_allowed


class GroupsView(BreadcrumbMixin, TemplateView):
    """
    Группы регионов того, кто смотрит: перечень и правка одной (``?edit=``) или новой.
    Из исследования (``?next=``) после сохранения — обратно к нему.
    """

    template_name = "userdata/groups.html"

    def get_crumbs(self) -> tuple[Crumb, ...]:
        return (
            Crumb(title=_("Свои данные"), url=reverse("userdata:index")),
            Crumb(title=_("Группы регионов")),
        )

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        own = list(groups.owned(self.request))
        editing = kwargs.get("editing")
        if editing is None and self.request.GET.get("edit"):
            editing = next(
                (item for item in own if str(item.public_id) == self.request.GET["edit"]), None
            )
        context.update(
            page_title=_("Группы регионов"),
            own=own,
            editing=editing,
            title=kwargs.get("title", editing.title if editing else ""),
            chosen=kwargs.get("codes", editing.codes if editing else []),
            territory_groups=grouped_territories(),
            next_url=_next(self.request),
            limit=groups.MAX_GROUPS,
            guest=not self.request.user.is_authenticated,
        )
        return context

    def post(self, request: HttpRequest) -> HttpResponse:
        action = request.POST.get("action", "save")
        group = None
        if request.POST.get("group"):
            try:
                group = groups.own_or_404(request, request.POST["group"])
            except (ValueError, Http404) as error:
                raise Http404 from error
        if not edit_allowed(request):
            messages.error(request, gettext("Слишком много изменений подряд. Повторите позже."))
            return redirect("userdata:groups")
        if action == "delete" and group is not None:
            title = group.title
            group.delete()
            messages.success(request, gettext("Группа «%(title)s» удалена.") % {"title": title})
            return redirect("userdata:groups")
        title, codes = request.POST.get("title", ""), request.POST.getlist("territory")
        try:
            saved = groups.save(request, group, title, codes)
        except groups.GroupError as error:
            messages.error(request, str(error))
            return self.render_to_response(
                self.get_context_data(editing=group, title=title, codes=codes)
            )
        messages.success(request, gettext("Группа «%(title)s» сохранена.") % {"title": saved.title})
        return redirect(_next(request) or reverse("userdata:groups"))


def _next(request: HttpRequest) -> str:
    """Куда вернуться после сохранения: только адрес этого сайта."""
    target = request.POST.get("next") or request.GET.get("next") or ""
    allowed = url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    )
    return target if target and allowed else ""


class StudyGroupView(TemplateView):
    """
    Выбор своей группы для карточки «Группа регионов против остальных»: окном на поле
    (фрагмент) или страницей без сценариев; групп нет — путь к их созданию.
    """

    template_name = "userdata/study_group.html"

    def get_template_names(self) -> list[str]:
        if self.request.headers.get("HX-Request"):
            return ["userdata/_group_pick.html"]
        return [self.template_name]

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        study = studies.owned(self.request).filter(public_id=kwargs["public_id"]).first()
        if study is None:
            raise Http404
        key = self.request.GET.get("series", "")
        back = f"{reverse('userdata:study', args=[study.public_id])}?{urlencode({'series': key})}"
        context.update(
            study=study,
            series=key,
            own=list(groups.owned(self.request)),
            create_url=f"{reverse('userdata:groups')}?{urlencode({'next': back})}",
        )
        return context
