"""
Выборки для панели управления.

Обращения к складу защищены от отказа: из панели склад и пересобирают.
"""

from __future__ import annotations

import logging
import shutil
from datetime import timedelta
from pathlib import Path
from typing import Any

from django.conf import settings
from django.contrib.auth.models import Group
from django.contrib.sessions.models import Session
from django.core.cache import cache
from django.db import connection
from django.db.models import Count, QuerySet
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from apps.accounts.constants import ASSIGNABLE_ROLES, ROLE_USER, ROLES, ROLES_BY_NAME
from apps.accounts.models import User
from apps.catalog.models import DatasetVersion
from apps.content.models import GlossaryTerm, MethodologySection
from apps.core.models import ServiceBeat
from apps.core.search import search_q
from apps.feedback.constants import TicketStatus
from apps.feedback.models import Ticket
from apps.sources import dataset_watch
from apps.sources.models import Release, Source
from apps.warehouse.duckdb_client import WarehouseNotBuiltError
from apps.warehouse.models import DataQualityCheck, EtlRun
from apps.warehouse.queries import warehouse_summary

logger = logging.getLogger(__name__)

# Период, за который считается «недавняя» активность на обзорной странице.
RECENT_DAYS = 7

# Доля занятого места на диске, начиная с которой — тревога.
DISK_WARNING_SHARE = 0.85

# Копия снимается раз в сутки; копия старше полутора суток — тревога.
BACKUP_STALE = timedelta(hours=36)
# Сбор идёт по таймеру ежедневно; двое суток без отметки — таймер не работает.
COLLECT_STALE = timedelta(hours=48)
# Посещения пересчитываются каждый час; три часа без отметки — таймер не работает.
VISITS_STALE = timedelta(hours=3)
# Версия набора проверяется сбором раз в сутки.
DATASET_STALE = timedelta(hours=48)


# ---------------------------------------------------------------------------------------
# Обзор системы
# ---------------------------------------------------------------------------------------


def overview_metrics() -> dict[str, Any]:
    """Сводные показатели для плиток обзорной страницы."""
    since = timezone.now() - timedelta(days=RECENT_DAYS)

    return {
        "users_total": User.objects.count(),
        "users_active": User.objects.filter(is_active=True).count(),
        "users_new": User.objects.filter(created_at__gte=since).count(),
        "users_online": _recent_visitors(),
        "tickets_open": Ticket.objects.open().count(),
        "tickets_total": Ticket.objects.count(),
        "quality_total": latest_checks().count(),
        "quality_errors": latest_checks().filter(severity=DataQualityCheck.Severity.ERROR).count(),
    }


def _recent_visitors() -> int:
    """
    Число пользователей, работавших в системе за последний час.

    По отметке активности: сеанс живёт две недели и после закрытия браузера.
    """
    threshold = timezone.now() - timedelta(hours=1)
    return User.objects.filter(last_seen_at__gte=threshold).count()


def role_distribution() -> list[dict[str, Any]]:
    """Распределение учётных записей по ролям: «Пользователь» — у кого нет ролей панели."""
    total = User.objects.count() or 1
    counts = dict(
        Group.objects.filter(name__in=[role.name for role in ASSIGNABLE_ROLES])
        .annotate(total=Count("user"))
        .values_list("name", "total")
    )
    with_roles = (
        User.objects.filter(groups__name__in=[role.name for role in ASSIGNABLE_ROLES])
        .distinct()
        .count()
    )
    counts[ROLE_USER] = User.objects.count() - with_roles
    return [
        {
            "code": role.name,
            "title": role.label,
            "description": role.description,
            "count": counts.get(role.name, 0),
            "share": counts.get(role.name, 0) / total,
        }
        for role in ROLES
    ]


def warehouse_state() -> dict[str, Any]:
    """Состояние аналитического склада: признак готовности и сводка; отказ — не ошибка."""
    path: Path = settings.DUCKDB_PATH
    state: dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
        "size_mb": round(path.stat().st_size / 1024 / 1024, 1) if path.exists() else 0,
        "summary": None,
        "error": "",
    }

    if not path.exists():
        state["error"] = "Склад не собран. Выполните: python manage.py etl_build --full"
        return state

    try:
        state["summary"] = warehouse_summary()
    except WarehouseNotBuiltError as error:
        state["error"] = str(error)
    except Exception as error:
        logger.warning("Не удалось прочитать сводку склада: %s", error)
        state["error"] = f"Склад недоступен для чтения: {error}"

    return state


