"""Форма обращения, открытая без входа, подтверждение приёма и свои обращения в кабинете."""

from __future__ import annotations

from typing import Any

from django.db.models import QuerySet
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import DetailView, FormView, ListView, TemplateView

from apps.accounts.cabinet import CabinetViewMixin
from apps.core.documents import TICKET_TRACE_DAYS
from apps.core.navigation import Crumb
from apps.core.views import BreadcrumbMixin

from . import services
from .constants import TicketTopic
from .forms import FeedbackForm
from .models import Ticket


class FeedbackCreateView(BreadcrumbMixin, FormView):
    """Форма обратной связи; адрес исходной страницы передаётся скрытым полем."""

    template_name = "feedback/feedback_form.html"
    form_class = FeedbackForm

    def get_crumbs(self) -> tuple[Crumb, ...]:
        """Путь к странице."""
        return (Crumb(title=_("Обратная связь")),)

    def get_form_kwargs(self) -> dict[str, Any]:
        """Передать форме текущего пользователя для подстановки его данных."""
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_initial(self) -> dict[str, Any]:
        """Подставить тему, если она задана ссылкой со страницы данных."""
        initial = super().get_initial()
        topic = self.request.GET.get("topic", "")
        if topic in TicketTopic.values:
            initial["topic"] = topic
        return initial

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Дополнить контекст сведениями о порядке рассмотрения обращений."""
        context = super().get_context_data(**kwargs)
        context["page_title"] = _("Обратная связь")
        context["topics"] = TicketTopic.choices
        context["source_page"] = self.request.META.get("HTTP_REFERER", "")[:500]
        context["ticket_trace_days"] = TICKET_TRACE_DAYS
        return context

    def form_valid(self, form: FeedbackForm) -> HttpResponse:
        """Принять обращение и показать страницу подтверждения."""
        try:
            services.check_rate_limit(self.request)
        except services.RateLimitExceededError as error:
            form.add_error(None, str(error))
            return self.form_invalid(form)

        ticket = services.create_ticket(
            self.request,
            contact_name=form.cleaned_data["contact_name"],
            contact_email=form.cleaned_data["contact_email"],
            topic=form.cleaned_data["topic"],
            subject=form.cleaned_data["subject"],
            body=form.cleaned_data["body"],
            page_url=self.request.POST.get("page_url", ""),
        )

        self.request.session["feedback_ticket"] = str(ticket.public_id)
        return redirect("feedback:sent")


class FeedbackSentView(BreadcrumbMixin, TemplateView):
    """Страница подтверждения с номером обращения: обновление не отправляет его повторно."""

    template_name = "feedback/feedback_sent.html"

    def get_crumbs(self) -> tuple[Crumb, ...]:
        """Путь к странице."""
        return (
            Crumb(title=_("Обратная связь"), url=reverse("feedback:create")),
            Crumb(title=_("Обращение принято")),
        )

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Показать принятое обращение, если оно есть в текущем сеансе."""
        context = super().get_context_data(**kwargs)
        context["page_title"] = _("Обращение принято")

        public_id = self.request.session.get("feedback_ticket")
        context["ticket"] = (
            Ticket.objects.filter(public_id=public_id).first() if public_id else None
        )
        return context


# ---------------------------------------------------------------------------------------
# Обращения в личном кабинете
# ---------------------------------------------------------------------------------------


class MyTicketsView(CabinetViewMixin, ListView):
    """Обращения, отправленные из учётной записи: состояние и ответ."""

    template_name = "feedback/my_tickets.html"
    context_object_name = "tickets"
    section_code = "tickets"
    paginate_by = 20

    def get_queryset(self) -> QuerySet[Ticket]:
        """Свои обращения, новые сверху."""
        return Ticket.objects.for_user(self.current_user).order_by("-created_at")

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Сколько ждут ответа."""
        context = super().get_context_data(**kwargs)
        context["waiting"] = Ticket.objects.for_user(self.current_user).open().count()
        return context


class MyTicketDetailView(CabinetViewMixin, DetailView):
    """Обращение и ответ на него; чужое обращение — «не найдено»."""

    template_name = "feedback/my_ticket_detail.html"
    context_object_name = "ticket"
    section_code = "tickets"
    slug_field = "public_id"
    slug_url_kwarg = "public_id"

    def get_queryset(self) -> QuerySet[Ticket]:
        """Только свои обращения."""
        return Ticket.objects.for_user(self.current_user)

    def get_crumbs(self) -> tuple[Crumb, ...]:
        """Путь к странице: кабинет, обращения, номер."""
        return (
            Crumb(title=_("Личный кабинет"), url=reverse("accounts:dashboard")),
            Crumb(title=self.section.title, url=self.section.url),
            Crumb(title=_("Обращение № %(number)s") % {"number": self.object.pk}),
        )

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Заголовок страницы — тема обращения."""
        context = super().get_context_data(**kwargs)
        context["page_title"] = self.object.subject
        return context
