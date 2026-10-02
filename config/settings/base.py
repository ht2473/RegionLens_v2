"""
Настройки, общие для всех сред: ``local``, ``production`` и ``test`` импортируют их целиком.

Значения, зависящие от окружения, читаются из переменных окружения или ``.env``.
"""

from __future__ import annotations

from pathlib import Path

import environ
from django.utils.csp import CSP

# ---------------------------------------------------------------------------------------
# Пути проекта
# ---------------------------------------------------------------------------------------

# Корень репозитория.
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Каталог с данными: исходный parquet, справочники, собранный склад DuckDB.
DATA_DIR = BASE_DIR / "data"

# ---------------------------------------------------------------------------------------
# Чтение окружения
# ---------------------------------------------------------------------------------------

env = environ.Env()

# Файл .env необязателен: на стенде переменные приходят от Docker.
_env_file = BASE_DIR / ".env"
if _env_file.exists():
    environ.Env.read_env(_env_file)

# ---------------------------------------------------------------------------------------
# Идентификация проекта: подвал, «О проекте», колонтитулы отчётов, метаданные страниц
# ---------------------------------------------------------------------------------------

PROJECT_NAME = "RegionLens"
PROJECT_TAGLINE = "Анализ и визуализация социально-экономических показателей регионов России"
PROJECT_VERSION = "0.1.0"
PROJECT_AUTHOR = "Кузьмин Евгений Олегович"
PROJECT_AUTHOR_SHORT = "Кузьмин Е. О."
PROJECT_AUTHOR_EN = "Evgeny Kuzmin"
PROJECT_AUTHOR_STUDENT_ID = "ГМФИТД0401/25-вос"
PROJECT_AUTHOR_EMAIL = "lord10000297@gmail.com"
PROJECT_THESIS_TITLE = (
    "Приложение для анализа и визуализации социально-экономических показателей регионов РФ"
)
PROJECT_REPOSITORY_URL = "https://github.com/ht2473/RegionLens_v2"

# Атрибуция источника данных обязательна по условиям лицензии CC BY.
DATA_SOURCE_TITLE = "Социально-экономические показатели регионов России"
DATA_SOURCE_TITLE_EN = "Socio-economic indicators of Russian regions"
DATA_SOURCE_ORIGIN = "Росстат"
DATA_SOURCE_PROCESSOR = "Если быть точным"
DATA_SOURCE_URL = "https://tochno.st/datasets/regions_collection"
DATA_SOURCE_LICENSE = "Creative Commons BY"
# Версия набора, пока ни одна не загружена; дальше действует текущая из журнала версий.
DATA_SOURCE_VERSION = "v20260313"

# ---------------------------------------------------------------------------------------
# Основные параметры безопасности
# ---------------------------------------------------------------------------------------

SECRET_KEY = env.str("DJANGO_SECRET_KEY")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])

# Заголовок с адресом посетителя от обратного прокси (apps/core/client_ip); пусто — прокси нет.
CLIENT_IP_HEADER = env.str("CLIENT_IP_HEADER", default="")

# ---------------------------------------------------------------------------------------
# Приложения
# ---------------------------------------------------------------------------------------

DJANGO_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "django.contrib.postgres",
    # Без django.contrib.sites: адрес берётся из запроса.
    "django.contrib.sitemaps",
]

THIRD_PARTY_APPS = [
    "parler",  # переводимые поля моделей справочников
    "rest_framework",  # программный интерфейс
    "drf_spectacular",  # схема OpenAPI и интерактивная документация
    "drf_spectacular_sidecar",  # файлы Swagger UI, отдаваемые самим приложением
    "django_filters",  # декларативная фильтрация выборок API
    "axes",  # защита формы входа от подбора пароля
]

