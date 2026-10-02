# =====================================================================================
# Образ приложения RegionLens.
#
# Сборка в два этапа: зависимости ставятся в отдельное окружение, в рабочий образ
# переносятся только оно и исходный код — без компилятора и кэша менеджера пакетов.
#
# Сборка:  docker build -f docker/Dockerfile -t regionlens:latest .
# =====================================================================================

# --- Этап 1: зависимости --------------------------------------------------------------
FROM python:3.14-slim-trixie AS builder

# uv — из официального образа; зависимости ставятся по uv.lock.
COPY --from=ghcr.io/astral-sh/uv:0.11.15 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Слой зависимостей пересобирается только при изменении uv.lock.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev --extra production

# --- Этап 2: рабочий образ ------------------------------------------------------------
FROM python:3.14-slim-trixie AS runtime

# Библиотеки времени выполнения:
#   libpq5     — клиент PostgreSQL для psycopg;
#   fonts-dejavu-core — шрифт с кириллицей для PDF (во встроенных шрифтах reportlab её нет);
#   curl       — проверка состояния контейнера;
#   libarchive-tools — bsdtar: распаковка выпусков Росстата в RAR;
#   goaccess   — статистика посещений по журналу Caddy (manage.py visits).
# Каталоги перевода собирает scripts/i18n_tools.py, GNU gettext не нужен.
# DEBIAN_MIRROR — зеркало вместо deb.debian.org (например, http://mirror.yandex.ru):
# из России часть адресов его сети доставки не отвечает, и загрузка пакетов виснет.
ARG DEBIAN_MIRROR=""
RUN if [ -n "$DEBIAN_MIRROR" ]; then \
        sed -i "s#http://deb.debian.org#$DEBIAN_MIRROR#g" /etc/apt/sources.list.d/debian.sources; \
    fi \
    && apt-get update \
    && apt-get install --no-install-recommends -y \
        libpq5 \
        fonts-dejavu-core \
        curl \
        libarchive-tools \
        goaccess \
    && rm -rf /var/lib/apt/lists/*

# Непривилегированный пользователь: процесс, доступный из сети, не пишет в системные каталоги.
RUN groupadd --system --gid 1000 regionlens \
    && useradd --system --uid 1000 --gid regionlens --create-home regionlens

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=config.settings.production

WORKDIR /app

COPY --from=builder --chown=regionlens:regionlens /app/.venv /app/.venv
COPY --chown=regionlens:regionlens . .

# Рабочая копия правится и в Windows: окончания строк сценариев приводятся к LF
# (с CRLF — `env: 'sh\r': No such file or directory`).
RUN sed -i 's/\r$//' /app/docker/entrypoint.sh /app/scripts/*.sh

# Каталоги записи приложения: в compose это тома, но нужны и при запуске без томов.
RUN mkdir -p /app/data/warehouse /app/data/uploads /app/data/archive /app/data/sources \
        /app/data/visits /app/data/tmp /app/staticfiles /app/logs/caddy \
    && chown -R regionlens:regionlens /app/data /app/staticfiles /app/logs

USER regionlens

# Статика и переводы собираются при сборке образа: запуск быстрее, результат одинаков.
RUN DJANGO_SECRET_KEY=build-only \
    DATABASE_URL=postgres://build:build@localhost/build \
    python manage.py collectstatic --noinput --clear \
    && python scripts/i18n_tools.py compile

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl --fail --silent http://127.0.0.1:8000/healthz || exit 1

ENTRYPOINT ["/app/docker/entrypoint.sh"]

# Процессы — на два ядра VPS; потоки — потому что запрос в основном ждёт PostgreSQL и DuckDB.
CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "3", \
     "--threads", "4", \
     "--worker-class", "gthread", \
     "--timeout", "120", \
     "--graceful-timeout", "30", \
     "--max-requests", "2000", \
     "--max-requests-jitter", "200", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
