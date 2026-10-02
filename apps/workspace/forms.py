"""Формы личного кабинета: ряд и территория выбираются строковым ключом."""

from __future__ import annotations

from typing import Any

from django import forms
from django.utils.translation import gettext as _

from .constants import QUERY_TARGETS_BY_CODE
from .models import SavedQuery


class SavedQueryForm(forms.ModelForm):
    """Сохранение и переименование выборки."""

    class Meta:
        model = SavedQuery
        fields = ["title", "description"]
        widgets = {
            "title": forms.TextInput(
                attrs={
                    "class": "input",
                    "placeholder": "Например: неравенство доходов по округам",
                    "maxlength": 160,
                }
            ),
            "description": forms.Textarea(attrs={"class": "textarea", "rows": 3}),
        }

    def __init__(self, *args: Any, user: Any = None, **kwargs: Any) -> None:
        """Запомнить владельца: проверка уникальности названия выполняется в его пределах."""
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_title(self) -> str:
        """Проверить, что название выборки у пользователя свободно, — до ограничения базы."""
        title = self.cleaned_data["title"].strip()
        if self.user is None:
            return title

        duplicates = SavedQuery.objects.filter(user=self.user, title=title)
        if self.instance.pk:
            duplicates = duplicates.exclude(pk=self.instance.pk)
        if duplicates.exists():
            raise forms.ValidationError(_("Вид с таким названием уже сохранён"))
        return title


class SavedQueryCreateForm(SavedQueryForm):
    """Сохранение выборки со страницы расчёта: название, код страницы и строка запроса."""

    target = forms.CharField(widget=forms.HiddenInput())
    query_string = forms.CharField(widget=forms.HiddenInput(), required=False)

    def clean_target(self) -> str:
        """Проверить, что страница входит в перечень сохраняемых."""
        target = self.cleaned_data["target"]
        if target not in QUERY_TARGETS_BY_CODE:
            raise forms.ValidationError("Состояние этой страницы сохранить нельзя")
        return target


class FavoriteForm(forms.Form):
    """Отметка объекта избранным со страницы самого объекта."""

    kind = forms.ChoiceField(
        choices=[("indicator", "показатель"), ("series", "ряд"), ("territory", "территория")]
    )
    identifier = forms.CharField(max_length=200)
    note = forms.CharField(max_length=200, required=False)


class FavoriteNoteForm(forms.Form):
    """Пометка к отметке из «Сохранённого»."""

    note = forms.CharField(max_length=200, required=False)
