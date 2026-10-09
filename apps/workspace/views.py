"""
Страницы личного кабинета: сохранённые выборки и избранное.

Чужой идентификатор даёт «не найдено», а не отказ: иначе чужие записи узнавались бы перебором.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.views import redirect_to_login as django_redirect_to_login
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.views.generic import DeleteView, TemplateView, UpdateView, View

from apps.accounts.cabinet import CabinetViewMixin
from apps.accounts.permissions import QuotaExceededError
from apps.catalog.selectors import MAX_COMPARE
from apps.core.utils.redirects import safe_back

from . import services
from .forms import FavoriteForm, FavoriteNoteForm, SavedQueryForm, SaveViewForm
from .menu import save_menu_context
from .models import SavedQuery
from .selectors import favorites, saved_queries, vanished_series


class SavedView(CabinetViewMixin, TemplateView):
    """Всё сохранённое без разбиения на страницы: регионы, показатели и виды экрана."""

    template_name = "workspace/saved.html"
    section_code = "saved"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Разложить отметки по видам объектов и добавить выборки."""
        context = super().get_context_data(**kwargs)
        marked = list(favorites(self.current_user))
        territories = [item for item in marked if item.kind == "territory"]
        # Показатель и ряд показателя открывают одну страницу.
        indicators = [item for item in marked if item.kind != "territory"]
        context["territories"] = territories
        context["indicators"] = indicators
        # Третье — подсказка для пустой группы, четвёртое — переходы со всей группой.
        context["favorite_groups"] = [
            (
                _("Регионы"),
                territories,
                _("Кнопка «Сохранить» — в паспорте региона."),
                territory_actions(territories),
            ),
            (_("Показатели"), indicators, _("Кнопка «Сохранить» — на странице показателя."), []),
        ]
        context["compare_limit"] = MAX_COMPARE
        context["over_limit"] = len(territories) > MAX_COMPARE
        queries = list(saved_queries(self.current_user))
        vanished = vanished_series(queries)
        for query in queries:
            query.vanished = tuple(vanished.get(query.pk, ()))
        context["queries"] = queries
        return context


def territory_actions(territories: list[Any]) -> list[tuple[Any, str, str]]:
    """Переходы с сохранёнными регионами: динамика и сравнение — от двух, карта — от одного."""
    codes = [item.territory.code for item in territories if item.territory_id][:MAX_COMPARE]
    if not codes:
        return []
    query = urlencode([("territory", code) for code in codes])
    actions = []
    if len(codes) > 1:
        actions.append(
            (_("Сравнить отмеченные регионы"), f"{reverse('compare:index')}?{query}", "compare")
        )
    actions.append((_("Показать на карте"), f"{reverse('maps:choropleth')}?{query}", "map"))
    return actions


# ---------------------------------------------------------------------------------------
# Сохранённые выборки
# ---------------------------------------------------------------------------------------


class SavedQueryCreateView(View):
    """«В Сохранённое» со страницы расчёта: вид с её строкой запроса, название — само."""

    def post(self, request: HttpRequest) -> HttpResponse:
        """Сохранить вид; HTMX — меню заново, иначе — назад с сообщением."""
        if not request.user.is_authenticated:
            return redirect_to_login(request)

        # После проверки выше mypy сужает тип пользователя до User.
        user = request.user
        form = SaveViewForm(request.POST)
        back = safe_back(request, reverse("core:home"))

        if not form.is_valid():
            messages.error(request, _("Вид сохранить не удалось: %s") % form.errors.as_text())
            return HttpResponseRedirect(back)

        target = form.cleaned_data["target"]
        query_string = form.cleaned_data.get("query_string", "")
        try:
            query, created = services.save_view(user, target=target, query_string=query_string)
        except QuotaExceededError as error:
            messages.error(request, str(error))
            return HttpResponseRedirect(back)

        if request.headers.get("HX-Request"):
            return render_save_menu(request, "view", target, query_string=query_string, back=back)
        if created:
            messages.success(request, _("Вид «%s» сохранён в кабинете") % query.title)
        else:
            messages.info(request, _("Этот вид уже в «Сохранённом»: «%s»") % query.title)
        return HttpResponseRedirect(back)


class SavedQueryUpdateView(CabinetViewMixin, UpdateView):
    """Правка названия и пояснения сохранённой выборки."""

    template_name = "workspace/query_form.html"
    form_class = SavedQueryForm
    section_code = "saved"
    context_object_name = "query"
    slug_field = "public_id"
    slug_url_kwarg = "public_id"

    def get_queryset(self) -> QuerySet[SavedQuery]:
        """Только выборки текущего пользователя."""
        return saved_queries(self.current_user)

    def get_form_kwargs(self) -> dict[str, Any]:
        """Передать форме владельца для проверки уникальности названия."""
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.current_user
        return kwargs

    def get_success_url(self) -> str:
        """После сохранения вернуться к перечню выборок."""
        messages.success(self.request, _("Вид обновлён"))
        return reverse("workspace:saved")


