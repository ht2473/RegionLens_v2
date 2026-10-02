"""Маршруты аналитического раздела: адреса названы по задаче, а не по методу."""

from __future__ import annotations

from django.urls import path

from . import views

app_name = "analytics"

urlpatterns = [
    path("analytics/", views.AnalyticsIndexView.as_view(), name="index"),
    path("analytics/inequality/", views.InequalityView.as_view(), name="inequality"),
    path("analytics/convergence/", views.ConvergenceView.as_view(), name="convergence"),
    path("analytics/correlation/", views.CorrelationView.as_view(), name="correlation"),
    path("analytics/spatial/", views.SpatialView.as_view(), name="spatial"),
    path("analytics/index-builder/", views.IndexBuilderView.as_view(), name="index-builder"),
    path("analytics/revisions/", views.RevisionsView.as_view(), name="revisions"),
    # Версии одного наблюдения — фрагментом из таблицы пересмотров.
    path("analytics/revisions/trace/", views.RevisionTraceView.as_view(), name="revision-trace"),
]