def attention_items() -> list[dict[str, Any]]:
    """Перечень того, что требует вмешательства; пустые пункты не показываются."""
    items: list[dict[str, Any]] = []

    waiting = Ticket.objects.open().count()
    if waiting:
        items.append(
            {
                "level": "warning",
                "title": gettext("Обращений ждут ответа: %(count)s") % {"count": waiting},
                "hint": _("Ответ уходит письмом на адрес отправителя"),
                "url_name": "dashboard:ticket-list",
            }
        )

    errors = latest_checks().filter(severity=DataQualityCheck.Severity.ERROR).count()
    if errors:
        items.append(
            {
                "level": "danger",
                "title": gettext("Ошибок качества в последней сборке: %(count)s")
                % {"count": errors},
                "hint": _("Проверьте, не связаны ли они со сменой методики источника"),
                "url_name": "dashboard:quality",
                "query": "severity=error",
            }
        )

    failed = EtlRun.objects.filter(status=EtlRun.Status.FAILED).count()
    if failed:
        items.append(
            {
                "level": "danger",
                "title": gettext("Загрузок, завершившихся с ошибкой: %(count)s")
                % {"count": failed},
                "hint": _("Откройте журнал запуска и устраните причину"),
                "url_name": "dashboard:data",
            }
        )

    broken = Release.objects.filter(status=Release.Status.FAILED).count()
    if broken:
        items.append(
            {
                "level": "danger",
                "title": gettext("Выпусков источников не разобрано: %(count)s") % {"count": broken},
                "hint": _("Вероятно, источник сменил вид таблиц: склад остался прежним"),
                "url_name": "dashboard:sources",
            }
        )

    awaiting = awaiting_candidates()
    newer = dataset_watch.newer_version()
    if awaiting:
        items.append(
            {
                "level": "warning",
                "title": gettext("Проверенная версия набора ждёт решения: %(count)s")
                % {"count": len(awaiting)},
                "hint": _("Посмотрите отчёт о различиях и примите или отклоните версию"),
                "url_name": "dashboard:data",
            }
        )
    elif newer:
        items.append(
            {
                "level": "warning",
                "title": gettext("Вышла новая версия набора данных: %(version)s")
                % {"version": newer},
                "hint": _(
                    "Она будет скачана и проверена при следующем сборе источников; "
                    "загрузить её можно и вручную в разделе «Загрузка данных»"
                ),
                "url_name": "dashboard:data",
            }
        )

    outdated = latest_checks().filter(check_type=DataQualityCheck.CheckType.STATUS_OUTDATED).count()
    if outdated:
        items.append(
            {
                "level": "warning",
                "title": gettext(
                    "У рядов с пометкой «публикация прекращена» появились значения: %(count)s"
                )
                % {"count": outdated},
                "hint": _("Снимите пометку или уточните год в data/reference/series_status.json"),
                "url_name": "dashboard:quality",
                "query": "type=status_outdated",
            }
        )

    if not DatasetVersion.objects.filter(is_current=True).exists():
        items.append(
            {
                "level": "warning",
                "title": _("Не отмечена текущая версия набора данных"),
                "hint": _("Без неё ссылка для цитирования собирается неполно"),
                "url_name": "dashboard:data",
            }
        )

    return items


# ---------------------------------------------------------------------------------------
# Пользователи
# ---------------------------------------------------------------------------------------


def users(query: str = "", role: str = "", state: str = "") -> Any:
    """Выборка учётных записей с фильтрами; поиск — по имени и адресу."""
    queryset = User.objects.prefetch_related("groups").order_by("full_name")

    if query:
        queryset = queryset.filter(search_q(query, "full_name", "email"))
    if role in ROLES_BY_NAME and role != ROLE_USER:
        queryset = queryset.filter(groups__name=role)
    if state == "active":
        queryset = queryset.filter(is_active=True)
    elif state == "blocked":
        queryset = queryset.filter(is_active=False)

    return queryset


