"""
Маршруты программного интерфейса под ``/api/v1/`` без языкового префикса.

Версия в адресе позволяет менять формат ответа, не ломая клиентов.
"""

from __future__ import annotations

from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularSwaggerSplitView,
)
from rest_framework.routers import DefaultRouter

from . import views
from .docs import ApiDocsView

app_name = "api"

router = DefaultRouter()
router.register("territories", views.TerritoryViewSet, basename="territory")
router.register("indicators", views.IndicatorViewSet, basename="indicator")
router.register("series", views.SeriesViewSet, basename="series")
router.register("sources", views.SourceViewSet, basename="source")

v1_patterns = [
    path("observations/", views.ObservationListView.as_view(), name="observations"),
    path("monthly/", views.MonthlyObservationListView.as_view(), name="monthly"),
    path("releases/", views.ReleaseListView.as_view(), name="releases"),
    path("rankings/", views.RankingView.as_view(), name="rankings"),
    path("", include(router.urls)),
]

urlpatterns = [
    path("docs/", ApiDocsView.as_view(), name="docs"),
    # Машиночитаемая схема и интерактивная оболочка поверх неё. Сценарий запуска
    # оболочки — отдельным файлом (?script): встроенный сценарий политика содержимого не пропустит.
    path("schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "docs/swagger/",
        SpectacularSwaggerSplitView.as_view(
            url_name="api:schema", template_name="api/swagger_ui.html"
        ),
        name="swagger",
    ),
    path("v1/", include((v1_patterns, "v1"))),
]
