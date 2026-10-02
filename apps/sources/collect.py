"""
Сбор выпусков: найти новые на сайте, положить в архив, разобрать, записать в журнал
и пересобрать склад.

Ошибка получения или разбора остаётся в журнале и не трогает склад: он пересобирается
только тогда, когда появился новый разобранный выпуск.
"""

from __future__ import annotations

import logging
import os
import subprocess  # запускается только собственная команда manage.py
import sys
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from types import ModuleType
from typing import Any

import pandas as pd
from django.conf import settings
from django.db import connection
from django.utils import timezone

from apps.core import alerts
from apps.core.models import ServiceBeat

from . import archive, cbr, fns_sme, network, parsed, rosstat_bulletin, rosstat_grp
from .base import Candidate
from .models import Release, Source
from .sheets import SheetFormatError
from .unpack import UnpackError, unpack

logger = logging.getLogger(__name__)

SOURCES: dict[str, ModuleType] = {
    module.code: module for module in (rosstat_bulletin, rosstat_grp, cbr, fns_sme)
}

# Номер рекомендательной блокировки PostgreSQL для сбора.
COLLECT_LOCK_ID = 7_310_002
# Сколько новых выпусков одного источника брать за проверку; модуль может задать своё
# (``max_per_check``, ``None`` — без предела).
MAX_NEW_PER_CHECK = 2
# Различие значений двух выпусков меньше этого — погрешность записи числа.
SAME_NUMBER = 1e-9


class CollectBusyError(RuntimeError):
    """Другой сбор уже идёт."""


@dataclass(slots=True)
class Outcome:
    """Итог сбора по одному источнику."""

    source: str
    fetched: list[str] = field(default_factory=list)
    parsed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        """Сбор прошёл без ошибок получения и разбора."""
        return not self.error and not self.failed


