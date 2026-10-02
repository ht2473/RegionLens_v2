"""Настройки рабочего стенда: HTTPS, заголовки безопасности, блокировка подбора, Sentry."""

from __future__ import annotations

from .base import *  # noqa: F403
from .base import ALLOWED_HOSTS, CSP_POLICY, env

DEBUG = False

# --- Обращения контейнера к самому себе -------------------------------------------------------
# Проверка живости Docker приходит с Host: 127.0.0.1:8000; петлевые узлы добавляются
# к доменам стенда, иначе она получала бы 400.
LOOPBACK_HOSTS = ["127.0.0.1", "localhost", "[::1]"]
ALLOWED_HOSTS = [*ALLOWED_HOSTS, *(h for h in LOOPBACK_HOSTS if h not in ALLOWED_HOSTS)]

# --- Транспортная безопасность ---------------------------------------------------------------
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)
# Проверки состояния — без перехода на HTTPS: `curl --fail` считает 301 успехом.
SECURE_REDIRECT_EXEMPT = [r"^healthz$", r"^readyz$"]
SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=31_536_000)  # один год
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SESSION_COOKIE_SECURE = env.bool("SESSION_COOKIE_SECURE", default=True)
CSRF_COOKIE_SECURE = env.bool("CSRF_COOKIE_SECURE", default=True)
LANGUAGE_COOKIE_SECURE = True

# Обратный прокси (Caddy) терминирует TLS и передаёт исходную схему заголовком.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True

# Он же сообщает адрес посетителя.
CLIENT_IP_HEADER = env.str("CLIENT_IP_HEADER", default="HTTP_X_REAL_IP")

# --- Защита формы входа --------------------------------------------------------------------
# django-axes объявлен в базовых настройках; повторное объявление ломает запуск.
AXES_FAILURE_LIMIT = 5  # попыток до блокировки
AXES_COOLOFF_TIME = 1  # часов блокировки
# На стенде блокировка — по каждому признаку в отдельности: перебор идёт с многих адресов.
AXES_LOCKOUT_PARAMETERS = ["ip_address", "username"]  # type: ignore[list-item]
# Адрес для django-axes — из того же заголовка, что CLIENT_IP_HEADER: REMOTE_ADDR
# за прокси у всех один.
AXES_IPWARE_META_PRECEDENCE_ORDER = ("HTTP_X_REAL_IP", "REMOTE_ADDR")
AXES_RESET_ON_SUCCESS = True
AXES_LOCKOUT_TEMPLATE = "accounts/lockout.html"

# --- Политика безопасности содержимого ------------------------------------------------------
SECURE_CSP = {**CSP_POLICY, "upgrade-insecure-requests": True}

# --- Наблюдаемость -----------------------------------------------------------------------------
LOG_FORMAT = env.str("LOG_FORMAT", default="json")

SENTRY_DSN = env.str("SENTRY_DSN", default="")
if SENTRY_DSN:
    import sentry_sdk
    from sentry_sdk.integrations.django import DjangoIntegration

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[DjangoIntegration()],
        traces_sample_rate=env.float("SENTRY_TRACES_SAMPLE_RATE", default=0.05),
        send_default_pii=False,
        environment="production",
        release=f"regionlens@{env.str('PROJECT_VERSION', default='0.1.0')}",
    )

# --- Ресурсные ограничения VPS ------------------------------------------------------------------
DUCKDB_READ_ONLY = env.bool("DUCKDB_READ_ONLY", default=True)
