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
- `uv run python manage.py configure_ai --workspace <id> --mode off|external|local [--supplier mistral|openai|claude] [--accept-external-ai]`: AI per workspace (`external` asks to type `akkoord`)

Web (`web/`, from the repo root through `cd web &&`; Node 24+ and pnpm):

- `pnpm test`: Vitest (queries by role); `pnpm build`: `tsc --noEmit` + Vite build
- `pnpm lint`: Biome; `pnpm typecheck`: `tsc --noEmit`
- `pnpm gen:api`: regenerate `src/api/schema.d.ts` from `docs/api/openapi.json` (after `make openapi`)
- `pnpm extract`: update the Dutch text catalogue after changing visible strings

## Rules in short

- Code, comments, commits, table and field names in English. User-facing text in
  Dutch (B1): in the backend always through `gettext`, in `web/` always through Lingui
  (`t` macros; run `pnpm extract` afterwards).
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
- Text goes to an external AI service only with a recorded consent for that address
  (`AISettings.consented_base_url` and the history `AIConsentEvent`, both written only by
  `apps.pipeline.settings_service.configure`). An AI API key is write-only: no response
  or log line holds it.
- No new dependency without a line in `docs/DECISIONS.md`.
- Apps of phase 1c: `people` (persons, name matching), `labels`, `rules` (engine, executor,
  presets) and `tasks`; `documents.organise` and `documents.suggestions` order post.
- Only `apps.documents.organise` adds or removes a document's labels and addressees (it
  writes the history).
- A label or addressee proposal is a `DocumentSuggestion`; nothing automatic repeats a
  dismissed one.
- The rules engine (`apps.rules.engine`) stays pure: no Django import.
- No Redis, no Celery, no Node runtime container. Background jobs: Procrastinate, in an
  app's `jobs.py`; a job takes ids only and enters `workspace_context` itself, in short
  transactions (none open during an external call).
- Text is BSN-masked (`apps.common.privacy.mask_bsns`) before it is stored, logged or sent
  to an AI service. Document files are sealed (`apps.common.crypto`) before they touch disk.
- A field added to an existing API schema has a default (never newly required; the
  contract test compares with `backend/tests/fixtures/openapi_1b.json`).
- Nullable fields in API schemas are primitives only, and nullable inputs have a default
  (the iOS generator needs both); the contract test forbids `anyOf`.
- Tests first for logic. Conventional commits.