LOCAL_APPS = [
    "apps.core",  # общие абстракции, утилиты, служебные страницы
    "apps.accounts",  # пользователи и роли
    "apps.catalog",  # справочники: территории, разделы, показатели, ряды, источники
    "apps.warehouse",  # ETL и доступ к аналитическому складу DuckDB
    "apps.maps",  # картограмма, плиточная карта, шкалы классификации
    "apps.rankings",  # рейтинги регионов и движение позиций
    "apps.compare",  # сопоставление территорий между собой
    "apps.surface",  # рабочая поверхность: показатель, территории и год на одном экране
    "apps.analytics",  # аналитическое ядро и инструменты анализа
    "apps.workspace",  # личный кабинет: выборки и избранное
    "apps.exports",  # выгрузка документов: CSV, XLSX и PDF
    "apps.api",  # программный интерфейс REST
    "apps.content",  # методология и глоссарий
    "apps.feedback",  # обращения посетителей
    "apps.dashboard",  # панель управления системой
    "apps.sources",  # сбор выпусков внешних источников: архив, разбор, журнал
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

# ---------------------------------------------------------------------------------------
# Промежуточные слои
# ---------------------------------------------------------------------------------------

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.csp.ContentSecurityPolicyMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    # LocaleMiddleware обязан идти после SessionMiddleware и до CommonMiddleware:
    # он определяет язык по префиксу URL, cookie и заголовку Accept-Language.
    "django.middleware.locale.LocaleMiddleware",
    # После LocaleMiddleware: запоминает уже определённый язык.
    "apps.core.middleware.LanguageMemoryMiddleware",
    # После LocaleMiddleware: страница 400 — на языке адреса.
    "apps.core.middleware.NullCharacterMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django.middleware.gzip.GZipMiddleware",
    # AxesMiddleware — последним: он обрабатывает результат аутентификации.
    "axes.middleware.AxesMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

# ---------------------------------------------------------------------------------------
# Шаблоны
# ---------------------------------------------------------------------------------------

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.template.context_processors.i18n",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                # Собственные процессоры: реквизиты проекта и структура навигации.
                "apps.core.context_processors.project_metadata",
                "apps.core.navigation.navigation_context",
                "apps.accounts.context_processors.region",
            ],
        },
    },
]

# ---------------------------------------------------------------------------------------
# База данных: состояние приложения; наблюдения — в складе DuckDB
# ---------------------------------------------------------------------------------------

DATABASES = {
    "default": env.db_url(
        "DATABASE_URL",
        default="postgres://regionlens:regionlens@localhost:5432/regionlens",
    ),
}
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DATABASE_CONN_MAX_AGE", default=60)
DATABASES["default"]["ATOMIC_REQUESTS"] = False

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------------------
# Аутентификация
# ---------------------------------------------------------------------------------------

AUTH_USER_MODEL = "accounts.User"

# axes — первым: заблокированный отправитель отклоняется до сверки пароля.
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]

# Argon2; остальные — для проверки паролей, захешированных ими.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.ScryptPasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 10},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "accounts:dashboard"
LOGOUT_REDIRECT_URL = "core:home"

# Срок действия ссылки восстановления пароля.
PASSWORD_RESET_TIMEOUT = 3 * 24 * 3600

# ---------------------------------------------------------------------------------------
# Защита формы входа от подбора пароля (django-axes): учёт по паре «адрес
# отправителя + адрес почты», чтобы не отрезать общий адрес организации.
# ---------------------------------------------------------------------------------------

AXES_ENABLED = env.bool("AXES_ENABLED", default=True)
AXES_FAILURE_LIMIT = env.int("AXES_FAILURE_LIMIT", default=8)
AXES_COOLOFF_TIME = 1  # час до автоматического снятия блокировки
AXES_LOCKOUT_PARAMETERS = [["ip_address", "username"]]

# Поле формы входа Django называется ``username``; иначе axes не видит учётную запись.
AXES_USERNAME_FORM_FIELD = "username"
AXES_RESET_ON_SUCCESS = True
AXES_LOCKOUT_TEMPLATE = "accounts/lockout.html"
AXES_VERBOSE = True
# Журнал удачных входов не ведётся: последний вход хранит учётная запись, а адреса
# и браузеры входивших защите не нужны и после удаления учётной записи оставались бы.
AXES_DISABLE_ACCESS_LOG = True

# ---------------------------------------------------------------------------------------
# Интернационализация: строки — gettext, содержимое — django-parler
# ---------------------------------------------------------------------------------------

LANGUAGE_CODE = env.str("DJANGO_LANGUAGE_CODE", default="ru")
TIME_ZONE = env.str("DJANGO_TIME_ZONE", default="Europe/Moscow")
USE_I18N = True
USE_TZ = True

# Глобально разряды не разделяются: иначе «1 464» попадало бы в ключи, годы и адреса.
# Величины форматирует фильтр ru_number.
USE_THOUSAND_SEPARATOR = False

LANGUAGES = [
    ("ru", "Русский"),
    ("en", "English"),
]

LOCALE_PATHS = [BASE_DIR / "locale"]

PARLER_DEFAULT_LANGUAGE_CODE = "ru"
PARLER_LANGUAGES = {
    None: (
        {"code": "ru"},
        {"code": "en"},
    ),
    "default": {
        # Без перевода — русский оригинал.
        "fallbacks": ["ru"],
        "hide_untranslated": False,
    },
}

