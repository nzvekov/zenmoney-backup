COMPOSE := docker compose -f deploy/compose.yaml
SERVICE := zenmoney-backup

.DEFAULT_GOAL := help
.PHONY: help install lint format test check build up down restart ps logs backup

help: ## Показать список команд
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

install: ## Установить зависимости и pre-commit
	uv sync
	uv run pre-commit install

lint: ## Проверить код через ruff
	uv run ruff check .
	uv run ruff format --check .

format: ## Отформатировать код через ruff
	uv run ruff check --fix .
	uv run ruff format .

test: ## Запустить тесты
	uv run pytest

check: lint test ## Запустить линтер и тесты

build: ## Собрать образ
	$(COMPOSE) build

up: ## Собрать образ и запустить контейнер
	$(COMPOSE) up -d --build

down: ## Остановить и удалить контейнер
	$(COMPOSE) down

restart: ## Перезапустить контейнер
	$(COMPOSE) restart

ps: ## Показать состояние контейнера
	$(COMPOSE) ps

logs: ## Смотреть логи
	$(COMPOSE) logs -f --tail=100

backup: ## Сделать копию вручную в работающем контейнере
	$(COMPOSE) exec $(SERVICE) zenmoney-backup --once
