"""Маршруты своих данных: мастер загрузки таблицы."""

from __future__ import annotations

from django.urls import path

from . import views

app_name = "userdata"

urlpatterns = [
    path("own-data/new/", views.UploadView.as_view(), name="upload"),
    path("own-data/<uuid:public_id>/file/", views.FileStepView.as_view(), name="file"),
    path("own-data/<uuid:public_id>/table/", views.TableStepView.as_view(), name="table"),
    path(
        "own-data/<uuid:public_id>/table/status/",
        views.TableStatusView.as_view(),
        name="table-status",
    ),
]
