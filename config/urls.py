"""Корневые маршруты: служебные и API без языкового префикса, страницы — с ``/ru/`` и ``/en/``."""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.conf.urls.i18n import i18n_patterns
from django.urls import include, path

# --- Служебные маршруты без языкового префикса --------------------------------------------
urlpatterns: list[Any] = [
    # Проверки состояния, карта сайта, robots.txt.
    path("", include("apps.core.urls_service")),
    path("i18n/", include("django.conf.urls.i18n")),
    # Адрес точки данных не зависит от языка.
    path("api/", include("apps.api.urls")),
]

# --- Пользовательские маршруты с языковым префиксом ------------------------------------------
urlpatterns += i18n_patterns(
    path("", include("apps.core.urls")),
    path("", include("apps.catalog.urls")),
    path("", include("apps.maps.urls")),
    path("", include("apps.rankings.urls")),
    path("", include("apps.compare.urls")),
    path("", include("apps.surface.urls")),
    path("", include("apps.analytics.urls")),
    path("", include("apps.accounts.urls")),
    path("", include("apps.workspace.urls")),
    path("", include("apps.exports.urls")),
    path("", include("apps.content.urls")),
    path("", include("apps.feedback.urls")),
    path("", include("apps.dashboard.urls")),
    path("", include("apps.userdata.urls")),
    prefix_default_language=True,
)

# --- Разработка -------------------------------------------------------------------------------
# Панель отладки включается явно (DEBUG_TOOLBAR в local.py).
if settings.DEBUG and "debug_toolbar" in settings.INSTALLED_APPS:  # pragma: no cover
    urlpatterns += [path("__debug__/", include("debug_toolbar.urls"))]