# ---------------------------------------------------------------------------------------
# Статика и медиа
# ---------------------------------------------------------------------------------------

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    # Сжатие и отпечатки WhiteNoise плюс переписывание адресов в ES-модулях клиента.
    "staticfiles": {"BACKEND": "apps.core.storage.ModuleAwareStaticFilesStorage"},
}

# Файл набора больше порога пишется на диск, а не в память.
DATA_UPLOAD_MAX_MEMORY_SIZE = 20 * 1024 * 1024  # 20 МБ
FILE_UPLOAD_MAX_MEMORY_SIZE = 20 * 1024 * 1024
DATA_UPLOAD_MAX_NUMBER_FIELDS = 5000  # конструктор индексов передаёт много полей

# ---------------------------------------------------------------------------------------
# Кэш
# ---------------------------------------------------------------------------------------

# Valkey (протокол Redis): кэш и атомарные счётчики ограничителей частоты.
VALKEY_URL = env.str("VALKEY_URL", default="redis://127.0.0.1:6379").rstrip("/")

CACHES = {
    "default": {
        # Отказ Valkey превращается в промах кэша, а не в ошибку страницы.
        "BACKEND": "apps.core.cache.ResilientValkeyCache",
        "LOCATION": f"{VALKEY_URL}/0",
        # При нехватке памяти Valkey вытесняет давно не читанные ключи (allkeys-lru).
        "TIMEOUT": 60 * 60 * 24,
        "KEY_PREFIX": "regionlens",
        "OPTIONS": {
            "socket_connect_timeout": 0.5,
            "socket_timeout": 1.0,
        },
    },
}

SESSION_ENGINE = "django.contrib.sessions.backends.cached_db"
SESSION_COOKIE_NAME = "regionlens_sessionid"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
LANGUAGE_COOKIE_NAME = "regionlens_language"
LANGUAGE_COOKIE_SAMESITE = "Lax"

# ---------------------------------------------------------------------------------------
# Почта
# ---------------------------------------------------------------------------------------

SMTP_BACKEND = "django.core.mail.backends.smtp.EmailBackend"


def mailer(backend: str) -> dict[str, object]:
    """
    Описание почтового узла для ``MAILERS``; параметры соединения — только SMTP.

    Переменные окружения — ``EMAIL_HOST`` и др.: в Django 6.1 устарели настройки, а не они.
    """
    if backend != SMTP_BACKEND:
        return {"BACKEND": backend}
    return {
        "BACKEND": backend,
        "OPTIONS": {
            "host": env.str("EMAIL_HOST", default="localhost"),
            "port": env.int("EMAIL_PORT", default=465),
            "username": env.str("EMAIL_HOST_USER", default=""),
            "password": env.str("EMAIL_HOST_PASSWORD", default=""),
            "use_ssl": env.bool("EMAIL_USE_SSL", default=True),
            # Письмо уходит внутри запроса.
            "timeout": 10,
        },
    }


MAILERS = {
    "default": mailer(
        env.str("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")
    ),
}
DEFAULT_FROM_EMAIL = env.str("DEFAULT_FROM_EMAIL", default="RegionLens <noreply@regionlens.ru>")
SERVER_EMAIL = env.str("SERVER_EMAIL", default=DEFAULT_FROM_EMAIL)
EMAIL_SUBJECT_PREFIX = "[RegionLens] "

# ---------------------------------------------------------------------------------------
# Аналитический склад DuckDB: собирается командой etl_build, в репозитории не хранится
# ---------------------------------------------------------------------------------------

DUCKDB_PATH = BASE_DIR / env.str("DUCKDB_PATH", default="data/warehouse/regionlens.duckdb")
# Предел памяти одного соединения на чтение; соединений — по потоку рабочих процессов.
DUCKDB_MEMORY_LIMIT = env.str("DUCKDB_MEMORY_LIMIT", default="256MB")
# Предел памяти сборки склада: она идёт отдельным процессом одна за раз.
DUCKDB_BUILD_MEMORY_LIMIT = env.str("DUCKDB_BUILD_MEMORY_LIMIT", default="1GB")
DUCKDB_THREADS = env.int("DUCKDB_THREADS", default=2)
# Склад открывается на чтение: DuckDB допускает одного писателя или многих читателей,
# а сборка открывает своё соединение (writable_connection).
DUCKDB_READ_ONLY = env.bool("DUCKDB_READ_ONLY", default=True)

SOURCE_PARQUET_PATH = BASE_DIR / env.str(
    "SOURCE_PARQUET_PATH",
    default="data/raw/data_regions_collection_102_v20260313.parquet",
)

