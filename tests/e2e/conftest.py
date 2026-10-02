"""
Приспособления сквозных проверок: сервер в отдельном потоке и данные, зафиксированные
в базе; без браузеров Playwright набор пропускается.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

# Playwright работает в потоке с циклом событий, где Django запрещает обращения к базе;
# обращения здесь последовательны. Разрешение — до импорта моделей.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "1")

# Каталог, куда складываются снимки экрана при разборе отказов.
ARTIFACTS_DIR = Path(__file__).resolve().parents[2] / "data" / "tmp" / "e2e"


def browser_available(name: str) -> bool:
    """Проверить, установлен ли исполняемый файл браузера."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:  # pragma: no cover - зависит от состава окружения
        return False

    try:
        with sync_playwright() as driver:
            engine = getattr(driver, name)
            handle = engine.launch()
            handle.close()
    except Exception:  # pragma: no cover - браузер не установлен
        return False
    return True


@pytest.fixture(scope="session")
def chromium_available() -> bool:
    """Признак доступности браузера: вычисляется один раз на прогон."""
    return browser_available("chromium")


@pytest.fixture(autouse=True)
def skip_without_browser(chromium_available: bool) -> None:
    """Пропустить сквозные проверки, если браузеры не установлены."""
    if not chromium_available:
        pytest.skip("Браузеры Playwright не установлены: uv run playwright install")


@pytest.fixture
def site(live_server: Any, transactional_db: None, reference_seed_committed: None) -> Any:
    """Работающий экземпляр приложения со справочником территорий."""
    return live_server


@pytest.fixture
def reference_seed_committed(transactional_db: None) -> None:
    """
    Загрузить справочник территорий с фиксацией в базе.

    Сеансовое приспособление ``reference_seed`` здесь непригодно: сквозные проверки
    работают в режиме реальных транзакций, и таблицы очищаются после каждой из них.
    """
    from django.core.management import call_command

    call_command("seed_reference", verbosity=0)


@pytest.fixture
def registered_user(transactional_db: None) -> Any:
    """Учётная запись для сценариев, требующих входа."""
    from apps.accounts.models import User

    user = User.objects.create_user(
        email="e2e@example.com",
        password="e2e-password-12345",
        full_name="Петров Пётр Петрович",
    )
    return user


@pytest.fixture
def warehouse_committed(
    transactional_db: None,
    settings: Any,
    warehouse_file: Path,
    reference_seed_committed: None,
) -> Iterator[Path]:
    """Собранный склад и каталог, зафиксированные в базе для сервера в другом потоке."""
    from apps.warehouse import duckdb_client
    from tests.support.warehouse import sync_catalog

    settings.DUCKDB_PATH = warehouse_file
    # Соединения потока указывают на старый файл склада.
    duckdb_client.close_connections()
    sync_catalog(warehouse_file)
    yield warehouse_file
    duckdb_client.close_connections()


@pytest.fixture
def artifacts_dir() -> Iterator[Path]:
    """Каталог для снимков экрана, сохраняемых сценариями."""
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    yield ARTIFACTS_DIR
