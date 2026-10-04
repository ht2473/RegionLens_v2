#!/usr/bin/env bash
# =====================================================================================
# Резервное копирование рабочего стенда.
#
#   ./scripts/backup.sh            # копия в backups/
#   ./scripts/backup.sh --quiet    # без вывода, для вызова из других сценариев
#   ./scripts/backup.sh --dir ПУТЬ # другой каталог назначения
#
# Запускается таймером хоста (deploy/systemd/regionlens-backup.timer): копия нужна
# и тогда, когда приложение упало.
#
# Копируются база PostgreSQL, загруженные из панели выпуски набора, архив выпусков
# источников, накопленная статистика посещений и таблицы пользователей с учётной записью
# (таблицы гостей живут сутки и в копию не входят). Склад DuckDB и разобранные выпуски
# не копируются: они воспроизводятся командами за минуты.
# =====================================================================================

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Описание служб, дополнения сервера и .env подставляет compose.sh.
COMPOSE="${ROOT}/scripts/compose.sh"
TARGET_DIR="${ROOT}/backups"
QUIET=0

# Настройки копии — из .env стенда, если их не задало окружение: таймер передаёт .env
# через EnvironmentFile, а ручной запуск и копия перед выкладкой — нет.
for name in BACKUP_RETENTION_DAYS BACKUP_REMOTE BACKUP_REMOTE_RETENTION_DAYS POSTGRES_USER POSTGRES_DB; do
    if [ -z "${!name:-}" ] && [ -f "${ROOT}/.env" ]; then
        value="$(sed -n "s/^${name}=//p" "${ROOT}/.env" | tail -1 | tr -d '\r')"
        [ -z "$value" ] || export "${name}=${value}"
    fi
done

while [ $# -gt 0 ]; do
    case "$1" in
        --quiet) QUIET=1 ;;
        --dir)
            TARGET_DIR="$2"
            shift
            ;;
        *)
            echo "Неизвестный ключ: $1" >&2
            exit 2
            ;;
    esac
    shift
done

# Отказ отмечается в панели: иначе она показывала бы последнюю удачную копию как свежую.
on_failure() {
    $COMPOSE exec -T web python manage.py record_backup --failed \
        --error "backup.sh завершился с ошибкой в строке $1" >/dev/null 2>&1 || true
}
trap 'on_failure $LINENO' ERR

# Две недели ежедневных копий — чтобы успеть заметить порчу данных и откатиться до неё.
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"

say() {
    [ "$QUIET" -eq 1 ] || printf '%s\n' "$1"
}

stamp="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$TARGET_DIR"
# Каталог для тома docker — путём хоста; в Git Bash под Windows — видом Windows.
HOST_TARGET_DIR="$TARGET_DIR"
if command -v cygpath >/dev/null 2>&1; then
    HOST_TARGET_DIR="$(cygpath -m "$TARGET_DIR")"
fi

# --- База данных ------------------------------------------------------------------------
# Формат custom: сжат и восстанавливается выборочно.
say "Выгрузка базы данных…"
$COMPOSE exec -T postgres pg_dump \
    --username "${POSTGRES_USER:-regionlens}" \
    --dbname "${POSTGRES_DB:-regionlens}" \
    --format=custom \
    --compress=6 \
    > "${TARGET_DIR}/db-${stamp}.dump"

# --- Файлы ------------------------------------------------------------------------------------
# Загруженные наборы, архив выпусков источников и история посещений: журнал Caddy хранится
# 30 суток, а история — за всё время.
say "Архивирование наборов, архива выпусков, статистики посещений и таблиц пользователей…"
# Каталоги таблиц гостей перечисляет приложение; временные каталоги DuckDB не нужны.
$COMPOSE run --rm --no-deps -T \
    --volume "${HOST_TARGET_DIR}:/backup" \
    web sh -c "python manage.py userdata_guest_dirs > /tmp/guests \
        && tar czf /backup/files-${stamp}.tar.gz -C /app --exclude-from=/tmp/guests \
            --exclude='*.duckdb.tmp' --exclude='.duckdb-tmp' \
            data/uploads data/archive data/visits data/userdata"

# --- Ротация --------------------------------------------------------------------------------------
say "Удаление копий старше ${RETENTION_DAYS} суток…"
find "$TARGET_DIR" -maxdepth 1 -type f \
    \( -name 'db-*.dump' -o -name 'files-*.tar.gz' \) \
    -mtime "+${RETENTION_DAYS}" -delete

# --- Вывоз за пределы сервера ------------------------------------------------------------------
# Если задан адрес хранилища rclone (BACKUP_REMOTE, например selectel:regionlens-backups),
# свежая копия отправляется туда, а копии старше BACKUP_REMOTE_RETENTION_DAYS суток
# (по умолчанию 30; 0 — хранить все) там удаляются. Ключи доступа — в настройках rclone
# пользователя, а не в .env: .env попадает и в контейнеры.
OFFSITE=""
if [ -n "${BACKUP_REMOTE:-}" ]; then
    say "Вывоз копии в ${BACKUP_REMOTE}…"
    rclone copy "${TARGET_DIR}" "${BACKUP_REMOTE}" --include "*-${stamp}.*"
    OFFSITE="--offsite"
    remote_days="${BACKUP_REMOTE_RETENTION_DAYS:-30}"
    if [ "$remote_days" -gt 0 ]; then
        say "Удаление копий старше ${remote_days} суток в ${BACKUP_REMOTE}…"
        rclone delete "${BACKUP_REMOTE}" --min-age "${remote_days}d" \
            --include "db-*.dump" --include "files-*.tar.gz"
    fi
fi

# --- Отметка для панели управления -------------------------------------------------------------
size="$(stat -c %s "${TARGET_DIR}/db-${stamp}.dump")"
# shellcheck disable=SC2086 — пустой признак не должен стать отдельным аргументом.
$COMPOSE exec -T web python manage.py record_backup \
    --file "db-${stamp}.dump" --size "$size" $OFFSITE >/dev/null

# --- Итог -------------------------------------------------------------------------------------------
if [ "$QUIET" -eq 0 ]; then
    printf '\nСозданные файлы:\n'
    ls -lh "${TARGET_DIR}"/*-"${stamp}".* | awk '{printf "  %s  %s\n", $5, $9}'
    printf '\nПроверьте восстановимость копии: ./scripts/restore.sh --check %s\n' \
        "${TARGET_DIR}/db-${stamp}.dump"
fi
