"""Процессоры контекста шаблонов."""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.http import HttpRequest
from django.utils import timezone
from django.utils.functional import SimpleLazyObject
from django.utils.translation import get_language, gettext
from django.utils.translation import gettext_lazy as _

# Переводимые строки из настроек: перечислены здесь, чтобы попасть в каталог сообщений.
TRANSLATABLE_SETTINGS = (
    _("Анализ и визуализация социально-экономических показателей регионов России"),
    _("Приложение для анализа и визуализации социально-экономических показателей регионов РФ"),
    _("Росстат"),
)


def project_metadata(request: HttpRequest) -> dict[str, Any]:  # noqa: ARG001
    """Добавить в контекст шаблона сведения о проекте, авторе и источнике данных."""
    english = get_language() == "en"
    return {
        "project": {
            "name": settings.PROJECT_NAME,
            "tagline": gettext(settings.PROJECT_TAGLINE),
            "version": settings.PROJECT_VERSION,
            # На английских страницах — имя латиницей, иначе — полное русское.
            "author": settings.PROJECT_AUTHOR_EN if english else settings.PROJECT_AUTHOR,
            "author_lang": "en" if english else "ru",
            "author_short": settings.PROJECT_AUTHOR_SHORT,
            "student_id": settings.PROJECT_AUTHOR_STUDENT_ID,
            "author_email": settings.PROJECT_AUTHOR_EMAIL,
            "thesis_title": gettext(settings.PROJECT_THESIS_TITLE),
            "repository_url": settings.PROJECT_REPOSITORY_URL,
            "current_year": timezone.now().year,
        },
        "data_source": {
            # Ссылка на источник — по оригинальному названию; в тексте — на языке страницы.
            "title": settings.DATA_SOURCE_TITLE,
            "title_local": settings.DATA_SOURCE_TITLE_EN if english else settings.DATA_SOURCE_TITLE,
            "title_is_translated": english,
            "origin": gettext(settings.DATA_SOURCE_ORIGIN),
            "processor": settings.DATA_SOURCE_PROCESSOR,
            "url": settings.DATA_SOURCE_URL,
            "license": settings.DATA_SOURCE_LICENSE,
            "version": SimpleLazyObject(_dataset_version),
        },
    }


def _dataset_version() -> str:
    """Версия набора — запрос к базе только если шаблон её выводит."""
    from apps.catalog.versions import current_version_code

    return current_version_code()
