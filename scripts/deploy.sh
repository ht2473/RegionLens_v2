#!/usr/bin/env bash
# =====================================================================================
# Выкладка RegionLens на сервер без git и реестра образов.
#
# На машине разработчика, из корня проекта:
#   ./scripts/deploy.sh --host regionlens@203.0.113.10             # обычная выкладка
#   ./scripts/deploy.sh --host regionlens@203.0.113.10 --with-etl  # и пересборка склада
#   ./scripts/deploy.sh --host regionlens@203.0.113.10 --rollback  # вернуть прежний образ
#
# Хост, порт SSH и каталог стенда задаются и переменными DEPLOY_HOST, DEPLOY_PORT (22)
# и DEPLOY_DIR (/srv/regionlens); зеркало Debian для сборки — DEPLOY_DEBIAN_MIRROR
# (http://mirror.yandex.ru; пустое значение — deb.debian.org), дополнительные ключи
# сборки — DEPLOY_BUILD_ARGS. Образ собирается здесь и уходит потоком
# `docker save | ssh … docker load`; образы Caddy, PostgreSQL и Valkey — так же, если
# на сервере их нет или они другие: Docker Hub из России доступен не всегда. Затем
# на сервер кладутся описание служб, Caddyfile, сценарии и юниты systemd, и там этот
# же сценарий с ключом --apply снимает копию, заменяет контейнеры и ждёт готовности.
#
# Отказ на любом шаге до замены контейнеров оставляет работающую версию. Откат
# (--rollback) возвращает прежний образ, но не схему базы: если выкладка её меняла,
# база возвращается копией, снятой перед выкладкой (scripts/restore.sh).
# =====================================================================================

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE_NAME="regionlens"
# Сколько прежних образов приложения хранить на сервере для отката.
KEEP_IMAGES=3

HOST="${DEPLOY_HOST:-}"
PORT="${DEPLOY_PORT:-22}"
REMOTE_DIR="${DEPLOY_DIR:-/srv/regionlens}"
MODE="deploy"
TAG=""
WITH_ETL=0

usage() {
    sed -n '3,22p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
    case "$1" in
        --host)
            HOST="$2"
            shift
            ;;
        --port)
            PORT="$2"
            shift
            ;;
        --dir)
            REMOTE_DIR="$2"
            shift
            ;;
        --with-etl) WITH_ETL=1 ;;
        --rollback) MODE="rollback" ;;
        # Режимы сервера: их вызывает этот же сценарий по SSH.
        --apply)
            MODE="apply"
            TAG="$2"
            shift
            ;;
        --apply-rollback) MODE="apply-rollback" ;;
        -h | --help)
            usage
            exit 0
            ;;
        *)
            echo "Неизвестный ключ: $1" >&2
            exit 2
            ;;
    esac
    shift
done

step() {
    printf '\n\033[1m==> %s\033[0m\n' "$1"
}

fail() {
    echo "$1" >&2
    exit 1
}

# =====================================================================================
# На сервере
# =====================================================================================

COMPOSE="${ROOT}/scripts/compose.sh"
DEPLOYED_FILE="${ROOT}/.deployed"

wait_ready() {
    step "Ожидание готовности приложения"
    local attempt=0
    until $COMPOSE exec -T web curl --fail --silent http://127.0.0.1:8000/healthz >/dev/null 2>&1; do
        attempt=$((attempt + 1))
        if [ "$attempt" -ge 60 ]; then
            echo "Приложение не отвечает. Журнал последних событий:" >&2
            $COMPOSE logs --tail 50 web >&2
            exit 1
        fi
        sleep 2
    done
    echo "Приложение отвечает"
}

