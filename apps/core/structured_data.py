"""Описание страниц для машин (schema.org, JSON-LD): сайт, набор данных целиком и ряд."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.http import HttpRequest
from django.urls import reverse
from django.utils.translation import get_language, gettext

from apps.catalog.versions import current_version_code
from apps.core.citation import source_reference

if TYPE_CHECKING:  # pragma: no cover - только для проверки типов
    from apps.catalog.models import Series

# Лицензия адресом: по нему машина сверяет условия.
LICENCE_URL = "https://creativecommons.org/licenses/by/4.0/"

# Охват названием: рамка координат вокруг России пересекла бы 180-й меридиан.
SPATIAL_COVERAGE = "Российская Федерация"


def _publisher() -> dict[str, Any]:
    """Правообладатель исходных сведений."""
    return {
        "@type": "GovernmentOrganization",
        "name": gettext(settings.DATA_SOURCE_ORIGIN),
        "url": settings.DATA_SOURCE_URL,
    }


def _processor() -> dict[str, Any]:
    """Тот, кто привёл исходный сборник к машиночитаемому виду."""
    return {"@type": "Organization", "name": settings.DATA_SOURCE_PROCESSOR}


def _catalog(request: HttpRequest) -> dict[str, Any]:
    """Каталог, в котором лежит набор."""
    return {
        "@type": "DataCatalog",
        "name": settings.PROJECT_NAME,
        "url": request.build_absolute_uri(reverse("catalog:dataset")),
    }


def _distributions(request: HttpRequest, api_url: str = "") -> list[dict[str, Any]]:
    """Способы получить данные: наблюдения через программный интерфейс и его описание."""
    items: list[dict[str, Any]] = []
    if api_url:
        items.append(
            {
                "@type": "DataDownload",
                "name": gettext("Наблюдения через программный интерфейс"),
                "encodingFormat": "application/json",
                "contentUrl": request.build_absolute_uri(api_url),
            }
        )
    items.append(
        {
            "@type": "DataDownload",
            "name": gettext("Описание программного интерфейса"),
            "encodingFormat": "application/json",
            "contentUrl": request.build_absolute_uri(reverse("api:schema")),
        }
    )
    return items


def _base(request: HttpRequest, *, name: str, description: str, url: str) -> dict[str, Any]:
    """Общая часть описания любого набора."""
    return {
        "@context": "https://schema.org",
        "@type": "Dataset",
        "name": name,
        "description": description,
        "url": request.build_absolute_uri(url),
        "inLanguage": ["ru", "en"],
        "isAccessibleForFree": True,
        "license": LICENCE_URL,
        "creator": _publisher(),
        "publisher": _processor(),
        "spatialCoverage": SPATIAL_COVERAGE,
        "includedInDataCatalog": _catalog(request),
        "citation": source_reference(),
    }


def collection(
    request: HttpRequest,
    *,
    first_year: int | None = None,
    last_year: int | None = None,
    observations: int | None = None,
) -> dict[str, Any]:
    """Описание набора данных целиком."""
    description = gettext(
        "Социально-экономические показатели субъектов Российской Федерации по данным "
        "Росстата, Банка России и ФНС России: значения по годам, пропуски, разрывы "
        "сопоставимости и пересмотры официальных оценок между выпусками."
    )
    if observations:
        description = f"{description} {gettext('Наблюдений в наборе')}: {observations}."

    data = _base(
        request,
        name=settings.DATA_SOURCE_TITLE,
        description=description,
        url=reverse("catalog:dataset"),
    )
    data["alternateName"] = settings.PROJECT_NAME
    data["version"] = current_version_code()
    data["distribution"] = _distributions(request, reverse("api:v1:series-list"))
    data["keywords"] = [
        gettext("регионы России"),
        gettext("социально-экономические показатели"),
        gettext("Росстат"),
        gettext("официальная статистика"),
    ]
    if first_year and last_year:
        data["temporalCoverage"] = f"{first_year}/{last_year}"
    return data


def series_dataset(request: HttpRequest, series: Series, url: str) -> dict[str, Any]:
    """Описание одного ряда наблюдений как набора данных, с единицей измерения."""
    data = _base(
        request,
        name=series.full_title,
        description=gettext(
            "Значения показателя по субъектам Российской Федерации по годам: "
            "динамика, распределение, полнота наблюдений и пересмотры значений "
            "между выпусками."
        ),
        url=url,
    )
    data["variableMeasured"] = {
        "@type": "PropertyValue",
        "name": series.full_title,
        "unitText": series.unit.name if series.unit else "",
    }
    collected = _collected_source(series.key)
    if collected:
        # Лицензия CC BY — у набора; у Банка России и ФНС — открытые сведения без неё.
        data.pop("license", None)
        data.update(collected)
    data["distribution"] = _distributions(
        request, f"{reverse('api:v1:observations')}?series={series.key}"
    )
    if series.first_year and series.last_year:
        data["temporalCoverage"] = f"{series.first_year}/{series.last_year}"
    return data


def _collected_source(series_key: str) -> dict[str, Any]:
    """Издатель ряда проекта (Банк России, ФНС) вместо сборника набора и его лицензии."""
    from apps.sources.collect import SOURCES
    from apps.sources.registry import registry

    item = registry().by_key.get(series_key)
    if item is None:
        return {}
    module = SOURCES[item.source]
    english = get_language() == "en"
    title = module.title_en if english else module.title_ru
    publisher = module.publisher_en if english else module.publisher_ru
    return {
        "creator": {"@type": "GovernmentOrganization", "name": publisher, "url": module.page_url},
        "publisher": {"@type": "Person", "name": settings.PROJECT_AUTHOR},
        "citation": f"{publisher}, «{title}». URL: {module.page_url}",
    }


def site(request: HttpRequest) -> dict[str, Any]:
    """Описание сайта с адресом поиска — тем же, что у быстрого перехода."""
    home = request.build_absolute_uri(reverse("core:home"))
    search = request.build_absolute_uri(reverse("catalog:indicator-list"))
    return {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": settings.PROJECT_NAME,
        "url": home,
        "description": gettext(settings.PROJECT_TAGLINE),
        "inLanguage": ["ru", "en"],
        "publisher": {"@type": "Person", "name": settings.PROJECT_AUTHOR},
        "potentialAction": {
            "@type": "SearchAction",
            "target": {
                "@type": "EntryPoint",
                "urlTemplate": f"{search}?q={{search_term_string}}",
            },
            "query-input": "required name=search_term_string",
        },
    }
