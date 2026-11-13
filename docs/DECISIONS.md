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
- **django-axes** (9.0.0, Task 4): login lockout per email + client address, stored in the database (shared by all gunicorn workers). Classifiers include Django 6.1. Dropped: a hand-written attempt counter; Ninja's cache-based throttle (the default cache is per process).

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

Caddy is the only service that publishes ports to the network: 443, and 80 for the
redirect to https (Task 6). The API port is not published. PostgreSQL still publishes
5432 on 127.0.0.1 only, because `make test` and `make migrate` run on the host;
a deliberate development convenience, loopback only. The production Compose
template (phase 1d) must not publish it.

## 2026-11-13: User model

`accounts.User` logs in with its email address. The address is stored in lower case:
`create_user` and `clean()` normalise it, and a check constraint
(`email = lower(email)`) rejects anything else, so the plain unique index is
case-insensitive. Dropped: a functional `UNIQUE (lower(email))` index (Django requires
`USERNAME_FIELD` to be `unique=True`, so it would be a second index).
Django's own tables (permissions, groups, sessions, the user's group and
permission join tables) keep Django's integer keys; the UUID rule covers our tables.
The same exception holds for third-party tables (django-axes' `axes_*`) and the
database cache table `brievie_cache` (created by `createcachetable`): they do not
follow the `id`/`created_at`/`updated_at` rule.

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
`SECURE_HSTS_SECONDS` from `BRIEVIE_HSTS_SECONDS` (one year when unset; see the HSTS
entry below), `CSRF_TRUSTED_ORIGINS` from the environment.
`LANGUAGES` offers Dutch only until a second language ships.

## 2026-11-13: Folder names

A folder name is trimmed and may not be blank or contain `/` (the path is built from
names). `Folder.save()` raises `ValidationError`; a check constraint
(`name ~ '\S' AND name NOT LIKE '%/%'`) guards writes that bypass the model.

## 2026-11-13: Login, setup and the API surface (Task 4)

- **Lockout.** django-axes with `AXES_LOCKOUT_PARAMETERS = [["username", "ip_address"]]`,
  limit 5, cool-off 15 minutes, reset on a successful login, no success log. The
  username is the normalised email (`AXES_USERNAME_CALLABLE`), so case variants share
  one counter. `accounts.services.login` asks axes whether the pair is locked before
  checking the password and raises `ApiError(429, "too_many_attempts")` with
  `Retry-After`; failures 1-5 answer 401 `invalid_credentials`, the sixth attempt 429.
  `AxesMiddleware` is not installed (check `axes.W002` silenced): it would turn the
  fifth failure itself into axes' own lockout response. The password check runs
  outside `transaction.atomic`, so the attempt axes records survives the 401.
- **Client address.** `apps.common.net.client_ip` reads `REMOTE_ADDR`, or behind
  `BRIEVIE_PROXY_COUNT` proxies the matching `X-Forwarded-For` entry from the right.
  Compose sets 1 (Caddy) from Task 6 on. Used by axes.
- **Passwords.** Django's four standard validators, minimum length 12.
- **Setup race.** `POST /setup` takes `pg_advisory_xact_lock` before checking that no
  user exists; the lock lasts until the request transaction commits.
- **Workspace in the URL.** `workspaces.deps.workspace_member` answers 404
  `workspace_not_found` for both unknown and foreign ids, 403 `forbidden` for a role
  below the minimum, and otherwise sets the RLS setting for the rest of the request
  transaction (`enter_workspace_for_transaction`).
- **No public docs.** The Ninja docs page and the live `openapi.json` exist only with
  `DEBUG`; the contract is the committed `docs/api/openapi.json`.

## 2026-11-13: Device pairing (Task 5)

- **Secrets stored as hashes.** A pairing code (8 characters from
  `ABCDEFGHJKLMNPQRSTUVWXYZ23456789`, shown `XXXX-XXXX`, 10 minutes, single use) and a
  device token (`secrets.token_urlsafe(32)`) are stored only as SHA-256 hex. Lookup is
  by hash, so the database compares hashes, never the secret; the final comparison
  uses `hmac.compare_digest`. A new code deletes the user's older unused codes.
  Unsalted SHA-256 is enough for the token (256 bits); the code has 40 bits but lives
  10 minutes, is single use and pairing is rate limited.
- **Auth order.** `[DeviceTokenAuth(), django_auth]`: a bearer token (no CSRF needed,
  browsers never add it on their own) or the session (CSRF enforced). Creating a pairing
  code and logging out accept only the session. `last_seen_at` is written at most once
  a minute.
- **Pairing rate limit.** Ninja's `SimpleRateThrottle` at 10 per client address per
  10 minutes (`PairingThrottle`), stored in Django's **database cache** so all gunicorn
  workers share the count. `createcachetable` runs after `migrate` (Compose `migrate`
  service, `make migrate`); the test run creates the table itself. Dropped: the
  per-process local-memory cache (each worker would allow 10); a dedicated attempts
  table (more code for the same result).
- **Public URLs.** `BRIEVIE_PUBLIC_URLS` (comma-separated, each `https://`, local address
  first) feeds the QR payload; settings refuse a non-https entry.

## 2026-11-13: HTTPS with Caddy and the certificate pin (Task 6)

- **Caddy 2** (`caddy:2` image) terminates TLS for `{$BRIEVIE_HOSTNAME}` with
  `tls internal` (its own CA), TLS 1.2 minimum, HSTS (opt-in, see fix round 1), `nosniff`,
  `Referrer-Policy: same-origin`, `frame-ancestors 'none'`, and proxies to `api:8000`.
  `skip_install_trust`: the root reaches clients by pinning, not via a trust store.
- **Only the public root reaches the api.** Caddy keeps its CA in its data volume with
  mode 0700, next to `root.key`. Sharing that volume with `api` (as first planned)
  fails on permissions, and loosening them would hand the CA's private key to the API
  process, which would defeat the pin. `deploy/caddy-publish-root.sh` runs Caddy and
  copies `root.crt` (0644) into a separate `caddy-ca` volume as soon as Caddy has
  created its CA; `api` mounts only that volume, read-only, at the default
  `BRIEVIE_CA_CERT_PATH` (`/caddy-data/caddy/pki/authorities/local/root.crt`).
- **Pin.** `ca_sha256` = SHA-256 of the root's DER bytes (stdlib `ssl`, no new
  dependency). No file (Caddy not started yet, or a public certificate): the payload
  carries `null` and `GET /server/ca-certificate` answers 404 `no_private_ca`.

