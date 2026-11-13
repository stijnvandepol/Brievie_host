# Brievie host

Self-hosted Django server for Brievie. The product spec is `docs/SPEC.md` (read
sections 11, 12, 14, 18 and 19 before changing architecture). Decisions live in
`docs/DECISIONS.md`, Dutch-English terms in `docs/GLOSSARY.md`.

## Commands (from the repo root)

- `make dev`: start PostgreSQL 18 (creates `deploy/.env` from the example first)
- `make test`: pytest (runs as the non-superuser `brievie_owner` role)
- `make lint`: `ruff check` + `ruff format --check`
- `make typecheck`: `mypy --strict`
- `make migrate`: apply migrations as the owner role
- `make openapi`: write `docs/api/openapi.json`

## Rules in short

- Code, comments, commits, table and field names in English. User-facing text in
  Dutch (B1), always through `gettext`.
- Every table: UUID `id`, `created_at`, `updated_at`. Every workspace content table:
  `workspace_id` (subclass `WorkspaceModel`) and RLS enabled **and forced**.
- `mypy --strict` and `ruff check` clean. No `Any`, no bare `except`.
- Money is `Decimal`; datetimes are timezone-aware.
- API only under `/api/v1`, additive changes only. Every endpoint: typed schemas and an
  explicit camelCase `operation_id`. Endpoints without auth say `auth=None`.
- Every non-2xx body: `{"code": "<snake_case>", "message": "<Dutch sentence>"}` via
  `apps.common.errors.ApiError`.
- Thin endpoints: validate, call one function in the app's `services.py`, return.
- Every mutating service function is wrapped in `transaction.atomic`, so raising inside
  it rolls back its own writes. An error response (`ApiError`, Ninja `HttpError`,
  `Http404`) does not roll back the request transaction; only a 500 does.
- No document content, tokens, passwords or BSNs in logs.
- No new dependency without a line in `docs/DECISIONS.md`.
- No Redis, no Celery, no Node runtime container.
- Tests first for logic. Conventional commits.
