"""
Сбор выпусков внешних источников: ``collect <источник>…``, ``--due``, ``--all``.

Новый разобранный выпуск пересобирает склад (кроме ``--no-build``). ``--file`` и ``--url``
кладут в архив названный файл; ``--reparse`` разбирает архив заново после правки разбора.
С ``--due`` и ``--all`` заодно проверяется, не вышла ли новая версия набора; новая
скачивается и проверяется сборкой рядом с рабочим складом.
"""

from __future__ import annotations

from argparse import ArgumentParser
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.sources import archive, collect, dataset_watch


class Command(BaseCommand):
    """Собрать выпуски источников."""

    help = "Получает новые выпуски источников в архив, разбирает их и пересобирает склад"

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Описать параметры командной строки."""
        parser.add_argument(
            "sources",
            nargs="*",
            help=f"коды источников: {', '.join(collect.SOURCES)}",
        )
        parser.add_argument("--all", action="store_true", help="все источники")
        parser.add_argument(
            "--due", action="store_true", help="все источники, которые пора проверить"
        )
        parser.add_argument("--file", type=Path, default=None, help="положить в архив этот файл")
        parser.add_argument("--url", default="", help="получить выпуск по этому адресу")
        parser.add_argument(
            "--reparse", action="store_true", help="разобрать заново все выпуски из архива"
        )
        parser.add_argument(
            "--no-build", action="store_true", help="не пересобирать склад после разбора"
        )
        parser.add_argument("--verify", action="store_true", help="сверить архив с описью и выйти")

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ARG002
        """Точка входа команды."""
        if options["verify"]:
            self._verify()
            return

        codes: list[str] = options["sources"]
        if options["all"] or options["due"]:
            codes = list(collect.SOURCES)
        unknown = [code for code in codes if code not in collect.SOURCES]
        if unknown or not codes:
            raise CommandError(
                f"Укажите источник: {', '.join(collect.SOURCES)}, --all или --due"
                + (f"; неизвестны: {', '.join(unknown)}" if unknown else "")
            )
        if (options["file"] or options["url"]) and len(codes) != 1:
            raise CommandError("--file и --url относятся к одному источнику")
        if options["file"] is not None and not options["file"].exists():
            raise CommandError(f"Файл не найден: {options['file']}")

        try:
            outcomes = collect.run(
                codes,
                due_only=options["due"],
                reparse=options["reparse"],
                file=options["file"],
                url=options["url"],
                progress=self._report,
            )
        except collect.CollectBusyError as error:
            raise CommandError(str(error)) from error

        if options["all"] or (options["due"] and dataset_watch.is_due()):
            self._check_dataset()

        if not outcomes:
            self.stdout.write("Проверять пока нечего: источники проверены недавно")
        for outcome in outcomes:
            state = self.style.SUCCESS("✓") if outcome.ok else self.style.ERROR("✗")
            self.stdout.write(
                f"  {state} {outcome.source}: получено {len(outcome.fetched)}, "
                f"разобрано {len(outcome.parsed)}, с ошибкой {len(outcome.failed)}"
                + (f" — {outcome.error}" if outcome.error else "")
            )

        if any(outcome.parsed for outcome in outcomes) and not options["no_build"]:
            self._report("Пересборка склада")
            if not collect.rebuild_warehouse(self._report):
                raise CommandError("Склад не пересобран; действует прежний")
        if not all(outcome.ok for outcome in outcomes):
            raise CommandError("Сбор завершён с ошибками; подробности — в журнале выпусков")

    def _check_dataset(self) -> None:
        """
        Сверить версию набора на сайте с загруженной; новую — скачать и проверить сборкой.

        Отказ сайта или неудачная проверка сбор не прерывают.
        """
        beat = dataset_watch.check()
        detail = beat.detail or {}
        if not beat.ok:
            self._report(f"набор: проверка не удалась — {detail.get('error', '')}")
        elif dataset_watch.newer_version():
            self._report(f"набор: на сайте версия {detail['found']}, загружена {detail['current']}")
            try:
                run = dataset_watch.offer_newer(self._report)
            except Exception as error:
                # Причина записана в запуске проверки, администратору ушло письмо; сбор
                # выпусков и пересборка склада продолжаются.
                self._report(f"набор: проверка новой версии не удалась — {error!r}")
            else:
                if run is not None:
                    self._report(f"набор: проверка № {run.pk} — {run.get_status_display()}")
        else:
            self._report(f"набор: новых версий нет ({detail['current']})")

    def _verify(self) -> None:
        """Сверить архив с описью."""
        problems = archive.verify()
        for problem in problems:
            self.stdout.write(self.style.ERROR(f"  ✗ {problem}"))
        if problems:
            raise CommandError("Архив не сходится с описью")
        count = len(archive.entries())
        self.stdout.write(self.style.SUCCESS(f"Архив сходится с описью: {count} файлов"))

    def _report(self, message: str) -> None:
        self.stdout.write(f"  → {message}")
