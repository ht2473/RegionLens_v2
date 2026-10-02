"""«Мой регион» в контексте шаблонов — для ссылки в шапке; вычисляется при обращении."""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest
from django.utils.functional import SimpleLazyObject

from .region import my_region


def region(request: HttpRequest) -> dict[str, Any]:
    """Регион посетителя или ``None``; запрос к базе — только если шаблон его прочтёт."""
    return {"my_region": SimpleLazyObject(lambda: my_region(request))}
