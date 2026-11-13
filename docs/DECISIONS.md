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
