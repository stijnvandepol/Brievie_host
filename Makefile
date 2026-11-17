## The compose project: COMPOSE_PROJECT_NAME from the environment, else from deploy/.env
## (plain or `export COMPOSE_PROJECT_NAME=...`, the last one counts), else brievie.
## Always passed with -p, so a missing or stray line never leads a make target into
## another installation's project.
PROJECT := $(or $(COMPOSE_PROJECT_NAME),$(shell sed -n 's/^[[:space:]]*\(export[[:space:]][[:space:]]*\)\{0,1\}COMPOSE_PROJECT_NAME=//p' deploy/.env 2>/dev/null | tail -n 1),brievie)
COMPOSE := docker compose -p $(PROJECT) --project-directory deploy -f deploy/docker-compose.yml
BACKEND := cd backend &&

.PHONY: dev down migrate test lint format typecheck openapi web-install web-test web-lint web-build web-api web-i18n

## Start PostgreSQL for local development and tests. init-env.sh only adds missing
## lines to an existing deploy/.env; ensure-backup-role.sh brings an existing database
## up to date with the back-up role.
dev:
	sh deploy/init-env.sh
	$(COMPOSE) up -d --wait postgres
	sh deploy/ensure-backup-role.sh

## Stop the local stack (data volume is kept).
down:
	$(COMPOSE) down

## Apply migrations as the schema owner role.
migrate:
	$(BACKEND) BRIEVIE_DB_ROLE=owner uv run python manage.py migrate
	$(BACKEND) BRIEVIE_DB_ROLE=owner uv run python manage.py createcachetable

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
	$(BACKEND) uv run python manage.py export_openapi

## Web interface (web/): needs Node 24+ and pnpm.
web-install:
	cd web && pnpm install --frozen-lockfile

web-test:
	cd web && pnpm test

web-lint:
	cd web && pnpm lint && pnpm typecheck

web-build:
	cd web && pnpm build

## Regenerate web/src/api/schema.d.ts from docs/api/openapi.json (run after `make openapi`).
web-api:
	cd web && pnpm gen:api

## Update the Dutch text catalogue (web/src/locales) after changing visible strings.
web-i18n:
	cd web && pnpm extract
