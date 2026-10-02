"""
Смоук-проверки: каждый маршрут из схемы адресов отвечает, а не падает с ошибкой 5xx.

Перечень маршрутов собирается из схемы, поэтому новая страница попадает сюда сама.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import URLPattern, URLResolver, get_resolver, reverse
from django.urls.exceptions import NoReverseMatch

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

# Пространства имён вне смоук-прогона; api проверяется отдельным набором.
EXCLUDED_NAMESPACES = frozenset({"api"})

# Маршруты, проверяемые отдельно: служебные без склада отвечают отказом, выгрузке нужны
# параметры запроса (test_exports.py), кадрам живой карты — склад (test_showcase.py).
EXCLUDED_NAMES = frozenset(
    {
        "healthz",
        "readyz",
        "robots",
        "exports:data-csv",
        "exports:document",
        "core:home-frames",
    }
)

# Коды «страница жива»: 302 — вход для гостя, 405 — маршрут только для POST.
HEALTHY_CODES = frozenset({200, 302, 405})


def collect_routes() -> list[str]:
    """
    Собрать имена всех маршрутов, вызываемых без аргументов.

    Маршруты с аргументами (карточка показателя, паспорт региона) требуют
    существующих объектов и проверяются в подробных наборах.
    """
    names: list[str] = []

    def walk(resolver: URLResolver, prefix: str) -> None:
        """Обойти схему адресов, собирая имена конечных маршрутов."""
        for entry in resolver.url_patterns:
            if isinstance(entry, URLResolver):
                namespace = entry.namespace or ""
                if namespace in EXCLUDED_NAMESPACES:
                    continue
                walk(entry, f"{prefix}{namespace}:" if namespace else prefix)
            elif isinstance(entry, URLPattern) and entry.name:
                names.append(f"{prefix}{entry.name}")

    walk(get_resolver(), "")

    resolvable: list[str] = []
    for name in sorted(set(names)):
        if name in EXCLUDED_NAMES:
            continue
        try:
            reverse(name)
        except NoReverseMatch:
            # Маршрут требует аргументов: его проверяют подробные наборы.
            continue
        resolvable.append(name)
    return resolvable


ROUTES = collect_routes()


class TestRoutes:
    """Все маршруты без аргументов."""

    def test_route_list_is_not_empty(self) -> None:
        """
        Перечень маршрутов собран.

        Пустой перечень означал бы, что смоук-прогон молча ничего не проверяет.
        """
        assert len(ROUTES) >= 40

    @pytest.mark.parametrize("name", ROUTES)
    def test_route_answers_for_guest(self, client: Client, name: str) -> None:
        """Маршрут отвечает гостю страницей или перенаправлением на вход."""
        response = client.get(reverse(name))
        assert response.status_code in HEALTHY_CODES, name

    @pytest.mark.parametrize("name", ROUTES)
    def test_route_answers_for_administrator(self, admin_panel_client: Client, name: str) -> None:
        """
        Маршрут отвечает и пользователю с полными правами.

        Роль администратора открывает разделы, недоступные гостю, — именно они
        чаще всего и оказываются непроверенными.
        """
        response = admin_panel_client.get(reverse(name))
        assert response.status_code in HEALTHY_CODES, name


class TestServiceEndpoints:
    """Служебные точки, по которым система проверяется извне."""

    def test_liveness_answers_without_dependencies(self, client: Client) -> None:
        """Проверка живости отвечает без обращений к базе и складу."""
        response = client.get("/healthz")

        assert response.status_code == 200
        assert response.content == b"ok"

    def test_readiness_reports_components(self, client: Client) -> None:
        """
        Проверка готовности перечисляет составные части системы.

        Точка используется контейнером и системой наблюдения: она обязана
        отвечать и тогда, когда часть подсистем недоступна.
        """
        response = client.get("/readyz")
        payload = response.json()

        assert response.status_code in {200, 503}
        assert set(payload["checks"]) == {"database", "reference", "warehouse"}

    def test_robots_file_is_served(self, client: Client) -> None:
        """Файл правил обхода отдаётся и указывает на карту сайта."""
        response = client.get("/robots.txt")

        assert response.status_code == 200
        assert "sitemap" in response.content.decode("utf-8").lower()

    def test_sitemap_is_served(self, client: Client) -> None:
        """Карта сайта отдаётся в формате XML."""
        response = client.get("/sitemap.xml")

        assert response.status_code == 200
        assert response["Content-Type"].startswith("application/xml")


class TestPageContract:
    """Общие требования ко всем страницам."""

    @pytest.mark.parametrize(
        "name",
        [
            "core:home",
            "core:about",
            "catalog:indicator-list",
            "maps:choropleth",
            "analytics:index",
            "content:methodology",
        ],
    )
    def test_page_has_breadcrumbs_and_author(self, client: Client, name: str) -> None:
        """На странице есть «хлебные крошки» (кроме витрины) и фамилия автора работы."""
        content = client.get(reverse(name)).content.decode("utf-8")

        assert ("breadcrumbs" in content) is (name != "core:home")
        assert "Кузьмин" in content

    def test_public_pages_are_enough(self, client: Client) -> None:
        """Гостю доступно не менее десяти страниц, считая по схеме адресов без перехода ко входу."""
        public = [name for name in ROUTES if client.get(reverse(name)).status_code == 200]
        assert len(public) >= 10