def user_card(user: User) -> dict[str, Any]:
    """Сведения об учётной записи для её карточки, включая созданное пользователем."""
    return {
        "saved_queries": user.saved_queries.count(),
        "favorites": user.favorites.count(),
        "tickets": Ticket.objects.for_user(user).count(),
    }


# ---------------------------------------------------------------------------------------
# Данные и качество
# ---------------------------------------------------------------------------------------


def etl_runs(limit: int = 20) -> list[EtlRun]:
    """Последние запуски конвейера загрузки."""
    return list(
        EtlRun.objects.select_related("dataset_version", "started_by").order_by("-started_at")[
            :limit
        ]
    )


def awaiting_candidates() -> list[EtlRun]:
    """Проверенные версии набора, по которым ещё нет решения, — новые первыми."""
    runs = EtlRun.objects.filter(mode=EtlRun.Mode.CANDIDATE, status=EtlRun.Status.SUCCESS).order_by(
        "-started_at"
    )
    return [run for run in runs if run.awaits_decision]


def dataset_versions() -> list[DatasetVersion]:
    """Версии набора данных с числом запусков загрузки по каждой."""
    return list(DatasetVersion.objects.annotate(runs=Count("etl_runs")).order_by("-published_on"))


def latest_checks() -> QuerySet[DataQualityCheck]:
    """Замечания последней сборки склада — текущее состояние данных."""
    latest_run_id = (
        DataQualityCheck.objects.order_by("-created_at").values_list("run_id", flat=True).first()
    )
    return DataQualityCheck.objects.filter(run_id=latest_run_id)


def quality_checks(severity: str = "", check_type: str = "") -> Any:
    """Замечания последней сборки с фильтрами перечня."""
    queryset = latest_checks().select_related("run").order_by("-created_at")

    if severity:
        queryset = queryset.filter(severity=severity)
    if check_type:
        queryset = queryset.filter(check_type=check_type)

    return queryset


