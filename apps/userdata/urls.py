"""Маршруты своих данных: раздел, мастер загрузки таблицы и страница таблицы."""

from __future__ import annotations

from django.urls import path

from . import formula_views, month_views, pages, related_views, views

app_name = "userdata"

urlpatterns = [
    path("own-data/", pages.SectionView.as_view(), name="index"),
    path("own-data/new/", views.UploadView.as_view(), name="upload"),
    path("own-data/example/", pages.ExampleView.as_view(), name="example"),
    path("own-data/<uuid:public_id>/", pages.DatasetView.as_view(), name="dataset"),
    path("own-data/<uuid:public_id>/delete/", pages.DeleteView.as_view(), name="delete"),
    path(
        "own-data/<uuid:public_id>/related/",
        related_views.RelatedView.as_view(),
        name="related",
    ),
    path(
        "own-data/<uuid:public_id>/months/",
        month_views.MonthsView.as_view(),
        name="months",
    ),
    path(
        "own-data/<uuid:public_id>/formula/",
        formula_views.FormulaView.as_view(),
        name="formula-new",
    ),
    path(
        "own-data/<uuid:public_id>/formula/<slug:code>/",
        formula_views.FormulaView.as_view(),
        name="formula",
    ),
    path(
        "own-data/<uuid:public_id>/download/<str:kind>/",
        pages.DownloadView.as_view(),
        name="download",
    ),
    path("own-data/<uuid:public_id>/file/", views.FileStepView.as_view(), name="file"),
    path("own-data/<uuid:public_id>/table/", views.TableStepView.as_view(), name="table"),
    path(
        "own-data/<uuid:public_id>/table/status/",
        views.TableStatusView.as_view(),
        name="table-status",
    ),
    path("own-data/<uuid:public_id>/series/", views.SeriesStepView.as_view(), name="series"),
    path(
        "own-data/<uuid:public_id>/series/status/",
        views.SeriesStatusView.as_view(),
        name="series-status",
    ),
]
