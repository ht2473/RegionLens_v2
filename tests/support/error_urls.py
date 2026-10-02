"""Маршруты проверок письма об ошибке 500: страницы, падающие по-разному."""

from __future__ import annotations

from django.http import HttpRequest, HttpResponse
from django.urls import path

from config.urls import urlpatterns as project_urlpatterns


def fail_here(request: HttpRequest, number: int = 0) -> HttpResponse:
    """Ошибка в одном месте кода."""
    raise RuntimeError("сбой расчёта")


def not_ready(request: HttpRequest) -> HttpResponse:
    """Ответ 503 без исключения — как у /readyz без склада."""
    return HttpResponse("not ready", status=503)


def fail_elsewhere(request: HttpRequest) -> HttpResponse:
    """Ошибка того же вида в другом месте."""
    raise RuntimeError("другой сбой")


# Маршруты проекта — за падающими: страница 500 и панель ссылаются на них по именам.
urlpatterns = [
    path("fail/", fail_here),
    path("fail/<int:number>/", fail_here),
    path("fail-elsewhere/", fail_elsewhere),
    path("not-ready/", not_ready),
    *project_urlpatterns,
]
