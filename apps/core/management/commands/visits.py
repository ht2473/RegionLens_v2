"""
Пересчёт статистики посещений по журналу Caddy.

На стенде запускается таймером хоста (deploy/systemd/regionlens-visits.timer) раз в час.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.core import visits


class Command(BaseCommand):
    """Перенести новые строки журнала в историю посещений и пересчитать отчёт."""

    help = "Пересчитывает статистику посещений по журналу Caddy (GoAccess)"

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ARG002 — контракт команды
        """Запустить пересчёт и напечатать итог."""
        result = visits.refresh()
        if not result.ok:
            raise CommandError(result.error)
        summary = visits.summarize(visits.read_report())
        if summary is None:
            self.stdout.write(f"Новых строк: {result.lines}; посещений в истории пока нет")
            return
        general = summary["general"]
        self.stdout.write(
            self.style.SUCCESS(
                f"Новых строк: {result.lines}; посетителей: {general['visitors']}; "
                f"запросов: {general['valid']}; последний день: {summary['last_day'] or '—'}"
            )
        )
