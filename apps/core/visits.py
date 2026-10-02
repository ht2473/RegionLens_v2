"""
Статистика посещений по журналу Caddy: GoAccess считает, панель показывает.

Счётчиков на страницах нет. Новые строки журнала переписываются в помесячную историю
проекта уже без последней части адреса, без строки запроса и без адресов со служебными
метками; статика и проверки состояния не берутся. По истории GoAccess каждый раз заново
считает отчёт и своей базы не ведёт: полные адреса остаются только в журнале Caddy.
Посетитель — пара «адрес + браузер» за день; обходчики поисковых систем не считаются.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import re
import subprocess  # nosec B404 — запускается только GoAccess с заданными ключами
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)

REPORT_NAME = "report.json"
HISTORY_DIR = "history"
# Докуда прочитан каждый файл журнала: по номеру файла — смещение и отпечаток первой строки.
STATE_NAME = "offsets.json"
# Журналы Caddy: текущий и прежние части (roll_uncompressed в docker/Caddyfile).
LOG_PATTERN = "access*.log"
RUN_TIMEOUT = 600

# Время истории и отчёта — московское.
MOSCOW = timezone(timedelta(hours=3))
# Не посещения: файлы оформления и сценарии, проверки состояния.
SKIPPED_PREFIXES = ("/static/", "/healthz", "/readyz", "/favicon.ico")
# Адреса со служебными метками (сброс пароля, подтверждение почты): метка заменяется.
MASKED_ROUTES = (
    (re.compile(r"(/password/reset/)[^/]+/[^/]+/"), r"\1*/*/"),
    (re.compile(r"(/email/confirm/)[^/]+/"), r"\1*/"),
)

# Сколько строк в перечнях раздела панели.
TOP_ROWS = 15
# Сколько последних дней на графике.
CHART_DAYS = 90

DATE_FORMATS = ("%Y%m%d", "%d/%b/%Y", "%Y-%m-%d")
# Первое звено адреса, по которому запросы делятся на языки сайта и программный интерфейс.
SECTIONS = frozenset({"ru", "en", "api"})


@dataclass(frozen=True, slots=True)
class RefreshResult:
    """Исход пересчёта статистики."""

    ok: bool
    lines: int = 0
    error: str = ""


def visits_dir() -> Path:
    """Каталог истории посещений и отчёта."""
    return Path(settings.VISITS_DIR)


def log_files(directory: Path | None = None) -> list[Path]:
    """Журналы по времени изменения: прежние части раньше текущего."""
    root = Path(directory or settings.VISITS_LOG_DIR)
    if not root.is_dir():
        return []
    return sorted(root.glob(LOG_PATTERN), key=lambda path: path.stat().st_mtime)


def history_files(target: Path | None = None) -> list[Path]:
    """Месяцы истории по порядку."""
    root = (target or visits_dir()) / HISTORY_DIR
    return sorted(root.glob("*.log")) if root.is_dir() else []


# ---------------------------------------------------------------------------------------
# Перенос новых строк журнала в историю
# ---------------------------------------------------------------------------------------


def mask_ip(value: str) -> str:
    """Адрес без последней части: IPv4 — до /24, IPv6 — до /64."""
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return "0.0.0.0"  # noqa: S104 — заглушка вместо испорченного адреса
    prefix = 24 if isinstance(address, ipaddress.IPv4Address) else 64
    return str(ipaddress.ip_network(f"{address}/{prefix}", strict=False).network_address)


def history_line(raw: bytes) -> tuple[datetime, str] | None:
    """
    Строка журнала Caddy (JSON) — строка истории в формате COMBINED.

    ``None`` — не посещение (статика, проверки) или испорченная строка.
    """
    try:
        entry = json.loads(raw)
        request = entry["request"]
        moment = datetime.fromtimestamp(float(entry["ts"]), tz=UTC).astimezone(MOSCOW)
    except ValueError, KeyError, TypeError:
        return None
    path = str(request.get("uri", "")).split("?", 1)[0] or "/"
    if path.startswith(SKIPPED_PREFIXES):
        return None
    for pattern, replacement in MASKED_ROUTES:
        path = pattern.sub(replacement, path)
    headers = request.get("headers") or {}

    def header(name: str) -> str:
        values = headers.get(name) or ["-"]
        return str(values[0]).replace('"', "'") or "-"

    address = mask_ip(str(request.get("client_ip") or request.get("remote_ip") or ""))
    line = (
        f"{address} - - [{moment:%d/%b/%Y:%H:%M:%S %z}] "
        f'"{request.get("method", "GET")} {path.replace(chr(34), "%22")} '
        f'{request.get("proto", "HTTP/1.1")}" {int(entry.get("status", 0))} '
        f'{int(entry.get("size", 0))} "{header("Referer")}" "{header("User-Agent")}"'
    )
    return moment, line


def _read_state(target: Path) -> dict[str, dict[str, Any]]:
    """Прочитанное по номерам файлов: смещение и отпечаток первой строки."""
    try:
        state = json.loads((target / STATE_NAME).read_text(encoding="utf-8"))
    except OSError, ValueError:
        return {}
    return state if isinstance(state, dict) else {}


def _first_line(path: Path) -> str:
    """Отпечаток первой законченной строки файла; её Caddy не меняет."""
    with path.open("rb") as source:
        first = source.readline()
    return hashlib.sha256(first).hexdigest()[:16] if first.endswith(b"\n") else ""


def take_new_lines(files: list[Path], target: Path) -> int:
    """
    Дописать в историю строки журналов, появившиеся после прошлого раза; вернуть их число.

    Файл узнаётся по номеру в файловой системе: при делении журнала Caddy переименовывает
    текущий файл, и номер сохраняется. Номер удалённой части может достаться новому файлу —
    тогда не совпадёт первая строка, и файл читается с начала. Незаконченная последняя
    строка ждёт следующего раза.
    """
    state = _read_state(target)
    fresh: dict[str, dict[str, Any]] = {}
    months: dict[str, list[str]] = defaultdict(list)
    count = 0
    for path in files:
        info = path.stat()
        key = str(info.st_ino)
        first = _first_line(path)
        saved = state.get(key)
        offset = 0
        if isinstance(saved, dict) and saved.get("first") == first:
            offset = int(saved.get("offset", 0))
        if offset > info.st_size:
            offset = 0
        with path.open("rb") as source:
            source.seek(offset)
            for raw in source:
                if not raw.endswith(b"\n"):
                    break
                offset += len(raw)
                converted = history_line(raw)
                if converted is None:
                    continue
                moment, line = converted
                months[f"{moment:%Y-%m}"].append(line)
                count += 1
        fresh[key] = {"offset": offset, "first": first or _first_line(path)}

    history = target / HISTORY_DIR
    history.mkdir(parents=True, exist_ok=True)
    for month, lines in sorted(months.items()):
        with (history / f"{month}.log").open("a", encoding="utf-8", newline="\n") as out:
            out.write("\n".join(lines) + "\n")
    (target / STATE_NAME).write_text(json.dumps(fresh), encoding="utf-8")
    return count


# ---------------------------------------------------------------------------------------
# Отчёт GoAccess
# ---------------------------------------------------------------------------------------


def goaccess_command(history: list[Path], target: Path) -> list[str]:
    """Команда GoAccess: отчёт в JSON по всей истории, без собственной базы."""
    return [
        settings.GOACCESS_BIN,
        *(str(path) for path in history),
        "--log-format=COMBINED",
        f"--output={target / REPORT_NAME}",
        "--ignore-crawlers",
        "--no-progress",
        "--no-global-config",
    ]


def refresh() -> RefreshResult:
    """Перенести новые строки журнала, пересчитать отчёт и оставить отметку для панели."""
    from apps.core.models import ServiceBeat

    files = log_files()
    target = visits_dir()
    target.mkdir(parents=True, exist_ok=True)
    lines = take_new_lines(files, target) if files else 0
    history = history_files(target)
    if not files and not history:
        result = RefreshResult(ok=False, error=f"Журналов нет в {settings.VISITS_LOG_DIR}")
    elif not any(path.stat().st_size for path in history):
        result = RefreshResult(ok=True, lines=lines)
    else:
        result = _run(history, target, lines)
    summary = summarize(read_report()) if result.ok else None
    ServiceBeat.record(
        ServiceBeat.Service.VISITS,
        ok=result.ok,
        lines=result.lines,
        error=result.error[:500],
        visitors=summary["general"]["visitors"] if summary else None,
        last_day=summary["last_day"] if summary else "",
    )
    return result


def _run(history: list[Path], target: Path, lines: int) -> RefreshResult:
    """Запустить GoAccess и вернуть исход."""
    try:
        completed = subprocess.run(  # noqa: S603 # nosec B603 — команда собрана из настроек
            goaccess_command(history, target),
            capture_output=True,
            text=True,
            timeout=RUN_TIMEOUT,
            env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
            check=False,
        )
    except FileNotFoundError:
        return RefreshResult(
            ok=False, lines=lines, error=f"GoAccess не найден: {settings.GOACCESS_BIN}"
        )
    except subprocess.TimeoutExpired:
        return RefreshResult(ok=False, lines=lines, error=f"GoAccess не уложился в {RUN_TIMEOUT} с")
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout).strip()
        return RefreshResult(ok=False, lines=lines, error=message[-500:])
    return RefreshResult(ok=True, lines=lines)


def read_report(path: Path | None = None) -> dict[str, Any] | None:
    """Отчёт GoAccess в JSON; нет файла или он испорчен — ``None``."""
    source = path or visits_dir() / REPORT_NAME
    try:
        return json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as error:
        logger.warning("Отчёт посещений не читается: %s", error)
        return None


# ---------------------------------------------------------------------------------------
# Разбор отчёта
# ---------------------------------------------------------------------------------------


def _count(item: dict[str, Any], field: str) -> int:
    """Число из поля панели: ``{"count": …}`` или само число."""
    value = item.get(field, 0)
    if isinstance(value, dict):
        value = value.get("count", 0)
    try:
        return int(value or 0)
    except TypeError, ValueError:
        return 0


def _panel(report: dict[str, Any], name: str) -> list[dict[str, Any]]:
    """Строки панели отчёта."""
    panel = report.get(name) or {}
    data = panel.get("data") if isinstance(panel, dict) else None
    return data if isinstance(data, list) else []


def _day(value: str) -> date | None:
    """День из подписи панели посетителей."""
    for pattern in DATE_FORMATS:
        try:
            return datetime.strptime(value, pattern).date()
        except ValueError:
            continue
    return None


def _rows(report: dict[str, Any], name: str, limit: int = TOP_ROWS) -> list[dict[str, Any]]:
    """Первые строки панели по числу запросов."""
    rows: list[dict[str, Any]] = [
        {
            "label": str(item.get("data", "")),
            "hits": _count(item, "hits"),
            "visitors": _count(item, "visitors"),
        }
        for item in _panel(report, name)
    ]
    rows.sort(key=lambda row: row["hits"], reverse=True)
    return rows[:limit]


def _shares(report: dict[str, Any], name: str) -> list[dict[str, Any]]:
    """Доли посетителей по группам панели (семейства систем и браузеров)."""
    rows: list[dict[str, Any]] = [
        {"label": str(item.get("data", "")), "visitors": _count(item, "visitors")}
        for item in _panel(report, name)
    ]
    total = sum(row["visitors"] for row in rows) or 1
    rows.sort(key=lambda row: row["visitors"], reverse=True)
    return [{**row, "share": row["visitors"] / total} for row in rows if row["visitors"]]


def summarize(report: dict[str, Any] | None) -> dict[str, Any] | None:
    """Сводка для панели: итоги, дни, месяцы, страницы, источники переходов, 404."""
    if not report:
        return None
    general = report.get("general") or {}

    days: list[dict[str, Any]] = []
    for item in _panel(report, "visitors"):
        day = _day(str(item.get("data", "")))
        if day is not None:
            days.append(
                {"day": day, "hits": _count(item, "hits"), "visitors": _count(item, "visitors")}
            )
    days.sort(key=lambda row: row["day"])

    months: dict[tuple[int, int], dict[str, int]] = defaultdict(lambda: {"hits": 0, "visitors": 0})
    for row in days:
        bucket = months[(row["day"].year, row["day"].month)]
        bucket["hits"] += row["hits"]
        bucket["visitors"] += row["visitors"]

    pages = _rows(report, "requests")
    languages: dict[str, int] = defaultdict(int)
    for item in _panel(report, "requests"):
        prefix = str(item.get("data", "")).lstrip("/").split("/", 1)[0]
        languages[prefix if prefix in SECTIONS else "other"] += _count(item, "hits")

    return {
        "general": {
            "requests": _count(general, "total_requests"),
            "valid": _count(general, "valid_requests"),
            "failed": _count(general, "failed_requests"),
            "excluded": _count(general, "excluded_hits"),
            "visitors": _count(general, "unique_visitors"),
            "not_found": _count(general, "unique_not_found"),
        },
        "first_day": days[0]["day"] if days else None,
        "last_day": days[-1]["day"].isoformat() if days else "",
        "days": days,
        "months": [
            {"start": date(year, month, 1), **values}
            for (year, month), values in sorted(months.items(), reverse=True)
        ],
        "pages": pages,
        "languages": dict(languages),
        "referrers": _rows(report, "referring_sites"),
        "not_found": _rows(report, "not_found"),
        "systems": _shares(report, "os"),
        "browsers": _shares(report, "browsers"),
        "statuses": _rows(report, "status_codes"),
    }
