#!/usr/bin/env bash
# =====================================================================================
# docker compose рабочего стенда: описание служб, дополнения этого сервера и .env.
#
#   ./scripts/compose.sh ps
#   ./scripts/compose.sh logs -f --tail 100 web
#   ./scripts/compose.sh exec web python manage.py createsuperuser
#
# Им пользуются deploy.sh, backup.sh, restore.sh и юниты systemd. --env-file нужен
# в каждом вызове: иначе Compose ищет docker/.env, POSTGRES_PASSWORD не подставляется,
# и разбор описания обрывается даже у `logs` и `ps`. docker/compose.server.yml —
# необязательные дополнения этого сервера (порты, пределы памяти): выкладка
# перезаписывает compose.prod.yml, а его не трогает.
# =====================================================================================

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Git Bash в Windows переписывает аргументы-пути (/app → C:/Program Files/Git/app):
# файлы описания передаются путями Windows, остальные аргументы — без переписывания.
if command -v cygpath >/dev/null 2>&1; then
    ROOT="$(cygpath -m "$ROOT")"
    export MSYS2_ARG_CONV_EXCL="*"
fi
FILES=(-f "${ROOT}/docker/compose.prod.yml")
if [ -f "${ROOT}/docker/compose.server.yml" ]; then
    FILES+=(-f "${ROOT}/docker/compose.server.yml")
fi

exec docker compose "${FILES[@]}" --env-file "${ROOT}/.env" "$@"
