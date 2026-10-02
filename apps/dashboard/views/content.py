"""
Раздел «Содержимое»: термины глоссария и разделы методики.

Форма правит один язык за раз (параметр ``lang``), перечень показывает полноту обоих.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.contrib import messages
from django.db.models import QuerySet
from django.http import HttpResponse
from django.urls import reverse, reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.views.generic import CreateView, DeleteView, TemplateView, UpdateView

from apps.content.forms import GlossaryTermForm, MethodologySectionForm
from apps.content.models import GlossaryTerm, MethodologySection
from apps.content.selectors import content_summary
from apps.core.navigation import Crumb

from ..navigation import AdminViewMixin

# Языки, между которыми переключается редактор.
EDITOR_LANGUAGES: list[tuple[str, str]] = list(settings.LANGUAGES)


class ContentView(AdminViewMixin, TemplateView):
    """Перечни терминов и разделов методологии с переключением вкладок."""

    template_name = "dashboard/content.html"
    section_code = "content"

    @property
    def active_tab(self) -> str:
        """Выбранный вид содержимого."""
        tab = self.request.GET.get("tab", "glossary")
        return tab if tab in {"glossary", "methodology"} else "glossary"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Собрать перечень выбранного вида содержимого и сводку по локализации."""
        context = super().get_context_data(**kwargs)
        context["page_title"] = _("Содержимое")
        context["summary"] = content_summary()
        context["active_tab"] = self.active_tab
        context["rows"] = self._rows()
        context["languages"] = EDITOR_LANGUAGES
        return context

    def _rows(self) -> list[Any]:
        """Записи выбранного вида с отметкой полноты перевода."""
        queryset: QuerySet[Any]
        if self.active_tab == "methodology":
            queryset = MethodologySection.objects.prefetch_related("translations").order_by(
                "block", "display_order"
            )
        else:
            queryset = GlossaryTerm.objects.prefetch_related("translations").order_by("slug")

        return [
            {
                "record": record,
                "languages": sorted(record.get_available_languages()),
                "translated": "en" in record.get_available_languages(),
            }
            for record in queryset[:100]
        ]


class TranslatableEditMixin(AdminViewMixin):
    """
    Общая часть форм правки двуязычного содержимого.

    Язык выставляется записи до построения формы, иначе django-parler правил бы русскую версию.
    """

    section_code = "content"
    # Тип — как у TemplateResponseMixin, иначе mypy сочтёт переопределение несовместимым.
    template_name: str | None = "dashboard/content_form.html"
    tab: str = "glossary"
    entity_title: Any = ""

    # Атрибут представления-наследника — для проверки типов.
    object: Any

    @property
    def editing_language(self) -> str:
        """Язык, который правится сейчас."""
        code = self.request.GET.get("lang", settings.LANGUAGE_CODE)
        allowed = {value for value, _label in EDITOR_LANGUAGES}
        return code if code in allowed else settings.LANGUAGE_CODE

    def get_object(self, queryset: Any = None) -> Any:
        """Получить запись и переключить её на правимый язык."""
        obj = super().get_object(queryset)  # type: ignore[misc]
        obj.set_current_language(self.editing_language)
        return obj

    def get_form_kwargs(self) -> dict[str, Any]:
        """Передать форме язык перевода параметром, который ждёт django-parler."""
        kwargs = super().get_form_kwargs()  # type: ignore[misc]
        kwargs["_current_language"] = self.editing_language
        return kwargs

    def get_crumbs(self) -> tuple[Crumb, ...]:
        """Путь к странице правки."""
        title = str(self.object) if getattr(self, "object", None) else str(_("Новая запись"))
        return (
            Crumb(title=_("Панель управления"), url=reverse("dashboard:index")),
            Crumb(title=_("Содержимое"), url=reverse("dashboard:content")),
            Crumb(title=title),
        )

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Дополнить контекст переключателем языка и сведениями о переводах."""
        context = super().get_context_data(**kwargs)
        record = getattr(self, "object", None)

        context["entity_title"] = self.entity_title
        context["tab"] = self.tab
        context["editing_language"] = self.editing_language
        context["languages"] = [
            {
                "code": code,
                "title": title,
                "active": code == self.editing_language,
                "filled": bool(record and code in record.get_available_languages()),
            }
            for code, title in EDITOR_LANGUAGES
        ]
        context["page_title"] = str(record) if record else str(self.entity_title)
        return context

    def get_success_url(self) -> str:
        """Вернуться к перечню соответствующего вида содержимого."""
        return f"{reverse('dashboard:content')}?tab={self.tab}"

    def form_valid(self, form: Any) -> HttpResponse:
        """Сохранить запись и сообщить, какая языковая версия правилась."""
        response = super().form_valid(form)  # type: ignore[misc]
        messages.success(
            self.request,
            _("Сохранено (язык: %(lang)s)") % {"lang": self.editing_language},
        )
        return response


class GlossaryTermEditView(TranslatableEditMixin, UpdateView):
    """Правка термина глоссария."""

    model = GlossaryTerm
    form_class = GlossaryTermForm
    context_object_name = "record"
    slug_field = "slug"
    slug_url_kwarg = "slug"
    tab = "glossary"
    entity_title = _("Термин глоссария")


class GlossaryTermCreateView(TranslatableEditMixin, CreateView):
    """Создание термина глоссария."""

    model = GlossaryTerm
    form_class = GlossaryTermForm
    context_object_name = "record"
    tab = "glossary"
    entity_title = _("Новый термин")


class GlossaryTermDeleteView(AdminViewMixin, DeleteView):
    """Удаление термина глоссария."""

    model = GlossaryTerm
    template_name = "dashboard/content_confirm_delete.html"
    section_code = "content"
    context_object_name = "record"
    slug_field = "slug"
    slug_url_kwarg = "slug"
    success_url = reverse_lazy("dashboard:content")

    def get_queryset(self) -> QuerySet[GlossaryTerm]:
        """Только неопубликованные термины."""
        return GlossaryTerm.objects.filter(is_published=False)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Пояснить, что именно удаляется."""
        context = super().get_context_data(**kwargs)
        context["page_title"] = _("Удаление термина")
        context["entity_title"] = _("Термин глоссария")
        return context


class MethodologySectionEditView(TranslatableEditMixin, UpdateView):
    """Правка раздела методики; состав разделов задаёт команда наполнения."""

    model = MethodologySection
    form_class = MethodologySectionForm
    context_object_name = "record"
    slug_field = "code"
    slug_url_kwarg = "code"
    tab = "methodology"
    entity_title = _("Раздел методики")

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Дополнить контекст ссылкой на раздел на публичной странице."""
        context = super().get_context_data(**kwargs)
        context["public_url"] = f"{reverse('content:methodology')}#{self.object.anchor}"
        return context
