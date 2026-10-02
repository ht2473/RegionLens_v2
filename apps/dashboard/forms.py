"""Формы панели управления: каждая правит только поля своей задачи."""

from __future__ import annotations

from typing import Any

from django import forms
from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from django.utils.translation import gettext_lazy as _

from apps.accounts.constants import ASSIGNABLE_ROLES, ROLE_ADMIN, ROLES, ROLES_BY_NAME
from apps.accounts.models import User
from apps.accounts.roles import is_last_administrator
from apps.catalog.models import DatasetVersion


class UserRolesForm(forms.Form):
    """Роли учётной записи флажками; «Пользователь» есть у всех и не снимается."""

    roles = forms.MultipleChoiceField(
        label=_("Роли"),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text=_("Роль определяет доступные разделы панели управления"),
    )

    def __init__(self, *args: Any, account: User, **kwargs: Any) -> None:
        """Варианты — назначаемые роли; отмечены нынешние."""
        super().__init__(*args, **kwargs)
        self.account = account
        field: Any = self.fields["roles"]
        field.choices = [(role.name, role.label) for role in ASSIGNABLE_ROLES]
        field.initial = [name for name in account.role_names if name in ROLES_BY_NAME]

    def clean_roles(self) -> list[str]:
        """Не позволить снять роль «Администратор» у последнего администратора."""
        names = list(self.cleaned_data["roles"])
        if (
            ROLE_ADMIN not in names
            and not self.account.is_superuser
            and is_last_administrator(self.account)
        ):
            raise forms.ValidationError(
                _(
                    "Это последняя учётная запись администратора. "
                    "Сначала назначьте администратором кого-то ещё."
                )
            )
        return names

    @property
    def role_descriptions(self) -> list[dict[str, Any]]:
        """Описания ролей для подсказки рядом с флажками."""
        return [
            {"code": role.name, "title": role.label, "description": role.description}
            for role in ROLES
        ]


class UserProfileForm(forms.ModelForm):
    """Правка сведений об учётной записи, не влияющих на доступ."""

    class Meta:
        model = User
        fields = ["full_name", "email"]
        widgets = {
            "full_name": forms.TextInput(attrs={"class": "input"}),
            "email": forms.EmailInput(attrs={"class": "input"}),
        }

    def clean_email(self) -> str:
        """Проверить, что адрес не занят другой учётной записью."""
        email = self.cleaned_data["email"].strip().lower()
        taken = User.objects.filter(email__iexact=email).exclude(pk=self.instance.pk).exists()
        if taken:
            raise forms.ValidationError(_("Этот адрес уже используется другой учётной записью"))
        return email


class DatasetVersionForm(forms.ModelForm):
    """Правка сведений о версии набора данных."""

    class Meta:
        model = DatasetVersion
        fields = ["title", "publisher", "processor", "source_url", "licence", "is_current"]
        widgets = {
            "title": forms.TextInput(attrs={"class": "input"}),
            "publisher": forms.TextInput(attrs={"class": "input"}),
            "processor": forms.TextInput(attrs={"class": "input"}),
            "source_url": forms.URLInput(attrs={"class": "input"}),
            "licence": forms.TextInput(attrs={"class": "input"}),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Пояснить последствия отметки текущей версии."""
        super().__init__(*args, **kwargs)
        self.fields["is_current"].help_text = _(
            "Текущая версия используется в ссылке для цитирования и на странице о данных. "
            "Отметка снимается с прежней версии автоматически."
        )


class DatasetUploadForm(forms.Form):
    """
    Загрузка нового выпуска набора: форма проверяет расширение и размер файла.

    Состав атрибутов проверяет представление, объёмы и справочник — сборка.
    """

    dataset = forms.FileField(
        label=_("Файл набора (.parquet)"),
        help_text=_(
            "Выгрузка «Социально-экономические показатели регионов России» "
            "с сайта «Если быть точным» в формате parquet"
        ),
        widget=forms.ClearableFileInput(attrs={"class": "input", "accept": ".parquet"}),
    )

    def clean_dataset(self) -> UploadedFile:
        """Проверить расширение и размер файла."""
        upload: UploadedFile = self.cleaned_data["dataset"]
        if not (upload.name or "").lower().endswith(".parquet"):
            raise forms.ValidationError(_("Нужен файл в формате parquet"))
        limit = settings.DATASET_UPLOAD_MAX_BYTES
        if upload.size and upload.size > limit:
            raise forms.ValidationError(
                _("Файл больше %(limit)s МБ") % {"limit": limit // 1024 // 1024}
            )
        return upload
