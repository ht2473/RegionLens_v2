"""Раздел «Безопасность»: адрес почты, сеансы на других устройствах, вход с кодом."""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext_lazy as _
from django.views.generic import FormView, TemplateView, View

from apps.core.navigation import Crumb
from apps.core.throttle import allow_key
from apps.core.views import BreadcrumbMixin

from .. import security, totp
from ..cabinet import CABINET_SECTIONS, CabinetViewMixin
from ..constants import EMAIL_CHANGE_PER_USER
from ..forms import EmailChangeForm, PasswordConfirmForm, TwoFactorDisableForm, TwoFactorEnableForm


class SecurityContextMixin(CabinetViewMixin):
    """Сведения страницы «Безопасность»; формы с ошибками подставляются представлениями."""

    section_code = "security"
    template_name: str | None = "accounts/security.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Собрать состояние учётной записи и формы раздела."""
        context = super().get_context_data(**kwargs)
        user = self.current_user
        context.setdefault("email_form", EmailChangeForm(user=user))
        context.setdefault("disable_form", TwoFactorDisableForm(user=user))
        context.setdefault("codes_form", PasswordConfirmForm(user=user))
        context["recovery_left"] = len(user.recovery_codes)
        context["panel_needs_code"] = user.has_panel_access
        return context


class SecurityView(SecurityContextMixin, TemplateView):
    """Страница раздела."""


class EmailChangeView(SecurityContextMixin, FormView):
    """Запрос смены адреса: письмо со ссылкой на новый адрес."""

    form_class = EmailChangeForm

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:  # noqa: ARG002
        """Форма живёт на странице раздела."""
        return redirect("accounts:security")

    def get_form_kwargs(self) -> dict[str, Any]:
        """Передать форме пользователя."""
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.current_user
        return kwargs

    def form_invalid(self, form: EmailChangeForm) -> HttpResponse:
        """Показать страницу раздела с ошибками формы."""
        return self.render_to_response(self.get_context_data(email_form=form))

    def form_valid(self, form: EmailChangeForm) -> HttpResponse:
        """Отправить ссылку подтверждения, если предел писем не исчерпан."""
        user = self.current_user
        rate = EMAIL_CHANGE_PER_USER
        if not allow_key("email-change", str(user.pk), limit=rate.limit, window=rate.window):
            form.add_error(None, _("Слишком много запросов смены адреса. Попробуйте через час."))
            return self.form_invalid(form)
        error = security.request_email_change(self.request, user, form.cleaned_data["new_email"])
        if error:
            messages.error(
                self.request,
                _("Письмо на новый адрес не ушло: почтовый узел не ответил. Попробуйте позже."),
            )
        else:
            messages.success(
                self.request,
                _("На новый адрес отправлена ссылка. Адрес сменится, когда вы её откроете."),
            )
        return redirect("accounts:security")


class EmailChangeCancelView(CabinetViewMixin, View):
    """Отказ от ожидающей смены адреса."""

    def post(self, request: HttpRequest) -> HttpResponse:
        """Забыть новый адрес: ссылка из письма перестаёт действовать."""
        security.cancel_email_change(self.current_user)
        messages.success(request, _("Смена адреса отменена"))
        return redirect("accounts:security")


