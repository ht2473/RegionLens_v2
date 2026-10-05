"""Обзор кабинета и профиль учётной записи."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.contrib import messages
from django.db.models import QuerySet
from django.forms import ModelForm
from django.http import HttpResponse
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView, UpdateView

from ..cabinet import CabinetViewMixin, role_label
from ..constants import CABINET_LIMITS, ROLES_BY_NAME
from ..forms import ProfileForm
from ..models import User
from ..region import my_region, region_choices, write_cookie

# Граница «нового по сохранённому» на время сеанса: обновление страницы её не сдвигает.
UPDATES_SINCE_SESSION_KEY = "cabinet:updates-since"
# Сколько последних открытых видов показывает обзор.
RECENT_VIEWS = 4


class CabinetOverviewView(CabinetViewMixin, TemplateView):
    """Первая страница кабинета: мой регион, новое по сохранённому, последние виды."""

    template_name = "accounts/overview.html"
    section_code = "overview"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Собрать блоки обзора."""
        from apps.catalog.brief import region_brief
        from apps.feedback.models import Ticket
        from apps.workspace.selectors import favorites, saved_queries
        from apps.workspace.updates import saved_updates

        context = super().get_context_data(**kwargs)
        user = self.current_user
        region = my_region(self.request)
        context["region"] = region_brief(region) if region is not None else None
        if region is None:
            context["region_choices"] = region_choices()

        since = self._updates_since(user)
        context["updates"] = saved_updates(user, since)
        context["updates_since"] = since

        views = saved_queries(user)
        opened = list(
            views.filter(last_opened_at__isnull=False).order_by("-last_opened_at")[:RECENT_VIEWS]
        )
        context["recent_views"] = opened or list(views[:RECENT_VIEWS])
        context["recent_opened"] = bool(opened)
        context["counts"] = {
            "views": views.count(),
            "marks": favorites(user).count(),
            "tickets_open": Ticket.objects.filter(author=user).open().count(),
            "tickets_answered": Ticket.objects.filter(author=user).answered().count(),
        }
        context["own_tables"] = list(
            user.datasets.select_related("current_version").order_by("-updated_at")[:RECENT_VIEWS]
        )
        context["own_tables_count"] = user.datasets.count()
        context["own_tables_bytes"] = sum(item.size_bytes for item in user.datasets.all())
        context["own_boards"] = list(user.boards.order_by("-updated_at")[:RECENT_VIEWS])
        context["panel_needs_code"] = user.has_panel_access and not user.two_factor_enabled
        return context

    def _updates_since(self, user: User) -> datetime:
        """
        Граница нового: прошлый визит; впервые в сеансе — запомнить её и сдвинуть отметку.

        До первого визита границей служит регистрация: выпуски до неё новыми не считаются.
        """
        session = self.request.session
        stored = session.get(UPDATES_SINCE_SESSION_KEY)
        if stored:
            return datetime.fromisoformat(stored)
        since = user.updates_seen_at or user.created_at
        session[UPDATES_SINCE_SESSION_KEY] = since.isoformat()
        User.objects.filter(pk=user.pk).update(updates_seen_at=timezone.now())
        return since


class ProfileView(CabinetViewMixin, UpdateView):
    """Сведения о себе, мой регион и роли."""

    template_name = "accounts/profile.html"
    form_class = ProfileForm
    section_code = "profile"
    success_url = reverse_lazy("accounts:profile")

    def get_object(self, queryset: QuerySet[Any] | None = None) -> Any:  # noqa: ARG002
        """Правится только собственная учётная запись."""
        return self.current_user

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Дополнить контекст ролями и их возможностями."""
        context = super().get_context_data(**kwargs)
        context["page_title"] = _("Профиль")
        context["roles"] = [ROLES_BY_NAME[name] for name in self.current_user.role_names]
        context["role_label"] = role_label(self.current_user)
        context["limits"] = CABINET_LIMITS
        return context

    def form_valid(self, form: ModelForm[Any]) -> HttpResponse:
        """Сохранить изменения; регион запоминается и в cookie — для страниц после выхода."""
        response = super().form_valid(form)
        write_cookie(response, form.cleaned_data.get("region"))
        messages.success(self.request, _("Профиль сохранён"))
        return response
