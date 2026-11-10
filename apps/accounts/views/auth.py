"""Вход (с кодом вторым шагом), регистрация и работа с паролем."""

from __future__ import annotations

import time
from typing import Any

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth import views as auth_views
from django.contrib.auth.signals import user_login_failed
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect, JsonResponse
from django.http.response import HttpResponseBase
from django.shortcuts import redirect, resolve_url
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.generic import FormView, TemplateView

from apps.core.navigation import Crumb
from apps.core.throttle import allow, allow_key
from apps.core.utils.redirects import is_local_path
from apps.core.views import BreadcrumbMixin

from ..cabinet import CABINET_SECTIONS, CabinetViewMixin, settings_url
from ..constants import (
    CODE_ATTEMPTS_PER_LOGIN,
    CODE_PER_IP,
    PENDING_LOGIN_EXTENSIONS,
    PENDING_LOGIN_SECONDS,
    PENDING_LOGIN_WARNING,
    REGISTER_PER_EMAIL,
    REGISTER_PER_IP,
    RESET_PER_EMAIL,
    RESET_PER_IP,
)
from ..forms import (
    CodeForm,
    EmailAuthenticationForm,
    RegistrationForm,
    StyledPasswordChangeForm,
    StyledPasswordResetForm,
    StyledSetPasswordForm,
)
from ..models import User
from ..security import mark_second_factor, passed_second_factor, verify_code
from ..services import RegistrationError, confirm_registration, request_registration

# Сеанс между верным паролем и кодом: кто входит, куда потом и сколько кодов не подошло.
PENDING_LOGIN_SESSION_KEY = "accounts:pending-login"


