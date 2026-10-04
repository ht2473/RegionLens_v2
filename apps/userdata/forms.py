"""Формы мастера своих данных: файл, вставка из буфера, выбор таблицы."""

from __future__ import annotations

from typing import Any

from django import forms
from django.conf import settings
from django.utils.translation import gettext_lazy as _

from . import ingest

ACCEPTED_SUFFIXES = ".csv,.tsv,.txt,.xlsx,.xlsm,.xls,.ods,.parquet,.zip"


class UploadForm(forms.Form):
    """Файл таблицы или архив ZIP."""

    file = forms.FileField(
        label=_("Файл таблицы"),
        help_text=_("CSV, TXT, XLSX, XLS, ODS, Parquet или архив ZIP с ними, до %(limit)s МБ")
        % {"limit": settings.USERDATA_UPLOAD_MAX_BYTES // (1024 * 1024)},
        widget=forms.FileInput(attrs={"accept": ACCEPTED_SUFFIXES, "class": "file-input"}),
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["file"].widget.attrs["data-max-bytes"] = settings.USERDATA_UPLOAD_MAX_BYTES


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

    table = forms.CharField(widget=forms.HiddenInput, required=False)
    encoding = forms.ChoiceField(
        label=_("Кодировка текста"),
        required=False,
        choices=[("", _("определить по тексту")), *ingest.ENCODINGS.items()],
        widget=forms.Select(attrs={"class": "select"}),
    )
