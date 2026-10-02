"""Маршруты обратной связи: форма открыта без входа, свои обращения — в кабинете."""

from __future__ import annotations

from django.urls import path

from . import views

app_name = "feedback"

urlpatterns = [
    path("feedback/", views.FeedbackCreateView.as_view(), name="create"),
    path("feedback/sent/", views.FeedbackSentView.as_view(), name="sent"),
    # --- Обращения в личном кабинете ---------------------------------------------------
    path("cabinet/tickets/", views.MyTicketsView.as_view(), name="mine"),
    path(
        "cabinet/tickets/<uuid:public_id>/",
        views.MyTicketDetailView.as_view(),
        name="mine-detail",
    ),
]
