"""
Раздел «Пользователи и роли».

Учётные записи не удаляются, а блокируются: на них ссылаются виды, отметки и обращения.
"""

from __future__ import annotations

from typing import Any

from django.contrib import messages
from django.db.models import QuerySet
from django.forms import ModelForm
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import DetailView, ListView, UpdateView, View

from apps.accounts.constants import ASSIGNABLE_ROLES
from apps.accounts.models import User
from apps.accounts.roles import LastAdministratorError, is_last_administrator, set_roles
from apps.accounts.security import disable_two_factor
from apps.core.navigation import Crumb

from .. import selectors
from ..forms import UserProfileForm, UserRolesForm
from ..navigation import AdminViewMixin


class UserListView(AdminViewMixin, ListView):
    """Перечень учётных записей с поиском и фильтрами."""

    template_name = "dashboard/user_list.html"
    context_object_name = "users"
    section_code = "users"
    paginate_by = 30

    def get_queryset(self) -> QuerySet[User]:
        """Учётные записи с учётом строки поиска и фильтров."""
        return selectors.users(
            query=self.request.GET.get("q", "").strip(),
            role=self.request.GET.get("role", "").strip(),
            state=self.request.GET.get("state", "").strip(),
        )

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Дополнить контекст фильтрами и распределением по ролям."""
        context = super().get_context_data(**kwargs)
        context["roles"] = [(role.name, role.label) for role in ASSIGNABLE_ROLES]
        context["role_distribution"] = selectors.role_distribution()
        context["query"] = self.request.GET.get("q", "")
        context["selected_role"] = self.request.GET.get("role", "")
        context["selected_state"] = self.request.GET.get("state", "")
        return context


class UserDetailView(AdminViewMixin, DetailView):
    """Карточка учётной записи со сводкой созданного пользователем и формами правки."""

    template_name = "dashboard/user_detail.html"
    context_object_name = "account"
    section_code = "users"
    slug_field = "public_id"
    slug_url_kwarg = "public_id"

    def get_queryset(self) -> QuerySet[User]:
        """Все учётные записи."""
        return User.objects.all()

    def get_crumbs(self) -> tuple[Crumb, ...]:
        """Путь к странице: панель, перечень пользователей, текущая запись."""
        return (
            Crumb(title=_("Панель управления"), url=reverse("dashboard:index")),
            Crumb(title=_("Пользователи и роли"), url=reverse("dashboard:user-list")),
            Crumb(title=self.object.get_short_name()),
        )

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Дополнить контекст сводкой по созданному и формами правки."""
        context = super().get_context_data(**kwargs)
        account = self.object
        context["page_title"] = account.full_name
        context["card"] = selectors.user_card(account)
        context["role_form"] = UserRolesForm(account=account)
        context["profile_form"] = UserProfileForm(instance=account)
        # Себя заблокировать нельзя.
        context["is_self"] = account.pk == self.current_user.pk
        return context


class UserRoleUpdateView(AdminViewMixin, View):
    """Роли учётной записи: флажки назначаемых ролей."""

    section_code = "users"

    def post(self, request: HttpRequest, public_id: str) -> HttpResponse:
        """Назначить отмеченные роли; причину отказа показать сообщением."""
        account = get_object_or_404(User, public_id=public_id)
        target = reverse("dashboard:user-detail", kwargs={"public_id": account.public_id})
        form = UserRolesForm(request.POST, account=account)
        if not form.is_valid():
            for error in form.errors.get("roles", []):
                messages.error(request, str(error))
            return redirect(target)
        try:
            set_roles(account, form.cleaned_data["roles"])
        except LastAdministratorError:
            messages.error(request, _("Это последняя учётная запись администратора"))
            return redirect(target)
        labels = ", ".join(str(label) for label in account.role_labels)
        messages.success(request, _("Роли: %(roles)s") % {"roles": labels})
        return redirect(target)


class UserProfileUpdateView(AdminViewMixin, UpdateView):
    """Правка сведений об учётной записи."""

    model = User
    form_class = UserProfileForm
    section_code = "users"
    template_name = "dashboard/user_detail.html"
    slug_field = "public_id"
    slug_url_kwarg = "public_id"

    def form_valid(self, form: ModelForm[Any]) -> HttpResponse:
        """Сохранить сведения и сообщить об этом."""
        response = super().form_valid(form)
        messages.success(self.request, _("Сведения об учётной записи сохранены"))
        return response

    def form_invalid(self, form: ModelForm[Any]) -> HttpResponse:
        """Показать ошибки формы и вернуться к карточке."""
        for field_errors in form.errors.values():
            for error in field_errors:
                messages.error(self.request, str(error))
        return redirect(self.get_success_url())

    def get_success_url(self) -> str:
        """Вернуться к карточке учётной записи."""
        return reverse("dashboard:user-detail", kwargs={"public_id": self.object.public_id})


class UserToggleActiveView(AdminViewMixin, View):
    """Блокировка и разблокировка учётной записи, только методом POST."""

    section_code = "users"

    def post(self, request: HttpRequest, public_id: str) -> HttpResponse:
        """Переключить признак активности учётной записи."""
        account = get_object_or_404(User, public_id=public_id)
        target = reverse("dashboard:user-detail", kwargs={"public_id": account.public_id})

        if account.pk == request.user.pk:
            messages.error(request, _("Нельзя заблокировать собственную учётную запись"))
            return redirect(target)

        if account.is_active and is_last_administrator(account):
            messages.error(request, _("Это последняя действующая учётная запись администратора"))
            return redirect(target)

        account.is_active = not account.is_active
        account.save(update_fields=["is_active", "updated_at"])

        if account.is_active:
            messages.success(request, _("Учётная запись разблокирована"))
        else:
            messages.warning(request, _("Учётная запись заблокирована"))
        return redirect(target)


class UserTwoFactorResetView(AdminViewMixin, View):
    """Сброс входа с кодом, когда пользователь потерял телефон и резервные коды."""

    section_code = "users"

    def post(self, request: HttpRequest, public_id: str) -> HttpResponse:
        """Выключить вход с кодом и завершить сеансы учётной записи."""
        account = get_object_or_404(User, public_id=public_id)
        target = reverse("dashboard:user-detail", kwargs={"public_id": account.public_id})
        if account.pk == request.user.pk:
            messages.error(
                request, _("Свой вход с кодом меняется в кабинете, в разделе «Безопасность»")
            )
            return redirect(target)
        if account.two_factor_enabled:
            disable_two_factor(None, account)
            messages.warning(
                request,
                _(
                    "Вход с кодом сброшен, сеансы учётной записи завершены. Настроить его заново "
                    "пользователь сможет после входа по паролю."
                ),
            )
        return redirect(target)