class LoginView(BreadcrumbMixin, auth_views.LoginView):
    """Вход по адресу почты; при включённом входе с кодом — второй шаг."""

    template_name = "accounts/login.html"
    authentication_form = EmailAuthenticationForm
    redirect_authenticated_user = True

    def get_crumbs(self) -> tuple[Crumb, ...]:
        """Путь к странице."""
        return (Crumb(title=_("Вход")),)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Дополнить контекст заголовком страницы."""
        context = super().get_context_data(**kwargs)
        context["page_title"] = _("Вход")
        return context

    def form_valid(self, form: EmailAuthenticationForm) -> HttpResponse:  # type: ignore[override]
        """Верный пароль без второго фактора — вход; с ним — переход ко вводу кода."""
        user: Any = form.get_user()
        if not user.two_factor_enabled:
            return super().form_valid(form)
        self.request.session[PENDING_LOGIN_SESSION_KEY] = {
            "user": user.pk,
            "backend": user.backend,
            "since": time.time(),
            "next": self.get_success_url(),
            "failures": 0,
        }
        return redirect("accounts:login-code")


class LoginCodeView(BreadcrumbMixin, FormView):
    """
    Второй шаг входа — одноразовый код; тот же шаг подтверждает уже открытый сеанс.

    Неверный код учитывается django-axes как неудачный вход: подбор кода закрывает и пароль.
    """

    template_name = "accounts/login_code.html"
    form_class = CodeForm

    pending: dict[str, Any] | None
    account: User | None

    def get_crumbs(self) -> tuple[Crumb, ...]:
        """Путь к странице."""
        return (Crumb(title=_("Код входа")),)

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        """Без ожидающего входа и без сеанса, требующего подтверждения, — на форму входа."""
        self.pending, self.account = self._pending(request)
        if self.account is None:
            user: Any = request.user
            if (
                user.is_authenticated
                and user.two_factor_enabled
                and not passed_second_factor(request)
            ):
                self.account = user
            elif user.is_authenticated:
                return redirect(settings.LOGIN_REDIRECT_URL)
            else:
                return redirect("accounts:login")
        return super().dispatch(request, *args, **kwargs)

    @staticmethod
    def _pending(request: HttpRequest) -> tuple[dict[str, Any] | None, User | None]:
        """Ожидающий второй шаг, если он не истёк."""
        data = request.session.get(PENDING_LOGIN_SESSION_KEY)
        if not data or time.time() - data.get("since", 0) > PENDING_LOGIN_SECONDS:
            request.session.pop(PENDING_LOGIN_SESSION_KEY, None)
            return None, None
        account = User.objects.filter(pk=data["user"], is_active=True).first()
        return (data, account) if account is not None else (None, None)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Заголовок и адрес, для которого нужен код."""
        context = super().get_context_data(**kwargs)
        context["page_title"] = _("Код входа")
        context["account_email"] = self.account.email if self.account else ""
        context["reverify"] = self.pending is None
        context["timer"] = self._timer()
        return context

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """Продление ожидания — отдельное действие формы; остальное — проверка кода."""
        if request.POST.get("action") == "extend":
            return self._extend()
        return super().post(request, *args, **kwargs)

    def _timer(self) -> dict[str, int] | None:
        """Остаток времени на ввод кода и число оставшихся продлений; без ожидания — нет."""
        if self.pending is None:
            return None
        passed = time.time() - self.pending.get("since", 0)
        return {
            "seconds": max(0, int(PENDING_LOGIN_SECONDS - passed)),
            "warning": PENDING_LOGIN_WARNING,
            "extensions": PENDING_LOGIN_EXTENSIONS - self.pending.get("extensions", 0),
        }

    def _extend(self) -> HttpResponse:
        """
        Продлить ожидание кода (WCAG 2.2.1): отсчёт заново, не больше десяти раз подряд.

        Сценарий страницы ждёт ответа JSON и оставляет набранные цифры; без сценариев
        форма отправляется обычным образом и страница открывается заново.
        """
        request = self.request
        pending = self.pending
        if pending is not None and pending.get("extensions", 0) < PENDING_LOGIN_EXTENSIONS:
            pending["since"] = time.time()
            pending["extensions"] = pending.get("extensions", 0) + 1
            request.session[PENDING_LOGIN_SESSION_KEY] = pending
        if "application/json" in request.headers.get("Accept", ""):
            return JsonResponse(self._timer() or {})
        return redirect(request.get_full_path())

    def form_valid(self, form: CodeForm) -> HttpResponse:
        """Проверить код и завершить вход."""
        request = self.request
        account = self.account
        assert account is not None
        if not allow(request, "login-code", limit=CODE_PER_IP.limit, window=CODE_PER_IP.window):
            form.add_error(None, _("Слишком много попыток ввода кода. Попробуйте через час."))
            return self.form_invalid(form)

        before = len(account.recovery_codes)
        if not verify_code(account, form.cleaned_data["code"]):
            return self._reject(form, account)

        target = self._target()
        if self.pending is not None:
            request.session.pop(PENDING_LOGIN_SESSION_KEY, None)
            login(request, account, backend=self.pending["backend"])
        mark_second_factor(request)
        if len(account.recovery_codes) < before:
            messages.warning(
                request,
                _(
                    "Вход по резервному коду; осталось кодов: %(count)d. Новые выпускаются "
                    "в «Настройках» кабинета."
                )
                % {"count": len(account.recovery_codes)},
            )
        return HttpResponseRedirect(target)

    def _reject(self, form: CodeForm, account: User) -> HttpResponse:
        """Неверный код: учесть попытку; после пяти — ввод пароля заново."""
        request = self.request
        user_login_failed.send(
            sender=__name__, credentials={"username": account.email}, request=request
        )
        if self.pending is not None:
            self.pending["failures"] = self.pending.get("failures", 0) + 1
            if self.pending["failures"] >= CODE_ATTEMPTS_PER_LOGIN:
                request.session.pop(PENDING_LOGIN_SESSION_KEY, None)
                messages.error(request, _("Слишком много неверных кодов. Войдите заново."))
                return redirect("accounts:login")
            request.session[PENDING_LOGIN_SESSION_KEY] = self.pending
        form.add_error(
            "code", _("Код не подошёл. Проверьте время на телефоне и введите новый код.")
        )
        return self.form_invalid(form)

    def _target(self) -> str:
        """Куда перейти после кода: сохранённый при вводе пароля адрес или ``next``."""
        request = self.request
        wanted = (self.pending or {}).get("next") or request.GET.get("next", "")
        if is_local_path(request, str(wanted)):
            return str(wanted)
        return resolve_url(settings.LOGIN_REDIRECT_URL)


