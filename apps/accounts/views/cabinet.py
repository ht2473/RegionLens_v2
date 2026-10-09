"""Обзор кабинета и сохранение профиля."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from functools import partial
from typing import TYPE_CHECKING, Any

from django.contrib import messages
from django.db.models import F, QuerySet
from django.forms import ModelForm
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView, UpdateView

from ..cabinet import CabinetViewMixin, settings_url
from ..forms import ProfileForm
from ..models import User
from ..region import my_region, region_choices, write_cookie
from .settings import SettingsContextMixin

if TYPE_CHECKING:  # pragma: no cover - только для проверки типов
    from apps.workspace.tiles import Tile

# Граница «нового по сохранённому» на время сеанса: обновление страницы её не сдвигает.
UPDATES_SINCE_SESSION_KEY = "cabinet:updates-since"
# Сколько плиток в «Продолжить»: последние виды, исследования и свои таблицы вместе.
CONTINUE_TILES = 6


class CabinetOverviewView(CabinetViewMixin, TemplateView):
    """Обзор кабинета — «мой стол»: мой регион, продолжить начатое, новое по сохранённому."""

    template_name = "accounts/overview.html"
    section_code = "overview"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Собрать блоки обзора."""
        from apps.catalog.brief import BAND_NOW, region_brief
        from apps.workspace.selectors import favorites, saved_queries
        from apps.workspace.tiles import dataset_tile, query_tile, study_tile
        from apps.workspace.updates import news_rows, saved_updates

        context = super().get_context_data(**kwargs)
        user = self.current_user
        region = my_region(self.request)
        context["region"] = (
            region_brief(region, metrics=0, now=BAND_NOW) if region is not None else None
        )
        if region is None:
            context["region_choices"] = region_choices()

        since = self._updates_since(user)
        context["news"] = news_rows(saved_updates(user, since))
        context["has_marks"] = favorites(user).exists()
        context["updates_since"] = since

        # Последние виды, исследования и таблицы — по времени последнего обращения.
        views = list(
            saved_queries(user).order_by(F("last_opened_at").desc(nulls_last=True), "-updated_at")[
                :CONTINUE_TILES
            ]
        )
        tables = list(
            user.datasets.select_related("current_version").order_by("-updated_at")[:CONTINUE_TILES]
        )
        studies = list(user.studies.order_by("-updated_at")[:CONTINUE_TILES])
        candidates: list[tuple[datetime, Callable[[], Tile]]] = [
            *(
                (query.last_opened_at or query.updated_at, partial(query_tile, query))
                for query in views
            ),
            *((study.updated_at, partial(study_tile, study)) for study in studies),
            *((table.updated_at, partial(dataset_tile, table)) for table in tables),
        ]
        candidates.sort(key=lambda item: item[0], reverse=True)
        context["continue_tiles"] = [build() for _moment, build in candidates[:CONTINUE_TILES]]
        context["panel_needs_code"] = user.has_panel_access and not user.two_factor_enabled
        return context

    def _updates_since(self, user: User) -> datetime:
        """
        Граница нового: прошлый визит; впервые в сеансе — запомнить её и сдвинуть отметку.

        До первого визита границей служит регистрация: выпуски до неё новыми не считаются.
        """
        session = self.request.session
        stored = session.get(UPDATES_SINCE_SESSION_KEY)
        if stored:
            return datetime.fromisoformat(stored)
        since = user.updates_seen_at or user.created_at
        session[UPDATES_SINCE_SESSION_KEY] = since.isoformat()
        User.objects.filter(pk=user.pk).update(updates_seen_at=timezone.now())
        return since


class ProfileView(SettingsContextMixin, UpdateView):
    """Сохранение профиля из «Настроек»: имя и мой регион."""

    form_class = ProfileForm

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:  # noqa: ARG002
        """Форма живёт на странице «Настройки»."""
        return redirect(settings_url("profile"))

    def get_object(self, queryset: QuerySet[Any] | None = None) -> Any:  # noqa: ARG002
        """Правится только собственная учётная запись."""
        return self.current_user

    def get_success_url(self) -> str:
        """Вернуться к профилю в «Настройках»."""
        return settings_url("profile")

    def form_invalid(self, form: ModelForm[Any]) -> HttpResponse:
        """Показать «Настройки» с ошибками формы профиля."""
        return self.render_to_response(self.get_context_data(profile_form=form))

    def form_valid(self, form: ModelForm[Any]) -> HttpResponse:
        """Сохранить изменения; регион запоминается и в cookie — для страниц после выхода."""
        response = super().form_valid(form)
        write_cookie(response, form.cleaned_data.get("region"))
        messages.success(self.request, _("Профиль сохранён"))
        return response