class SavedQueryDeleteView(CabinetViewMixin, DeleteView):
    """Удаление сохранённой выборки."""

    template_name = "workspace/query_confirm_delete.html"
    section_code = "saved"
    context_object_name = "query"
    slug_field = "public_id"
    slug_url_kwarg = "public_id"
    success_url = reverse_lazy("workspace:saved")

    def get_queryset(self) -> QuerySet[SavedQuery]:
        """Только выборки текущего пользователя."""
        return saved_queries(self.current_user)

    def form_valid(self, form: Any) -> HttpResponse:
        """Сообщить об удалении."""
        messages.success(self.request, _("Вид удалён"))
        return super().form_valid(form)


class SavedQueryOpenView(CabinetViewMixin, View):
    """Открытие сохранённой выборки: переход на страницу с её параметрами."""

    section_code = "saved"

    def get(self, request: HttpRequest, public_id: str) -> HttpResponse:
        """Отметить открытие и перенаправить на страницу расчёта."""
        query = get_object_or_404(saved_queries(self.current_user), public_id=public_id)
        url = query.url
        if not url:
            messages.error(request, _("Страницы этого вида больше нет"))
            return redirect("workspace:saved")

        missing = vanished_series([query]).get(query.pk)
        if missing:
            messages.warning(
                request,
                _("Этих рядов больше нет в данных, вид открыт без них: %(names)s")
                % {"names": "; ".join(missing)},
            )
        query.register_open()
        return HttpResponseRedirect(url)


# ---------------------------------------------------------------------------------------
# Избранное
# ---------------------------------------------------------------------------------------


class FavoriteToggleView(View):
    """Постановка и снятие отметки избранного: форме — переход назад, HTMX — новая кнопка."""

    def post(self, request: HttpRequest) -> HttpResponse:
        """Переключить отметку избранного."""
        if not request.user.is_authenticated:
            return redirect_to_login(request)

        user = request.user
        form = FavoriteForm(request.POST)
        back = safe_back(request, reverse("core:home"))
        if not form.is_valid():
            messages.error(request, _("Не удалось определить объект"))
            return HttpResponseRedirect(back)

        try:
            _favorite, added = services.toggle_favorite(
                user,
                kind=form.cleaned_data["kind"],
                identifier=form.cleaned_data["identifier"],
                note=form.cleaned_data.get("note", ""),
            )
        except QuotaExceededError as error:
            messages.error(request, str(error))
            return HttpResponseRedirect(back)

        if request.headers.get("HX-Request"):
            return render_save_menu(
                request,
                form.cleaned_data["kind"],
                form.cleaned_data["identifier"],
                series=form.cleaned_data.get("series", ""),
                back=back,
            )

        messages.success(
            request,
            _("Сохранено в кабинете") if added else _("Убрано из сохранённого"),
        )
        return HttpResponseRedirect(back)


class FavoriteNoteView(CabinetViewMixin, View):
    """Пометка к отметке: зачем сохранён регион или показатель."""

    section_code = "saved"

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        """Сохранить пометку; пустая — убрать."""
        favorite = get_object_or_404(favorites(self.current_user), pk=pk)
        form = FavoriteNoteForm(request.POST)
        if form.is_valid():
            favorite.note = form.cleaned_data["note"].strip()
            favorite.save(update_fields=["note", "updated_at"])
            messages.success(
                request, _("Пометка сохранена") if favorite.note else _("Пометка убрана")
            )
        else:
            messages.error(request, _("Пометка не длиннее 200 знаков"))
        return redirect("workspace:saved")


class FavoriteDeleteView(CabinetViewMixin, View):
    """Удаление отметки из перечня избранного."""

    section_code = "saved"

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        """Удалить отметку."""
        favorite = get_object_or_404(favorites(self.current_user), pk=pk)
        favorite.delete()
        messages.success(request, _("Убрано из сохранённого"))
        return redirect("workspace:saved")


def redirect_to_login(request: HttpRequest) -> HttpResponse:
    """Отправить неаутентифицированного посетителя на форму входа."""
    return django_redirect_to_login(safe_back(request, request.path))


def render_save_menu(
    request: HttpRequest,
    kind: str,
    identifier: str,
    *,
    series: str = "",
    query_string: str = "",
    back: str,
) -> HttpResponse:
    """Меню «Сохранить» после действия — для замены HTMX: страница, с которой оно пришло."""
    context = save_menu_context(
        request, kind, identifier, series=series, query_string=query_string, back=back
    )
    return render(request, "workspace/partials/_save_menu.html", context)
