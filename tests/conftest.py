"""
Общие приспособления тестов.

Кэш очищается перед каждым тестом, справочник территорий загружается раз на прогон:
изменения тестов откатываются вместе с их транзакциями.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from django.conf import settings as project_settings
from django.core.cache import cache
from django.test import Client
from django.utils import translation


@pytest.fixture(autouse=True)
def clear_caches() -> Iterator[None]:
    """Очистить кэш до и после каждого теста."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def reset_active_language() -> Iterator[None]:
    """
    Вернуть язык по умолчанию после каждого теста.

    ``LocaleMiddleware`` не выключает язык страницы, и следующий ``reverse`` дал бы ``/en/``.
    """
    yield
    translation.activate(project_settings.LANGUAGE_CODE)


@pytest.fixture(scope="session")
def reference_seed(django_db_setup: None, django_db_blocker: Any) -> None:
    """
    Загрузить справочник территорий один раз на весь прогон.

    Запись выполняется вне транзакции отдельного теста, поэтому справочник виден
    всем тестам; собственные изменения теста откатываются как обычно.
    """
    from django.core.management import call_command

    with django_db_blocker.unblock():
        call_command("seed_reference", verbosity=0)


# ---------------------------------------------------------------------------------------
# Аналитический склад
#
# Большая часть системы — карта, рейтинги, сравнение, инструменты анализа,
# отчёты и программный интерфейс — обращается к складу DuckDB. Проверять их на пустом
# складе значит проверять только сообщения «данных нет». Поэтому прогон собирает
# уменьшенный склад из синтетического набора: структура источника та же, объём —
# в сорок раз меньше.
# ---------------------------------------------------------------------------------------


@pytest.fixture(scope="session")
def synthetic_dataset(tmp_path_factory: pytest.TempPathFactory) -> Any:
    """Построить синтетический исходный набор один раз на прогон."""
    from tests.support.synthetic import build_source_file

    path = tmp_path_factory.mktemp("dataset") / "source.parquet"
    return build_source_file(path)


@pytest.fixture(scope="session")
def warehouse_file(
    synthetic_dataset: Any,
    reference_seed: None,
    django_db_blocker: Any,
    tmp_path_factory: pytest.TempPathFactory,
) -> Path:
    """Собрать тестовый склад из синтетического набора один раз; записи в базе откатываются."""
    from tests.support.warehouse import build_warehouse

    target = tmp_path_factory.mktemp("warehouse") / "regionlens.duckdb"
    with django_db_blocker.unblock():
        build_warehouse(synthetic_dataset, target)
    return target


@pytest.fixture
def warehouse(
    db: None,
    settings: Any,
    warehouse_file: Path,
    synthetic_dataset: Any,
    reference_seed: None,
) -> Iterator[Any]:
    """
    Готовый склад и синхронизированный с ним каталог показателей.

    Синхронизация выполняется внутри транзакции теста и откатывается вместе с ней:
    так непустой каталог не влияет на проверки, рассчитанные на пустую базу.
    """
    from apps.warehouse import duckdb_client
    from tests.support.warehouse import sync_catalog

    settings.DUCKDB_PATH = warehouse_file
    # Соединения потока указывают на старый файл склада.
    duckdb_client.close_connections()
    sync_catalog(warehouse_file)
    yield synthetic_dataset
    duckdb_client.close_connections()


# ---------------------------------------------------------------------------------------
# Учётные записи
#
# Большинство проверок доступа требует двух пользователей: того, кому действие
# разрешено, и того, кому оно должно быть запрещено. Поэтому приспособления создают
# пользователей по роли, а не «одного тестового пользователя».
# ---------------------------------------------------------------------------------------

# Пароль тестовых учётных записей. Проверки стойкости в тестовых настройках отключены,
# поэтому значение выбрано читаемым: оно попадает в сообщения об ошибках.
TEST_PASSWORD = "test-password-12345"

# Ключ кодов входа тестовых учётных записей с правами панели.
TEST_TOTP_SECRET = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"


@pytest.fixture
def make_user(db: None) -> Callable[..., Any]:
    """Создать пользователя с ролями; ``two_factor`` — с включённым входом по коду."""

    def factory(
        email: str = "user@example.com",
        roles: tuple[str, ...] = (),
        *,
        two_factor: bool = False,
        **extra: Any,
    ) -> Any:
        from django.utils import timezone

        from apps.accounts.models import User

        user = User.objects.create_user(
            email=email,
            password=TEST_PASSWORD,
            full_name=extra.pop("full_name", "Иванов Иван Иванович"),
            roles=tuple(roles),
            **extra,
        )
        if two_factor:
            user.totp_secret = TEST_TOTP_SECRET
            user.totp_enabled_at = timezone.now()
            user.save(update_fields=["totp_secret", "totp_enabled_at"])
        return user

    return factory


def login_with_code(client: Client, user: Any) -> Client:
    """Войти как после второго шага: сеанс с отметкой пройденного кода."""
    from apps.accounts.security import SECOND_FACTOR_SESSION_KEY

    client.force_login(user)
    session = client.session
    session[SECOND_FACTOR_SESSION_KEY] = True
    session.save()
    return client


@pytest.fixture
def member(make_user: Callable[..., Any]) -> Any:
    """Обычный пользователь."""
    return make_user(email="member@example.com")


@pytest.fixture
def member_client(client: Client, member: Any) -> Client:
    """Клиент, вошедший как обычный пользователь."""
    client.force_login(member)
    return client


@pytest.fixture
def second_admin(make_user: Callable[..., Any]) -> Any:
    """Второй администратор: разбирает обращения и ведёт тексты."""
    from apps.accounts.constants import ROLE_ADMIN

    return make_user(email="admin2@example.com", roles=(ROLE_ADMIN,), two_factor=True)


@pytest.fixture
def administrator(make_user: Callable[..., Any]) -> Any:
    """Пользователь с ролью «Администратор»: полный доступ к панели управления."""
    from apps.accounts.constants import ROLE_ADMIN

    return make_user(email="admin@example.com", roles=(ROLE_ADMIN,), two_factor=True)


@pytest.fixture
def editor(make_user: Callable[..., Any]) -> Any:
    """«Редактор содержимого»: глоссарий, методика и обращения."""
    from apps.accounts.constants import ROLE_EDITOR

    return make_user(email="editor@example.com", roles=(ROLE_EDITOR,), two_factor=True)


@pytest.fixture
def second_admin_client(client: Client, second_admin: Any) -> Client:
    """Клиент, вошедший как второй администратор."""
    return login_with_code(client, second_admin)


@pytest.fixture
def admin_panel_client(client: Client, administrator: Any) -> Client:
    """
    Клиент, вошедший под ролью «Администратор» и прошедший код входа.

    Имя отличается от ``admin_client`` приспособления pytest-django: то входит
    в служебную панель Django, а это — в собственную панель управления проекта.
    """
    return login_with_code(client, administrator)
