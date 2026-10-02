#!/usr/bin/env python
"""
Точка входа: проверка окружения, первоначальная подготовка и сервер разработки.

Запуск: ``main.py`` (сервер на :8000), ``--port N``, ``--check``, ``--setup``.
"""

from __future__ import annotations

import argparse
import os
import subprocess  # запускаются только собственные команды manage.py
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# Проверки готовности окружения: файл, подсказка при его отсутствии.
REQUIRED_FILES: tuple[tuple[Path, str], ...] = (
    (
        BASE_DIR / ".env",
        "Файл .env не найден. Создайте его командой: python main.py --setup "
        "— она заполнит его из образца и поставит свежий секретный ключ.",
    ),
)


def run_manage(*args: str) -> int:
    """Выполнить команду manage.py в текущем интерпретаторе и вернуть код возврата."""
    command = [sys.executable, str(BASE_DIR / "manage.py"), *args]
    print(f"→ {' '.join(args)}")
    return subprocess.call(command, cwd=BASE_DIR)  # noqa: S603


def check_environment() -> bool:
    """Проверить наличие обязательных файлов и доступность зависимостей."""
    ok = True

    for path, hint in REQUIRED_FILES:
        if not path.exists():
            print(f"[!] {hint}")
            ok = False

    try:
        import django  # noqa: F401
    except ImportError:
        print(
            "[!] Зависимости не установлены. Выполните: uv sync\n"
            "    и запускайте приложение через: uv run python main.py"
        )
        ok = False

    source_parquet = BASE_DIR / "data" / "raw" / "data_regions_collection_102_v20260313.parquet"
    if not source_parquet.exists():
        print(
            f"[!] Исходный набор данных не найден: {source_parquet}\n"
            "    Поместите файл parquet в data/raw/ — он является входом ETL-конвейера."
        )
        ok = False

    warehouse = BASE_DIR / "data" / "warehouse" / "regionlens.duckdb"
    if not warehouse.exists():
        print("[i] Аналитический склад ещё не собран. Выполните: python manage.py etl_build --full")

    # Недоступная база — частая причина отказа на новой машине: называется с подсказкой.
    if ok:
        ok = check_database() and ok
    if ok:
        check_valkey()

    if ok:
        print("[+] Окружение готово к запуску.")
    return ok


def check_database() -> bool:
    """Проверить, отвечает ли база данных приложения."""
    try:
        import django

        django.setup()
        from django.db import connection

        connection.ensure_connection()
    except Exception as error:  # причина важна: она и есть подсказка
        print(
            f"[!] База данных недоступна: {error}\n"
            "    Поднимите её: docker compose -f docker/compose.dev.yml up -d"
        )
        return False
    return True


def check_valkey() -> bool:
    """Проверить, отвечает ли Valkey; без него сайт работает медленнее — это предупреждение."""
    from django.conf import settings

    try:
        import redis

        redis.Redis.from_url(settings.VALKEY_URL, socket_connect_timeout=1).ping()
    except Exception as error:  # причина важна: она и есть подсказка
        print(
            f"[!] Valkey недоступен: {error}\n"
            "    Кэш не работает. Поднимите его:\n"
            "    docker compose -f docker/compose.dev.yml up -d"
        )
        return False
    return True


def ensure_env_file() -> bool:
    """Создать ``.env`` из образца со свежим секретным ключом; существующий файл не трогается."""
    target = BASE_DIR / ".env"
    if target.exists():
        return True

    example = BASE_DIR / ".env.example"
    if not example.exists():
        print("[!] Не найден .env.example — создайте .env вручную.")
        return False

    import secrets

    lines = example.read_text(encoding="utf-8").splitlines()
    key = secrets.token_urlsafe(64)
    lines = [
        f"DJANGO_SECRET_KEY={key}" if line.startswith("DJANGO_SECRET_KEY=") else line
        for line in lines
    ]
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"→ создан .env со свежим секретным ключом ({target})")
    return True


def setup_project() -> int:
    """
    Подготовить проект к первому запуску: миграции, справочники, склад, содержимое, переводы.

    Всё, кроме сборки склада, идемпотентно; уже собранный склад не пересобирается.
    """
    if not ensure_env_file():
        return 1

    warehouse = BASE_DIR / "data" / "warehouse" / "regionlens.duckdb"

    steps: tuple[tuple[str, ...], ...] = (
        ("migrate", "--noinput"),
        ("seed_reference",),
    )
    if warehouse.exists():
        print(f"[i] Склад уже собран ({warehouse.name}), сборка пропущена.")
        print("    Пересобрать принудительно: python manage.py etl_build --full")
    else:
        steps += (("etl_build", "--full"),)

    steps += (
        # Тексты методики и глоссария.
        ("seed_content",),
        ("seed_content_en",),
        # Английские названия справочников: переводы данных — в самих записях.
        ("seed_catalog_names",),
    )

    for step in steps:
        code = run_manage(*step)
        if code != 0:
            print(f"[!] Шаг «{step[0]}» завершился с ошибкой (код {code}). Подготовка прервана.")
            return code

    # Каталог перевода — своим сценарием, без GNU gettext (scripts/i18n_tools.py).
    print("→ сборка каталогов перевода")
    subprocess.call(  # noqa: S603
        [sys.executable, str(BASE_DIR / "scripts" / "i18n_tools.py"), "compile"],
        cwd=BASE_DIR,
    )

    print("\n[+] Подготовка завершена. Запустите приложение: python main.py")
    print("    Учётная запись администратора: python manage.py createsuperuser")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Собрать разбор аргументов командной строки."""
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Запуск веб-приложения RegionLens",
    )
    parser.add_argument("--host", default="127.0.0.1", help="адрес прослушивания")
    parser.add_argument("--port", default="8000", help="порт прослушивания")
    parser.add_argument("--check", action="store_true", help="только проверить окружение")
    parser.add_argument(
        "--setup",
        action="store_true",
        help="применить миграции, загрузить справочники и собрать аналитический склад",
    )
    return parser


def main() -> int:
    """Точка входа."""
    args = build_parser().parse_args()
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")

    print("RegionLens — анализ социально-экономических показателей регионов России")
    print("Автор: Кузьмин Евгений Олегович\n")

    if args.check:
        return 0 if check_environment() else 1

    if args.setup:
        return setup_project()

    if not check_environment():
        print("\n[!] Устраните замечания выше и повторите запуск.")
        return 1

    print(f"\nСервер разработки: http://{args.host}:{args.port}/\n")
    return run_manage("runserver", f"{args.host}:{args.port}")


if __name__ == "__main__":
    sys.exit(main())
