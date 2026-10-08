"""Маршруты своих данных: раздел, мастер загрузки, страница таблицы, ссылки и исследования."""

from __future__ import annotations

from django.urls import path

from . import (
    formula_views,
    month_views,
    pages,
    related_views,
    share_views,
    study_views,
    version_views,
    views,
)

app_name = "userdata"

urlpatterns = [
    path("own-data/", pages.SectionView.as_view(), name="index"),
    path("own-data/new/", views.UploadView.as_view(), name="upload"),
    path("own-data/example/", pages.ExampleView.as_view(), name="example"),
    path("own-data/studies/new/", study_views.StudyCreateView.as_view(), name="study-new"),
    path("own-data/studies/add/", study_views.StudyAddView.as_view(), name="study-add"),
    path("own-data/studies/<uuid:public_id>/", study_views.StudyView.as_view(), name="study"),
    path(
        "own-data/studies/<uuid:public_id>/edit/",
        study_views.StudyEditView.as_view(),
        name="study-edit",
    ),
    path(
        "own-data/studies/<uuid:public_id>/cards/<slug:block_id>/",
        study_views.StudyCardView.as_view(),
        name="study-card",
    ),
    path(
        "own-data/studies/<uuid:public_id>/shares/",
        share_views.StudyShareCreateView.as_view(),
        name="study-share-create",
    ),
    path(
        "own-data/studies/<uuid:public_id>/shares/<uuid:share_id>/revoke/",
        share_views.StudyShareRevokeView.as_view(),
        name="study-share-revoke",
    ),
    path("own-data/<uuid:public_id>/", pages.DatasetView.as_view(), name="dataset"),
    path("own-data/<uuid:public_id>/delete/", pages.DeleteView.as_view(), name="delete"),
    path(
        "own-data/<uuid:public_id>/study/", pages.DatasetStudyView.as_view(), name="dataset-study"
    ),
    path(
        "own-data/<uuid:public_id>/versions/new/",
        version_views.VersionUploadView.as_view(),
        name="version-new",
    ),
    path(
        "own-data/<uuid:public_id>/versions/draft/discard/",
        version_views.VersionDiscardView.as_view(),
        name="version-discard",
    ),
    path(
        "own-data/<uuid:public_id>/versions/<int:number>/",
        version_views.VersionView.as_view(),
        name="version",
    ),
    path(
        "own-data/<uuid:public_id>/versions/<int:number>/restore/",
        version_views.VersionRestoreView.as_view(),
        name="version-restore",
    ),
    path(
        "own-data/<uuid:public_id>/shares/",
        share_views.DatasetShareCreateView.as_view(),
        name="share-create",
    ),
    path(
        "own-data/<uuid:public_id>/shares/<uuid:share_id>/revoke/",
        share_views.DatasetShareRevokeView.as_view(),
        name="share-revoke",
    ),
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
