"""Раздел «Мои данные»: выгрузка всего своего одним файлом и удаление учётной записи."""

from __future__ import annotations

import json
from typing import Any

from django.contrib import messages
from django.contrib.auth import logout
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.generic import FormView, View

from apps.core.documents import consent_label

from ..cabinet import CabinetViewMixin
from ..forms import DeleteAccountForm
from ..roles import LastAdministratorError, is_last_administrator
from ..services import delete_account, export_account


class DataView(CabinetViewMixin, FormView):
    """Страница раздела: что хранится, выгрузка, удаление с подтверждением паролем."""

    template_name = "accounts/data.html"
    form_class = DeleteAccountForm
    section_code = "data"

    def get_form_kwargs(self) -> dict[str, Any]:
        """Передать форме пользователя."""
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.current_user
        return kwargs

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Сколько чего хранится."""
        from apps.feedback.models import Ticket

        context = super().get_context_data(**kwargs)
        user = self.current_user
        context["stored"] = {
            "marks": user.favorites.count(),
            "views": user.saved_queries.count(),
            "tickets": Ticket.objects.filter(author=user).count(),
        }
        context["last_administrator"] = is_last_administrator(user)
        context["account"] = user
        context["consent_label"] = consent_label()
        return context

    def form_valid(self, form: DeleteAccountForm) -> HttpResponse:
        """Удалить учётную запись, выйти и сообщить об этом."""
        user = self.current_user
        try:
            delete_account(user)
        except LastAdministratorError:
            form.add_error(
                None,
                _("Это последняя учётная запись администратора. Сначала назначьте другого."),
            )
            return self.form_invalid(form)
        logout(self.request)
        messages.success(self.request, _("Учётная запись удалена. На её адрес отправлено письмо."))
        return redirect("core:home")


class ConsentView(CabinetViewMixin, View):
    """Согласие на обработку на действующую редакцию: у записей, заведённых без него."""

    def post(self, request: HttpRequest) -> HttpResponse:
        """Записать согласие, если отмечен флажок, и вернуться в «Мои данные»."""
        if request.POST.get("consent"):
            self.current_user.record_consent()
            messages.success(request, _("Согласие на обработку персональных данных записано"))
        else:
            messages.error(request, _("Отметьте флажок согласия"))
        return redirect("accounts:data")


class ExportView(CabinetViewMixin, View):
    """Выгрузка своих данных файлом JSON."""

    def get(self, request: HttpRequest) -> HttpResponse:  # noqa: ARG002
        """Отдать файл для сохранения."""
        payload = json.dumps(export_account(self.current_user), ensure_ascii=False, indent=2)
        name = f"regionlens-account-{timezone.localdate():%Y-%m-%d}.json"
        response = HttpResponse(payload, content_type="application/json; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{name}"'
        response["Cache-Control"] = "no-store"
        return response
