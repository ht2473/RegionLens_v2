"""Формы мастера своих данных: файл, вставка из буфера, выбор таблицы."""

from __future__ import annotations

import re
from typing import Any

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from . import ingest
from .models import Dataset

ACCEPTED_SUFFIXES = ".csv,.tsv,.txt,.xlsx,.xlsm,.xls,.ods,.parquet,.docx,.pdf,.zip"


class UploadForm(forms.Form):
    """Файл таблицы или архив ZIP."""

    file = forms.FileField(
        label=_("Файл таблицы"),
        widget=forms.FileInput(attrs={"accept": ACCEPTED_SUFFIXES, "class": "file-drop__input"}),
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["file"].widget.attrs["data-max-bytes"] = settings.USERDATA_UPLOAD_MAX_BYTES
        # Подсказка — на языке запроса: при объявлении поля язык ещё не выбран.
        self.fields["file"].help_text = gettext(
            "CSV, TXT, XLSX, XLS, ODS, Parquet, DOCX, PDF с текстом или архив ZIP, до %(limit)s МБ"
        ) % {"limit": settings.USERDATA_UPLOAD_MAX_BYTES // (1024 * 1024)}
        # Подсказка — в зоне файла; поле связано с ней (обращение к полю — после подсказки:
        # связанное поле запоминает её при создании).
        self.fields["file"].widget.attrs["aria-describedby"] = f"{self['file'].auto_id}-hint"


class PasteForm(forms.Form):
    """Таблица, скопированная из Excel, Word или со страницы сайта."""

    # Без обрезки краёв: первая клетка шапки бывает пустой, и вставка начинается с табуляции.
    text = forms.CharField(
        strip=False,
        label=_("Вставить таблицу"),
        help_text=_(
            "Скопируйте таблицу целиком вместе с шапкой. Из PDF столбцы при вставке "
            "теряются — такую таблицу лучше взять в виде файла."
        ),
        widget=forms.Textarea(
            attrs={"class": "textarea textarea--table", "rows": 8, "spellcheck": "false"}
        ),
    )
    # Разметка таблицы из буфера: в ней видны объединённые клетки шапки.
    markup = forms.CharField(required=False, strip=False, widget=forms.HiddenInput)

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        size = len((cleaned.get("text") or "").encode()) + len(
            (cleaned.get("markup") or "").encode()
        )
        if size > settings.USERDATA_PASTE_MAX_BYTES:
            raise forms.ValidationError(
                _("Вставка больше %(limit)s МБ. Загрузите таблицу файлом.")
                % {"limit": settings.USERDATA_PASTE_MAX_BYTES // (1024 * 1024)}
            )
        return cleaned


class ChooseTableForm(forms.Form):
    """Таблица файла и кодировка текста."""

    # Без обрезки краёв: имя листа бывает с пробелом на конце («всего »).
    table = forms.CharField(widget=forms.HiddenInput, required=False, strip=False)
    encoding = forms.ChoiceField(
        label=_("Кодировка текста"),
        required=False,
        choices=[("", _("определить по тексту")), *ingest.ENCODINGS.items()],
        widget=forms.Select(attrs={"class": "select"}),
    )


# Адрес с протоколом и адрес без него: «rosstat.gov.ru/folder» — тоже адрес.
_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)
_HOST = re.compile(r"^[\w-]+(\.[\w-]+)+(:\d+)?(/\S*)?$")


class DatasetMetaForm(forms.ModelForm):
    """
    Название таблицы, её источник и описание — для страницы таблицы и документов.

    Формальности сборку не останавливают: пустое название остаётся прежним, адрес без
    https:// дополняется, а текст вместо адреса («Росстат, бюллетень») уходит в источник.
    """

    source_url = forms.CharField(
        label=_("Адрес источника"),
        required=False,
        max_length=500,
        help_text=_("Страница, откуда взята таблица, можно без https://. Необязательно."),
        widget=forms.TextInput(attrs={"class": "input", "inputmode": "url"}),
    )

    class Meta:
        model = Dataset
        fields = ("title", "source_title", "source_url", "description")
        labels = {
            "title": _("Название таблицы"),
            "source_title": _("Источник данных"),
            "description": _("Описание"),
        }
        help_texts = {
            "source_title": _(
                "Кто опубликовал данные: «Росстат», «МВД России», своё исследование."
            ),
        }
        widgets = {
            "title": forms.TextInput(attrs={"class": "input"}),
            "source_title": forms.TextInput(attrs={"class": "input"}),
            "description": forms.Textarea(attrs={"class": "textarea", "rows": 3}),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["title"].required = False
        # Текст из поля адреса, который адресом не оказался.
        self.source_note = ""

    def clean_title(self) -> str:
        title = (self.cleaned_data.get("title") or "").strip()
        return title or self.instance.title or gettext("Своя таблица")

    def clean_source_url(self) -> str:
        text = (self.cleaned_data.get("source_url") or "").strip()
        if not text:
            return ""
        candidate = ""
        if _SCHEME.match(text):
            candidate = text
        elif _HOST.match(text):
            candidate = f"https://{text}"
        if candidate:
            try:
                URLValidator(schemes=["http", "https"])(candidate)
            except ValidationError:
                pass
            else:
                return candidate
        self.source_note = text
        return ""

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        if self.source_note:
            source = (cleaned.get("source_title") or "").strip()
            joined = f"{source}, {self.source_note}" if source else self.source_note
            cleaned["source_title"] = joined[: Dataset._meta.get_field("source_title").max_length]
        return cleaned
