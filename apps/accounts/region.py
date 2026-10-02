"""«Мой регион»: у вошедшего — поле учётной записи, у гостя — cookie с кодом субъекта."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.http import HttpRequest, HttpResponse

if TYPE_CHECKING:  # pragma: no cover - только для проверки типов
    from apps.catalog.models import Territory

REGION_COOKIE = "regionlens_region"
# Регион выбирают надолго; cookie живёт год и продлевается при смене.
REGION_COOKIE_AGE = 365 * 24 * 3600
# Код субъекта — «RU-MOW»; длинное значение cookie не разбирается.
MAX_CODE_LENGTH = 16

_CACHE_ATTRIBUTE = "_my_region"


def find_region(code: str | None) -> Territory | None:
    """Субъект по коду; округа, страна и неизвестные коды — ничего."""
    from apps.catalog.models import Territory

    if not code or len(code) > MAX_CODE_LENGTH:
        return None
    return Territory.objects.comparable().filter(code=code).first()


def my_region(request: HttpRequest) -> Territory | None:
    """Регион посетителя; вычисляется раз на запрос."""
    if hasattr(request, _CACHE_ATTRIBUTE):
        return getattr(request, _CACHE_ATTRIBUTE)
    user: Any = request.user
    region = None
    if user.is_authenticated and user.region_id:
        region = user.region
    if region is None:
        region = find_region(request.COOKIES.get(REGION_COOKIE))
    setattr(request, _CACHE_ATTRIBUTE, region)
    return region


def write_cookie(response: HttpResponse, region: Territory | None) -> None:
    """Записать код региона в cookie или удалить её."""
    if region is None:
        response.delete_cookie(REGION_COOKIE, samesite="Lax")
        return
    response.set_cookie(
        REGION_COOKIE,
        region.code,
        max_age=REGION_COOKIE_AGE,
        samesite="Lax",
        secure=settings.SESSION_COOKIE_SECURE,
        httponly=True,
    )


def remember_region(request: HttpRequest, response: HttpResponse, region: Territory | None) -> None:
    """Запомнить регион в учётной записи (если вошёл) и в cookie; ``None`` — забыть."""
    user: Any = request.user
    if user.is_authenticated:
        user.region = region
        user.save(update_fields=["region", "updated_at"])
    write_cookie(response, region)
    setattr(request, _CACHE_ATTRIBUTE, region)


def adopt_guest_region(
    request: HttpRequest | None = None, user: Any = None, **_kwargs: Any
) -> None:
    """При входе регион, выбранный гостем, переходит в учётную запись, если в ней пусто."""
    if request is None or user.region_id:
        return
    region = find_region(request.COOKIES.get(REGION_COOKIE))
    if region is not None:
        user.region = region
        user.save(update_fields=["region", "updated_at"])


def region_choices() -> list[Territory]:
    """Субъекты для выбора своего региона — по названию на языке страницы."""
    from django.utils.translation import get_language

    from apps.catalog.models import Territory

    order = "name_en" if get_language() == "en" else "name_ru"
    return list(Territory.objects.comparable().order_by(order))
