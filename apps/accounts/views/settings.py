"""«Настройки»: профиль, вход и безопасность, персональные данные — одной страницей."""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.views.generic import TemplateView, View

from apps.core.documents import consent_label

from ..cabinet import CabinetViewMixin, settings_url
from ..constants import CABINET_LIMITS, ROLES_BY_NAME
from ..forms import (
    DeleteAccountForm,
    EmailChangeForm,
    PasswordConfirmForm,
    ProfileForm,
    TwoFactorDisableForm,
)
from ..roles import is_last_administrator


class SettingsContextMixin(CabinetViewMixin):
    """
    Сведения и формы страницы «Настройки».

    Действие с ошибкой формы показывает эту же страницу: форма с ошибками приходит
    в ``get_context_data`` под своим именем и заменяет чистую.
    """

    section_code = "settings"
    template_name: str | None = "accounts/settings.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Собрать формы и сведения всех трёх разделов."""
        from apps.feedback.models import Ticket

        context = super().get_context_data(**kwargs)
        user = self.current_user
        context.setdefault("profile_form", ProfileForm(instance=user))
        context.setdefault("email_form", EmailChangeForm(user=user))
        context.setdefault("disable_form", TwoFactorDisableForm(user=user))
        context.setdefault("codes_form", PasswordConfirmForm(user=user))
        context.setdefault("delete_form", DeleteAccountForm(user=user))
        # Форма действия возвращает страницу по адресу действия — это не подстраница.
        context["cabinet_subpage"] = False
        context["recovery_left"] = len(user.recovery_codes)
        context["panel_needs_code"] = user.has_panel_access
        context["roles"] = [ROLES_BY_NAME[name] for name in user.role_names]
        context["limits"] = CABINET_LIMITS
        context["stored"] = {
            "marks": user.favorites.count(),
            "views": user.saved_queries.count(),
            "tickets": Ticket.objects.filter(author=user).count(),
            "tables": user.datasets.count(),
            "studies": user.studies.count(),
        }
        context["last_administrator"] = is_last_administrator(user)
        context["account"] = user
        context["consent_label"] = consent_label()
        return context


class SettingsView(SettingsContextMixin, TemplateView):
    """Страница раздела."""


class SettingsPartView(CabinetViewMixin, View):
    """Прежний адрес раздела настроек: открытие ведёт к его месту на странице."""

    part = "profile"

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:  # noqa: ARG002
        """Перейти к разделу «Настроек»."""
        return redirect(settings_url(self.part))
