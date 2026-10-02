"""
Формат журналов (читаемый в разработке, построчный JSON на стенде) и письмо
администраторам об ошибке 500 с пределом частоты.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import threading
import time
import traceback
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.http import HttpRequest
from django.utils.log import AdminEmailHandler
from django.views.debug import SafeExceptionReporterFilter

logger = logging.getLogger(__name__)

# Одна и та же ошибка — не чаще одного письма за это время, секунды.
ERROR_MAIL_INTERVAL = 3600
# Писем об ошибках от одного процесса за тот же срок — не больше.
ERROR_MAIL_LIMIT = 20
SERVER_ERROR = 500

# Стандартные атрибуты LogRecord; остальные попадают в JSON отдельными ключами.
_STANDARD_ATTRS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
    }
)


class JSONFormatter(logging.Formatter):
    """Форматтер, преобразующий запись журнала в одну строку JSON."""

    def format(self, record: logging.LogRecord) -> str:
        """Собрать словарь полей записи и сериализовать его."""
        payload: dict[str, Any] = {
            "timestamp": dt.datetime.fromtimestamp(record.created, tz=dt.UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "line": record.lineno,
        }

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        # Поля из extra={...}.
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = _safe_value(value)

        return json.dumps(payload, ensure_ascii=False, default=str)


def _safe_value(value: Any) -> Any:
    """Привести значение к сериализуемому виду, не роняя логирование на сложных объектах."""
    if isinstance(value, str | int | float | bool | type(None)):
        return value
    if isinstance(value, dt.datetime | dt.date):
        return value.isoformat()
    if isinstance(value, list | tuple | set):
        return [_safe_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _safe_value(item) for key, item in value.items()}
    return str(value)


# ---------------------------------------------------------------------------------------
# Письмо об ошибке 500
# ---------------------------------------------------------------------------------------


def error_signature(record: logging.LogRecord) -> str:
    """Признак ошибки для предела частоты: вид исключения и место, где оно возникло."""
    if record.exc_info and record.exc_info[0] is not None:
        kind, _error, trace = record.exc_info
        frames = traceback.extract_tb(trace)
        place = f"{frames[-1].filename}:{frames[-1].lineno}" if frames else ""
        return f"{kind.__qualname__}@{place}"
    # Без исключения — по шаблону сообщения: адрес страницы в нём — подстановка.
    return f"{record.name}:{record.msg}"


class _Limiter:
    """Предел писем: в процессе — всегда, между процессами — через кэш, пока он доступен."""

    def __init__(self) -> None:
        self._sent: dict[str, float] = {}
        self._lock = threading.Lock()

    def allow(self, signature: str) -> bool:
        """Отметить ошибку и сказать, пора ли писать о ней."""
        now = time.monotonic()
        with self._lock:
            self._sent = {
                key: moment
                for key, moment in self._sent.items()
                if now - moment < ERROR_MAIL_INTERVAL
            }
            if signature in self._sent or len(self._sent) >= ERROR_MAIL_LIMIT:
                return False
            self._sent[signature] = now
        key = "error-mail:" + hashlib.sha256(signature.encode()).hexdigest()[:32]
        # Запись есть — письмо уже отправил другой процесс; кэш недоступен — решает процесс.
        return bool(cache.add(key, 1, timeout=ERROR_MAIL_INTERVAL) or cache.get(key) is None)

    def reset(self) -> None:
        """Забыть отправленные письма (для проверок)."""
        with self._lock:
            self._sent.clear()


error_mail_limiter = _Limiter()


class FormFreeReporterFilter(SafeExceptionReporterFilter):
    """Отчёт об ошибке без значений полей форм: в них бывают пароли и сведения о людях."""

    def get_post_parameters(self, request: HttpRequest | None) -> dict[str, Any]:
        """Названия полей остаются, значения заменены заглушкой."""
        if request is None:
            return {}
        return dict.fromkeys(request.POST, self.cleansed_substitute)


class ErrorMailHandler(AdminEmailHandler):
    """
    Письмо администраторам (``ADMINS``) об ошибке 500.

    Письмо — только об ответе 500 или исключении: 503 от ``/readyz`` при несобранном складе —
    состояние, а не сбой. Одна и та же ошибка — не чаще раза в час. Отказ почты не должен
    превратить ответ с ошибкой в обрыв соединения: встроенный обработчик в Django 6.1
    его не перехватывает.
    """

    def emit(self, record: logging.LogRecord) -> None:
        """Отправить письмо, если это сбой, адресаты заданы и предел не исчерпан."""
        failure = record.exc_info or getattr(record, "status_code", None) == SERVER_ERROR
        if not settings.ADMINS or not failure:
            return
        if not error_mail_limiter.allow(error_signature(record)):
            return
        super().emit(record)

    def send_mail(self, subject: Any, message: Any, *args: Any, **kwargs: Any) -> None:
        """Отправить письмо и отметить исход для «Состояния служб»."""
        from apps.core.mail import MAIL_ERRORS, describe
        from apps.core.models import ServiceBeat

        try:
            super().send_mail(subject, message, *args, **kwargs)
        except MAIL_ERRORS as error:
            logger.warning("Письмо об ошибке не ушло: %s", describe(error))
            _record_mail(ServiceBeat, ok=False, error=describe(error))
            return
        _record_mail(ServiceBeat, ok=True)


def _record_mail(model: Any, **detail: Any) -> None:
    """Отметить отправку; ошибка 500 может быть вызвана и недоступной базой."""
    try:
        model.record(model.Service.MAIL, **detail)
    except Exception as error:  # отметка необязательна, письмо важнее
        logger.warning("Отметка почты не записана: %s", error)
