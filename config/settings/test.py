"""Настройки тестов: кэш и письма в памяти, быстрое хеширование паролей."""

from __future__ import annotations

from .base import *  # noqa: F403
from .base import BASE_DIR, REST_FRAMEWORK

DEBUG = False
SECRET_KEY = "test-secret-key-not-used-outside-of-test-runs"  # noqa: S105  # nosec B105
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]

# --- Ускорение прогонов -------------------------------------------------------------------
# MD5 в тестах допустим: хеши паролей здесь не защищают реальные данные.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
AUTH_PASSWORD_VALIDATORS = []

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "regionlens-tests",
    },
}
SESSION_ENGINE = "django.contrib.sessions.backends.db"

MAILERS = {"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}}

# Хранилище без манифеста: collectstatic в тестах не выполняется.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
# Без собранной статики WhiteNoise ищет файлы по приложениям, как при разработке,
# и не предупреждает на каждом запросе об отсутствии STATIC_ROOT.
WHITENOISE_AUTOREFRESH = True
WHITENOISE_USE_FINDERS = True

# --- Изоляция данных -----------------------------------------------------------------------
# Тестовый склад — отдельный файл рядом с рабочим.
DUCKDB_PATH = BASE_DIR / "data" / "warehouse" / "test_regionlens.duckdb"
DUCKDB_MEMORY_LIMIT = "256MB"
DUCKDB_THREADS = 1

DATASET_UPLOAD_DIR = BASE_DIR / "data" / "tmp" / "test_uploads"
SOURCE_ARCHIVE_DIR = BASE_DIR / "data" / "tmp" / "test_archive"
SOURCE_PARSED_DIR = BASE_DIR / "data" / "tmp" / "test_sources"
VISITS_DIR = BASE_DIR / "data" / "tmp" / "test_visits"
VISITS_LOG_DIR = BASE_DIR / "data" / "tmp" / "test_visit_logs"

# Учёт неудачных входов связывал бы проверки между собой; его тест включает учёт сам.
AXES_ENABLED = False

# Предел API поднят по той же причине; его тест задаёт предел сам.
REST_FRAMEWORK = {**REST_FRAMEWORK, "DEFAULT_THROTTLE_RATES": {"anon": "10000/hour"}}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": True,
    "handlers": {"null": {"class": "logging.NullHandler"}},
    "root": {"handlers": ["null"], "level": "CRITICAL"},
}
