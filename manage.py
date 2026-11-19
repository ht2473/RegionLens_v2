#!/usr/bin/env python
"""Стандартная утилита управления Django."""

from __future__ import annotations

import os
import sys


def utf8_output() -> None:
    """
    Писать вывод команд в UTF-8, если он перенаправлен в файл или канал.

    Консоль Windows и так получает UTF-8, а перенаправленный вывод — кодировку системы
    (cp1251): на «→» или «✓» в сообщениях команды он обрывался бы ошибкой кодирования.
    """
    for stream in (sys.stdout, sys.stderr):
        encoding = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
        if encoding != "utf8" and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def main() -> None:
    """Передать управление системе команд Django."""
    utf8_output()
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
