.PHONY: up down test lint fmt migrate

up:
	docker compose up --build

down:
	docker compose down

# Tests run inside a container that has the Docker socket mounted so
# testcontainers can spin up Postgres / MinIO for integration tests.
# On a machine with a local Python env, `pytest` also works directly.
test:
	docker compose run --rm --no-deps test pytest -v

lint:
	ruff check app tests
	mypy app

fmt:
	ruff format app tests
	ruff check --fix app tests

migrate:
	alembic upgrade head
