"""
Порядок этапов сборки склада, замер длительности и журнал.

Склад собирается во временном файле и заменяет рабочий только после всех этапов.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
from django.conf import settings
from django.utils import timezone

from apps.catalog.models import DatasetVersion
from apps.warehouse.duckdb_client import writable_connection
from apps.warehouse.models import EtlRun

from . import (
    catalog_sync,
    compare,
    dimensions,
    facts,
    integrity,
    marts,
    monthly,
    quality,
    releases,
    source,
)

logger = logging.getLogger(__name__)

# Файл со схемой склада.
SCHEMA_PATH = Path(__file__).resolve().parent.parent / "sql" / "schema.sql"

# Длина кода версии набора данных в цифрах: ГГГГММДД.
VERSION_DATE_LENGTH = 8

# Сколько раз и с какой паузой пробовать подменить занятый файл склада (только Windows).
REPLACE_ATTEMPTS = 20
REPLACE_PAUSE = 0.5

# Логические ключи таблиц склада: ограничений PRIMARY KEY в схеме нет.
UNIQUE_KEYS: dict[str, tuple[str, ...]] = {
    "dim_territory": ("territory_code",),
    "dim_section": ("section_code",),
    "dim_unit": ("unit_code",),
    "dim_edition": ("edition_code",),
    "dim_indicator": ("indicator_code",),
    "dim_series": ("series_key",),
    "fact_observation": ("series_key", "territory_code", "year"),
    "fact_month": ("series_key", "territory_code", "year", "month", "kind"),
    "mart_series_coverage": ("series_key",),
    "mart_series_stats": ("series_key", "year"),
    "mart_rank": ("series_key", "year", "territory_code"),
    "mart_revision": ("series_key", "territory_code", "year"),
    "mart_source_link": ("series_key",),
    "mart_source_note": ("series_key", "position"),
    "meta_build": ("key",),
}


class EtlError(RuntimeError):
    """Ошибка выполнения ETL-конвейера."""


@dataclass(slots=True)
class StepResult:
    """Результат выполнения одного этапа."""

    name: str
    duration: float
    detail: Any = None


@dataclass(slots=True)
class PipelineResult:
    """Итог выполнения конвейера."""

    profile: source.SourceProfile
    steps: list[StepResult] = field(default_factory=list)
    statistics: dict[str, Any] = field(default_factory=dict)

    @property
    def total_duration(self) -> float:
        """Суммарная длительность всех этапов, секунд."""
        return sum(step.duration for step in self.steps)

    def as_dict(self) -> dict[str, Any]:
        """Представление для журнала запуска: служебные объекты этапов заменяются ``None``."""
        return {
            "source": self.profile.as_dict(),
            "steps": [
                {
                    "name": step.name,
                    "duration": round(step.duration, 2),
                    "detail": _serializable(step.detail),
                }
                for step in self.steps
            ],
            "statistics": self.statistics,
            "total_duration": round(self.total_duration, 2),
        }


class Pipeline:
    """Последовательность этапов сборки аналитического склада."""

    def __init__(
        self,
        *,
        source_path: Path | None = None,
        target_path: Path | None = None,
        run: EtlRun | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> None:
        self.source_path = source_path or settings.SOURCE_PARQUET_PATH
        self.target_path = target_path or settings.DUCKDB_PATH
        self.run = run
        self._progress = progress or logger.info
        self._steps: list[StepResult] = []

    # -----------------------------------------------------------------------------------
    # Публичный интерфейс
    # -----------------------------------------------------------------------------------

    def build(self) -> PipelineResult:
        """Выполнить полную сборку склада во временный файл рядом с рабочим."""
        if not self.source_path.exists():
            raise EtlError(
                f"Исходный файл не найден: {self.source_path}. "
                "Поместите parquet Росстата в data/raw/ или укажите путь параметром --source."
            )

        temporary_path = self.target_path.with_suffix(".build.duckdb")
        temporary_path.unlink(missing_ok=True)

        with writable_connection(temporary_path) as connection:
            assembled = self._assemble(connection)
            profile = assembled["profile"]

            self._report("Синхронизация справочников")
            catalog_statistics = self._step(
                "catalog", lambda: catalog_sync.sync_all(connection)
            ).detail

            quality_findings: dict[str, int] = {}
            run = self.run
            if run is not None:
                self._report("Проверки качества данных")
                quality_findings = self._step(
                    "quality", lambda: quality.run_checks(connection, run)
                ).detail
                if quality_findings:
                    self._report(
                        "Замечания к данным: "
                        + ", ".join(f"{name} — {count}" for name, count in quality_findings.items())
                    )

        # Замена рабочего файла выполняется после закрытия соединения.
        self._report("Замена рабочего файла склада")
        self.target_path.parent.mkdir(parents=True, exist_ok=True)
        replace_file(temporary_path, self.target_path)

        version = self._register_dataset_version(profile)
        result = PipelineResult(
            profile=profile,
            steps=self._steps,
            statistics={
                "values": assembled["values"],
                "catalog": catalog_statistics,
                "findings": quality_findings,
                "releases": assembled["releases"],
                "source_series": assembled["source_series"],
                "missing_series": assembled["missing_series"],
            },
        )

        self._finalize_run(result, version)
        self._report(f"Сборка завершена за {result.total_duration:.1f} с")
        return result

    def check_candidate(self, *, current_source: Path | None) -> PipelineResult:
        """
        Собрать склад из нового набора и сравнить с рабочим, ничего не меняя.

        Справочники PostgreSQL и рабочий склад не трогаются: собранный файл удаляется,
        остаётся отчёт о различиях. ``current_source`` — набор действующего склада.
        """
        if not self.source_path.exists():
            raise EtlError(f"Исходный файл не найден: {self.source_path}")

        temporary_path = self.target_path.with_suffix(".candidate.duckdb")
        temporary_path.unlink(missing_ok=True)
        try:
            with writable_connection(temporary_path) as connection:
                assembled = self._assemble(connection)
                self._report("Сравнение с рабочим складом")
                report = self._step(
                    "compare",
                    lambda: compare.compare(connection, self.target_path, current_source),
                ).detail
        finally:
            temporary_path.unlink(missing_ok=True)

        report["references"] = assembled["missing_series"]
        profile = assembled["profile"]
        result = PipelineResult(
            profile=profile,
            steps=self._steps,
            statistics={
                "values": assembled["values"],
                "releases": assembled["releases"],
                "source_series": assembled["source_series"],
                "missing_series": assembled["missing_series"],
                "report": report,
            },
        )
        if self.run is not None:
            self.run.source_checksum = profile.checksum
            self.run.observation_count = assembled["values"]["total"]
            self.run.series_count = report["candidate"]["series"]
            self.run.statistics = {**(self.run.statistics or {}), **result.as_dict()}
            self.run.save()
        self._report(f"Проверка завершена за {result.total_duration:.1f} с")
        return result

    def _assemble(self, connection: duckdb.DuckDBPyConnection) -> dict[str, Any]:
        """Общая часть полной сборки и проверки кандидата: от схемы до сверки ссылок."""
        self._report("Подготовка схемы склада")
        self._step("schema", lambda: connection.execute(SCHEMA_PATH.read_text("utf-8")))

        self._report("Чтение исходного набора данных")
        source.register_source(connection, self.source_path)
        profile = self._step(
            "profile",
            lambda: source.profile_source(connection, self.source_path),
        ).detail

        if not profile.is_valid:
            raise EtlError(
                "Исходный набор не прошёл проверку:\n  - " + "\n  - ".join(profile.problems)
            )

        self._report(
            f"Наблюдений: {profile.observation_count:,}, "
            f"показателей: {profile.indicator_count}, "
            f"период: {profile.first_year}–{profile.last_year}".replace(",", " ")
        )

        self._report("Загрузка справочника территорий")
        self._step("territories", lambda: dimensions.load_territories(connection))

        missing = source.unmapped_territories(connection)
        if missing:
            raise EtlError(
                "В исходных данных есть территории, отсутствующие в справочнике:\n  - "
                + "\n  - ".join(missing)
                + "\nДополните data/reference/territories.json и повторите сборку."
            )

        self._report("Построение измерений")
        self._step("sections", lambda: len(dimensions.build_sections(connection)))
        self._step("editions", lambda: len(dimensions.build_editions(connection)))
        self._step("unit_scales", lambda: dimensions.build_unit_scales(connection))
        self._step("series", lambda: len(dimensions.build_series(connection)[0]))

        self._report("Загрузка наблюдений")
        self._step("vintages", lambda: facts.load_vintages(connection))
        self._report("Выпуски внешних источников и сшивка с набором")
        release_statistics = self._step(
            "releases", lambda: releases.load_releases(connection)
        ).detail
        if release_statistics.get("releases"):
            self._report(
                "Выпусков: {releases}, связей рядов: {links} (условных {conditional}), "
                "версий значений: {vintages}".format(**release_statistics)
            )
        self._step("ranks_editions", lambda: facts.rank_editions(connection))
        self._report("Ряды Банка России и ФНС, помесячный слой")
        source_statistics = self._step("source_series", lambda: _source_series(connection)).detail
        if source_statistics.get("series") or source_statistics.get("months"):
            self._report(
                "Рядов источников: {series}, годовых версий: {vintages}, "
                "месячных значений: {months}".format(**source_statistics)
            )
        self._step("observations", lambda: facts.build_observations(connection))
        self._step("revisions", lambda: facts.build_revisions(connection))

        self._report("Расчёт витрин")
        self._step("coverage", lambda: marts.build_coverage(connection))
        self._step("stats", lambda: marts.build_stats(connection))
        self._step("ranks", lambda: marts.build_ranks(connection))
        self._step("counters", lambda: dimensions.refresh_dimension_counters(connection))

        value_quality = facts.observation_quality_summary(connection)
        self._report(
            "Качество значений: наблюдений {observed:,}, "
            "нет данных {no_data:,}, скрыто {hidden:,}".format(**value_quality).replace(",", " ")
        )

        self._write_metadata(connection, profile, value_quality)
        self._step("keys", lambda: check_unique_keys(connection))
        absent_series = self._check_references(connection)
        return {
            "profile": profile,
            "values": value_quality,
            "releases": release_statistics,
            "source_series": source_statistics,
            "missing_series": absent_series,
        }

    def rebuild_marts(self) -> PipelineResult:
        """Пересчитать только витрины — за секунды, после правки порогов или основного набора."""
        if not self.target_path.exists():
            raise EtlError("Склад не собран: пересчёт витрин невозможен")

        with writable_connection(self.target_path) as connection:
            self._report("Пересчёт витрин")
            for table in ("mart_rank", "mart_series_stats", "mart_series_coverage"):
                # Имя таблицы — из кортежа констант.
                connection.execute(f"DELETE FROM {table}")

            self._step("coverage", lambda: marts.build_coverage(connection))
            self._step("stats", lambda: marts.build_stats(connection))
            self._step("ranks", lambda: marts.build_ranks(connection))
            self._step("keys", lambda: check_unique_keys(connection))
            self._step("units", lambda: catalog_sync.sync_units(connection))
            self._step("catalog", lambda: catalog_sync.sync_series(connection))

        return PipelineResult(
            profile=source.SourceProfile(
                path=self.source_path,
                size_bytes=0,
                checksum="",
                observation_count=0,
                indicator_count=0,
                series_count=0,
                territory_count=0,
                section_count=0,
                edition_count=0,
                first_year=0,
                last_year=0,
                version_code="",
            ),
            steps=self._steps,
        )

    # -----------------------------------------------------------------------------------
    # Внутренние операции
    # -----------------------------------------------------------------------------------

    def _step(self, name: str, action: Callable[[], Any]) -> StepResult:
        """Выполнить этап, измерив его длительность."""
        started = time.perf_counter()
        detail = action()
        duration = time.perf_counter() - started

        result = StepResult(name=name, duration=duration, detail=detail)
        self._steps.append(result)
        logger.debug("Этап %s завершён за %.2f с", name, duration)
        return result

    def _check_references(self, connection: duckdb.DuckDBPyConnection) -> list[dict[str, str]]:
        """Сверить ссылки справочников и сохранённого пользователями с рядами склада."""
        known = {
            row[0] for row in connection.execute("SELECT series_key FROM dim_series").fetchall()
        }
        missing = self._step(
            "references", lambda: [item.as_dict() for item in integrity.missing_series(known)]
        ).detail
        if missing:
            self._report(f"Ссылки на ряды, которых нет в складе: {len(missing)}")
        return list(missing)

    def _report(self, message: str) -> None:
        """Передать сообщение о ходе работы вызывающей стороне и в журнал запуска."""
        self._progress(message)
        if self.run is not None:
            self.run.append_log(message, save=True)

    def _write_metadata(
        self,
        connection: Any,
        profile: source.SourceProfile,
        quality: dict[str, int],
    ) -> None:
        """Записать в склад сведения о сборке."""
        metadata = {
            "dataset_version": profile.version_code,
            "source_checksum": profile.checksum,
            "source_path": str(profile.path),
            "built_at": timezone.now().isoformat(),
            "observation_count": str(quality["total"]),
            "first_year": str(profile.first_year),
            "last_year": str(profile.last_year),
            "application_version": settings.PROJECT_VERSION,
        }
        for key, value in metadata.items():
            connection.execute("INSERT INTO meta_build VALUES (?, ?)", [key, value])

    def _register_dataset_version(self, profile: source.SourceProfile) -> DatasetVersion:
        """Зафиксировать версию набора данных, на которой собран склад."""
        published_on = _parse_version_date(profile.version_code)

        DatasetVersion.objects.filter(is_current=True).update(is_current=False)
        version, _ = DatasetVersion.objects.update_or_create(
            code=profile.version_code or "unknown",
            defaults={
                "published_on": published_on,
                "title": settings.DATA_SOURCE_TITLE,
                "publisher": settings.DATA_SOURCE_ORIGIN,
                "processor": settings.DATA_SOURCE_PROCESSOR,
                "source_url": settings.DATA_SOURCE_URL,
                "licence": settings.DATA_SOURCE_LICENSE,
                "file_name": profile.path.name,
                "file_size_bytes": profile.size_bytes,
                "file_checksum": profile.checksum,
                "observation_count": profile.observation_count,
                "first_year": profile.first_year,
                "last_year": profile.last_year,
                "is_current": True,
            },
        )
        return version

    def _finalize_run(self, result: PipelineResult, version: DatasetVersion) -> None:
        """Сохранить итоги сборки в журнале запуска."""
        if self.run is None:
            return

        self.run.dataset_version = version
        self.run.source_path = str(self.source_path)
        self.run.source_checksum = result.profile.checksum
        self.run.observation_count = result.statistics["values"]["total"]
        self.run.series_count = result.statistics["catalog"]["series"]
        self.run.break_count = result.statistics["catalog"]["breaks"]
        self.run.statistics = result.as_dict()
        self.run.save()


def check_unique_keys(connection: duckdb.DuckDBPyConnection) -> int:
    """Проверить единственность логических ключей склада; при повторе прервать сборку."""
    repeated: list[str] = []
    for table, columns in UNIQUE_KEYS.items():
        listed = ", ".join(columns)
        # Имена таблиц и столбцов — из констант модуля.
        row = connection.execute(
            f"SELECT {listed} FROM {table} GROUP BY {listed} HAVING count(*) > 1 LIMIT 1"
        ).fetchone()
        if row is not None:
            repeated.append(f"{table}: {dict(zip(columns, row, strict=True))}")
    if repeated:
        raise EtlError("Повторяющиеся ключи в складе:\n  - " + "\n  - ".join(repeated))
    return len(UNIQUE_KEYS)


def _source_series(connection: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """Ряды внешних источников; сменившийся вид данных прерывает сборку."""
    try:
        statistics = monthly.load_source_series(connection)
    except monthly.SourceSeriesError as error:
        raise EtlError(f"Ряды внешних источников: {error}") from error
    releases.refresh_edition_counters(connection)
    return statistics


def replace_file(source_path: Path, target_path: Path) -> None:
    """
    Подменить рабочий файл склада собранным, атомарно.

    Windows не даёт заменить открытый файл, поэтому попытка повторяется, пока читатели
    его не отпустят.
    """
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            source_path.replace(target_path)
        except PermissionError as error:
            if attempt == REPLACE_ATTEMPTS - 1:
                raise EtlError(
                    f"Файл склада занят другим процессом: {target_path}. В Windows сервер "
                    "разработки держит его открытым — остановите сервер и повторите сборку "
                    "командой etl_build."
                ) from error
            time.sleep(REPLACE_PAUSE)
        else:
            return


def _parse_version_date(code: str) -> date:
    """Извлечь дату публикации из кода версии вида ``v20260313``; иначе — текущая дата."""
    digits = "".join(character for character in code if character.isdigit())
    if len(digits) == VERSION_DATE_LENGTH:
        try:
            return date(int(digits[0:4]), int(digits[4:6]), int(digits[6:8]))
        except ValueError:
            logger.warning("Не удалось разобрать дату версии из кода %r", code)
    return timezone.now().date()


def _serializable(value: Any) -> Any:
    """Привести результат этапа к простым значениям для поля JSON; прочее — ``None``."""
    if isinstance(value, bool | int | float | str) or value is None:
        return value
    if isinstance(value, dict):
        return {str(key): _serializable(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_serializable(item) for item in value]
    return None
