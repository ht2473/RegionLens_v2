# =====================================================================================
# Часто выполняемые операции — одни и те же команды для README, CI и сервера.
#
# Требуется GNU make (в Windows — Git Bash или WSL); без него команды выполняются
# напрямую — они видны в тексте правил.
# =====================================================================================

.DEFAULT_GOAL := help
.PHONY: help install setup run check lint format typecheck test test-fast test-e2e \
        coverage security audit i18n migrate etl up down logs deploy backup shell

UV := uv run
COMPOSE_DEV := docker compose -f docker/compose.dev.yml
# Описание служб, дополнения сервера и .env подставляет compose.sh.
COMPOSE_PROD := ./scripts/compose.sh

help:  ## Показать перечень команд
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

# --- Окружение ---------------------------------------------------------------------------
install:  ## Установить зависимости и хуки перед коммитом
	uv sync
	$(UV) pre-commit install

setup:  ## Подготовить проект с нуля: .env, миграции, справочники, склад, содержимое
	$(UV) python main.py --setup

run:  ## Запустить сервер разработки
	$(UV) python main.py

check:  ## Проверить готовность окружения без запуска
	$(UV) python main.py --check

# --- Качество кода -----------------------------------------------------------------------------
lint:  ## Линтер
	$(UV) ruff check .

format:  ## Форматирование
	$(UV) ruff format .
	$(UV) ruff check --fix .

typecheck:  ## Статическая типизация
	$(UV) mypy apps config

security:  ## Статический анализ безопасности
	$(UV) bandit -c pyproject.toml -r apps config -q

audit:  ## Проверка зависимостей на известные уязвимости
	$(UV) pip-audit --skip-editable

# --- Тестирование ------------------------------------------------------------------------------------
test:  ## Полный прогон, кроме сквозных сценариев
	$(UV) pytest

test-fast:  ## Прогон без длительных проверок и сквозных сценариев
	$(UV) pytest -m "not slow and not e2e"

test-e2e:  ## Сквозные браузерные сценарии
	$(UV) pytest -m e2e

coverage:  ## Прогон с отчётом о покрытии
	$(UV) pytest --cov --cov-report=term-missing --cov-report=html
	@echo "Отчёт: htmlcov/index.html"

# --- Данные ---------------------------------------------------------------------------------------------
migrate:  ## Применить миграции
	$(UV) python manage.py migrate

etl:  ## Собрать аналитический склад в этом процессе
	$(UV) python manage.py etl_build --full

i18n:  ## Обновить и скомпилировать каталоги перевода
	$(UV) python scripts/i18n_tools.py extract
	$(UV) python scripts/i18n_tools.py compile

# --- Инфраструктура разработки -------------------------------------------------------------------------------
up:  ## Поднять PostgreSQL и Valkey для разработки
	$(COMPOSE_DEV) up -d

down:  ## Остановить инфраструктуру разработки
	$(COMPOSE_DEV) down

shell:  ## Интерактивная оболочка Django
	$(UV) python manage.py shell_plus --ipython

# --- Рабочий стенд -----------------------------------------------------------------------------------------------
deploy:  ## Выложить обновление на сервер (DEPLOY_HOST=пользователь@адрес)
	./scripts/deploy.sh

backup:  ## Снять резервную копию
	./scripts/backup.sh

logs:  ## Журнал приложения на сервере
	$(COMPOSE_PROD) logs -f --tail 100 web
