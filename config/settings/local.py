"""Настройки разработки: отладка, письма в консоль, простые пароли, без HTTPS."""

from __future__ import annotations

from .base import *  # noqa: F403
from .base import CSP_POLICY, INSTALLED_APPS, MIDDLEWARE, env

DEBUG = True

# Любые хосты локальной сети — для проверки с телефона.
ALLOWED_HOSTS = ["*"]

# Без постоянных подключений: runserver заводит поток на запрос, и подключения потоков
# копятся до предела PostgreSQL («too many clients») при частых обращениях.
DATABASES["default"]["CONN_MAX_AGE"] = 0  # noqa: F405

# --- Инструменты разработчика ------------------------------------------------------------
INSTALLED_APPS = [*INSTALLED_APPS, "django_extensions"]

# Панель отладки — только явно (DEBUG_TOOLBAR=True в .env): она замедляет страницы.
DEBUG_TOOLBAR = env.bool("DEBUG_TOOLBAR", default=False)
if DEBUG_TOOLBAR:
    INSTALLED_APPS = [*INSTALLED_APPS, "debug_toolbar"]
    # После GZipMiddleware: в сжатый ответ панель не встроится.
    MIDDLEWARE = [*MIDDLEWARE, "debug_toolbar.middleware.DebugToolbarMiddleware"]

# Политика содержимого только сообщает о нарушениях: панель отладки встраивает сценарии.
SECURE_CSP = {}
SECURE_CSP_REPORT_ONLY = CSP_POLICY

# --- Статика в разработке -------------------------------------------------------------------
# Статику отдаёт WhiteNoise, как на стенде: runserver не выставляет Cache-Control,
# и правка CSS или JS видна не с первой перезагрузки. Приложение — первым в перечне.
INSTALLED_APPS = ["whitenoise.runserver_nostatic", *INSTALLED_APPS]

# Сверка с сервером при каждом обращении и перечитывание каталога.
WHITENOISE_MAX_AGE = 0
WHITENOISE_AUTOREFRESH = True

# Панель отладки показывается только при обращении с локальной машины.
INTERNAL_IPS = ["127.0.0.1", "::1"]


def _show_debug_toolbar(request: object) -> bool:
    """Показывать панель отладки, кроме запросов HTMX и адресов с ``?notoolbar``."""
    if request.headers.get("HX-Request"):  # type: ignore[attr-defined]
        return False
    if "notoolbar" in request.GET:  # type: ignore[attr-defined]
        return False
    return DEBUG


DEBUG_TOOLBAR_CONFIG = {"SHOW_TOOLBAR_CALLBACK": _show_debug_toolbar}

# --- Упрощения для разработки -------------------------------------------------------------
MAILERS = {"default": {"BACKEND": "django.core.mail.backends.console.EmailBackend"}}

# Ослабленные требования к паролю для тестовых учётных записей.
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 8},
    },
]

# Статика без отпечатков и сжатия.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
