"""Разделы панели управления и общая часть её страниц."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from django.http import HttpRequest
from django.http.response import HttpResponseBase
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from apps.accounts.constants import MANAGE_CONTENT, MANAGE_DATA, MANAGE_TICKETS, MANAGE_USERS
from apps.accounts.models import User
from apps.accounts.permissions import PanelPermissionMixin
from apps.core.navigation import Crumb, build_breadcrumbs


@dataclass(frozen=True, slots=True)
class AdminSection:
    """Раздел панели управления; ``permission`` пусто — раздел открыт с любым правом панели."""

    code: str
    title: Any
    url_name: str
    hint: Any
    permission: str = ""

    @property
    def url(self) -> str:
        """Адрес раздела."""
        return reverse(self.url_name)


# ---------------------------------------------------------------------------------------
# Разделы панели
# ---------------------------------------------------------------------------------------

ADMIN_SECTIONS: tuple[AdminSection, ...] = (
    AdminSection(
        code="overview",
        title=_("Обзор системы"),
        url_name="dashboard:index",
        hint=_("Состояние данных, пользователей и служб одной страницей"),
    ),
    AdminSection(
        code="users",
        title=_("Пользователи и роли"),
        url_name="dashboard:user-list",
        hint=_("Учётные записи, роли, блокировка"),
        permission=MANAGE_USERS,
    ),
    AdminSection(
        code="data",
        title=_("Загрузка данных"),
        url_name="dashboard:data",
        hint=_("Версии набора, запуски сборки, склад и замечания проверок"),
        permission=MANAGE_DATA,
    ),
    AdminSection(
        code="sources",
        title=_("Источники"),
        url_name="dashboard:sources",
        hint=_("Сбор выпусков Росстата, журнал, ошибки разбора и сшивка с набором"),
        permission=MANAGE_DATA,
    ),
    AdminSection(
        code="tickets",
        title=_("Обратная связь"),
        url_name="dashboard:ticket-list",
        hint=_("Обращения пользователей и ответы на них"),
        permission=MANAGE_TICKETS,
    ),
    AdminSection(
        code="content",
        title=_("Содержимое"),
        url_name="dashboard:content",
        hint=_("Глоссарий и разделы методики"),
        permission=MANAGE_CONTENT,
    ),
    AdminSection(
        code="search",
        title=_("Поиск"),
        url_name="dashboard:search",
        hint=_("Запросы без ответа: по ним пополняется словарь синонимов поиска"),
        permission=MANAGE_CONTENT,
    ),
    AdminSection(
        code="visits",
        title=_("Посещения"),
        url_name="dashboard:visits",
        hint=_("Посетители по дням, страницы и переходы по журналу сервера"),
    ),
)

ADMIN_SECTIONS_BY_CODE: dict[str, AdminSection] = {
    section.code: section for section in ADMIN_SECTIONS
}


def visible_sections(user: User) -> list[AdminSection]:
    """Разделы панели, открытые пользователю."""
    granted = user.panel_permissions
    return [
        section
        for section in ADMIN_SECTIONS
        if (section.permission in granted if section.permission else granted)
    ]


class AdminViewMixin(PanelPermissionMixin):
    """Общая часть страниц панели: право раздела, вкладки и «хлебные крошки»."""

    section_code: str = "overview"

    # Атрибут базового представления — для проверки типов.
    request: HttpRequest

    @property
    def current_user(self) -> User:
        """Пользователь, открывший страницу панели."""
        return cast(User, self.request.user)

    @property
    def section(self) -> AdminSection:
        """Описание текущего раздела."""
        return ADMIN_SECTIONS_BY_CODE[self.section_code]

    def get_permission_required(self) -> tuple[str, ...]:
        """Право раздела страницы."""
        code = self.section.permission
        return (f"accounts.{code}",) if code else ()

    def get_crumbs(self) -> tuple[Crumb, ...]:
        """Путь к странице: панель управления и текущий раздел."""
        panel = Crumb(title=_("Панель управления"), url=reverse("dashboard:index"))
        if self.section_code == "overview":
            return (Crumb(title=_("Панель управления")),)
        return (panel, Crumb(title=self.section.title))

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Дополнить контекст навигацией панели."""
        context = super().get_context_data(**kwargs)  # type: ignore[misc]
        context["breadcrumbs"] = build_breadcrumbs(self.request, *self.get_crumbs())
        context["admin_sections"] = [
            {
                "code": section.code,
                "title": section.title,
                "url": section.url,
                "hint": section.hint,
                "active": section.code == self.section_code,
            }
            for section in visible_sections(self.current_user)
        ]
        context["admin_section"] = self.section_code
        context.setdefault("page_title", self.section.title)
        return context

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        """Отметить активность при обращении к панели."""
        response = super().dispatch(request, *args, **kwargs)
        if request.user.is_authenticated:
            request.user.touch()
        return response
