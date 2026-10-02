"""Маршруты панели управления под ``/manage/``; действия — отдельными адресами, только POST."""

from __future__ import annotations

from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    # --- Обзор ------------------------------------------------------------------------
    path("manage/", views.IndexView.as_view(), name="index"),
    # --- Пользователи и роли ------------------------------------------------------------
    path("manage/users/", views.UserListView.as_view(), name="user-list"),
    path(
        "manage/users/<uuid:public_id>/",
        views.UserDetailView.as_view(),
        name="user-detail",
    ),
    path(
        "manage/users/<uuid:public_id>/role/",
        views.UserRoleUpdateView.as_view(),
        name="user-role",
    ),
    path(
        "manage/users/<uuid:public_id>/profile/",
        views.UserProfileUpdateView.as_view(),
        name="user-profile",
    ),
    path(
        "manage/users/<uuid:public_id>/toggle/",
        views.UserToggleActiveView.as_view(),
        name="user-toggle",
    ),
    path(
        "manage/users/<uuid:public_id>/code/reset/",
        views.UserTwoFactorResetView.as_view(),
        name="user-two-factor-reset",
    ),
    # --- Загрузка данных -------------------------------------------------------------------
    path("manage/data/", views.DataView.as_view(), name="data"),
    path("manage/data/upload/", views.DatasetUploadView.as_view(), name="data-upload"),
    path(
        "manage/data/versions/<str:code>/",
        views.DatasetVersionEditView.as_view(),
        name="dataset-version-edit",
    ),
    path("manage/data/runs/<int:pk>/", views.EtlRunDetailView.as_view(), name="etl-run"),
    path("manage/data/quality/", views.QualityView.as_view(), name="quality"),
    # --- Источники данных -------------------------------------------------------------------
    path("manage/sources/", views.SourcesView.as_view(), name="sources"),
    path(
        "manage/sources/<slug:code>/collect/",
        views.CollectNowView.as_view(),
        name="source-collect",
    ),
    path(
        "manage/sources/releases/<int:pk>/",
        views.ReleaseDetailView.as_view(),
        name="release",
    ),
    # --- Посещения ----------------------------------------------------------------------------
    path("manage/visits/", views.VisitsView.as_view(), name="visits"),
    path("manage/visits/days.csv", views.VisitsExportView.as_view(), name="visits-export"),
    # --- Обратная связь -----------------------------------------------------------------------
    path("manage/tickets/", views.TicketListView.as_view(), name="ticket-list"),
    path(
        "manage/tickets/<uuid:public_id>/",
        views.TicketDetailView.as_view(),
        name="ticket-detail",
    ),
    # --- Содержимое ---------------------------------------------------------------------------
    path("manage/content/", views.ContentView.as_view(), name="content"),
    path(
        "manage/content/glossary/new/",
        views.GlossaryTermCreateView.as_view(),
        name="term-create",
    ),
    path(
        "manage/content/glossary/<slug:slug>/",
        views.GlossaryTermEditView.as_view(),
        name="term-edit",
    ),
    path(
        "manage/content/glossary/<slug:slug>/delete/",
        views.GlossaryTermDeleteView.as_view(),
        name="term-delete",
    ),
    path(
        "manage/content/methodology/<slug:code>/",
        views.MethodologySectionEditView.as_view(),
        name="methodology-edit",
    ),
]
