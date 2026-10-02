"""
Доступ к панели управления и пределы кабинета.

Гость отправляется на вход, вошедший без права раздела получает 403. С правом, но без
входа по коду — страница «Безопасность»: панель открывается только после второго фактора.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest
from django.http.response import HttpResponseBase
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from .constants import CABINET_LIMITS
from .security import passed_second_factor


class QuotaExceededError(PermissionDenied):
    """Исчерпан предел кабинета на записи одного вида."""


def ensure_quota(name: str, current: int) -> None:
    """Проверить, что предел кабинета на записи указанного вида не исчерпан."""
    limit = CABINET_LIMITS[name]
    if current >= limit:
        raise QuotaExceededError(f"Достигнут предел: {limit}. Удалите ненужные записи.")


class PanelPermissionMixin(PermissionRequiredMixin):
    """Страница панели: право раздела (пусто — любой раздел) и пройденный второй фактор."""

    permission_required: Any = ()

    # Атрибут базового представления — для проверки типов.
    request: HttpRequest

    def has_permission(self) -> bool:
        """Без заданного права достаточно любого права панели."""
        user: Any = self.request.user
        if not user.is_authenticated:
            return False
        if not self.get_permission_required():
            return bool(user.has_panel_access)
        return super().has_permission()

    def get_permission_required(self) -> tuple[str, ...]:
        """Права страницы перечнем; пустой перечень Django считает ошибкой настройки."""
        required = self.permission_required
        return (required,) if isinstance(required, str) else tuple(required)

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        """С правом, но без второго фактора — на страницу его включения."""
        user: Any = request.user
        if self.has_permission() and not (
            user.two_factor_enabled and passed_second_factor(request)
        ):
            if user.two_factor_enabled:
                messages.warning(request, _("Подтвердите вход одноразовым кодом."))
                target = reverse("accounts:login-code")
                return redirect(f"{target}?{urlencode({'next': request.get_full_path()})}")
            messages.warning(
                request,
                _("Для панели управления нужен вход с одноразовым кодом. Включите его ниже."),
            )
            return redirect("accounts:security")
        return super().dispatch(request, *args, **kwargs)
