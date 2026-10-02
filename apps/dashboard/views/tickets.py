"""Раздел «Обратная связь»: ответ письмом, закрытие без ответа, повторная отправка."""

from __future__ import annotations

from typing import Any

from django.contrib import messages
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import DetailView, ListView

from apps.core.navigation import Crumb
from apps.feedback import services as feedback
from apps.feedback.constants import DeliveryStatus, TicketStatus, TicketTopic
from apps.feedback.forms import TicketAnswerForm
from apps.feedback.models import Ticket

from .. import selectors
from ..navigation import AdminViewMixin


class TicketListView(AdminViewMixin, ListView):
    """Перечень обращений с фильтрами и сводкой."""

    template_name = "dashboard/ticket_list.html"
    context_object_name = "tickets"
    section_code = "tickets"
    paginate_by = 25

    def get_queryset(self) -> QuerySet[Ticket]:
        """Обращения с учётом выбранных фильтров."""
        return selectors.tickets(
            status=self.request.GET.get("status", "open").strip(),
            topic=self.request.GET.get("topic", "").strip(),
            query=self.request.GET.get("q", "").strip(),
        )

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Дополнить контекст сводкой и состоянием фильтров."""
        context = super().get_context_data(**kwargs)
        context["page_title"] = _("Обращения пользователей")
        context["statistics"] = selectors.ticket_statistics()
        context["statuses"] = [
            (value, label) for value, label in TicketStatus.choices if value != TicketStatus.NEW
        ]
        context["topics"] = TicketTopic.choices
        context["selected_status"] = self.request.GET.get("status", "open")
        context["selected_topic"] = self.request.GET.get("topic", "")
        context["query"] = self.request.GET.get("q", "")
        return context


class TicketDetailView(AdminViewMixin, DetailView):
    """Карточка обращения: текст, ответ письмом, закрытие без ответа."""

    template_name = "dashboard/ticket_detail.html"
    context_object_name = "ticket"
    section_code = "tickets"
    slug_field = "public_id"
    slug_url_kwarg = "public_id"

    def get_queryset(self) -> QuerySet[Ticket]:
        """Все обращения со связанными записями."""
        return Ticket.objects.with_related()

    def get_crumbs(self) -> tuple[Crumb, ...]:
        """Путь к карточке обращения."""
        return (
            Crumb(title=_("Панель управления"), url=reverse("dashboard:index")),
            Crumb(title=_("Обратная связь"), url=reverse("dashboard:ticket-list")),
            Crumb(title=f"#{self.object.pk}"),
        )

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Дополнить контекст формой ответа и числом прежних обращений автора."""
        context = super().get_context_data(**kwargs)
        ticket = self.object
        context["page_title"] = ticket.subject
        context.setdefault("answer_form", TicketAnswerForm())
        context["author_tickets"] = (
            Ticket.objects.filter(contact_email__iexact=ticket.contact_email)
            .exclude(pk=ticket.pk)
            .count()
        )
        return context

    def post(self, request: HttpRequest, **kwargs: Any) -> HttpResponse:
        """Выполнить действие, выбранное кнопкой формы."""
        ticket = get_object_or_404(self.get_queryset(), public_id=kwargs["public_id"])
        action = request.POST.get("action", "")

        if action == "answer" and not ticket.is_answered and ticket.can_reply:
            return self._answer(request, ticket)
        if action == "resend" and ticket.can_resend:
            ticket = feedback.send_answer(request, ticket)
            if ticket.answer_delivery == DeliveryStatus.SENT:
                messages.success(request, _("Письмо с ответом отправлено"))
            else:
                messages.warning(request, _("Письмо снова не ушло: %s") % ticket.answer_error)
            return redirect(ticket.manage_url)
        if action == "close" and ticket.is_open:
            feedback.close_ticket(ticket)
            messages.success(request, _("Обращение закрыто без ответа"))
            return redirect(ticket.manage_url)

        messages.error(request, _("Это действие для обращения сейчас недоступно"))
        return redirect(ticket.manage_url)

    def _answer(self, request: HttpRequest, ticket: Ticket) -> HttpResponse:
        """Сохранить ответ и отправить письмо; при ошибке формы — карточка с тем же текстом."""
        form = TicketAnswerForm(request.POST)
        if form.is_valid():
            ticket = feedback.answer_ticket(request, ticket, answer=form.cleaned_data["answer"])
            if ticket.answer_delivery == DeliveryStatus.FAILED:
                messages.warning(
                    request,
                    _("Ответ сохранён, но письмо не ушло. Отправьте его ещё раз позже."),
                )
            else:
                messages.success(
                    request,
                    _("Ответ сохранён и отправлен на %(email)s") % {"email": ticket.contact_email},
                )
            return redirect(ticket.manage_url)

        self.object = ticket
        return self.render_to_response(self.get_context_data(answer_form=form))