@contextmanager
def collect_lock() -> Iterator[bool]:
    """Взять блокировку сбора без ожидания; ``True``, если взята."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [COLLECT_LOCK_ID])
        acquired = bool(cursor.fetchone()[0])
    try:
        yield acquired
    finally:
        if acquired:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [COLLECT_LOCK_ID])


def sync_source(code: str) -> Source:
    """Завести или обновить запись источника по его модулю."""
    module = SOURCES[code]
    source, _ = Source.objects.update_or_create(
        code=code,
        defaults={
            "title_ru": module.title_ru,
            "title_en": module.title_en,
            "publisher_ru": module.publisher_ru,
            "publisher_en": module.publisher_en,
            "licence_ru": module.licence_ru,
            "licence_en": module.licence_en,
            "page_url": module.page_url,
            "check_every": module.check_every,
        },
    )
    return source


def run(
    codes: list[str],
    *,
    due_only: bool = False,
    reparse: bool = False,
    file: Path | None = None,
    url: str = "",
    progress: Callable[[str], None] | None = None,
) -> list[Outcome]:
    """Собрать выпуски названных источников; с ``due_only`` — только тех, кому пора."""
    report = progress or logger.info
    outcomes: list[Outcome] = []
    with collect_lock() as acquired:
        if not acquired:
            raise CollectBusyError("Идёт другой сбор источников")
        for code in codes:
            source = sync_source(code)
            if due_only and not source.is_due and not reparse:
                continue
            outcomes.append(
                _collect_one(source, reparse=reparse, file=file, url=url, report=report)
            )
    if outcomes:
        ServiceBeat.record(
            ServiceBeat.Service.COLLECT,
            ok=all(item.ok for item in outcomes),
            sources=[item.source for item in outcomes],
            parsed=[f"{item.source}:{code}" for item in outcomes for code in item.parsed],
            failed=[f"{item.source}:{code}" for item in outcomes for code in item.failed],
            errors=[f"{item.source}: {item.error}" for item in outcomes if item.error],
        )
    return outcomes


def _collect_one(
    source: Source,
    *,
    reparse: bool,
    file: Path | None,
    url: str,
    report: Callable[[str], None],
) -> Outcome:
    module = SOURCES[source.code]
    outcome = Outcome(source=source.code)
    failed_before = source.checked_at is not None and not source.check_ok
    source.running_since = timezone.now()
    source.save(update_fields=["running_since", "updated_at"])
    try:
        if file is not None:
            content = file.read_bytes()
            candidate = module.identify(file.name, url)
            _register(source, candidate, content, None, outcome=outcome, report=report)
        elif url:
            content, remote = network.download(url)
            candidate = module.identify(url.rsplit("/", 1)[-1], url)
            _register(source, candidate, content, remote, outcome=outcome, report=report)
        elif not reparse:
            limit = getattr(module, "max_per_check", MAX_NEW_PER_CHECK)
            for candidate in _new_candidates(source, module, report)[:limit]:
                if hasattr(module, "fetch"):
                    # Выпуск программного интерфейса собирается из нескольких ответов.
                    content, remote = module.fetch(candidate), None
                else:
                    content, remote = network.download(candidate.url)
                _register(source, candidate, content, remote, outcome=outcome, report=report)
        source.check_ok, source.check_error = True, ""
    except (network.FetchError, ValueError, OSError) as error:
        outcome.error = str(error)
        source.check_ok, source.check_error = False, str(error)
        report(f"{source.code}: {error}")
        if failed_before:
            _alert_unreachable(source, str(error))

    pending = (
        source.releases.all()
        if reparse
        else source.releases.exclude(
            status=Release.Status.PARSED, parser_version__gte=module.parser_version
        )
    )
    for release in pending.order_by("reference_year", "published_on", "fetched_at"):
        if parse_release(release, module, report):
            outcome.parsed.append(release.code)
        else:
            outcome.failed.append(release.code)

    source.checked_at = timezone.now()
    source.running_since = None
    source.save(
        update_fields=["checked_at", "check_ok", "check_error", "running_since", "updated_at"]
    )
    return outcome


def _new_candidates(
    source: Source, module: ModuleType, report: Callable[[str], None]
) -> list[Candidate]:
    """Выпуски на сайте, которых нет в журнале или которые изменились на сайте."""
    candidates = module.discover()
    if not candidates and hasattr(module, "next_candidates"):
        # Страница не ответила: прямой адрес выпуска за следующий месяц.
        latest = source.releases.order_by("-reference_year", "-published_on").first()
        if latest is not None:
            candidates = [
                item
                for item in module.next_candidates(latest.code)
                if network.head(item.url).status == 200  # noqa: PLR2004
            ]
    if not candidates:
        report(f"{source.code}: на сайте выпусков не найдено")
        return []

    # Источник, выпуск которого несёт одну дату, собирает историю: все выпуски страницы.
    history = getattr(module, "collect_history", False)
    newest_known = source.releases.order_by("-reference_year", "-published_on").first()
    if newest_known is None and not history:
        # Первый сбор берёт только последний выпуск: прежние догружаются через --url.
        return candidates[:1]
    fresh = []
    for candidate in candidates:
        known = source.releases.filter(code=candidate.release_code).order_by("-fetched_at").first()
        if known is None:
            if not history and newest_known is not None and _older(candidate, newest_known):
                continue
            fresh.append(candidate)
            continue
        if hasattr(module, "fetch"):
            # Выпуск программного интерфейса различается кодом: тот же код — тот же выпуск.
            continue
        remote = network.head(candidate.url)
        changed = remote.size is not None and remote.size != known.size_bytes
        if changed or (
            remote.modified and known.remote_modified and remote.modified > known.remote_modified
        ):
            fresh.append(candidate)
    if not fresh:
        report(f"{source.code}: новых выпусков нет")
    return fresh


def _older(candidate: Candidate, release: Release) -> bool:
    if candidate.reference_year != release.reference_year:
        return candidate.reference_year < release.reference_year
    if candidate.published_on and release.published_on:
        return candidate.published_on < release.published_on
    return False


def _register(
    source: Source,
    candidate: Candidate,
    content: bytes,
    remote: network.RemoteFile | None,
    *,
    outcome: Outcome,
    report: Callable[[str], None],
) -> Release | None:
    """Положить файл в архив и завести выпуск; тот же файл повторно не заводится."""
    file_name = candidate.file_name or Path(candidate.url).name or "release"
    record, is_new = archive.store(source.code, file_name, content, url=candidate.url)
    existing = Release.objects.filter(sha256=record.sha256).first()
    if existing is not None:
        report(f"{source.code}: {existing.code} уже в архиве ({record.path})")
        return existing

    fetched = record.captured
    modified = remote.modified if remote is not None else None
    published = candidate.published_on or (modified.date() if modified else fetched.date())
    code = candidate.release_code or published.isoformat()
    release = Release.objects.create(
        source=source,
        code=code,
        title=candidate.title or f"по состоянию на {published:%d.%m.%Y}",
        reference_year=candidate.reference_year or published.year,
        published_on=published,
        url=candidate.url,
        file_name=file_name,
        archive_path=record.path,
        sha256=record.sha256,
        size_bytes=record.size,
        remote_modified=modified,
        fetched_at=fetched,
    )
    outcome.fetched.append(code)
    note = "" if is_new else ", уже был в архиве"
    report(f"{source.code}: получен выпуск {code} ({_number(record.size)} байт{note})")
    return release


def launch(code: str) -> str:
    """
    Запустить ``collect <источник>`` отделённым процессом; вернуть пустую строку или причину отказа.

    Сбор и пересборка склада идут минуты: страница панели не ждёт их.
    """
    source = sync_source(code)
    if source.is_running:
        return "сбор этого источника уже идёт"
    command = [sys.executable, "-X", "utf8", str(settings.BASE_DIR / "manage.py"), "collect", code]
    detached: dict[str, Any] = (
        {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS}
        if os.name == "nt"
        else {"start_new_session": True}
    )
    try:
        subprocess.Popen(  # noqa: S603 - запускается только собственная команда manage.py
            command,
            cwd=settings.BASE_DIR,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **detached,
        )
    except OSError as error:
        return f"процесс сбора не запустился: {error}"
    source.running_since = timezone.now()
    source.save(update_fields=["running_since", "updated_at"])
    return ""


def rebuild_warehouse(report: Callable[[str], None]) -> bool:
    """Пересобрать склад с новыми выпусками; ``False`` — сборка не удалась, склад прежний."""
    from apps.warehouse import builds
    from apps.warehouse.etl.pipeline import EtlError
    from apps.warehouse.models import EtlRun

    run = EtlRun.objects.create(
        mode=EtlRun.Mode.FULL,
        status=EtlRun.Status.QUEUED,
        source_path=str(builds.current_source_path()),
    )
    run.append_log("Сборка после сбора выпусков источников", save=True)
    try:
        builds.execute(run, progress=report)
    except builds.BuildBusyError:
        run.finish(status=EtlRun.Status.CANCELLED, error="Идёт другая сборка склада")
        report("Склад не пересобран: идёт другая сборка")
        return False
    except EtlError as error:
        report(f"Склад не пересобран: {error}")
        return False
    return True


def parse_release(
    release: Release, module: ModuleType | None = None, report: Callable[[str], None] | None = None
) -> bool:
    """Разобрать выпуск из архива и записать итог в журнал; ``True`` — разобран."""
    module = module or SOURCES[release.source.code]
    say = report or logger.info
    path = archive.archive_root() / release.archive_path
    try:
        with tempfile.TemporaryDirectory(prefix="regionlens-release-") as directory:
            result = module.parse(unpack(path, Path(directory)))
    except (SheetFormatError, UnpackError, OSError, NotImplementedError) as error:
        parsed.remove(release.source.code, release.code, release.sha256)
        release.status = Release.Status.FAILED
        release.error = str(error) or type(error).__name__
        release.parsed_at = timezone.now()
        release.save(update_fields=["status", "error", "parsed_at", "updated_at"])
        say(f"{release.source.code}: выпуск {release.code} не разобран: {release.error}")
        _alert_unparsed(release, module.parser_version)
        return False

    info = parsed.ReleaseInfo(
        source=release.source.code,
        code=release.code,
        title=release.title,
        reference_year=release.reference_year,
        published_on=(release.published_on or release.fetched_at.date()).isoformat(),
        fetched_at=release.fetched_at.isoformat(timespec="seconds"),
        sha256=release.sha256,
        url=release.url,
        parser_version=module.parser_version,
    )
    parsed.write(info, result.frame, result.notes)
    release.status = Release.Status.PARSED
    release.error = ""
    release.parser_version = module.parser_version
    release.parsed_at = timezone.now()
    release.row_count = int(result.frame["value"].notna().sum())
    release.report = result.report
    release.changes = changes_since_previous(release, result.frame)
    release.save()
    count = _number(release.row_count)
    say(f"{release.source.code}: выпуск {release.code} разобран, значений {count}")
    return True


def _alert_unparsed(release: Release, parser_version: int) -> None:
    """Письмо о выпуске, который не разобран: чаще всего источник сменил вид таблицы."""
    alerts.notify(
        f"release:{release.source.code}:{release.code}:{parser_version}:{release.error}",
        f"выпуск {release.code} не разобран",
        f"Источник: {release.source.title_ru}\n"
        f"Выпуск: {release.code}\n"
        f"Причина: {release.error}\n\n"
        "Склад не тронут: сайт показывает прежние данные, без этого выпуска. Обычно это "
        "значит, что источник изменил вид таблицы или ответа: разбор нужно поправить "
        f"и разобрать выпуск заново (collect {release.source.code} --reparse).\n\n"
        f"Выпуск в панели: {alerts.panel_address('dashboard:release', release.pk)}",
    )


def _alert_unreachable(source: Source, error: str) -> None:
    """Письмо об источнике, который не отвечает вторую проверку подряд."""
    alerts.notify(
        f"source:{source.code}",
        f"источник «{source.title_ru}» не отвечает",
        f"Источник: {source.title_ru}\n"
        f"Адрес: {source.page_url}\n"
        f"Ошибка последней проверки: {error}\n\n"
        "Проверка не удалась два раза подряд. Новые выпуски не собираются; сайт работает "
        "на собранных ранее. Если адрес сменился, его нужно поправить в модуле источника.\n\n"
        f"Источники в панели: {alerts.panel_address('dashboard:sources')}",
    )


def changes_since_previous(release: Release, frame: pd.DataFrame) -> dict[str, Any]:
    """Последний период по показателям и число значений, изменённых против прежнего выпуска."""
    latest = {
        measure: _period_label(rows)
        for measure, rows in frame.dropna(subset=["value"]).groupby("measure")
    }
    earlier = [
        (info, path)
        for info, path in parsed.releases(release.source.code)
        if info.sha256 != release.sha256
        and (info.reference_year, info.published_on)
        <= (release.reference_year, (release.published_on or date.min).isoformat())
    ]
    if not earlier:
        return {"latest": latest, "previous": "", "revised": 0}
    previous_info, previous_path = earlier[-1]
    keys = ["measure", "territory_code", "year", "period_kind", "period"]
    joined = frame.merge(parsed.read(previous_path), on=keys, suffixes=("", "_before")).dropna(
        subset=["value", "value_before"]
    )
    revised = int(((joined["value"] - joined["value_before"]).abs() > SAME_NUMBER).sum())
    return {"latest": latest, "previous": previous_info.code, "revised": revised}


def _number(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def _period_label(rows: pd.DataFrame) -> str:
    """Последний период показателя: «2026-06» для месяцев, «2026 IV кв.», «2025»."""
    last = rows.sort_values(["year", "period"]).iloc[-1]
    kind, year, number = last["period_kind"], int(last["year"]), int(last["period"])
    if kind in {"month", "ytd"}:
        return f"{year}-{number:02d}"
    if kind == "quarter":
        return f"{year} Q{number}"
    return str(year)
