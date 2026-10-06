"""Разделы личного кабинета — общие для вкладок, меню учётной записи и «хлебных крошек»."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpRequest
from django.http.response import HttpResponseBase
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from apps.core.navigation import Crumb, build_breadcrumbs

from .constants import ROLES_BY_NAME
from .models import User


@dataclass(frozen=True, slots=True)
class CabinetSection:
    """Раздел личного кабинета."""

    code: str
    title: Any
    url_name: str
    hint: Any

    @property
    def url(self) -> str:
        """Адрес раздела."""
        return reverse(self.url_name)


CABINET_SECTIONS: tuple[CabinetSection, ...] = (
    CabinetSection(
        code="overview",
        title=_("Обзор"),
        url_name="accounts:dashboard",
        hint=_("Мой регион, новое по сохранённому и последние открытые виды"),
    ),
    CabinetSection(
        code="saved",
        title=_("Сохранённое"),
        url_name="workspace:saved",
        hint=_("Регионы, показатели и виды экрана, к которым нужно возвращаться"),
    ),
    CabinetSection(
        code="tickets",
        title=_("Обращения"),
        url_name="feedback:mine",
        hint=_("Ваши обращения, их состояние и ответы"),
    ),
    CabinetSection(
        code="profile",
        title=_("Профиль"),
        url_name="accounts:profile",
        hint=_("Имя и мой регион"),
    ),
    CabinetSection(
        code="security",
        title=_("Безопасность"),
        url_name="accounts:security",
        hint=_("Пароль, адрес почты, вход с кодом и сеансы на других устройствах"),
    ),
    CabinetSection(
        code="data",
        title=_("Персональные данные"),
        url_name="accounts:data",
        hint=_("Выгрузка всего, что хранится о вас, и удаление учётной записи"),
    ),
)

CABINET_SECTIONS_BY_CODE: dict[str, CabinetSection] = {
    section.code: section for section in CABINET_SECTIONS
}


def role_label(user: User) -> str:
    """Старшая роль пользователя словами."""
    return str(ROLES_BY_NAME[user.role_names[-1]].label)


class CabinetViewMixin(LoginRequiredMixin):
    """Общая часть страниц кабинета: вкладки, «хлебные крошки» и заголовок по коду раздела."""

    section_code: str = "overview"

    # Атрибут базового представления — для проверки типов.
    request: HttpRequest

    @property
    def current_user(self) -> User:
        """Пользователь, открывший страницу кабинета; вход гарантирует ``LoginRequiredMixin``."""
        return cast(User, self.request.user)

    @property
    def section(self) -> CabinetSection:
        """Описание текущего раздела кабинета."""
        return CABINET_SECTIONS_BY_CODE[self.section_code]

    def get_crumbs(self) -> tuple[Crumb, ...]:
        """Путь к странице: кабинет и текущий раздел."""
        first = CABINET_SECTIONS[0]
        if self.section_code == first.code:
            return (Crumb(title=_("Личный кабинет")),)
        return (
            Crumb(title=_("Личный кабинет"), url=first.url),
            Crumb(title=self.section.title),
        )

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Дополнить контекст навигацией кабинета."""
        context = super().get_context_data(**kwargs)  # type: ignore[misc]
        context.update(self.cabinet_context())
        context.setdefault("page_title", self.section.title)
        return context

    def cabinet_context(self) -> dict[str, Any]:
        """Навигация кабинета и шапка: кто вошёл и в какой роли."""
        context: dict[str, Any] = {}
        context["breadcrumbs"] = build_breadcrumbs(self.request, *self.get_crumbs())
        context["cabinet_sections"] = [
            {
                "code": section.code,
                "title": section.title,
                "url": section.url,
                "hint": section.hint,
                "active": section.code == self.section_code,
            }
            for section in CABINET_SECTIONS
        ]
        context["cabinet_section"] = self.section_code
        # Подстраница раздела (смена пароля, обращение) — со своим заголовком под вкладками.
        context["cabinet_subpage"] = self.request.path != self.section.url
        # Шапка кабинета: кто вошёл и в какой роли; с правами панели — переход в неё.
        user = self.current_user
        context["cabinet_user"] = {
            "name": user.get_short_name() or user.email,
            "email": user.email,
            "role": role_label(user),
            "manages": user.has_panel_access,
        }
        return context

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        """Отметить активность пользователя при обращении к кабинету."""
        response = super().dispatch(request, *args, **kwargs)
        if request.user.is_authenticated:
            request.user.touch()
        return response
