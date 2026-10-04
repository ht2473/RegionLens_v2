"""Ссылки на набор данных и на расчёт по ГОСТ Р 7.0.108-2022 (электронные документы в сети)."""

from __future__ import annotations

from django.conf import settings
from django.utils import timezone
from django.utils.translation import get_language, gettext

from apps.catalog.versions import current_version_code


def source_reference() -> str:
    """Ссылка на набор данных — одна для страницы «О наборе» и для описания schema.org."""
    return (
        f"«{settings.DATA_SOURCE_TITLE}» // {gettext(settings.DATA_SOURCE_ORIGIN)}; "
        f"{gettext('обработка')}: «{settings.DATA_SOURCE_PROCESSOR}». "
        f"URL: {settings.DATA_SOURCE_URL}"
    )


def _dataset_part() -> str:
    """Набор данных с версией, на которой выполнен расчёт."""
    return (
        f"{gettext('Набор данных')}: «{settings.DATA_SOURCE_TITLE}» / "
        f"{gettext(settings.DATA_SOURCE_ORIGIN)}; {gettext('обработка')}: "
        f"«{settings.DATA_SOURCE_PROCESSOR}», {gettext('версия')} "
        f"{current_version_code()}."
    )


def _series_source(series_key: str, year: int | str) -> tuple[str, str]:
    """
    Издатель и завершающая часть ссылки для ряда: набор с версией или выпуск источника.

    Версия набора нужна только значениям из набора: у рядов Банка России и ФНС и у лет,
    продолженных по изданиям Росстата, значения взяты из названного выпуска.
    """
    from apps.catalog.provenance import origin_of_year, source_title
    from apps.sources.collect import SOURCES
    from apps.sources.registry import registry
    from apps.warehouse.routing import is_user_key

    if is_user_key(series_key):
        from apps.userdata.series import user_series

        found = user_series(series_key)
        if found is not None:
            return found.publisher(), found.source_line()

    item = registry().by_key.get(series_key)
    if item is not None:
        module = SOURCES[item.source]
        publisher = module.publisher_en if get_language() == "en" else module.publisher_ru
        source = f"{gettext('Источник')}: {source_title(item.source)}. URL: {module.page_url}"
        return publisher, source

    origin = origin_of_year(series_key, int(year)) if year else None
    if origin is not None:
        return gettext(settings.DATA_SOURCE_ORIGIN), f"{gettext('Источник')}: {origin.source_text}."
    return gettext(settings.DATA_SOURCE_ORIGIN), _dataset_part()


def view_citation(*, title: str, url: str, year: int | str = "", series_key: str = "") -> str:
    """Ссылка на расчёт: ряд и год среза, адрес вида, дата обращения и источник значений."""
    subject = f"{title}, {year}" if year else title
    accessed = timezone.localdate().strftime("%d.%m.%Y")
    if series_key:
        publisher, source = _series_source(series_key, year)
    else:
        publisher, source = gettext(settings.DATA_SOURCE_ORIGIN), _dataset_part()

    return (
        f"{subject} : [{gettext('расчёт по данным')}: {publisher}] // "
        f"{settings.PROJECT_NAME} : [{gettext('сайт')}]. — "
        f"URL: {url} ({gettext('дата обращения')}: {accessed}). — {source}"
    )
