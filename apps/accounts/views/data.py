"""«Персональные данные» в «Настройках»: выгрузка, согласие, удаление учётной записи."""

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

from ..cabinet import CabinetViewMixin, settings_url
from ..forms import DeleteAccountForm
from ..roles import LastAdministratorError
from ..services import delete_account, export_account
from .settings import SettingsContextMixin


class DataView(SettingsContextMixin, FormView):
    """Удаление учётной записи с подтверждением паролем; форма — в «Настройках»."""

    form_class = DeleteAccountForm

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:  # noqa: ARG002
        """Форма живёт на странице «Настройки»."""
        return redirect(settings_url("data"))

    def get_form_kwargs(self) -> dict[str, Any]:
        """Передать форме пользователя."""
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.current_user
        return kwargs

    def form_invalid(self, form: DeleteAccountForm) -> HttpResponse:
        """Показать «Настройки» с ошибками формы удаления."""
        return self.render_to_response(self.get_context_data(delete_form=form))

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
        """Записать согласие, если отмечен флажок, и вернуться в «Персональные данные»."""
        if request.POST.get("consent"):
            self.current_user.record_consent()
            messages.success(request, _("Согласие на обработку персональных данных записано"))
        else:
            messages.error(request, _("Отметьте флажок согласия"))
        return redirect(settings_url("data"))


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
