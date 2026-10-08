"""Формы правки содержимого в панели управления: одна форма — одна языковая версия."""

from __future__ import annotations

from typing import Any

from django import forms
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _
from parler.forms import TranslatableModelForm
from slugify import slugify as translit_slugify

from apps.core.forms import field_of

from .constants import GlossaryCategory, MethodologyBlock
from .models import GlossaryTerm, MethodologySection

# Наименьшая длина слага.
MIN_SLUG_LENGTH = 3


class SlugMixin(forms.ModelForm):
    """Проверка слага записи; пустой строится транслитерацией заголовка."""

    title_field_for_slug = "title"

    def clean_slug(self) -> str:
        """Привести адрес к допустимому виду, при необходимости построив его из заголовка."""
        slug = (self.cleaned_data.get("slug") or "").strip()
        if not slug:
            source = self.cleaned_data.get(self.title_field_for_slug, "")
            slug = translit_slugify(str(source))
        slug = slugify(slug)

        if len(slug) < MIN_SLUG_LENGTH:
            raise forms.ValidationError(
                _("Адрес слишком короткий: укажите его вручную латинскими буквами")
            )
        return slug


class MethodologySectionForm(TranslatableModelForm):
    """Правка раздела методологии."""

    references_text = forms.CharField(
        label=_("Источники"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text=_("По одной библиографической ссылке в строке"),
    )

    class Meta:
        model = MethodologySection
        fields = [
            "code",
            "block",
            "display_order",
            "formula",
            "tool_url_name",
            "is_published",
            "title",
            "summary",
            "limitations",
            "body",
        ]
        widgets = {
            "summary": forms.Textarea(attrs={"rows": 2}),
            "limitations": forms.Textarea(attrs={"rows": 3}),
            "body": forms.Textarea(attrs={"rows": 8}),
            "formula": forms.Textarea(attrs={"rows": 4, "class": "input--mono"}),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Развернуть перечень источников в текст, удобный для правки."""
        super().__init__(*args, **kwargs)
        self.fields["block"].choices = MethodologyBlock.choices
        if self.instance.pk:
            self.fields["references_text"].initial = "\n".join(self.instance.references or [])

    def clean_references_text(self) -> list[str]:
        """Свернуть текст обратно в перечень, отбросив пустые строки."""
        raw = self.cleaned_data.get("references_text", "")
        return [line.strip() for line in raw.splitlines() if line.strip()]

    def save(self, commit: bool = True) -> MethodologySection:
        """Сохранить раздел вместе с перечнем источников."""
        section = super().save(commit=False)
        section.references = self.cleaned_data.get("references_text", [])
        if commit:
            section.save()
        return section


class GlossaryTermForm(SlugMixin, TranslatableModelForm):
    """Правка термина глоссария."""

    title_field_for_slug = "term"

    class Meta:
        model = GlossaryTerm
        fields = [
            "slug",
            "category",
            "is_published",
            "indicator",
            "methodology_section",
            "related_terms",
            "term",
            "short_definition",
            "definition",
            "synonyms",
        ]
        widgets = {
            "short_definition": forms.Textarea(attrs={"rows": 2}),
            "definition": forms.Textarea(attrs={"rows": 10}),
            "related_terms": forms.SelectMultiple(attrs={"size": 6}),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Настроить необязательные поля и перечень показателей."""
        super().__init__(*args, **kwargs)
        self.fields["slug"].required = False
        field_of(self, "category", forms.ChoiceField).choices = GlossaryCategory.choices
        # Подпись Django по умолчанию («- Select an option -») без русского перевода.
        for name in ("indicator", "methodology_section"):
            field_of(self, name, forms.ModelChoiceField).empty_label = _("не выбран")
        if self.instance.pk:
            # Термин не связывается сам с собой.
            field_of(
                self, "related_terms", forms.ModelMultipleChoiceField
            ).queryset = GlossaryTerm.objects.exclude(pk=self.instance.pk)
