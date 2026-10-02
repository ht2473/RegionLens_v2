#!/usr/bin/env python
"""Стандартная утилита управления Django."""

from __future__ import annotations

import os
import sys


def main() -> None:
    """Передать управление системе команд Django."""
    # По умолчанию — настройки разработки; на стенде — DJANGO_SETTINGS_MODULE.
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:  # pragma: no cover - диагностика окружения
        raise ImportError(
            "Django не найден. Убедитесь, что окружение установлено командой `uv sync` "
            "и активировано, либо запускайте команды через `uv run python manage.py ...`."
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
