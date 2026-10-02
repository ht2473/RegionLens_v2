"""Формы учётных записей на основе стандартных форм Django, с классами оформления."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, override

from django import forms
from django.contrib.auth.forms import (
    AuthenticationForm,
    PasswordChangeForm,
    PasswordResetForm,
    SetPasswordForm,
)
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _

from apps.core import throttle
from apps.core.documents import consent_label

from .constants import PASSWORD_CHECK_PER_USER
from .models import User


class StyledFormMixin:
    """Оформление полей классами по виду виджета: ``input``, ``select``, ``textarea``."""

    def apply_styles(self) -> None:
        """Проставить классы оформления всем полям формы."""
        for field in self.fields.values():  # type: ignore[attr-defined]
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput | forms.RadioSelect):
                continue
            if isinstance(widget, forms.Select):
                widget.attrs.setdefault("class", "select")
            elif isinstance(widget, forms.Textarea):
                widget.attrs.setdefault("class", "textarea")
            else:
                widget.attrs.setdefault("class", "input")


def honeypot_field() -> forms.CharField:
    """Поле-ловушка: человек его не видит, программа-отправитель заполняет."""
    return forms.CharField(
        label=_("Не заполняйте это поле"),
        required=False,
        widget=forms.TextInput(attrs={"tabindex": "-1", "autocomplete": "off"}),
    )


class HoneypotMixin:
    """Проверка поля-ловушки ``website``; само поле объявляет форма."""

    def clean_website(self) -> str:
        """Заполненная ловушка — отказ; сообщение намеренно неинформативно."""
        value = self.cleaned_data.get("website", "")  # type: ignore[attr-defined]
        if value:
            raise ValidationError(_("Форма не принята"))
        return value


def password_field(label: Any, autocomplete: str = "current-password") -> forms.CharField:
    """Поле пароля."""
    return forms.CharField(
        label=label,
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": autocomplete}),
    )


def code_field() -> forms.CharField:
    """Поле одноразового или резервного кода."""
    return forms.CharField(
        label=_("Код из приложения"),
        max_length=16,
        help_text=_("Шесть цифр из приложения-генератора или один из резервных кодов"),
        widget=forms.TextInput(
            attrs={
                "autocomplete": "one-time-code",
                "inputmode": "numeric",
                "autocapitalize": "off",
                "spellcheck": "false",
            }
        ),
    )


class RegistrationForm(HoneypotMixin, StyledFormMixin, forms.ModelForm):
    """Регистрация новой учётной записи; роль назначает администратор."""

    password1 = password_field(_("Пароль"), "new-password")
    password2 = password_field(_("Пароль ещё раз"), "new-password")
    accepted_terms = forms.BooleanField(label=_("Принимаю условия использования"), required=True)
    # Согласие — отдельным флажком от условий (ч. 1 ст. 9 152-ФЗ), без отметки по умолчанию.
    accepted_consent = forms.BooleanField(
        label=_("Даю согласие на обработку персональных данных"), required=True
    )
    website = honeypot_field()

    class Meta:
        model = User
        fields = ["email", "full_name"]
        widgets = {
            "email": forms.EmailInput(attrs={"autocomplete": "email"}),
            "full_name": forms.TextInput(attrs={"autocomplete": "name"}),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Оформить поля и уточнить подсказки."""
        super().__init__(*args, **kwargs)
        self.fields["password1"].help_text = _(
            "Не короче десяти знаков; пароль не должен совпадать с адресом почты"
        )
        # Ссылка на условия — в новой вкладке: переход не должен терять заполненную форму.
        terms = format_html(
            '<a href="{}" target="_blank" rel="noopener">{}</a>',
            reverse("core:terms"),
            _("условия использования"),
        )
        self.fields["accepted_terms"].label = format_html(str(_("Принимаю {terms}")), terms=terms)
        self.fields["accepted_consent"].label = consent_label()
        self.apply_styles()

    def clean_email(self) -> str:
        """Привести адрес к нижнему регистру и проверить, что он свободен."""
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError(_("Учётная запись с таким адресом уже зарегистрирована"))
        return email

    def clean_password2(self) -> str:
        """Проверить совпадение и стойкость пароля."""
        password1 = self.cleaned_data.get("password1", "")
        password2 = self.cleaned_data.get("password2", "")
        if password1 != password2:
            raise ValidationError(_("Пароли не совпадают"))

        candidate = User(
            email=self.cleaned_data.get("email", ""),
            full_name=self.cleaned_data.get("full_name", ""),
        )
        validate_password(password2, candidate)
        return password2


class EmailAuthenticationForm(StyledFormMixin, AuthenticationForm):
    """Форма входа по адресу электронной почты."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Заменить подпись поля и оформить его."""
        super().__init__(*args, **kwargs)
        self.fields["username"].label = _("Адрес электронной почты")
        self.fields["username"].widget = forms.EmailInput(
            attrs={"autocomplete": "email", "autofocus": True}
        )
        self.fields["password"].widget.attrs["autocomplete"] = "current-password"
        self.apply_styles()

    error_messages = {
        **AuthenticationForm.error_messages,
        # Одно сообщение для неверного пароля и неизвестного адреса.
        "invalid_login": _("Неверный адрес электронной почты или пароль"),
        "inactive": _("Учётная запись отключена"),
    }


