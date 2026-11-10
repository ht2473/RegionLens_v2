"""
Адрес возврата из формы: только путь на этом сайте. Строка, которая не адрес, раньше
уходила в ``redirect()`` и роняла запрос ошибкой сервера (найдено сканером OWASP ZAP).
"""

from __future__ import annotations

import pytest
from django.test import Client, RequestFactory
from django.urls import reverse

from apps.core.utils.redirects import is_local_path, safe_back

SHELLSHOCK = "() { :;}; /bin/cat /etc/passwd"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("candidate", "local"),
    [
        ("/ru/map/?series=Y477110005:00", True),
        ("/", True),
        (SHELLSHOCK, False),
        ("core:home", False),
        ("ru/map/", False),
        ("//evil.example/ru/", False),
        ("/\\evil.example", False),
        ("https://evil.example/ru/", False),
        ("javascript:alert(1)", False),
        ("", False),
    ],
)
def test_only_site_paths_are_local(candidate: str, local: bool) -> None:
    request = RequestFactory().post("/", {"back": candidate})

    assert is_local_path(request, candidate) is local
    assert safe_back(request, "/fallback/") == (candidate if local else "/fallback/")


@pytest.mark.integration
@pytest.mark.django_db
def test_add_to_study_with_hostile_back_goes_home(client: Client) -> None:
    """«В исследование» со строкой атаки в поле возврата — переход на главную, а не 500."""
    client.raise_request_exception = False
    for _ in range(4):  # и в пределе исследований гостя — тоже переход, а не ошибка
        response = client.post(
            reverse("userdata:study-add"),
            {"study": "new", "target": "maps:choropleth", "back": SHELLSHOCK},
        )
        assert response.status_code == 302
        assert response["Location"] == reverse("core:home")
