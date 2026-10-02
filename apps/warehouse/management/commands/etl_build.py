"""
Сборка аналитического склада: ``--full``, ``--marts``, ``--check``, ``--source``, ``--run``.

Новый файл заменяет рабочий только после успешной сборки. Без ``--source`` берётся набор,
из которого собран действующий склад.
"""

from __future__ import annotations

import time
from argparse import ArgumentParser
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.warehouse import builds
from apps.warehouse.duckdb_client import writable_connection
from apps.warehouse.etl import source
from apps.warehouse.etl.pipeline import EtlError, Pipeline
from apps.warehouse.models import EtlRun


class Command(BaseCommand):
    """Запустить ETL-конвейер."""

    help = "Собирает аналитический склад DuckDB из исходного набора данных Росстата"

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Описать параметры командной строки."""
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument(
            "--full",
            action="store_true",
            help="Полная сборка склада (режим по умолчанию)",
        )
        mode.add_argument(
            "--marts",
            action="store_true",
            help="Пересчитать только витрины покрытия, статистик и рангов",
        )
        mode.add_argument(
            "--check",
            action="store_true",
            help="Проверить исходный файл и вывести его профиль, ничего не записывая",
        )
        mode.add_argument(
            "--run",
            type=int,
            default=None,
            help="Выполнить сборку, начатую из панели управления (номер запуска)",
        )
        parser.add_argument(
            "--source",
            type=Path,
            default=None,
            help="Путь к исходному файлу parquet",
        )
        parser.add_argument(
            "--target",
            type=Path,
            default=None,
            help="Путь к создаваемому файлу склада",
        )

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ARG002
        """Точка входа команды."""
        source_path: Path = options["source"] or builds.current_source_path()
        target_path: Path = options["target"] or settings.DUCKDB_PATH

        if options["check"]:
            self._check(source_path)
            return

        if options["run"] is not None:
            # Запуск из панели: исход пишется в сам запуск.
            outcome = builds.run_queued(options["run"], progress=self._report)
            self.stdout.write(f"Сборка по запуску {options['run']}: {outcome}")
            return

        started = time.perf_counter()
        if options["marts"]:
            result = self._rebuild_marts(source_path, target_path)
        else:
            run = EtlRun.objects.create(
                mode=EtlRun.Mode.FULL,
                status=EtlRun.Status.QUEUED,
                source_path=str(source_path),
            )
            try:
                result = builds.execute(run, target_path=target_path, progress=self._report)
            except builds.BuildBusyError as error:
                run.finish(status=EtlRun.Status.CANCELLED, error=str(error))
                raise CommandError(
                    "Идёт другая сборка склада (например, запущенная из панели управления). "
                    "Дождитесь её окончания."
                ) from error
            except EtlError as error:
                raise CommandError(str(error)) from error

        duration = time.perf_counter() - started
        self.stdout.write("")
        self._print_summary(result, duration, target_path)

    def _rebuild_marts(self, source_path: Path, target_path: Path) -> Any:
        """Пересчитать витрины под той же блокировкой, что и полную сборку."""
        run = EtlRun.objects.create(mode=EtlRun.Mode.MARTS, source_path=str(source_path))
        with builds.build_lock() as acquired:
            if not acquired:
                run.finish(status=EtlRun.Status.CANCELLED, error="Идёт другая сборка склада")
                raise CommandError("Идёт другая сборка склада. Дождитесь её окончания.")
            pipeline = Pipeline(
                source_path=source_path,
                target_path=target_path,
                run=run,
                progress=self._report,
            )
            try:
                result = pipeline.rebuild_marts()
            except EtlError as error:
                run.finish(status=EtlRun.Status.FAILED, error=str(error))
                raise CommandError(str(error)) from error
            except Exception as error:  # причина пишется в журнал запуска и пробрасывается
                run.finish(status=EtlRun.Status.FAILED, error=repr(error))
                raise
            run.finish(status=EtlRun.Status.SUCCESS)
        return result

    # -----------------------------------------------------------------------------------
    # Режимы работы
    # -----------------------------------------------------------------------------------

    def _check(self, source_path: Path) -> None:
        """Вывести профиль исходного файла без изменения склада."""
        if not source_path.exists():
            raise CommandError(f"Исходный файл не найден: {source_path}")

        with writable_connection(Path(":memory:")) as connection:
            source.register_source(connection, source_path)
            profile = source.profile_source(connection, source_path)

        self.stdout.write(self.style.MIGRATE_HEADING("Профиль исходного набора данных"))
        rows = (
            ("Файл", profile.path.name),
            ("Размер", f"{profile.size_bytes / 1024 / 1024:.1f} МБ"),
            ("Контрольная сумма", profile.checksum[:16] + "…"),
            ("Версия набора", profile.version_code),
            ("Наблюдений", f"{profile.observation_count:,}".replace(",", " ")),
            ("Показателей", str(profile.indicator_count)),
            ("Рядов", str(profile.series_count)),
            ("Территорий", str(profile.territory_count)),
            ("Разделов", str(profile.section_count)),
            ("Выпусков изданий", str(profile.edition_count)),
            ("Период", f"{profile.first_year}–{profile.last_year}"),
        )
        for label, value in rows:
            self.stdout.write(f"  {label:.<26} {value}")

        if profile.problems:
            self.stdout.write("")
            self.stdout.write(self.style.ERROR("Замечания:"))
            for problem in profile.problems:
                self.stdout.write(self.style.ERROR(f"  ✗ {problem}"))
            raise CommandError("Исходный файл не пригоден к загрузке")

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("  ✓ Файл пригоден к загрузке"))

    # -----------------------------------------------------------------------------------
    # Вывод
    # -----------------------------------------------------------------------------------

    def _report(self, message: str) -> None:
        """Показать сообщение о ходе сборки."""
        self.stdout.write(f"  → {message}")

    def _print_summary(self, result: Any, duration: float, target_path: Path) -> None:
        """Вывести итоговую сводку сборки."""
        self.stdout.write(self.style.MIGRATE_HEADING("Итоги сборки"))

        values = result.statistics.get("values", {})
        catalog = result.statistics.get("catalog", {})
        findings = result.statistics.get("findings", {})

        rows = [
            ("Длительность", f"{duration:.1f} с"),
            ("Файл склада", str(target_path)),
        ]
        if target_path.exists():
            rows.append(("Размер склада", f"{target_path.stat().st_size / 1024 / 1024:.1f} МБ"))
        if values:
            rows.extend(
                [
                    ("Наблюдений всего", _number(values.get("total", 0))),
                    ("Из них со значением", _number(values.get("observed", 0))),
                    ("Нет данных", _number(values.get("no_data", 0))),
                    ("Значение скрыто", _number(values.get("hidden", 0))),
                ]
            )
        if catalog:
            rows.extend(
                [
                    ("Разделов", str(catalog.get("sections", 0))),
                    ("Показателей", str(catalog.get("indicators", 0))),
                    ("Рядов", str(catalog.get("series", 0))),
                    ("Единиц измерения", str(catalog.get("units", 0))),
                    ("Методических примечаний", str(catalog.get("notes", 0))),
                    ("Разрывов сопоставимости", str(catalog.get("breaks", 0))),
                ]
            )

        for label, value in rows:
            self.stdout.write(f"  {label:.<26} {value}")

        if findings:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING("Замечания к качеству данных"))
            titles = {
                "unit_inconsistency": "Несогласованность единиц измерения",
                "aggregate_mismatch": "Расхождение суммы регионов с итогом",
                "sudden_jump": "Масштабные аномалии значений",
                "gap": "Разрывы в рядах наблюдений",
            }
            for name, count in findings.items():
                self.stdout.write(f"  {titles.get(name, name):.<26} {count}")

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Аналитический склад готов к работе"))


def _number(value: int) -> str:
    """Отформатировать число с пробелом в качестве разделителя разрядов."""
    return f"{value:,}".replace(",", " ")
