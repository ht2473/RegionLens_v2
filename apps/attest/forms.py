"""Форма проверки документа: файл и, если есть, файл подписи."""

from __future__ import annotations

from typing import Any

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.utils.translation import gettext_lazy as _

# Подпись — около килобайта; больше — заведомо не она.
SIGNATURE_MAX_BYTES = 64 * 1024


class VerifyForm(forms.Form):
    """Документ для проверки; подпись нужна, только если документа нет в реестре сайта."""

    document = forms.FileField(
        label=_("Документ"),
        help_text=_("Файл CSV, XLSX или PDF, скачанный с сайта. Сайт его не сохраняет."),
    )
    signature = forms.FileField(
        label=_("Файл подписи (.p7s)"),
        required=False,
        help_text=_("Необязательно: документ, выданный сайтом, находится и без подписи."),
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Оформить поля."""
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "input")
        self.fields["signature"].widget.attrs["accept"] = ".p7s,application/pkcs7-signature"

    def clean_document(self) -> UploadedFile:
        """Документ не больше предела: выгрузки сайта заведомо меньше."""
        document: UploadedFile = self.cleaned_data["document"]
        if (document.size or 0) > settings.ATTEST_MAX_BYTES:
            raise ValidationError(
                _("Файл больше %(limit)s МБ: таких документов сайт не выдаёт.")
                % {"limit": settings.ATTEST_MAX_BYTES // (1024 * 1024)}
            )
        return document

    def clean_signature(self) -> UploadedFile | None:
        """Файл подписи — небольшой."""
        signature: UploadedFile | None = self.cleaned_data.get("signature")
        if signature is not None and (signature.size or 0) > SIGNATURE_MAX_BYTES:
            raise ValidationError(_("Это не файл подписи: он слишком большой."))
        return signature