class LogoutView(auth_views.LogoutView):
    """Выход из системы."""


class RegisterView(BreadcrumbMixin, FormView):
    """
    Регистрация учётной записи: заявка и письмо со ссылкой на адрес.

    Войти можно после перехода по ссылке — так запись получает только владелец адреса.
    Ответ страницы один и тот же для свободного и занятого адреса.
    """

    template_name = "accounts/register.html"
    form_class = RegistrationForm
    success_url = reverse_lazy("accounts:register-done")

    def get_crumbs(self) -> tuple[Crumb, ...]:
        """Путь к странице."""
        return (Crumb(title=_("Регистрация")),)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Дополнить контекст заголовком страницы."""
        context = super().get_context_data(**kwargs)
        context["page_title"] = _("Регистрация")
        return context

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        """Вошедшему пользователю регистрация не нужна."""
        if request.user.is_authenticated:
            return redirect("accounts:dashboard")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form: RegistrationForm) -> HttpResponse:
        """Создать учётную запись и войти под ней, если пределы частоты не исчерпаны."""
        email = form.cleaned_data["email"]
        if not allow(
            self.request, "register", limit=REGISTER_PER_IP.limit, window=REGISTER_PER_IP.window
        ) or not allow_key(
            "register-email",
            email,
            limit=REGISTER_PER_EMAIL.limit,
            window=REGISTER_PER_EMAIL.window,
        ):
            form.add_error(None, _("Слишком много регистраций подряд. Попробуйте через час."))
            return self.form_invalid(form)

        error = request_registration(
            self.request,
            email=email,
            password=form.cleaned_data["password2"],
            full_name=form.cleaned_data["full_name"],
        )
        if error:
            form.add_error(None, _("Письмо не ушло: почтовый узел не ответил. Попробуйте позже."))
            return self.form_invalid(form)
        return super().form_valid(form)


class RegisterDoneView(BreadcrumbMixin, TemplateView):
    """Сообщение об отправке письма; не раскрывает, был ли адрес зарегистрирован."""

    template_name = "accounts/register_done.html"

    def get_crumbs(self) -> tuple[Crumb, ...]:
        """Путь к странице."""
        return (Crumb(title=_("Регистрация")),)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Заголовок страницы и срок действия ссылки."""
        context = super().get_context_data(**kwargs)
        context["page_title"] = _("Письмо отправлено")
        context["days"] = settings.PASSWORD_RESET_TIMEOUT // (24 * 3600)
        return context


class RegisterConfirmView(BreadcrumbMixin, TemplateView):
    """
    Подтверждение адреса по ссылке из письма.

    Открытие ссылки только показывает кнопку: почтовые службы открывают ссылки сами.
    После подтверждения — обычный вход: пароль знает тот, кто заполнял форму.
    """

    template_name = "accounts/register_confirm.html"

    def get_crumbs(self) -> tuple[Crumb, ...]:
        """Путь к странице."""
        return (Crumb(title=_("Подтверждение регистрации")),)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Заголовок страницы."""
        context = super().get_context_data(**kwargs)
        context["page_title"] = _("Подтверждение регистрации")
        return context

    def post(self, request: HttpRequest, token: str) -> HttpResponse:
        """Подтвердить адрес и отправить ко входу."""
        try:
            confirm_registration(token)
        except RegistrationError as error:
            return self.render_to_response(self.get_context_data(error=str(error)))
        messages.success(request, _("Адрес почты подтверждён. Войдите с ним и своим паролем."))
        return redirect(settings.LOGIN_URL)


# ---------------------------------------------------------------------------------------
# Работа с паролем
# ---------------------------------------------------------------------------------------


class PasswordChangeView(CabinetViewMixin, auth_views.PasswordChangeView):
    """Смена пароля из «Настроек»; другие сеансы при этом завершаются."""

    template_name = "accounts/password_change.html"
    form_class = StyledPasswordChangeForm
    section_code = "settings"

    def get_success_url(self) -> str:
        """Вернуться к разделу «Вход и безопасность»."""
        return settings_url("security")

    def get_crumbs(self) -> tuple[Crumb, ...]:
        """Путь к странице: кабинет, «Настройки», смена пароля."""
        return (
            Crumb(title=_("Личный кабинет"), url=CABINET_SECTIONS[0].url),
            Crumb(title=self.section.title, url=self.section.url),
            Crumb(title=_("Смена пароля")),
        )

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Дополнить контекст заголовком страницы."""
        context = super().get_context_data(**kwargs)
        context["page_title"] = _("Смена пароля")
        return context

    # Вид формы задан ``form_class``; представления Django им не параметризуются.
    def form_valid(self, form: StyledPasswordChangeForm) -> HttpResponse:  # type: ignore[override]
        """Сменить пароль и сообщить об этом."""
        response = super().form_valid(form)
        messages.success(self.request, _("Пароль изменён. Сеансы на других устройствах завершены."))
        return response