class CodeForm(StyledFormMixin, forms.Form):
    """Одноразовый код второго шага входа."""

    code = code_field()

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Оформить поле и поставить в него курсор."""
        super().__init__(*args, **kwargs)
        self.fields["code"].widget.attrs["autofocus"] = True
        self.apply_styles()


class ProfileForm(StyledFormMixin, forms.ModelForm):
    """Правка сведений о себе и выбор своего региона."""

    class Meta:
        model = User
        fields = ["full_name", "region"]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Оформить поля; регион — только субъекты, по названию на языке страницы."""
        from apps.catalog.models import Territory

        super().__init__(*args, **kwargs)
        region: Any = self.fields["region"]
        order = "name_en" if get_language() == "en" else "name_ru"
        region.queryset = Territory.objects.comparable().order_by(order)
        region.label_from_instance = lambda territory: territory.name
        region.empty_label = _("не выбран")
        self.apply_styles()


def guard_password_check(user: User, check: Callable[[], object]) -> None:
    """
    Выполнить проверку текущего пароля ``check`` с пределом неверных попыток.

    Неверный пароль учитывается; после ``PASSWORD_CHECK_PER_USER`` неверных за час
    отказ получает и верный.
    """
    rate = PASSWORD_CHECK_PER_USER
    key = str(user.pk)
    if throttle.used("password-check", key) >= rate.limit:
        raise ValidationError(_("Слишком много неверных паролей. Попробуйте через час."))
    try:
        check()
    except ValidationError:
        throttle.allow_key("password-check", key, limit=rate.limit, window=rate.window)
        raise


class StyledPasswordChangeForm(StyledFormMixin, PasswordChangeForm):
    """Смена пароля из личного кабинета."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Оформить поля."""
        super().__init__(*args, **kwargs)
        self.apply_styles()

    def clean_old_password(self) -> str:
        """Текущий пароль — с общим пределом неверных попыток."""
        guard_password_check(self.user, super().clean_old_password)
        return str(self.cleaned_data["old_password"])


class PasswordCheckMixin:
    """Проверка текущего пароля пользователя — перед действием, которое трудно отменить."""

    user: User

    def clean_password(self) -> str:
        """Пароль должен подходить к учётной записи."""
        password = self.cleaned_data.get("password", "")  # type: ignore[attr-defined]

        def check() -> None:
            if not self.user.check_password(password):
                raise ValidationError(_("Пароль не подходит"))

        guard_password_check(self.user, check)
        return password


class EmailChangeForm(PasswordCheckMixin, StyledFormMixin, forms.Form):
    """Новый адрес почты; вступает в силу после перехода по ссылке из письма."""

    new_email = forms.EmailField(
        label=_("Новый адрес"),
        max_length=254,
        widget=forms.EmailInput(attrs={"autocomplete": "email"}),
    )
    password = password_field(_("Текущий пароль"))

    def __init__(self, *args: Any, user: User, **kwargs: Any) -> None:
        """Запомнить пользователя для проверки пароля и адреса."""
        self.user = user
        super().__init__(*args, **kwargs)
        self.apply_styles()

    def clean_new_email(self) -> str:
        """Адрес отличается от нынешнего и не занят."""
        email = self.cleaned_data["new_email"].strip().lower()
        if email == self.user.email.lower():
            raise ValidationError(_("Это нынешний адрес учётной записи"))
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError(_("Этот адрес уже занят другой учётной записью"))
        return email


class PasswordConfirmForm(PasswordCheckMixin, StyledFormMixin, forms.Form):
    """Подтверждение действия текущим паролем."""

    password = password_field(_("Текущий пароль"))

    def __init__(self, *args: Any, user: User, **kwargs: Any) -> None:
        """Запомнить пользователя для проверки пароля."""
        self.user = user
        super().__init__(*args, **kwargs)
        self.apply_styles()


class DeleteAccountForm(PasswordConfirmForm):
    """Удаление учётной записи: пароль и явное согласие."""

    confirm = forms.BooleanField(
        label=_("Я понимаю, что сохранённое будет удалено без возможности вернуть"),
        required=True,
    )


class TwoFactorEnableForm(StyledFormMixin, forms.Form):
    """Первый код из приложения — подтверждение, что ключ добавлен верно."""

    code = code_field()

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Подпись без упоминания резервных кодов: их ещё нет."""
        super().__init__(*args, **kwargs)
        self.fields["code"].help_text = _("Шесть цифр, которые показывает приложение")
        self.apply_styles()


class TwoFactorDisableForm(PasswordCheckMixin, StyledFormMixin, forms.Form):
    """Выключение входа с кодом: пароль и действующий код."""

    password = password_field(_("Текущий пароль"))
    code = code_field()

    def __init__(self, *args: Any, user: User, **kwargs: Any) -> None:
        """Запомнить пользователя для проверки пароля."""
        self.user = user
        super().__init__(*args, **kwargs)
        self.apply_styles()


class StyledPasswordResetForm(StyledFormMixin, PasswordResetForm):
    """Запрос ссылки восстановления пароля."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Оформить поле адреса."""
        super().__init__(*args, **kwargs)
        self.fields["email"].label = _("Адрес электронной почты")
        self.apply_styles()

    @override
    def send_mail(
        self,
        subject_template_name: str,
        email_template_name: str,
        context: dict[str, Any],
        from_email: str | None,
        to_email: str,
        html_email_template_name: str | None = None,
    ) -> None:
        """Отправить письмо со ссылкой; отказ почтового узла отмечается, а не роняет страницу."""
        from apps.core.mail import deliver

        subject = "".join(render_to_string(subject_template_name, context).splitlines())
        body = render_to_string(email_template_name, context)
        deliver(subject, body, [to_email])


class StyledSetPasswordForm(StyledFormMixin, SetPasswordForm):
    """Назначение нового пароля по ссылке из письма."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Оформить поля."""
        super().__init__(*args, **kwargs)
        self.apply_styles()
