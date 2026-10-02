"""
Маршруты личного кабинета под ``/cabinet/``.

Отметка избранного и сохранение вида — действия со страниц сайта, вне префикса.
"""

from __future__ import annotations

from django.urls import path

from . import views

app_name = "workspace"

urlpatterns = [
    path("cabinet/saved/", views.SavedView.as_view(), name="saved"),
    # --- Сохранённые выборки ---------------------------------------------------------
    path(
        "cabinet/queries/<uuid:public_id>/edit/",
        views.SavedQueryUpdateView.as_view(),
        name="query-edit",
    ),
    path(
        "cabinet/queries/<uuid:public_id>/delete/",
        views.SavedQueryDeleteView.as_view(),
        name="query-delete",
    ),
    path(
        "cabinet/queries/<uuid:public_id>/open/",
        views.SavedQueryOpenView.as_view(),
        name="query-open",
    ),
    path("queries/save/", views.SavedQueryCreateView.as_view(), name="query-save"),
    # --- Избранное --------------------------------------------------------------------
    path(
        "cabinet/favorites/<int:pk>/note/",
        views.FavoriteNoteView.as_view(),
        name="favorite-note",
    ),
    path(
        "cabinet/favorites/<int:pk>/delete/",
        views.FavoriteDeleteView.as_view(),
        name="favorite-delete",
    ),
    path("favorites/toggle/", views.FavoriteToggleView.as_view(), name="favorite-toggle"),
]
