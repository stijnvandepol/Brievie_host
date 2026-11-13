# Decisions

Short records: date, choice, reason, what was dropped. Every dependency has a line.

## 2026-11-13: Dependencies (host, phase 1a)

Runtime:

- **Django 6.1** (`~=6.1.0`, 6.1.3): web framework, accounts, migrations (spec section 18). Dropped: FastAPI, Litestar. Move to 6.2 LTS in April 2027.
- **django-ninja** (1.8.0): typed API with Pydantic schemas and an OpenAPI schema that is the contract with the iOS app. Dropped: Django REST Framework.
- **psycopg[binary]** (3.3.9): PostgreSQL driver (psycopg 3). Dropped: psycopg2 (maintenance only).
- **django-environ** (0.14.0): reads settings and database URLs from the environment. Dropped: hand-written `os.environ` parsing. Note: its classifiers stop at Django 6.0; it has no upper bound and works on 6.1 (test suite).
- **whitenoise** (6.12.0): serves static files (later the built web app) from the API container without an extra web server. Dropped: serving static files from Caddy (needs a shared volume). Classifiers stop at Django 6.0; no upper bound.
- **django-stubs-ext** (6.1.2): makes generic Django classes such as `ModelAdmin[User]` subscriptable at runtime, so `mypy --strict` needs no `Any`. Dropped: `TYPE_CHECKING` aliases per class.
- **gunicorn** (27.0.1): WSGI server in the container. Dropped: uvicorn/ASGI (no async needed yet).

Development:

- **ruff** (0.17.5): lint and format in one tool. Dropped: flake8 + black + isort.
- **mypy** (2.4.1, `--strict`) + **django-stubs** (6.1.2): type checking as a hard gate. Dropped: pyright (django-stubs targets mypy).
- **pytest** (9.2.0) + **pytest-django** (4.14.0): tests with fixtures. Dropped: Django's unittest runner. pytest-django classifiers stop at Django 6.0; works on 6.1.
- **factory-boy** (3.3.3): test data factories. Dropped: hand-written helpers per test.
- **pip-audit** (CI only, run with `uvx`): checks dependencies for known vulnerabilities. Dropped: Dependabot alerts alone (no gate in CI).
- **gitleaks** (CI only, GitHub Action): blocks committed secrets.

## 2026-11-13: Two database roles

`brievie_owner` owns the schema, runs migrations and the test suite; it has `CREATEDB`
but is not a superuser and has no `BYPASSRLS`, so forced row-level security applies to
it. `brievie_app` serves requests with DML rights only (`ALTER DEFAULT PRIVILEGES`).
Settings read `DATABASE_URL` (app) and `DATABASE_OWNER_URL`; `BRIEVIE_DB_ROLE=owner`
switches the default connection to the owner (migrate), and `tests/conftest.py` does
the same for the test run. Dropped: one role for everything (the app could alter the
schema and switch RLS off on its own tables).

## 2026-11-13: Error body and translations

Every non-2xx API response is `{"code": "<snake_case>", "message": "<Dutch sentence>"}`,
also for validation errors (422 `validation_error`), unknown endpoints and `Http404`
(404 `not_found`). Field-level validation detail is not exposed in phase 1a.
User-facing messages are written in Dutch inside `gettext(...)`: Dutch is the source
language, so no `.po` files are needed until a second language arrives. Dropped:
English msgids plus a Dutch catalogue (extra build step for one language).

## 2026-11-13: Local ports

PostgreSQL and the API publish their ports on 127.0.0.1 only, for local development.
Task 6 (Caddy) makes Caddy the only service with public ports.

## 2026-11-13: User model

`accounts.User` logs in with its email address. The address is stored in lower case:
`create_user` and `clean()` normalise it, and a check constraint
(`email = lower(email)`) rejects anything else, so the plain unique index is
case-insensitive. Dropped: a functional `UNIQUE (lower(email))` index (Django requires
`USERNAME_FIELD` to be `unique=True`, so it would be a second index).
Django's own tables (permissions, groups, sessions, admin log, the user's group and
permission join tables) keep Django's integer keys; the UUID rule covers our tables.

## 2026-11-13: Django admin

The Django admin is mounted at `/admin/` with the user registered (password hash never
shown). Only staff accounts (`createsuperuser`) can sign in; the app's own users are
not staff. It is a fallback for the server owner, not part of the product UI.

## 2026-11-13: Row-level security per workspace

Workspace content tables subclass `WorkspaceModel` and their first migration ends with
`enable_rls(<table>)`: RLS enabled **and forced**, policy `workspace_isolation` on
`workspace_id = NULLIF(current_setting('brievie.workspace_id', true), '')::uuid` for
both `USING` and `WITH CHECK`. Without a context the comparison is NULL, so code that
forgets the context sees zero rows and cannot write. `tests/test_rls_coverage.py`
fails for any `WorkspaceModel` table without this.

`workspace_context(workspace_id)` sets the value with `set_config(..., true)` inside
`transaction.atomic()` and restores the previous value on a normal exit, because a
transaction-local setting survives a savepoint release. When the block fails, the
savepoint rollback restores it.

`Workspace` and `Membership` are **not** under RLS in phase 1a: they are read before a
workspace is known (to decide which workspace a request may enter). They are guarded
by the API dependency in Task 4 and its tests. Dropped: a second, user-keyed policy on
these tables (needs a user setting as well; reconsider when the app role reads them).

Foreign-key checks bypass RLS, so a row could point at a row of another workspace when
it knows its id. `Folder.save()` rejects a parent from another workspace; later
cross-row references need the same guard.