# Каталог наборов, загруженных из панели управления.
DATASET_UPLOAD_DIR = BASE_DIR / env.str("DATASET_UPLOAD_DIR", default="data/uploads")
# Наибольший размер загружаемого набора.
DATASET_UPLOAD_MAX_BYTES = env.int("DATASET_UPLOAD_MAX_BYTES", default=200 * 1024 * 1024)

# Сроки хранения резервных копий на сервере и во внешнем хранилище (scripts/backup.sh);
# их называет страница условий. 0 во внешнем хранилище — без срока.
BACKUP_RETENTION_DAYS = env.int("BACKUP_RETENTION_DAYS", default=14)
BACKUP_REMOTE = env.str("BACKUP_REMOTE", default="")
BACKUP_REMOTE_RETENTION_DAYS = env.int("BACKUP_REMOTE_RETENTION_DAYS", default=30)

# Каталог справочников, подготовленных вручную (территории, смежность, единицы измерения).
REFERENCE_DIR = DATA_DIR / "reference"

# ---------------------------------------------------------------------------------------
# Сбор выпусков внешних источников (команда collect)
# ---------------------------------------------------------------------------------------

# Неизменяемый архив полученных файлов с описью и разобранные выпуски (воспроизводимы из архива).
SOURCE_ARCHIVE_DIR = BASE_DIR / env.str("SOURCE_ARCHIVE_DIR", default="data/archive")
SOURCE_PARSED_DIR = BASE_DIR / env.str("SOURCE_PARSED_DIR", default="data/sources")
# Корневые сертификаты НУЦ Минцифры: ими подписаны сайты Росстата, системы им не доверяют.
SOURCE_EXTRA_CA_FILE = BASE_DIR / env.str(
    "SOURCE_EXTRA_CA_FILE", default="data/reference/certs/russian_trusted_ca.crt"
)
# Распаковщик RAR: bsdtar (libarchive); в Windows 11 он же — системный tar.exe.
SOURCE_BSDTAR = env.str("SOURCE_BSDTAR", default="")
SOURCE_HTTP_TIMEOUT = env.int("SOURCE_HTTP_TIMEOUT", default=60)
SOURCE_HTTP_ATTEMPTS = env.int("SOURCE_HTTP_ATTEMPTS", default=4)
SOURCE_USER_AGENT = env.str(
    "SOURCE_USER_AGENT",
    default=f"RegionLens/{PROJECT_VERSION} (+mailto:{PROJECT_AUTHOR_EMAIL})",
)

# ---------------------------------------------------------------------------------------
# Статистика посещений по журналу Caddy (команда visits)
# ---------------------------------------------------------------------------------------

# Журналы Caddy (на стенде — том только для чтения) и отчёт с накопленной базой GoAccess.
VISITS_LOG_DIR = BASE_DIR / env.str("VISITS_LOG_DIR", default="logs/caddy")
VISITS_DIR = BASE_DIR / env.str("VISITS_DIR", default="data/visits")
GOACCESS_BIN = env.str("GOACCESS_BIN", default="goaccess")

# ---------------------------------------------------------------------------------------
# Программный интерфейс REST: только чтение, без ключей, предел по адресу клиента
# ---------------------------------------------------------------------------------------

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "DEFAULT_PAGINATION_CLASS": "apps.api.pagination.CatalogPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_FILTER_BACKENDS": ["django_filters.rest_framework.DjangoFilterBackend"],
    "DEFAULT_THROTTLE_CLASSES": [
        "apps.api.throttling.AnonymousHourlyThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        # Хватает на несколько рядов целиком, но не на весь склад.
        "anon": env.str("API_ANON_RATE", default="1000/hour"),
    },
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "EXCEPTION_HANDLER": "apps.api.exceptions.api_exception_handler",
}

SPECTACULAR_SETTINGS = {
    "TITLE": f"{PROJECT_NAME} API",
    "DESCRIPTION": (
        "Программный доступ к социально-экономическим показателям регионов России. "
        "Интерфейс открыт и работает только на чтение; ключей доступа нет, предел числа "
        "обращений один для всех и считается по адресу клиента. Значения используются "
        "на условиях их источников: источник наблюдения — в поле source, условия "
        "источников — в /api/v1/sources/."
    ),
    "VERSION": PROJECT_VERSION,
    "SERVE_INCLUDE_SCHEMA": False,
    "SCHEMA_PATH_PREFIX": "/api/v1",
    "COMPONENT_SPLIT_REQUEST": True,
    "SORT_OPERATIONS": False,
    "CONTACT": {"name": PROJECT_AUTHOR, "email": PROJECT_AUTHOR_EMAIL},
    # Условия у каждого источника свои (Росстат, Банк России, ФНС) — ссылка на страницу условий.
    "LICENSE": {"name": "Условия источников данных", "url": "/ru/terms/#api"},
    # Файлы Swagger UI — из приложения, без внешней сети доставки.
    "SWAGGER_UI_DIST": "SIDECAR",
    "SWAGGER_UI_FAVICON_HREF": "SIDECAR",
    "TAGS": [
        {"name": "Территории", "description": "Справочник субъектов и федеральных округов"},
        {"name": "Показатели", "description": "Каталог показателей и их рядов"},
        {"name": "Наблюдения", "description": "Значения рядов по территориям и годам"},
        {"name": "Рейтинги", "description": "Позиции регионов по показателю за год"},
    ],
}

