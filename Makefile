COMPOSE := docker compose --project-directory deploy -f deploy/docker-compose.yml
BACKEND := cd backend &&

.PHONY: dev down migrate test lint format typecheck openapi

deploy/.env:
	cp deploy/.env.example deploy/.env

## Start PostgreSQL for local development and tests.
dev: deploy/.env
	$(COMPOSE) up -d --wait postgres

## Stop the local stack (data volume is kept).
down:
	$(COMPOSE) down

## Apply migrations as the schema owner role.
migrate:
	$(BACKEND) BRIEVIE_DB_ROLE=owner uv run python manage.py migrate

test:
	$(BACKEND) uv run pytest

lint:
	$(BACKEND) uv run ruff check . && uv run ruff format --check .

format:
	$(BACKEND) uv run ruff check --fix . && uv run ruff format .

typecheck:
	$(BACKEND) uv run mypy .

## Write the OpenAPI schema (the contract with Brievie_ios) to docs/api/openapi.json.
openapi:
	$(BACKEND) uv run python manage.py export_openapi_schema --api brievie.api.api --indent 2 --sorted --output ../docs/api/openapi.json
