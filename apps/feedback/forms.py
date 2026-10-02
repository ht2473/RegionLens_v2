"""Формы обращения (с полем-ловушкой) и ответа на него."""

from __future__ import annotations

from typing import Any

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.core.documents import consent_label

from .constants import TicketTopic

# Наименьшая длина текста обращения.
MIN_BODY_LENGTH = 20


class FeedbackForm(forms.Form):
    """Форма обращения, доступная в том числе без входа в систему."""

    contact_name = forms.CharField(
        label=_("Как к вам обращаться"),
        max_length=180,
        widget=forms.TextInput(attrs={"class": "input", "autocomplete": "name"}),
    )
    contact_email = forms.EmailField(
        label=_("Адрес для ответа"),
        max_length=254,
        widget=forms.EmailInput(attrs={"class": "input", "autocomplete": "email"}),
        help_text=_("Ответ придёт на этот адрес; другим он не показывается"),
    )
    topic = forms.ChoiceField(
        label=_("Тема"),
        choices=TicketTopic.choices,
        initial=TicketTopic.OTHER,
        widget=forms.Select(attrs={"class": "select"}),
    )
    subject = forms.CharField(
        label=_("Заголовок"),
        max_length=200,
        widget=forms.TextInput(attrs={"class": "input"}),
        help_text=_("Одна строка, по которой понятно существо вопроса"),
    )
    body = forms.CharField(
        label=_("Сообщение"),
        widget=forms.Textarea(attrs={"class": "textarea", "rows": 8}),
        help_text=_(
            "Если речь об ошибке в данных, укажите показатель, регион и год — "
            "это избавит от уточняющего вопроса"
        ),
    )
    # Согласие — без отметки по умолчанию; подпись со ссылкой на текст ставит __init__.
    consent = forms.BooleanField(
        label=_("Даю согласие на обработку персональных данных"),
        required=True,
    )

    # Поле-ловушка: человек его не видит, программа-отправитель заполняет.
    website = forms.CharField(
        label=_("Не заполняйте это поле"),
        required=False,
        widget=forms.TextInput(attrs={"tabindex": "-1", "autocomplete": "off"}),
    )

    def __init__(self, *args: Any, user: Any = None, **kwargs: Any) -> None:
        """Подставить имя и адрес вошедшего пользователя и закрыть эти поля."""
        super().__init__(*args, **kwargs)
        self.fields["consent"].label = consent_label()
        if user is not None and user.is_authenticated:
            self.fields["contact_name"].initial = user.full_name
            self.fields["contact_email"].initial = user.email
            self.fields["contact_name"].disabled = True
            self.fields["contact_email"].disabled = True
            self.fields["contact_name"].help_text = _("Берётся из вашей учётной записи")

    def clean_body(self) -> str:
        """Отклонить сообщения, по которым невозможно понять существо вопроса."""
        body = self.cleaned_data["body"].strip()
        if len(body) < MIN_BODY_LENGTH:
            raise forms.ValidationError(
                _("Опишите вопрос подробнее — так ответ будет по существу с первого раза")
            )
        return body

    def clean_website(self) -> str:
        """Проверить поле-ловушку; сообщение об ошибке намеренно неинформативно."""
        value = self.cleaned_data.get("website", "")
        if value:
            raise forms.ValidationError(_("Обращение не принято"))
        return value


class TicketAnswerForm(forms.Form):
    """Ответ администратора на обращение. Уходит письмом на адрес отправителя."""

    answer = forms.CharField(
        label=_("Ответ"),
        widget=forms.Textarea(attrs={"class": "textarea", "rows": 10}),
        help_text=_("Письмо начнётся с обращения по имени и закончится текстом самого обращения"),
    )

    def clean_answer(self) -> str:
        """Отклонить пустой ответ."""
        answer = self.cleaned_data["answer"].strip()
        if not answer:
            raise forms.ValidationError(_("Текст ответа не может быть пустым"))
        return answer