# ---------------------------------------------------------------------------------------
# Формирование документов
# ---------------------------------------------------------------------------------------

# Шрифт для PDF: явный путь или первый системный с кириллицей из перечня.
PDF_FONT_PATH = env.str("PDF_FONT_PATH", default="")
PDF_FONT_CANDIDATES = [
    str(DATA_DIR / "fonts" / "DejaVuSans.ttf"),
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
]

# ---------------------------------------------------------------------------------------
# Прикладные ограничения
# ---------------------------------------------------------------------------------------

ANALYTICS_CACHE_TTL = env.int("ANALYTICS_CACHE_TTL", default=3600)

# Пороги пригодности ряда для сравнительного анализа: доля регионов и число лет.
SERIES_MIN_REGION_COVERAGE = 0.80
SERIES_MIN_YEARS = 10
# Ряды Банка России и ФНС начинаются с 2019 и 2016 годов: для них порог по годам ниже.
SERIES_MIN_YEARS_SOURCE = 5

# ---------------------------------------------------------------------------------------
# Логирование
# ---------------------------------------------------------------------------------------

LOG_LEVEL = env.str("LOG_LEVEL", default="INFO")
LOG_FORMAT = env.str("LOG_FORMAT", default="console")

# Письмо об ошибке 500 уходит адресатам из DJANGO_ADMINS (через запятую), одна и та же
# ошибка — не чаще раза в час (apps.core.logging.ErrorMailHandler).
ADMINS = env.list("DJANGO_ADMINS", default=[])

# Письма об ошибках — без значений полей форм (политика обработки персональных данных).
DEFAULT_EXCEPTION_REPORTER_FILTER = "apps.core.logging.FormFreeReporterFilter"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "require_debug_false": {"()": "django.utils.log.RequireDebugFalse"},
    },
    "formatters": {
        "console": {
            "format": "{asctime} {levelname:<8} {name:<28} {message}",
            "style": "{",
        },
        "json": {
            "()": "apps.core.logging.JSONFormatter",
        },
        "django.server": {
            "()": "django.utils.log.ServerFormatter",
            "format": "[{server_time}] {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": LOG_FORMAT,
        },
        "django.server": {
            "class": "logging.StreamHandler",
            "formatter": "django.server",
        },
        "error_mail": {
            "level": "ERROR",
            "filters": ["require_debug_false"],
            "class": "apps.core.logging.ErrorMailHandler",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": LOG_LEVEL,
    },
    "loggers": {
        # Обработчики Django по умолчанию заменяются: иначе письмо уходило бы без предела.
        "django": {"handlers": [], "level": "INFO", "propagate": True},
        "django.request": {"handlers": ["error_mail"], "propagate": True},
        "django.server": {"handlers": ["django.server"], "level": "INFO", "propagate": False},
        "django.db.backends": {"level": "WARNING", "propagate": True},
        "apps": {"level": LOG_LEVEL, "propagate": True},
    },
}

# ---------------------------------------------------------------------------------------
# Заголовки безопасности, общие для всех сред
# ---------------------------------------------------------------------------------------

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"

# Политика безопасности содержимого: все ресурсы — с собственного домена.
# В разработке она только сообщает о нарушениях (local.py).
CSP_POLICY: dict[str, list[str] | bool] = {
    "default-src": [CSP.SELF],
    # Встроенные сценарии запрещены.
    "script-src": [CSP.SELF],
    "style-src": [CSP.SELF, CSP.UNSAFE_INLINE],  # встроенные стили графиков ECharts
    "img-src": [CSP.SELF, "data:", "blob:"],  # data: нужен для выгрузки графиков в PNG
    "font-src": [CSP.SELF],
    "connect-src": [CSP.SELF],
    "frame-ancestors": [CSP.NONE],
    "base-uri": [CSP.SELF],
    "form-action": [CSP.SELF],
    "object-src": [CSP.NONE],
}
SECURE_CSP = CSP_POLICY