## 2026-11-13: OpenAPI contract (Task 7)

`python manage.py export_openapi` writes `docs/api/openapi.json` (sorted keys, 2-space
indent); `make openapi` runs it, CI runs it with `--check` and a test compares the
committed file with the generated schema. Every operation has a camelCase
`operationId` and documents at least one error response with `ErrorOut`, except
`getHealth` (no failure mode). `getSetupStatus` documents 500 `internal_error`: its only
failure is an unreachable database. `apps.common` is now an installed app (it holds the
command). Dropped: Ninja's `export_openapi_schema` (no `--check`).

## 2026-11-13: Fix round 1 for tasks 4-7

- **JSON bodies only (415).** Ninja parses a body as JSON whatever its Content-Type
  and its views are CSRF-exempt, so a cross-site `<form enctype="text/plain">` could
  reach `/setup` and `/auth/login`. `ApiJsonBodyMiddleware` answers 415
  `unsupported_media_type` to any `/api/` request with a body whose Content-Type is not
  `application/json`, before any view runs. A JSON Content-Type needs a CORS preflight,
  and the API allows no cross-origin requests. Session callers of state-changing
  endpoints still need a CSRF token (tested).
- **Installation code for the first setup.** The server is assumed reachable from the
  internet, so `POST /setup` needs `setup_token`: `BRIEVIE_SETUP_TOKEN` from the
  environment, or a code that `manage.py prepare_setup_token` generates when the api
  container starts (no user, no env value, no code yet): `secrets.token_urlsafe(16)`,
  stored only as SHA-256 in the one-row table `accounts_setuptoken`, printed once to
  stdout as `Installatiecode: …`. **That line is the one allowed exception to "no
  secrets in logs"**: the owner needs it once, it is useless after setup. Generation
  takes the same advisory lock as setup, so concurrent starts create one code and
  print it once; a later start prints nothing (`--new` replaces a lost code).
  Comparison is constant-time; wrong or missing gives 403 `setup_token_invalid`;
  attempts are limited to 10 per address per 10 minutes (429). Setup deletes the code.
  `GET /setup` still returns only `needs_setup`.
- **Throttles.** `apps.common.throttling.ClientAddressThrottle(scope, rate)` serves both
  pairing and setup.
- **413.** `RequestDataTooBig` gives 413 `payload_too_large`; other
  `SuspiciousOperation`s stay 400.
- **Contract.** `getSetupStatus` is exempt from the error-response rule like
  `getHealth` (no fake 500). Every operation with a body documents 400, 413, 415 and
  422 (`apps.common.schemas.BODY_ERRORS`).
- **Broken CA file.** An unreadable, non-ASCII or malformed root certificate is logged
  as a warning (path and error type, no content) and treated as no private CA.
- **HSTS opt-in with the private CA.** HSTS would make a browser's certificate warning
  impossible to click through. `BRIEVIE_HSTS_SECONDS` drives both Caddy's header and
  Django's `SECURE_HSTS_SECONDS`; Compose defaults to 0, set 31536000 with a public
  certificate. Django's own default without the variable stays one year.
- **Caddy pinned** to `caddy:2.11` (2.11.9 at the time). Caddy replaces a
  client-sent `X-Forwarded-For` (no trusted proxies configured), so the last entry
  is the real client address (verified manually, see the task report).

## Open items

- Pin GitHub Actions and container images by digest: later hardening task.
