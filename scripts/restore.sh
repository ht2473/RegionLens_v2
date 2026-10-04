#!/usr/bin/env bash
# =====================================================================================
# Восстановление рабочего стенда из резервной копии.
#
#   ./scripts/restore.sh --check backups/db-20260822-030000.dump   # проверить копию
#   ./scripts/restore.sh backups/db-20260822-030000.dump           # восстановить базу
#   ./scripts/restore.sh --all 20260822-030000                     # база и файлы
#
# Режим --check читает оглавление выгрузки, ничего не изменяя; проверять копию
# стоит после каждой смены схемы данных.
#
# Восстановление останавливает приложение, чтобы запись не смешала старые и новые данные.
# =====================================================================================

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Описание служб, дополнения сервера и .env подставляет compose.sh.
COMPOSE="${ROOT}/scripts/compose.sh"
BACKUP_DIR="${ROOT}/backups"
# Каталоги для томов docker — путями хоста; в Git Bash под Windows — видом Windows.
HOST_ROOT="$ROOT"
if command -v cygpath >/dev/null 2>&1; then
    HOST_ROOT="$(cygpath -m "$ROOT")"
fi
MODE="database"
ARGUMENT=""

# Имя базы и пользователя — из .env стенда, если их не задало окружение.
for name in POSTGRES_USER POSTGRES_DB; do
    if [ -z "${!name:-}" ] && [ -f "${ROOT}/.env" ]; then
        value="$(sed -n "s/^${name}=//p" "${ROOT}/.env" | tail -1 | tr -d '\r')"
        [ -z "$value" ] || export "${name}=${value}"
    fi
done

while [ $# -gt 0 ]; do
    case "$1" in
        --check)
            MODE="check"
            ARGUMENT="$2"
            shift
            ;;
        --all)
            MODE="all"
            ARGUMENT="$2"
            shift
            ;;
        *) ARGUMENT="$1" ;;
    esac
    shift
done

if [ -z "$ARGUMENT" ]; then
    echo "Укажите файл выгрузки или отметку времени. См. заголовок сценария." >&2
    exit 2
fi

step() {
    printf '\n\033[1m==> %s\033[0m\n' "$1"
}

# --- Проверка копии без изменений ---------------------------------------------------------
if [ "$MODE" = "check" ]; then
    step "Оглавление выгрузки"
    $COMPOSE run --rm --no-deps -T \
        --volume "${HOST_ROOT}:/host:ro" \
        postgres pg_restore --list "/host/${ARGUMENT#"$ROOT/"}" | head -40

    printf '\nВсего объектов: '
    $COMPOSE run --rm --no-deps -T \
        --volume "${HOST_ROOT}:/host:ro" \
        postgres pg_restore --list "/host/${ARGUMENT#"$ROOT/"}" | grep -c ';' || true
    exit 0
fi

if [ "$MODE" = "all" ]; then
    DB_FILE="${BACKUP_DIR}/db-${ARGUMENT}.dump"
    FILES_ARCHIVE="${BACKUP_DIR}/files-${ARGUMENT}.tar.gz"
else
    DB_FILE="$ARGUMENT"
    FILES_ARCHIVE=""
fi

[ -f "$DB_FILE" ] || {
    echo "Файл выгрузки не найден: $DB_FILE" >&2
    exit 1
}

printf 'Будут заменены данные рабочего стенда. Продолжить? [y/N] '
read -r answer
case "$answer" in
    y | Y) ;;
    *)
        echo "Отменено"
        exit 0
        ;;
esac

# --- Остановка приложения -------------------------------------------------------------------
step "Остановка приложения"
$COMPOSE stop web

# --- Восстановление базы ----------------------------------------------------------------------
step "Загрузка выгрузки в базу данных"
# --clean --if-exists удаляет прежние объекты: иначе загрузка падает на первой же таблице.
$COMPOSE exec -T postgres pg_restore \
    --username "${POSTGRES_USER:-regionlens}" \
    --dbname "${POSTGRES_DB:-regionlens}" \
    --clean --if-exists --no-owner --no-privileges \
    < "$DB_FILE"

# --- Загруженные наборы, архив выпусков, таблицы пользователей ---------------------------
if [ -n "$FILES_ARCHIVE" ] && [ -f "$FILES_ARCHIVE" ]; then
    step "Восстановление наборов, архива выпусков, статистики посещений и таблиц пользователей"
    $COMPOSE run --rm --no-deps -T \
        --volume "${HOST_ROOT}/backups:/backup:ro" \
        web tar xzf "/backup/$(basename "$FILES_ARCHIVE")" -C /app
fi

# --- Запуск --------------------------------------------------------------------------------------------
step "Запуск приложения"
$COMPOSE up -d web
# Точка входа применяет миграции новой версии к восстановленной базе: разбор и сборка —
# после того, как приложение ответит.
attempt=0
until $COMPOSE exec -T web curl --fail --silent http://127.0.0.1:8000/healthz >/dev/null 2>&1; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 60 ]; then
        echo "Приложение не отвечает. Журнал последних событий:" >&2
        $COMPOSE logs --tail 50 web >&2
        exit 1
    fi
    sleep 2
done

# Таблицы гостей в копию не входят: их записи в базе и каталоги без записей убирает очистка.
step "Очистка таблиц без файлов"
$COMPOSE exec -T web python manage.py prune_personal_data

step "Разбор архива выпусков источников"
# Разобранное в копию не входит: оно воспроизводится из архива.
$COMPOSE exec -T web python manage.py collect --all --reparse --no-build

step "Пересборка аналитического склада"
# Склад в копию не входит и должен соответствовать восстановленному каталогу.
$COMPOSE exec -T web python manage.py etl_build --full

step "Проверка готовности"
$COMPOSE exec -T web curl --silent http://127.0.0.1:8000/readyz

printf '\n\033[1;32mВосстановление завершено\033[0m\n'
