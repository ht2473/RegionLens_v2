"""Текущая версия набора данных: из журнала загруженных версий, без него — из настроек."""

from __future__ import annotations

from django.conf import settings

from apps.catalog.models import DatasetVersion


def current_version_code() -> str:
    """Код версии набора, на которой собран склад, например ``v20260313``."""
    code = DatasetVersion.objects.filter(is_current=True).values_list("code", flat=True).first()
    return code or settings.DATA_SOURCE_VERSION
