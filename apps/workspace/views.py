"""
Страницы личного кабинета: «Сохранённое» плитками, меню «Сохранить» и правка на месте.

Чужой идентификатор даёт «не найдено», а не отказ: иначе чужие записи узнавались бы перебором.
"""

from __future__ import annotations

from collections import Counter
from typing import Any
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.views import redirect_to_login as django_redirect_to_login
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView, UpdateView, View

from apps.accounts.cabinet import CabinetViewMixin
from apps.accounts.permissions import QuotaExceededError
from apps.catalog.selectors import MAX_COMPARE
from apps.core.throttle import allow_key
from apps.core.utils.redirects import safe_back

from . import services
from .constants import SAVED_EDITS, SAVED_EDITS_WINDOW
from .forms import FavoriteForm, FavoriteNoteForm, SavedQueryForm, SaveViewForm
from .menu import save_menu_context
from .models import SavedQuery
from .selectors import favorites, saved_queries, vanished_series
from .tiles import KIND_LABELS, KIND_TERRITORY, favorite_tile, query_tile

# Параметр отбора «Сохранённого» по виду и отметка «только что убрано».
KIND_PARAM = "kind"
REMOVED_PARAM = "removed"


class SavedView(CabinetViewMixin, TemplateView):
    """Всё сохранённое плитками без разбиения на страницы; отбор по виду."""

    template_name = "workspace/saved.html"
    section_code = "saved"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Плитки отметок и видов, отбор по виду и «Вернуть» после удаления."""
        context = super().get_context_data(**kwargs)
        user = self.current_user
        marked = list(favorites(user))
        queries = list(saved_queries(user))
        vanished = vanished_series(queries)
        for query in queries:
            query.vanished = tuple(vanished.get(query.pk, ()))

        tiles = [favorite_tile(item) for item in marked] + [query_tile(item) for item in queries]
        tiles.sort(key=lambda tile: tile.moment, reverse=True)
        counts = Counter(tile.kind for tile in tiles)
        chosen = self.request.GET.get(KIND_PARAM, "")
        if chosen not in counts:
            chosen = ""
        context["tiles"] = [tile for tile in tiles if not chosen or tile.kind == chosen]
        context["total"] = len(tiles)
        context["kind"] = chosen
        context["filters"] = [
            {"code": code, "title": title, "count": counts[code], "active": code == chosen}
            for code, title in KIND_LABELS.items()
            if counts[code]
        ]
        territories = [item for item in marked if item.territory_id]
        if chosen in ("", KIND_TERRITORY):
            context["territory_actions"] = territory_actions(territories)
            context["over_limit"] = len(territories) > MAX_COMPARE
        context["compare_limit"] = MAX_COMPARE
        removed = self.request.session.get(services.REMOVED_SESSION_KEY)
        if self.request.GET.get(REMOVED_PARAM) and removed:
            context["removed"] = removed.get("title", "")
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


def saved_url(kind: str = "", *, removed: bool = False) -> str:
    """Адрес «Сохранённого» с отбором по виду и отметкой «только что убрано»."""
    params = {
        KIND_PARAM: kind if kind in KIND_LABELS else "",
        REMOVED_PARAM: "1" if removed else "",
    }
    query = urlencode({name: value for name, value in params.items() if value})
    base = reverse("workspace:saved")
    return f"{base}?{query}" if query else base


def _edits_allowed(request: HttpRequest) -> bool:
    """Правка «Сохранённого» в пределах частоты учётной записи."""
    return allow_key(
        "saved-edit", str(request.user.pk), limit=SAVED_EDITS, window=SAVED_EDITS_WINDOW
    )


def _too_often(request: HttpRequest, *, json: bool = False) -> HttpResponse:
    """Ответ на правку сверх предела частоты."""
    text = _("Слишком много изменений подряд. Повторите позже.")
    if json:
        return JsonResponse({"error": str(text)}, status=429)
    messages.error(request, text)
    return redirect(saved_url(request.POST.get(KIND_PARAM, "")))


def _wants_json(request: HttpRequest) -> bool:
    """Запрос правки на месте (сценарий ждёт JSON), а не отправка формы."""
    return "application/json" in request.headers.get("Accept", "")


# ---------------------------------------------------------------------------------------
# Сохранённые виды
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
    """Правка названия и пометки вида на отдельной странице — без сценариев."""

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


class SavedQueryChangeView(CabinetViewMixin, View):
    """Правка на месте: название или пометка вида; ответ — JSON с новым значением."""

    section_code = "saved"

    def post(self, request: HttpRequest, public_id: str) -> HttpResponse:
        """Сохранить название (``title``) или пометку (``description``)."""
        query = get_object_or_404(saved_queries(self.current_user), public_id=public_id)
        if not _edits_allowed(request):
            return _too_often(request, json=True)
        if "title" in request.POST:
            title = request.POST["title"].strip()[: services.TITLE_LENGTH]
            if not title:
                return JsonResponse({"error": str(_("Название не может быть пустым"))}, status=400)
            taken = saved_queries(self.current_user).filter(title=title).exclude(pk=query.pk)
            if taken.exists():
                return JsonResponse(
                    {"error": str(_("Вид с таким названием уже сохранён"))}, status=400
                )
            query.title = title
            query.save(update_fields=["title", "updated_at"])
            return JsonResponse({"value": query.title})
        if "description" in request.POST:
            query.description = request.POST["description"].strip()[: services.DESCRIPTION_LENGTH]
            query.save(update_fields=["description", "updated_at"])
            return JsonResponse({"value": query.description})
        return JsonResponse({"error": str(_("Нечего менять"))}, status=400)


class SavedQueryDeleteView(CabinetViewMixin, View):
    """Убрать вид из «Сохранённого»; вернуть — кнопкой «Вернуть» на той же странице."""

    section_code = "saved"

    def post(self, request: HttpRequest, public_id: str) -> HttpResponse:
        """Удалить вид и запомнить его в сеансе."""
        query = get_object_or_404(saved_queries(self.current_user), public_id=public_id)
        if not _edits_allowed(request):
            return _too_often(request)
        request.session[services.REMOVED_SESSION_KEY] = services.forget_query(query)
        return redirect(saved_url(request.POST.get(KIND_PARAM, ""), removed=True))


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


class RestoreView(CabinetViewMixin, View):
    """«Вернуть» последнее убранное из «Сохранённого»."""

    section_code = "saved"

    def post(self, request: HttpRequest) -> HttpResponse:
        """Вернуть вид или отметку из сеанса."""
        kind = request.POST.get(KIND_PARAM, "")
        if not _edits_allowed(request):
            return _too_often(request)
        payload = request.session.pop(services.REMOVED_SESSION_KEY, None)
        if not payload:
            messages.info(request, _("Возвращать нечего"))
            return redirect(saved_url(kind))
        try:
            title = services.restore(self.current_user, payload)
        except QuotaExceededError as error:
            messages.error(request, str(error))
            return redirect(saved_url(kind))
        if title:
            messages.success(request, _("«%s» снова в «Сохранённом»") % title)
        return redirect(saved_url(kind))


# ---------------------------------------------------------------------------------------
# Избранное
# ---------------------------------------------------------------------------------------


class FavoriteToggleView(View):
    """Постановка и снятие отметки из меню «Сохранить»: HTMX — меню заново, форме — назад."""

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
        """Сохранить пометку; пустая — убрать. Правка на месте получает JSON."""
        favorite = get_object_or_404(favorites(self.current_user), pk=pk)
        json = _wants_json(request)
        if not _edits_allowed(request):
            return _too_often(request, json=json)
        form = FavoriteNoteForm(request.POST)
        if not form.is_valid():
            text = _("Пометка не длиннее 200 знаков")
            if json:
                return JsonResponse({"error": str(text)}, status=400)
            messages.error(request, text)
            return redirect("workspace:saved")
        favorite.note = form.cleaned_data["note"].strip()
        favorite.save(update_fields=["note", "updated_at"])
        if json:
            return JsonResponse({"value": favorite.note})
        messages.success(request, _("Пометка сохранена") if favorite.note else _("Пометка убрана"))
        return redirect("workspace:saved")


class FavoriteDeleteView(CabinetViewMixin, View):
    """Убрать отметку из «Сохранённого»; вернуть — кнопкой «Вернуть»."""

    section_code = "saved"

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        """Снять отметку и запомнить её в сеансе."""
        favorite = get_object_or_404(favorites(self.current_user), pk=pk)
        if not _edits_allowed(request):
            return _too_often(request)
        request.session[services.REMOVED_SESSION_KEY] = services.forget_favorite(favorite)
        return redirect(saved_url(request.POST.get(KIND_PARAM, ""), removed=True))


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