report_units() {
    # Юниты ставит администратор (нужен sudo); здесь — только напоминание о различиях.
    local unit installed stale=0
    for unit in "${ROOT}"/deploy/systemd/*; do
        installed="/etc/systemd/system/$(basename "$unit")"
        if [ ! -f "$installed" ]; then
            echo "Не установлен: $(basename "$unit")"
        elif ! cmp -s "$unit" "$installed"; then
            echo "Изменился: $(basename "$unit")"
        else
            continue
        fi
        stale=1
    done
    if [ "$stale" -eq 0 ]; then
        echo "Установлены и совпадают"
    else
        echo "Установить: sudo cp ${ROOT}/deploy/systemd/regionlens-* /etc/systemd/system/ && sudo systemctl daemon-reload"
    fi
}

switch_to() {
    # Контейнеры берут образ regionlens:latest (REGIONLENS_IMAGE в .env), поэтому
    # выкладка и откат — перестановка этого ярлыка на нужный образ.
    docker tag "$1" "${IMAGE_NAME}:latest"
    step "Запуск служб на образе $1"
    $COMPOSE up -d --no-build --remove-orphans
    wait_ready
}

finish() {
    step "Проверка готовности зависимостей"
    $COMPOSE exec -T web curl --silent http://127.0.0.1:8000/readyz
    echo
    step "Состояние служб"
    $COMPOSE ps
    step "Юниты systemd"
    report_units
    printf '\n\033[1;32mГотово: %s\033[0m\n' "$(cat "$DEPLOYED_FILE")"
}

if [ "$MODE" = "apply" ]; then
    cd "$ROOT"
    [ -f "${ROOT}/.env" ] || fail "Нет ${ROOT}/.env: скопируйте .env.example и заполните его."
    chmod +x "${ROOT}"/scripts/*.sh
    docker image inspect "$TAG" >/dev/null 2>&1 || fail "Образа $TAG на сервере нет."
    if grep -q '^REGIONLENS_IMAGE=' "${ROOT}/.env" &&
        ! grep -qx "REGIONLENS_IMAGE=${IMAGE_NAME}:latest" "${ROOT}/.env"; then
        fail "В .env REGIONLENS_IMAGE должен быть ${IMAGE_NAME}:latest: выкладка переставляет этот ярлык."
    fi

    step "Проверка конфигурации"
    $COMPOSE config --quiet
    echo "Конфигурация корректна"

    # Копия — до миграций новой версии; при первой выкладке базы ещё нет.
    if $COMPOSE ps --status running --services 2>/dev/null | grep -qx postgres; then
        step "Резервная копия"
        "${ROOT}/scripts/backup.sh" --quiet
    fi

    if [ -f "$DEPLOYED_FILE" ]; then
        cp "$DEPLOYED_FILE" "${DEPLOYED_FILE}.previous"
    fi
    switch_to "$TAG"
    echo "$TAG" >"$DEPLOYED_FILE"

    if [ "$WITH_ETL" -eq 1 ]; then
        step "Пересборка аналитического склада"
        $COMPOSE exec -T web python manage.py etl_build --full
    fi

    step "Прежние образы"
    # Остаются последние KEEP_IMAGES образов выкладки (ярлык «версия-дата-время»);
    # прочие ярлыки не трогаются. Перечень Docker упорядочен от новых к старым.
    docker image ls "$IMAGE_NAME" --format '{{.Tag}}' | grep -E '^[0-9.]+-[0-9]{8}-[0-9]{6}$' |
        tail -n "+$((KEEP_IMAGES + 1))" | while read -r old; do
        docker image rm "${IMAGE_NAME}:${old}" >/dev/null && echo "Удалён ${IMAGE_NAME}:${old}" || true
    done

    finish
    exit 0
fi

if [ "$MODE" = "apply-rollback" ]; then
    cd "$ROOT"
    [ -f "${DEPLOYED_FILE}.previous" ] || fail "Прежняя выкладка не записана: откатываться не к чему."
    previous="$(cat "${DEPLOYED_FILE}.previous")"
    current="$(cat "$DEPLOYED_FILE" 2>/dev/null || true)"
    docker image inspect "$previous" >/dev/null 2>&1 || fail "Образа $previous на сервере уже нет."
    switch_to "$previous"
    echo "$previous" >"$DEPLOYED_FILE"
    [ -z "$current" ] || echo "$current" >"${DEPLOYED_FILE}.previous"
    finish
    exit 0
fi

# =====================================================================================
# На машине разработчика
# =====================================================================================

[ -n "$HOST" ] || fail "Не задан сервер: --host пользователь@адрес или DEPLOY_HOST."
# DEPLOY_SSH заменяет подключение целиком (например, для проверки на пробном стенде).
read -r -a SSH <<<"${DEPLOY_SSH:-ssh -p ${PORT} -o ServerAliveInterval=30 ${HOST}}"

remote() {
    "${SSH[@]}" "$@"
}

step "Проверка сервера"
remote "docker version --format '{{.Server.Version}}'" >/dev/null ||
    fail "На сервере нет Docker или пользователь не входит в группу docker."

if [ "$MODE" = "rollback" ]; then
    remote "cd ${REMOTE_DIR} && bash ./scripts/deploy.sh --apply-rollback"
    exit 0
fi

cd "$ROOT"
step "Передача описания служб и сценариев"
# Первым делом: при первой выкладке .env на сервере заводится из этого .env.example.
tar -czf - docker/compose.prod.yml docker/Caddyfile deploy/systemd .env.example \
    scripts/compose.sh scripts/deploy.sh scripts/backup.sh scripts/restore.sh |
    remote "mkdir -p ${REMOTE_DIR} && tar -xzf - -C ${REMOTE_DIR}"
remote "test -f ${REMOTE_DIR}/.env" ||
    fail "Нет ${REMOTE_DIR}/.env: на сервере скопируйте .env.example в .env и заполните его (docs/DEPLOYMENT.md, раздел 4)."
echo "Сервер готов: ${HOST}:${REMOTE_DIR}"

VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -1)"
TAG="${IMAGE_NAME}:${VERSION}-$(date +%Y%m%d-%H%M%S)"

# Из России часть адресов сети доставки deb.debian.org не отвечает, и apt ждёт каждый
# по 30 с — сборка виснет, а не падает.
DEBIAN_MIRROR="${DEPLOY_DEBIAN_MIRROR-http://mirror.yandex.ru}"

pull_image() {
    # Связь с реестрами рвётся на рукопожатии TLS — до трёх попыток.
    local image="$1" attempt
    docker image inspect "$image" >/dev/null 2>&1 && return
    for attempt in 1 2 3; do
        docker pull --platform linux/amd64 "$image" >/dev/null && return
        [ "$attempt" -lt 3 ] || fail "Не загружается образ ${image}: реестр недоступен."
        sleep 5
    done
}

step "Проверка доступа к реестрам и пакетам"
# Базовые образы загружаются до сборки, PyPI и зеркало Debian проверяются из сети
# Docker, где идёт сборка: без доступа сборка висит до исчерпания памяти, а не падает.
# COPY --from с именем этапа сборки (builder) образом не является: берутся только ссылки с тегом.
base_images="$(sed -n -e 's/^FROM \([^ ]*\).*/\1/p' -e 's/^COPY --from=\([^ ]*:[^ ]*\) .*/\1/p' docker/Dockerfile | sort -u)"
for image in $base_images; do
    pull_image "$image"
    echo "${image}: есть"
done
probe_urls="https://pypi.org/simple/pip/ https://files.pythonhosted.org/"
[ -z "$DEBIAN_MIRROR" ] || probe_urls="${probe_urls} ${DEBIAN_MIRROR}/debian/dists/trixie/Release"
python_image="$(echo "$base_images" | grep '^python:' | head -1)"
# shellcheck disable=SC2086 — адреса намеренно разворачиваются в отдельные аргументы.
docker run --rm --platform linux/amd64 "$python_image" python -c '
import sys, time, urllib.error, urllib.request
failed = []
for url in sys.argv[1:]:
    for attempt in range(3):
        try:
            urllib.request.urlopen(url, timeout=20).close()
            break
        except urllib.error.HTTPError:
            break
        except OSError as exc:
            error = exc
            time.sleep(3)
    else:
        failed.append(f"{url}: {error}")
        continue
    print(f"{url}: отвечает")
if failed:
    print("Недоступны:", *failed, sep="\n  ", file=sys.stderr)
    sys.exit(1)
' $probe_urls || fail "Сборка не пройдёт: адреса выше недоступны из сети Docker."

step "Сборка образа ${TAG}"
# Сервер — x86-64; сборка на другой архитектуре дала бы образ, который там не запустится.
build_args=()
[ -z "$DEBIAN_MIRROR" ] || build_args+=(--build-arg "DEBIAN_MIRROR=${DEBIAN_MIRROR}")
# Связь с реестрами и PyPI рвётся — сборка повторяется до трёх раз.
for attempt in 1 2 3; do
    # shellcheck disable=SC2086 — ключи намеренно разворачиваются в отдельные слова.
    docker build --platform linux/amd64 "${build_args[@]}" ${DEPLOY_BUILD_ARGS:-} \
        -f docker/Dockerfile -t "$TAG" . && break
    [ "$attempt" -lt 3 ] || fail "Образ не собрался."
    echo "Сборка не удалась, повтор через 10 с…"
    sleep 10
done

ship_image() {
    local image="$1" local_id remote_id
    local_id="$(docker image inspect --format '{{.Id}}' "$image")"
    remote_id="$(remote "docker image inspect --format '{{.Id}}' ${image} 2>/dev/null" || true)"
    if [ "$local_id" = "$remote_id" ]; then
        echo "${image}: уже на сервере"
        return
    fi
    echo "${image}: передача…"
    docker save --platform linux/amd64 "$image" | gzip -1 | remote "gunzip | docker load"
}

step "Передача образов"
ship_image "$TAG"
# Образы служб — из описания; приложение описано переменной и передано выше.
for image in $(sed -n 's/^ *image: *\([^$ ][^ ]*\)$/\1/p' docker/compose.prod.yml); do
    pull_image "$image"
    ship_image "$image"
done

step "Обновление на сервере"
extra=""
[ "$WITH_ETL" -eq 0 ] || extra="--with-etl"
remote "cd ${REMOTE_DIR} && bash ./scripts/deploy.sh --apply ${TAG} ${extra}"
