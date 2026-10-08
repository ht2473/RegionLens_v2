"""
Страницы «Методика» и «Глоссарий», открытые без входа.

Снятый с публикации термин отвечает «не найдено».
"""

from __future__ import annotations

from typing import Any

from django.http import Http404
from django.utils.translation import gettext_lazy as _
from django.views.generic import RedirectView, TemplateView

from apps.core.navigation import Crumb
from apps.core.views import BreadcrumbMixin

from . import selectors
from .constants import GlossaryCategory
from .examples import examples_for


class MethodologyView(BreadcrumbMixin, TemplateView):
    """Страница «Методика»: описания всех методов по блокам."""

    template_name = "content/methodology.html"

    def get_crumbs(self) -> tuple[Crumb, ...]:
        """Путь к странице."""
        return (Crumb(title=_("Методика")),)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Собрать разделы методологии по блокам с примерами на нынешних данных."""
        context = super().get_context_data(**kwargs)
        blocks = selectors.methodology_blocks()
        examples = examples_for(self.request)
        for block in blocks:
            for section in block["sections"]:
                section.example = examples.get(section.code)
        context["page_title"] = _("Методика расчётов")
        context["blocks"] = blocks
        return context


class GlossaryView(BreadcrumbMixin, TemplateView):
    """Глоссарий: термины по буквам с указателем, поиском и отбором по разделу."""

    template_name = "content/glossary.html"

    def get_crumbs(self) -> tuple[Crumb, ...]:
        """Путь к странице."""
        return (Crumb(title=_("Глоссарий")),)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Собрать термины с учётом поиска и фильтра по разделу."""
        context = super().get_context_data(**kwargs)
        query = self.request.GET.get("q", "").strip()
        category = self.request.GET.get("category", "").strip()

        groups = selectors.terms_by_letter(query=query, category=category)

        context["page_title"] = _("Глоссарий")
        context["groups"] = groups
        context["alphabet"] = selectors.alphabet_index(groups)
        context["categories"] = GlossaryCategory.choices
        context["query"] = query
        context["selected_category"] = category
        context["total"] = sum(len(group["terms"]) for group in groups)
        return context


class TermRedirectView(RedirectView):
    """Постоянный адрес термина: переход на глоссарий к его якорю."""

    permanent = False

    def get_redirect_url(self, *args: Any, **kwargs: Any) -> str:  # noqa: ARG002
        """Адрес глоссария с якорем термина."""
        term = selectors.published_terms().filter(slug=kwargs.get("slug", "")).first()
        if term is None:
            raise Http404("Термин не найден или не опубликован")
        return term.get_absolute_url()