def quality_breakdown() -> list[dict[str, Any]]:
    """Разбивка замечаний по типу проверки."""
    rows = latest_checks().values("check_type").annotate(total=Count("id")).order_by("-total")
    labels = dict(DataQualityCheck.CheckType.choices)
    return [
        {
            "code": row["check_type"],
            "title": labels.get(row["check_type"], row["check_type"]),
            "total": row["total"],
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------------------
# Обращения
# ---------------------------------------------------------------------------------------


def tickets(status: str = "", topic: str = "", query: str = "") -> Any:
    """Обращения с фильтрами перечня панели управления."""
    queryset = Ticket.objects.with_related().order_by("-created_at")

    if status == "open":
        queryset = queryset.open()
    elif status:
        queryset = queryset.filter(status=status)

    if topic:
        queryset = queryset.filter(topic=topic)
    if query:
        queryset = queryset.filter(search_q(query, "subject", "body", "contact_email"))

    return queryset


def ticket_statistics() -> dict[str, Any]:
    """Сводка по обращениям: сколько ждут ответа и среднее время до ответа по отвеченным."""
    answered = Ticket.objects.answered().filter(answered_at__isnull=False)
    durations = [
        ticket.response_hours or 0 for ticket in answered.only("created_at", "answered_at")
    ]

    return {
        "total": Ticket.objects.count(),
        "open": Ticket.objects.open().count(),
        "answered": len(durations),
        "closed": Ticket.objects.filter(status=TicketStatus.CLOSED).count(),
        "avg_response_hours": round(sum(durations) / len(durations), 1) if durations else None,
    }


# ---------------------------------------------------------------------------------------
# Состояние системы
# ---------------------------------------------------------------------------------------


def system_health() -> dict[str, Any]:
    """
    Состояние служб; отказ одной проверки не мешает остальным.

    Резервное копирование и почту описывают их отметки (``ServiceBeat``).
    """
    beats = {beat.service: beat for beat in ServiceBeat.objects.all()}
    return {
        "database": _check_database(),
        "cache": _check_cache(),
        "build": _check_last_build(),
        "warehouse": _check_warehouse(),
        "backup": _check_backup(beats.get(ServiceBeat.Service.BACKUP)),
        "mail": _check_mail(beats.get(ServiceBeat.Service.MAIL)),
        "errors": _check_error_reports(),
        "collect": _check_collect(beats.get(ServiceBeat.Service.COLLECT)),
        "visits": _check_visits(beats.get(ServiceBeat.Service.VISITS)),
        "dataset": _check_dataset(beats.get(ServiceBeat.Service.DATASET)),
        "userdata": _check_userdata(),
        "disk": _check_disk(),
    }


def _check_userdata() -> dict[str, Any]:
    """
    Свои данные: число таблиц, место на диске, разборы и отказы за сутки, доски и действующие
    закрытые ссылки. Содержимого таблиц и досок панель не показывает.
    """
    from django.db.models import Q, Sum

    from apps.userdata.models import Board, Dataset, DatasetVersion
    from apps.userdata.shares import active

    title = _("Свои данные")
    since = timezone.now() - timedelta(days=1)
    tables = Dataset.objects.count()
    guests = Dataset.objects.filter(owner__isnull=True).count()
    size = Dataset.objects.aggregate(total=Sum("size_bytes"))["total"] or 0
    recent = DatasetVersion.objects.filter(updated_at__gte=since)
    built = recent.filter(state=DatasetVersion.State.BUILT).count()
    failed = recent.filter(
        Q(state=DatasetVersion.State.FAILED)
        | Q(report__extract_state="failed")
        | Q(report__build_state="failed")
    ).count()
    return {
        "ok": True,
        "title": title,
        "detail": _("таблиц: %(tables)s, из них без входа: %(guests)s; %(size).1f МБ")
        % {"tables": tables, "guests": guests, "size": size / 1024**2},
        "extra": _(
            "за сутки собрано: %(built)s, не разобрано: %(failed)s; досок: %(boards)s, "
            "действующих закрытых ссылок: %(shares)s"
        )
        % {
            "built": built,
            "failed": failed,
            "boards": Board.objects.count(),
            "shares": active().count(),
        },
    }


def _check_last_build() -> dict[str, Any]:
    """Показать последнюю сборку склада и её исход."""
    title = _("Последняя сборка")
    run = EtlRun.objects.filter(mode=EtlRun.Mode.FULL).order_by("-started_at").first()
    if run is None:
        return {"ok": False, "title": title, "detail": _("сборок не было")}
    moment = run.finished_at or run.started_at
    return {
        "ok": run.status != EtlRun.Status.FAILED,
        "title": title,
        "detail": f"{run.get_status_display()} · {timezone.localtime(moment):%d.%m.%Y %H:%M}",
        "extra": run.error_message[:120] if run.status == EtlRun.Status.FAILED else "",
        "url": reverse("dashboard:etl-run", kwargs={"pk": run.pk}),
    }


def _check_backup(beat: ServiceBeat | None) -> dict[str, Any]:
    """Показать последнюю резервную копию, отмеченную ``record_backup``."""
    title = _("Резервная копия")
    if beat is None:
        return {"ok": False, "title": title, "detail": _("копий не отмечено")}
    detail = beat.detail or {}
    size = detail.get("size_bytes")
    extra: list[str] = []
    if size:
        extra.append(str(_("%(size).1f МБ") % {"size": int(size) / 1024**2}))
    extra.append(str(_("вывезена с сервера") if detail.get("offsite") else _("только на сервере")))
    return {
        "ok": beat.ok and timezone.now() - beat.seen_at < BACKUP_STALE,
        "title": title,
        "detail": timezone.localtime(beat.seen_at).strftime("%d.%m.%Y %H:%M"),
        "extra": " · ".join(extra),
    }


def _check_mail(beat: ServiceBeat | None) -> dict[str, Any]:
    """Показать исход последней отправки письма."""
    title = _("Почта")
    backend = str(settings.MAILERS.get("default", {}).get("BACKEND", ""))
    if beat is None:
        return {
            "ok": True,
            "title": title,
            "detail": _("писем ещё не отправлялось"),
            "extra": backend.rsplit(".", 2)[-2] if backend else "",
        }
    moment = timezone.localtime(beat.seen_at).strftime("%d.%m.%Y %H:%M")
    if beat.ok:
        detail = _("последнее письмо принято %(time)s") % {"time": moment}
    else:
        detail = _("письмо не ушло %(time)s") % {"time": moment}
    return {
        "ok": beat.ok,
        "title": title,
        "detail": detail,
        "extra": (beat.detail or {}).get("error", ""),
    }


def _check_error_reports() -> dict[str, Any]:
    """Показать, кто узнает об ошибке 500: адресаты письма и Sentry."""
    title = _("Письма об ошибках")
    sentry = bool(getattr(settings, "SENTRY_DSN", ""))
    if settings.ADMINS:
        return {
            "ok": True,
            "title": title,
            "detail": ", ".join(settings.ADMINS),
            "extra": _("и Sentry") if sentry else _("одна ошибка — не чаще раза в час"),
        }
    if sentry:
        return {"ok": True, "title": title, "detail": _("только Sentry")}
    return {"ok": False, "title": title, "detail": _("адресаты не заданы (DJANGO_ADMINS)")}


def _check_collect(beat: ServiceBeat | None) -> dict[str, Any]:
    """Показать последний сбор выпусков источников и его исход."""
    title = _("Сбор источников")
    url = reverse("dashboard:sources")
    if beat is None:
        return {"ok": False, "title": title, "detail": _("сбора ещё не было"), "url": url}
    detail = beat.detail or {}
    moment = timezone.localtime(beat.seen_at).strftime("%d.%m.%Y %H:%M")
    parsed = detail.get("parsed") or []
    extra = (
        gettext("разобрано выпусков: %(count)s") % {"count": len(parsed)}
        if beat.ok
        else "; ".join([*detail.get("errors", []), *detail.get("failed", [])])[:160]
    )
    return {
        "ok": beat.ok and timezone.now() - beat.seen_at < COLLECT_STALE,
        "title": title,
        "detail": moment,
        "extra": extra,
        "url": url,
    }


def _check_dataset(beat: ServiceBeat | None) -> dict[str, Any]:
    """Показать последнюю проверку версии набора на сайте «Если быть точным»."""
    title = _("Версия набора")
    url = reverse("dashboard:data")
    if beat is None:
        return {"ok": False, "title": title, "detail": _("ещё не проверялась"), "url": url}
    detail = beat.detail or {}
    newer = dataset_watch.newer_version()
    if not beat.ok:
        extra = str(detail.get("error", ""))[:160]
    elif newer:
        extra = gettext("на сайте вышла %(version)s") % {"version": newer}
    else:
        extra = gettext("загружена последняя")
    return {
        "ok": beat.ok and not newer and timezone.now() - beat.seen_at < DATASET_STALE,
        "title": title,
        "detail": timezone.localtime(beat.seen_at).strftime("%d.%m.%Y %H:%M"),
        "extra": extra,
        "url": url,
    }


def _check_visits(beat: ServiceBeat | None) -> dict[str, Any]:
    """Показать последний пересчёт статистики посещений."""
    title = _("Статистика посещений")
    url = reverse("dashboard:visits")
    if beat is None:
        return {"ok": False, "title": title, "detail": _("ещё не пересчитывалась"), "url": url}
    detail = beat.detail or {}
    if beat.ok:
        extra = gettext("посетителей за всё время: %(count)s") % {"count": detail.get("visitors")}
    else:
        extra = str(detail.get("error", ""))[:160]
    return {
        "ok": beat.ok and timezone.now() - beat.seen_at < VISITS_STALE,
        "title": title,
        "detail": timezone.localtime(beat.seen_at).strftime("%d.%m.%Y %H:%M"),
        "extra": extra,
        "url": url,
    }


def _check_database() -> dict[str, Any]:
    """Проверить доступность базы данных."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT version()")
            version = cursor.fetchone()[0]
        return {
            "ok": True,
            "title": _("База данных"),
            "detail": version.split(",")[0],
            "extra": _("сеансов: %(count)s") % {"count": Session.objects.count()},
        }
    except Exception as error:
        return {"ok": False, "title": _("База данных"), "detail": str(error)}


def _check_cache() -> dict[str, Any]:
    """Проверить кэш записью и чтением: по чтению недоступный кэш не отличить от пустого."""
    probe_key = "dashboard:health-probe"
    try:
        cache.set(probe_key, "ok", 10)
        value = cache.get(probe_key)
        return {
            "ok": value == "ok",
            "title": _("Кэш"),
            "detail": str(settings.CACHES["default"]["LOCATION"]),
            "extra": (
                _("запись и чтение выполняются") if value == "ok" else _("значение не читается")
            ),
        }
    except Exception as error:
        return {"ok": False, "title": _("Кэш"), "detail": str(error)}


def _check_warehouse() -> dict[str, Any]:
    """Проверить наличие и читаемость аналитического склада."""
    state = warehouse_state()
    return {
        "ok": state["exists"] and not state["error"],
        "title": _("Аналитический склад"),
        "detail": state["path"],
        "extra": (
            _("%(size)s МБ") % {"size": state["size_mb"]} if state["exists"] else state["error"]
        ),
    }


def _check_disk() -> dict[str, Any]:
    """Проверить свободное место на диске стенда."""
    try:
        usage = shutil.disk_usage(settings.BASE_DIR)
        used_share = usage.used / usage.total
        return {
            "ok": used_share < DISK_WARNING_SHARE,
            "title": _("Дисковое пространство"),
            "detail": _("свободно %(free).1f ГБ из %(total).1f ГБ")
            % {"free": usage.free / 1024**3, "total": usage.total / 1024**3},
            "extra": _("занято %(share).0f %%") % {"share": used_share * 100},
        }
    except Exception as error:
        return {"ok": False, "title": _("Дисковое пространство"), "detail": str(error)}


def content_counts() -> dict[str, Any]:
    """Число записей содержимого для перечней раздела «Содержимое»."""
    return {
        "terms": GlossaryTerm.objects.count(),
        "sections": MethodologySection.objects.count(),
    }


# ---------------------------------------------------------------------------------------
# Источники данных
# ---------------------------------------------------------------------------------------


def sources_overview() -> list[dict[str, Any]]:
    """Источники с последним выпуском и состоянием проверки; записи заводятся по модулям."""
    from apps.sources.collect import SOURCES, sync_source

    found = []
    for code in SOURCES:
        source = sync_source(code)
        found.append(
            {
                "source": source,
                "latest": source.latest_release(),
                "failed": source.releases.filter(status=Release.Status.FAILED).count(),
                "releases": source.releases.count(),
            }
        )
    return found


def release_journal(per_source: int = 12) -> list[Release]:
    """
    Последние выпуски каждого источника и все неразобранные.

    Реестр МСП собирает историю по выпуску на месяц: общий предел вытеснил бы остальных.
    """
    found: dict[int, Release] = {}
    for code in Release.objects.values_list("source__code", flat=True).distinct():
        recent = (
            Release.objects.select_related("source")
            .filter(source__code=code)
            .order_by("-published_on", "-fetched_at")[:per_source]
        )
        found.update({release.pk: release for release in recent})
    failed = Release.objects.select_related("source").filter(status=Release.Status.FAILED)
    found.update({release.pk: release for release in failed})
    return sorted(
        found.values(),
        key=lambda release: (
            release.source.code,
            -(release.published_on or release.fetched_at.date()).toordinal(),
        ),
    )


def source_link_rows() -> list[dict[str, Any]]:
    """Связи рядов с источниками и итог сверки — по витрине склада."""
    from apps.catalog.indicator import series_descriptor
    from apps.catalog.provenance import SourceLink
    from apps.warehouse.queries.sources import source_links

    try:
        rows = source_links()
    except WarehouseNotBuiltError:
        return []
    found = []
    for key, row in rows.items():
        link = SourceLink.from_row(row)
        item = series_descriptor(key)
        found.append(
            {
                "link": link,
                "title": item.short_title if item is not None else key,
                "tolerance": row.get("tolerance"),
                "unit": row.get("deviation_unit"),
                "median": row.get("median_deviation"),
                "maximum": row.get("max_deviation"),
                "worst": row.get("worst_territory"),
                "worst_year": row.get("worst_year"),
                "loaded": row.get("loaded_count"),
            }
        )
    order = {"conditional": 0, "full": 1, "revision": 2}
    return sorted(found, key=lambda item: (order.get(item["link"].link, 3), item["title"]))


def source_by_code(code: str) -> Source | None:
    """Источник по коду модуля."""
    return Source.objects.filter(code=code).first()
