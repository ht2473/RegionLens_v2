"""
Обращения сборщика к сайтам источников: без прокси, с сертификатами НУЦ и повторами.

Государственные сайты рвут рукопожатие TLS через раз и зависают за прокси, а их
сертификаты подписаны корнем НУЦ Минцифры, которому системы не доверяют.
"""

from __future__ import annotations

import logging
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from functools import lru_cache

from django.conf import settings

logger = logging.getLogger(__name__)

# Пауза между попытками растёт: 2, 4, 8 секунд.
RETRY_PAUSE = 2.0
# Наибольший размер файла выпуска: защита от бесконечного ответа.
MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024


class FetchError(RuntimeError):
    """Файл или страница источника не получены после всех попыток."""


@dataclass(frozen=True, slots=True)
class RemoteFile:
    """Сведения о файле на сайте источника без его содержимого."""

    url: str
    status: int
    size: int | None
    modified: datetime | None
    etag: str


@lru_cache(maxsize=1)
def _ssl_context() -> ssl.SSLContext:
    """Системные корневые сертификаты и сертификаты НУЦ из справочника."""
    context = ssl.create_default_context()
    extra = settings.SOURCE_EXTRA_CA_FILE
    if extra and extra.exists():
        context.load_verify_locations(cafile=str(extra))
    return context


def _opener() -> urllib.request.OpenerDirector:
    """Открыватель без прокси из окружения: запросы к источникам идут напрямую."""
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=_ssl_context()),
    )


def _request(url: str, method: str = "GET", data: bytes | None = None) -> urllib.request.Request:
    headers = {"User-Agent": settings.SOURCE_USER_AGENT}
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
    return urllib.request.Request(  # noqa: S310 - адреса источников из кода, только https
        url, data=data, method=method, headers=headers
    )


def _with_retries[T](url: str, action: Callable[[], T]) -> T:
    """Выполнить обращение с повторами; ответ сервера с ошибкой не повторяется."""
    attempts = max(1, settings.SOURCE_HTTP_ATTEMPTS)
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return action()
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError, ssl.SSLError) as error:
            last = error
            logger.info("Попытка %d из %d к %s не удалась: %s", attempt + 1, attempts, url, error)
            if attempt < attempts - 1:
                time.sleep(RETRY_PAUSE * 2**attempt)
    raise FetchError(f"{url}: нет ответа после {attempts} попыток ({last})")


def head(url: str) -> RemoteFile:
    """Сведения о файле; отсутствующий файл — статус 404 без исключения."""

    def action() -> RemoteFile:
        try:
            with _opener().open(
                _request(url, "HEAD"), timeout=settings.SOURCE_HTTP_TIMEOUT
            ) as response:
                return _describe(url, response.status, response.headers)
        except urllib.error.HTTPError as error:
            if error.code in {403, 404, 410}:
                return RemoteFile(url=url, status=error.code, size=None, modified=None, etag="")
            raise

    try:
        return _with_retries(url, action)
    except urllib.error.HTTPError as error:
        raise FetchError(f"{url}: ответ {error.code}") from error


def download(url: str) -> tuple[bytes, RemoteFile]:
    """Содержимое файла и сведения о нём."""
    return _fetch(url, _request(url))


def post(url: str, form: dict[str, str]) -> tuple[bytes, RemoteFile]:
    """Ответ на отправку формы: так отдаёт данные страница статистики реестра МСП."""
    return _fetch(url, _request(url, "POST", urllib.parse.urlencode(form).encode("utf-8")))


def _fetch(url: str, request: urllib.request.Request) -> tuple[bytes, RemoteFile]:
    """Выполнить запрос с повторами и прочитать ответ целиком."""

    def action() -> tuple[bytes, RemoteFile]:
        with _opener().open(request, timeout=settings.SOURCE_HTTP_TIMEOUT) as response:
            content = response.read(MAX_DOWNLOAD_BYTES + 1)
            if len(content) > MAX_DOWNLOAD_BYTES:
                raise FetchError(f"{url}: файл больше {MAX_DOWNLOAD_BYTES} байт")
            declared = response.headers.get("Content-Length")
            if declared and declared.isdigit() and int(declared) != len(content):
                raise ConnectionError(f"получено {len(content)} байт из {declared}")
            return content, _describe(url, response.status, response.headers)

    try:
        return _with_retries(url, action)
    except urllib.error.HTTPError as error:
        raise FetchError(f"{url}: ответ {error.code}") from error


def page(url: str) -> str:
    """Текст страницы источника."""
    content, _remote = download(url)
    return content.decode("utf-8", errors="replace")


def _describe(url: str, status: int, headers: object) -> RemoteFile:
    get = getattr(headers, "get", lambda _name, _default=None: None)
    size = get("Content-Length")
    modified = get("Last-Modified")
    try:
        parsed = parsedate_to_datetime(modified) if modified else None
    except TypeError, ValueError:
        parsed = None
    return RemoteFile(
        url=url,
        status=status,
        size=int(size) if size and str(size).isdigit() else None,
        modified=parsed,
        etag=(get("ETag") or "").strip('"'),
    )
