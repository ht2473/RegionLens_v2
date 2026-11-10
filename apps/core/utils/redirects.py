"""Проверка адреса возврата, пришедшего от клиента."""

from __future__ import annotations

from django.http import HttpRequest
from django.utils.http import url_has_allowed_host_and_scheme


def is_local_path(request: HttpRequest, candidate: str) -> bool:
    """
    Адрес — путь на этом сайте: начинается с «/» и не ведёт на чужой узел.

    Одной проверки узла мало: строку без «/» вроде «() { :;};» она пропускает, а
    ``redirect()`` принимает такую строку за имя маршрута и падает с ошибкой сервера.
    """
    return (
        candidate.startswith("/")
        and not candidate.startswith(("//", "/\\"))
        and url_has_allowed_host_and_scheme(
            candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()
        )
    )


def safe_back(request: HttpRequest, fallback: str, field: str = "back") -> str:
    """
    Вернуть адрес возврата из поля ``field``, если он ведёт на этот же сайт, иначе ``fallback``.

    Без проверки ссылка с ``?back=https://чужой.сайт`` делала бы сайт перенаправителем.
    """
    candidate = request.POST.get(field) or request.GET.get(field) or ""
    return candidate if is_local_path(request, candidate) else fallback
