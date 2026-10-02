"""Прогрев кэша после пересборки склада."""

from __future__ import annotations

import logging

from django.conf import settings
from django.test import Client
from django.urls import reverse
from django.utils import translation

logger = logging.getLogger(__name__)

# Частые страницы; их сборка заполняет и кэш расчётов, на которые они опираются.
WARM_PAGES = ("core:home", "maps:choropleth", "rankings:index", "catalog:indicator-list")


def warm_cache() -> int:
    """
    Собрать частые страницы на всех языках; вернуть число собранных.

    Ключи кэша несут отпечаток склада и после его подмены не находятся.
    """
    client = Client(HTTP_HOST="127.0.0.1")
    warmed = 0
    for language, _title in settings.LANGUAGES:
        with translation.override(language):
            for name in WARM_PAGES:
                path = reverse(name)
                response = client.get(path, secure=True)
                if response.status_code == 200:  # noqa: PLR2004
                    warmed += 1
                else:
                    logger.warning("Прогрев %s: ответ %s", path, response.status_code)
    logger.info("Прогрев кэша: собрано страниц — %s", warmed)
    return warmed