class EmailConfirmView(BreadcrumbMixin, TemplateView):
    """
    Подтверждение нового адреса по ссылке из письма; вход не нужен.

    Открытие ссылки только показывает кнопку: почтовые службы открывают ссылки сами.
    """

    template_name = "accounts/email_confirm.html"

    def get_crumbs(self) -> tuple[Crumb, ...]:
        """Путь к странице."""
        return (Crumb(title=_("Новый адрес почты")),)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Заголовок страницы."""
        context = super().get_context_data(**kwargs)
        context["page_title"] = _("Новый адрес почты")
        return context

    def post(self, request: HttpRequest, token: str) -> HttpResponse:
        """Сменить адрес."""
        try:
            user = security.confirm_email_change(request, token)
        except security.EmailChangeError as error:
            return self.render_to_response(self.get_context_data(error=str(error)))
        if request.user.is_authenticated and request.user.pk == user.pk:
            messages.success(request, _("Адрес почты изменён: %(email)s") % {"email": user.email})
            return redirect("accounts:security")
        messages.success(
            request,
            _("Адрес почты изменён. Войдите с новым адресом: %(email)s") % {"email": user.email},
        )
        return redirect(settings.LOGIN_URL)


class SignOutOthersView(CabinetViewMixin, View):
    """Завершение сеансов на других устройствах."""

    def post(self, request: HttpRequest) -> HttpResponse:
        """Сменить версию сеансов; текущий сеанс пересчитывается и остаётся."""
        security.end_other_sessions(request, self.current_user)
        messages.success(request, _("Сеансы на других устройствах завершены"))
        return redirect("accounts:security")


# ---------------------------------------------------------------------------------------
# Вход с кодом
# ---------------------------------------------------------------------------------------


class TwoFactorSetupView(CabinetViewMixin, FormView):
    """Включение входа с кодом: ключ и QR-код, затем первый код из приложения."""

    template_name = "accounts/two_factor_setup.html"
    form_class = TwoFactorEnableForm
    section_code = "security"

    def get_crumbs(self) -> tuple[Crumb, ...]:
        """Путь к странице: кабинет, «Безопасность», вход с кодом."""
        return (
            Crumb(title=_("Личный кабинет"), url=CABINET_SECTIONS[0].url),
            Crumb(title=self.section.title, url=self.section.url),
            Crumb(title=_("Вход с кодом")),
        )

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> Any:
        """Уже включённый вход с кодом второй раз не настраивается."""
        user: Any = request.user
        if user.is_authenticated and user.two_factor_enabled:
            return redirect("accounts:security")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Ключ, адрес для приложения и QR-код."""
        context = super().get_context_data(**kwargs)
        user = self.current_user
        secret = security.pending_secret(self.request)
        uri = totp.provisioning_uri(secret, user.email, settings.PROJECT_NAME)
        context["page_title"] = _("Вход с кодом")
        context["secret"] = totp.grouped(secret)
        context["uri"] = uri
        context["qr"] = totp.qr_picture(uri)
        return context

    def form_valid(self, form: TwoFactorEnableForm) -> HttpResponse:
        """Код совпал — включить и показать резервные коды один раз."""
        secret = security.pending_secret(self.request)
        step = totp.matching_step(secret, form.cleaned_data["code"])
        if step is None:
            form.add_error(
                "code", _("Код не подошёл. Проверьте время на телефоне и введите новый код.")
            )
            return self.form_invalid(form)
        codes = security.enable_two_factor(self.request, self.current_user, secret, step)
        messages.success(self.request, _("Вход с кодом включён"))
        return _show_codes(self.request, self, codes)


class TwoFactorDisableView(SecurityContextMixin, FormView):
    """Выключение входа с кодом: пароль и действующий код."""

    form_class = TwoFactorDisableForm

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:  # noqa: ARG002
        """Форма живёт на странице раздела."""
        return redirect("accounts:security")

    def get_form_kwargs(self) -> dict[str, Any]:
        """Передать форме пользователя."""
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.current_user
        return kwargs

    def form_invalid(self, form: TwoFactorDisableForm) -> HttpResponse:
        """Показать страницу раздела с ошибками формы."""
        return self.render_to_response(self.get_context_data(disable_form=form))

    def form_valid(self, form: TwoFactorDisableForm) -> HttpResponse:
        """С правом панели выключить нельзя; иначе — проверить код и выключить."""
        user = self.current_user
        if user.has_panel_access:
            form.add_error(None, _("С правами панели управления вход с кодом обязателен."))
            return self.form_invalid(form)
        if not security.verify_code(user, form.cleaned_data["code"]):
            form.add_error("code", _("Код не подошёл"))
            return self.form_invalid(form)
        security.disable_two_factor(self.request, user)
        messages.success(self.request, _("Вход с кодом выключен"))
        return redirect("accounts:security")


class RecoveryCodesView(SecurityContextMixin, FormView):
    """Новый набор резервных кодов вместо прежнего; подтверждается паролем."""

    form_class = PasswordConfirmForm

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:  # noqa: ARG002
        """Форма живёт на странице раздела."""
        return redirect("accounts:security")

    def get_form_kwargs(self) -> dict[str, Any]:
        """Передать форме пользователя."""
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.current_user
        return kwargs

    def form_invalid(self, form: PasswordConfirmForm) -> HttpResponse:
        """Показать страницу раздела с ошибками формы."""
        return self.render_to_response(self.get_context_data(codes_form=form))

    def form_valid(self, form: PasswordConfirmForm) -> HttpResponse:  # noqa: ARG002
        """Выпустить коды и показать их один раз; пароль проверила форма."""
        user = self.current_user
        if not user.two_factor_enabled:
            return redirect("accounts:security")
        codes = security.renew_recovery_codes(user)
        messages.success(self.request, _("Выпущены новые резервные коды; прежние не действуют"))
        return _show_codes(self.request, self, codes)


def _show_codes(request: HttpRequest, view: CabinetViewMixin, codes: list[str]) -> HttpResponse:
    """Страница с резервными кодами; адреса у неё нет — обновление кодов не покажет."""
    context = view.cabinet_context()
    context.update(page_title=_("Резервные коды"), codes=codes)
    return render(request, "accounts/recovery_codes.html", context)
