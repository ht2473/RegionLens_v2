"""Единая форма ответа об ошибке: ``code``, ``detail`` и сведения о полях в ``fields``."""

from __future__ import annotations

from typing import Any

from django.http import Http404
from rest_framework import exceptions
from rest_framework.response import Response
from rest_framework.views import exception_handler


def api_exception_handler(exc: Exception, context: dict[str, Any]) -> Response | None:
    """Привести ответ об ошибке к единому виду."""
    response = exception_handler(exc, context)
    if response is None:
        return None

    detail = response.data
    fields: dict[str, Any] = {}

    if isinstance(detail, dict) and "detail" in detail:
        message = str(detail["detail"])
    elif isinstance(detail, dict):
        message = "Проверьте параметры запроса"
        fields = {
            key: [str(item) for item in value] if isinstance(value, list) else [str(value)]
            for key, value in detail.items()
        }
    elif isinstance(detail, list):
        message = "; ".join(str(item) for item in detail)
    else:
        message = str(detail)

    payload: dict[str, Any] = {
        "error": message,
        "status": response.status_code,
        # Http404 Django (объект не найден по коду) своего кода не несёт.
        "code": "not_found" if isinstance(exc, Http404) else getattr(exc, "default_code", "error"),
    }
    if fields:
        payload["fields"] = fields
    if isinstance(exc, exceptions.Throttled) and exc.wait is not None:
        payload["retry_after"] = int(exc.wait)

    response.data = payload
    return response
