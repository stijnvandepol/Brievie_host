# Decisions

Short records: date, choice, reason, what was dropped. Every dependency has a line.

## 2026-11-13: Dependencies (host, phase 1a)

Runtime:

- **Django 6.1** (`~=6.1.0`, 6.1.3): web framework, accounts, migrations (spec section 18). Dropped: FastAPI, Litestar. Move to 6.2 LTS in April 2027.
- **django-ninja** (1.8.0): typed API with Pydantic schemas and an OpenAPI schema that is the contract with the iOS app. Dropped: Django REST Framework.
- **psycopg[binary]** (3.3.9): PostgreSQL driver (psycopg 3). Dropped: psycopg2 (maintenance only).
- **django-environ** (0.14.0): reads settings and database URLs from the environment. Dropped: hand-written `os.environ` parsing. Note: its classifiers stop at Django 6.0; it has no upper bound and works on 6.1 (test suite).
- **whitenoise** (6.12.0): serves static files (later the built web app) from the API container without an extra web server. Dropped: serving static files from Caddy (needs a shared volume). Classifiers stop at Django 6.0; no upper bound.
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
Settings read `DATABASE_URL` (app) and the optional `DATABASE_OWNER_URL`, which is
required only with `BRIEVIE_DB_ROLE=owner` (migrate) and for the test run
(`tests/conftest.py`). In Compose a one-shot `migrate` service holds the owner URL;
the `api` service starts after it completes and never receives owner credentials. Dropped: one role for everything (the app could alter the
schema and switch RLS off on its own tables).

## 2026-11-13: Error body and translations

Every non-2xx API response is `{"code": "<snake_case>", "message": "<Dutch sentence>"}`:
validation errors (422 `validation_error`), unknown endpoints and `Http404`
(404 `not_found`), Ninja's `HttpError` family (401 `not_authenticated`, 403 `forbidden`,
429 `too_many_attempts` with `Retry-After`, other statuses `error`), a wrong method
(405 `method_not_allowed`, via middleware because Ninja returns a bare response) and
unhandled exceptions (500 `internal_error`). The 500 handler marks the request
transaction for rollback, because the view returns normally and `ATOMIC_REQUESTS`
would otherwise commit half-done work; it logs method, path and traceback, never the
body. `ApiError` does not roll back, so writes that must survive an error (such as a
failed-login record) stay; services raise it before writing otherwise. Field-level validation detail is not exposed in phase 1a.
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
Django's own tables (permissions, groups, sessions, the user's group and
permission join tables) keep Django's integer keys; the UUID rule covers our tables.

## 2026-11-13: No Django admin

Removed (review ruling): a second login surface on an internet-reachable server, with
no product need; the server owner manages users through the API and web interface.

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

Deleting a workspace must run inside its own `workspace_context`: Django's cascade
first selects the content rows, under RLS, so without the context it finds none and
the delete then fails on the foreign key.

Foreign-key checks bypass RLS, so a row could point at a row of another workspace when
it knows its id. `Folder.save()` rejects a parent from another workspace; later
cross-row references need the same guard.

## 2026-11-13: Security settings

Secure by default, overridable through the environment for local http development:
`SESSION_COOKIE_SECURE` and `CSRF_COOKIE_SECURE` (true), `SESSION_COOKIE_SAMESITE =
"Lax"`, `SECURE_PROXY_SSL_HEADER` for Caddy's `X-Forwarded-Proto`,
`SECURE_HSTS_SECONDS` (one year), `CSRF_TRUSTED_ORIGINS` from the environment.
`LANGUAGES` offers Dutch only until a second language ships.

## 2026-11-13: Folder names

A folder name is trimmed and may not be blank or contain `/` (the path is built from
names). `Folder.save()` raises `ValidationError`; a check constraint
(`name ~ '\S' AND name NOT LIKE '%/%'`) guards writes that bypass the model.

## Open items

- Password validators (minimum length 12): Task 4.
- Pin GitHub Actions and container images by digest: later hardening task.
