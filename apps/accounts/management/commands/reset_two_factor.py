"""
Сброс входа с кодом из командной строки стенда.

Нужен, когда телефон и резервные коды потерял единственный администратор: в панели
сбросить некому.
"""

from __future__ import annotations

from argparse import ArgumentParser
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import User
from apps.accounts.security import disable_two_factor


class Command(BaseCommand):
    """Выключить вход с кодом учётной записи и завершить её сеансы."""

    help = "Выключает вход с одноразовым кодом учётной записи и завершает её сеансы"

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Описать параметры командной строки."""
        parser.add_argument("email", help="адрес почты учётной записи")

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ARG002 — контракт команды
        """Найти учётную запись и выключить вход с кодом."""
        user = User.objects.filter(email__iexact=options["email"].strip()).first()
        if user is None:
            raise CommandError("Учётной записи с таким адресом нет")
        if not user.two_factor_enabled:
            self.stdout.write("Вход с кодом уже выключен")
            return
        disable_two_factor(None, user)
        self.stdout.write(
            self.style.SUCCESS(
                f"Вход с кодом для {user.email} выключен, сеансы завершены. "
                "При следующем входе в панель его нужно будет включить заново."
            )
        )
