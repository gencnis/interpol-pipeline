.PHONY: up down test lint fmt migrate

up:
	docker compose up --build

down:
	docker compose down

test:
	pytest

lint:
	ruff check app tests
	mypy app

fmt:
	ruff format app tests
	ruff check --fix app tests

migrate:
	alembic upgrade head