class PasswordResetView(BreadcrumbMixin, auth_views.PasswordResetView):
    """Запрос ссылки восстановления пароля; пределы — по адресу отправителя и по почте."""

    template_name = "accounts/password_reset.html"
    form_class = StyledPasswordResetForm
    email_template_name = "accounts/mail/password_reset.txt"
    subject_template_name = "accounts/mail/password_reset_subject.txt"
    success_url = reverse_lazy("accounts:password-reset-done")

    def get_crumbs(self) -> tuple[Crumb, ...]:
        """Путь к странице."""
        return (Crumb(title=_("Восстановление пароля")),)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Дополнить контекст заголовком страницы."""
        context = super().get_context_data(**kwargs)
        context["page_title"] = _("Восстановление пароля")
        return context

    def form_valid(self, form: StyledPasswordResetForm) -> HttpResponse:  # type: ignore[override]
        """
        Отправить письмо, если пределы не исчерпаны.

        Исчерпанный предел по почте не показывается: ответ тот же, что при отправке, иначе
        по нему узнавали бы зарегистрированные адреса.
        """
        if not allow(self.request, "reset", limit=RESET_PER_IP.limit, window=RESET_PER_IP.window):
            form.add_error(None, _("Слишком много запросов восстановления. Попробуйте через час."))
            return self.form_invalid(form)
        if not allow_key(
            "reset-email",
            form.cleaned_data["email"],
            limit=RESET_PER_EMAIL.limit,
            window=RESET_PER_EMAIL.window,
        ):
            return HttpResponseRedirect(self.get_success_url())
        return super().form_valid(form)


class PasswordResetDoneView(BreadcrumbMixin, auth_views.PasswordResetDoneView):
    """Сообщение об отправке письма восстановления."""

    template_name = "accounts/password_reset_done.html"

    def get_crumbs(self) -> tuple[Crumb, ...]:
        """Путь к странице."""
        return (Crumb(title=_("Восстановление пароля")),)


class PasswordResetConfirmView(BreadcrumbMixin, auth_views.PasswordResetConfirmView):
    """Назначение нового пароля по ссылке из письма."""

    template_name = "accounts/password_reset_confirm.html"
    form_class = StyledSetPasswordForm
    success_url = reverse_lazy("accounts:password-reset-complete")

    def get_crumbs(self) -> tuple[Crumb, ...]:
        """Путь к странице."""
        return (Crumb(title=_("Новый пароль")),)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Дополнить контекст заголовком страницы."""
        context = super().get_context_data(**kwargs)
        context["page_title"] = _("Новый пароль")
        return context

    def form_valid(self, form: StyledSetPasswordForm) -> HttpResponse:  # type: ignore[override]
        """
        Назначить пароль; ссылка из письма заодно подтверждает адрес почты.

        Так владелец адреса получает запись, которую на его почту завёл кто-то другой:
        новый пароль знает только он.
        """
        response = super().form_valid(form)
        user: Any = form.user
        if user.email_confirmed_at is None:
            user.email_confirmed_at = timezone.now()
            user.save(update_fields=["email_confirmed_at", "updated_at"])
        return response


class PasswordResetCompleteView(BreadcrumbMixin, auth_views.PasswordResetCompleteView):
    """Сообщение об успешной смене пароля."""

    template_name = "accounts/password_reset_complete.html"

    def get_crumbs(self) -> tuple[Crumb, ...]:
        """Путь к странице."""
        return (Crumb(title=_("Пароль изменён")),)
