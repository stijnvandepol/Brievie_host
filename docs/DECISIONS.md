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
`getHealth` and `getSetupStatus` (no failure mode besides a server error). `apps.common` is now an installed app (it holds the
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

## 2026-11-13: Fix round 2 for tasks 4-7

- **Body limit at the proxy.** Without it, a body over Django's limit made gunicorn
  close the connection and Caddy answered 502. One variable, `BRIEVIE_MAX_BODY_BYTES`
  (default 2621440, 2.5 MiB), drives Caddy's `request_body max_size` and Django's
  `DATA_UPLOAD_MAX_MEMORY_SIZE`; Caddy answers 413 itself. The document-upload endpoint
  in phase 1b needs its own, larger limit (a separate Caddy route and a streamed
  upload in Django).
- **Proxy errors in the API shape.** `handle_errors` in the Caddyfile answers 413 with
  `payload_too_large` and 502/503/504 with `server_unavailable`, as
  `{"code", "message"}` in Dutch.
- **Setup token.** `setup_token` is a required field (`minLength` 1) in the contract; a
  missing or empty value is 422, a wrong one 403. `BRIEVIE_SETUP_TOKEN` shorter than 16
  characters stops the server at startup (`ImproperlyConfigured`).

## 2026-11-13: Web interface (Task 8)

A static React app in `web/`, built in a Node stage of `deploy/Dockerfile` (`node:26.13-slim`,
the current LTS, pinned by minor) and copied to `/app/webdist`. Node is a build stage only:
the running image has no Node. Runtime and build dependencies, each with the reason:

- **React 19** (19.3.1) + **react-dom**: the UI library named in the plan.
- **Vite** (8.3.8) + **@vitejs/plugin-react** (6.3.0): dev server and build; hashed asset names, no inline script.
- **TypeScript** (5.9.3, strict): pinned to 5.x because `openapi-typescript` declares `typescript ^5`; 6 and 7 exist and can follow when it does.
- **Biome** (2.6.7): lint, format and import order in one tool. Dropped: ESLint + Prettier.
- **Vitest** (5.1.0) + **jsdom** (31.0.1) + **@testing-library/react** (16.3.3), **user-event** (14.6.10), **jest-dom** (7.0.1): tests run in the Vite config; queries by role prove accessible names.
- **MSW** (3.0.5): mocks the API at the network edge, so the real `openapi-fetch` client runs in tests. Dropped: mocking `fetch` by hand.
- **TanStack Query** (5.107.1): server state, mutations and their error state. **TanStack Router** (1.170.46): typed routes and guards (`beforeLoad` redirects). Dropped: React Router (untyped paths).
- **React Aria Components** (1.22.1): accessible form controls, radio cards and buttons with keyboard and focus handling built in. Dropped: hand-built widgets.
- **Lingui** (`@lingui/core`, `react`, `cli`, `vite-plugin`, `conf`, `format-po`, `babel-plugin-lingui-macro`, all 6.12.0): visible text through `t` macros, Dutch source strings in `src/locales/nl/messages.po`; English can be added as a second catalogue. `@rolldown/plugin-babel` (0.3.1) and `@babel/core` (8.1.2) run the macro plugin, because plugin-react 6 no longer includes Babel. CI re-extracts the catalogue and fails on a diff.
- **openapi-typescript** (7.13.0) + **openapi-fetch** (0.17.0): `src/api/schema.d.ts` is generated from `docs/api/openapi.json` (`pnpm gen:api`); no API type is written by hand; CI fails when it is stale.
- **qrcode.react** (4.2.0): renders `qr_payload` as an SVG QR code. Dropped: drawing to a canvas (not testable in jsdom).
- **jsqr** (1.4.0) is not a project dependency; the manual browser check installs it outside the repo to decode the QR.

Choices:

- **One bundle, no code splitting.** About 130 kB gzip for four screens; one request.
- **CSRF cookie from the app shell.** Django sets `csrftoken` only when a view uses it. `apps.common.web.web_app` (the SPA fallback) uses `ensure_csrf_cookie`, so the cookie exists before the first write, also after a browser cleared it while the session lived. The client sends it as `X-CSRFToken` on every non-safe call. No API endpoint changed.
- **SPA fallback.** WhiteNoise serves the built files from the site root (`WHITENOISE_ROOT`, `/assets/` immutable for a year); every other non-`/api` path gets `index.html` (`no-cache`). A path with a file extension that reaches the view is a 404, never the index; `/api` and `/api/...` keep the JSON 404.
- **Strict CSP from Caddy** (`default-src 'self'`, `script-src 'self'`, `style-src 'self'`, `img-src 'self' data:`, `frame-ancestors 'none'`, plus `object-src 'none'`, `base-uri`, `form-action`). React Aria wants to inject an inline `<style>` for `touch-action`; it skips that when an element with id `react-aria-pressable-style` exists, so `index.html` holds an empty `<template>` with that id and `styles.css` holds the rule. Verified in a browser: no violations.
- **Compose ports.** Caddy's host ports come from `BRIEVIE_HTTP_PORT` and `BRIEVIE_HTTPS_PORT` (defaults 80 and 443), so a second stack can run next to the first.
- **Countdown uses the browser clock** against the server's `expires_at`. A clock that is minutes off shows a wrong countdown; the server decides whether a code is valid.

## 2026-11-13: Fixes after the whole-branch review

- **Pairing needs a reachable address.** Compose derives `ALLOWED_HOSTS` and `BRIEVIE_PUBLIC_URLS` from `BRIEVIE_HOSTNAME` (`https://<host>:<BRIEVIE_HTTPS_PORT>`, the port always explicit because Compose cannot add it only when it is not 443); both stay overridable. System check `brievie.E001` (error) fails every management command, so the api container stops at start, when a public URL's host is not in `ALLOWED_HOSTS`. The pair-app page warns when every URL in the QR is `localhost`/127.0.0.1.
- **The app pairs from the QR payload, not from the short code.** The code alone lacks the server URLs and the CA fingerprint, so the page offers "Kopieer koppelgegevens" (the whole `qr_payload`) as the fallback.
- **Placeholder secrets.** With `DEBUG` off, `SECRET_KEY` containing `change-me` or shorter than 50 characters stops the server (`ImproperlyConfigured`). The two database passwords equal to the `.env.example` placeholders give warning `brievie.W001` instead of a refusal: a database volume keeps the password it was created with, and rotating it is a manual PostgreSQL step.
- **401 before 403.** A bearer token that is unknown or revoked is 401 at once (`DeviceTokenAuth` raises), and a request with no session is 401 as well: `apps.accounts.auth.SessionAuth` checks CSRF only for a caller who has a session (403 stays for a session without a valid token). Session-only endpoints (`createPairingCode`, `logout`) answer 401 to any bearer token.
- **Runtime image.** Multi-stage: the virtualenv is built where `uv` lives and copied over; `backend/tests` is excluded by `.dockerignore`.

## 2026-11-14: Phase 1b scope (owner decisions)

- **No heavy work on the server.** OCR happens on the iPhone (Apple Vision); in 1b the
  app is the only intake, so the server runs no OCR. OCRmyPDF/Tesseract arrive with web
  upload and the watch folder (1d). This deviates from the 1b row in spec section 16.
- **AI through an API by default.** Modes `off`, `external`, `local`; `external` and
  `local` are the same OpenAI-compatible provider with a different URL. Default for
  `external`: Mistral. `local` (Ollama) stays possible but is not tested on real
  hardware in 1b. New workspaces start with `off`.
- **No web screens in 1b.** The result is reviewed in the app.

## 2026-11-14: Contract fixes (phase 1b, Task 1)

- **Nullable fields are type arrays.** Pydantic writes `X | None` as
  `anyOf: [X, {"type": "null"}]`, which swift-openapi-generator (1.12.2) skips, so the
  field never reached the app. `apps.common.openapi.normalize_nullable` rewrites it to
  `type: [X, "null"]` in the exported file (verified: the generator emits
  `parentId: String?`). Only primitives can be nullable; a nullable object or `$ref`
  fails the export, and a test forbids `anyOf` in the contract. Nullable request
  fields get a default, because the generated client omits a nil field.
- **411 for a body without Content-Length.** gunicorn hands Django a chunked body
  without a length and Django reads it as empty, so the caller got a misleading 415 or
  422. `ApiContentLengthMiddleware` answers 411 `length_required` to any `/api/` request
  with `Transfer-Encoding` and no `Content-Length`. The app's uploads always carry a
  length (upload from a file).

## 2026-11-14: Encryption per workspace (phase 1b, Task 3)

- **cryptography** (50.0.2): AES-256-GCM (`AESGCM`). Dropped: PyNaCl (another cipher
  family, less common next to Django), the standard library (has no AEAD).
- **Envelope encryption.** `BRIEVIE_MASTER_KEY` (32 bytes as hex, from the environment)
  wraps one data key per workspace (`workspaces_workspacekey`, under forced RLS, bound to
  the workspace id as associated data). The data key seals document files and stored
  secrets (`apps.common.crypto.seal`: `BRV1` + nonce + ciphertext and tag; the
  associated data names the place, so a file moved to another page or workspace does
  not open). **Losing the master key loses every document**; back-ups (1d) must include
  it, separately from the data.
- **Read lazily, checked at start.** `crypto.master_key()` reads the setting on first
  use, so `migrate`, `export_openapi` and CI run without it. The api and worker
  containers run `manage.py check_master_key` first and stop when it is missing or
  malformed. Dropped: a system check (it would also fail `migrate`, which does not get
  the key).
- **init-env.sh** generates the key for a new `.env` and appends it to an existing
  `.env` that lacks it: the one change the script makes to an existing file.
- **Storage behind an interface.** `apps.documents.storage.DocumentStorage`
  (`FileSystemStorage` under `BRIEVIE_STORAGE_ROOT`, the `documents` volume); an
  S3-compatible implementation can follow without touching callers.

## 2026-11-14: Job queue (phase 1b, Task 5)

- **Procrastinate** (3.11.0): the job queue lives in PostgreSQL (spec section 18).
  Dropped: Celery + Redis (an extra container to run and back up), Django's own task
  framework (no production worker yet). Jobs are in each app's `jobs.py`
  (`PROCRASTINATE_AUTODISCOVER_MODULE_NAME`), because `tasks` is a domain app.
- **Worker container.** Same image, `procrastinate worker --queues pipeline`, the
  **app** database role (Procrastinate's tables get the default DML privileges), no
  published port, no documents volume: the pipeline works on text in the database.
- **Job arguments are ids only** (`workspace_id`, `document_id`), never text. Every job
  enters `workspace_context` itself, in short transactions: none stays open during a
  slow step such as an AI call.
- **A document never stays in `processing`.** Only `RetryableError` is retried (3
  attempts: after 10 s and 100 s); the last attempt, a `PermanentError` or any
  unexpected exception sets `failed` with a code. `retry_stalled_jobs` (every 5
  minutes) gives jobs of a worker that stopped sending heartbeats back to the queue.
  Every step first checks that the document is still `processing`, so a duplicate or
  late job changes nothing.

## 2026-11-14: Document upload (phase 1b, Task 6)

- **One multipart request per document** (`createDocument`): the JSON field `metadata`
  (`UploadMetadataIn`: `client_id`, and per page its SHA-256 and recognised text)
  followed by one `page` file (JPEG) per page. Atomic: all pages or nothing.
  `UploadMetadataIn` is in the contract's components although no operation names it,
  so clients generate it (`brievie.api.CONTRACT_ONLY_SCHEMAS`).
- **Idempotent on `client_id`.** The same upload again answers 200 with the existing
  document; other pages under the same id answer 409 `document_conflict`. The document
  row is inserted first, inside a savepoint, so a concurrent retry loses on the unique
  constraint before any file is written.
- **Limits.** `BRIEVIE_MAX_UPLOAD_BYTES` (32 MiB) for this route, in Caddy (its own
  `handle` block: two `request_body` directives on one request both apply and the
  smaller wins) and in `ApiContentLengthMiddleware` (413 before authentication reads
  anything). `BRIEVIE_MAX_PAGES` (30), `BRIEVIE_MAX_PAGE_TEXT_CHARS` (20000).
- **Uploads never touch the disk unencrypted.** `FILE_UPLOAD_HANDLERS` is the memory
  handler only, with `FILE_UPLOAD_MAX_MEMORY_SIZE` equal to the upload limit; set
  globally because the session's CSRF check parses the form before the view runs.
  Pages are sealed with the workspace key before they are written; the associated data
  is `<workspace>/<document>/<index>`.
- **Known gap: orphan files.** When a request fails after its pages were written but
  outside the intake function (rare), the rows roll back and the encrypted files stay.
  A storage sweep comes with back-ups (1d). (Narrowed on 2026-11-15, see below.)
- **Multipart only here.** `ApiJsonBodyMiddleware` accepts `multipart/form-data` on this
  one path; the endpoint needs a device token or a session with a CSRF token.
- **`needs_attention`** (confidence below 0.85 on a deadline or amount) is computed on
  `Extraction`, not stored.

## 2026-11-14: AI provider (phase 1b, Task 8)

- **httpx** (0.28.1): the call to the AI service, with a timeout and a mock transport
  for tests. Dropped: the `openai` SDK (a large dependency for one call), `urllib`
  (no mock transport, clumsy timeouts).
- **One provider for every mode.** `OpenAICompatibleProvider` posts to
  `<base_url>/chat/completions` at temperature 0 with a strict JSON schema
  (`response_format`). `external` defaults to Mistral (`https://api.mistral.ai/v1`,
  `mistral-small-latest`): a European company, cheap small models, structured output.
  `local` (Ollama and the like) is the same code with another URL; not tested on real
  hardware in 1b.
- **`local` cannot leave the network.** Its URL must name a Compose service (no dot),
  `localhost`, a `.local` name, or a literal loopback or private address; checked when
  configured and again before every use. A `.local` name is trusted to resolve inside
  the network.
- **The API key** is sealed with the workspace key, set with
  `manage.py configure_ai` from `BRIEVIE_AI_API_KEY` or a prompt, never from the
  command line, and never part of an exception or a log line (provider errors carry a
  code only and are raised without their cause).
- **Text cap.** At most 30000 characters go to the model, cut at a page boundary.
- **Failures.** Timeout, connection error, 429 and 5xx retry (`ai_unavailable`);
  401/403 `ai_key_rejected`, other 4xx `ai_request_rejected`, an answer without content
  `ai_invalid_output`: no retry.

## 2026-11-14: Fixes after the whole-branch review (phase 1b)

- **BSN masking is wider.** Groups may be separated by dots, spaces, no-break spaces or
  hyphens (also `1234.56.782`), and only a digit directly next to the number rules it
  out: letters or other numbers around it do not (OCR glues columns together; an old
  VAT id `NL…B01` holds the BSN). A number is masked too often rather than too rarely.
- **Abandoned documents.** `requeue_abandoned_documents` (every 5 minutes) queues a
  document again that has been `processing` for 15 minutes without a waiting or running
  job: recording a failure can itself fail when the database goes away, and nothing
  else would move that document.
- **gunicorn runs threaded** (`gthread`, 4 threads, timeout 120 s): Caddy streams an
  upload, so a slow connection held a sync worker past its 30 s timeout (502), and two
  slow uploads blocked the API.
- **Amounts must look like money.** A number counts as an amount only with `€`/`EUR`
  before it or cents after it, so "Postbus 2500" cannot confirm an invented amount; a
  due date found outside the invoice quote may not be the letter's own date.

## 2026-11-15: Deferred review findings (phase 1b)

- **Amounts.** `45,5` is read as 45.50 (one decimal digit counts tens of cents). The
  English form `1,234.56` is read as 1234.56 only with comma groups of three and two
  cents, a form a Dutch amount never takes; `1,234` alone is not read at all (it could
  be Dutch decimals). An ambiguous number gives no amount, never a smaller wrong one.
- **AI provider address.** `configure_ai` refuses a URL longer than its column and a
  host that is not a valid DNS name; `Extraction.provider` is 255 characters (DNS
  allows 253). `local` refuses a host made of digits or a `0x` number
  (`http://134744072` is 8.8.8.8 to the resolver), so it cannot pass as a Compose
  service name.
- **Confirm is idempotent by comparing the body.** A confirm on a `done` document
  answers 200 with the same document when the review equals what was stored (trimmed,
  BSNs masked, tasks in any order); any other review stays 409
  `document_not_reviewable`. Chosen over a client idempotency key: the stored
  document and tasks hold everything to compare, so the contract does not change.
- **An `in_document` deadline must be in its quote.** The quote must be in the letter
  (else 422) and must say the date (`dates_in`: ISO, `d-m-yyyy`, `d maand yyyy`). When
  the date is not in the quote, confirm keeps the task with the user's date as
  `manual`, without the quote: the same "not verified" outcome a proposal gets with
  confidence 0, not an error.
- **No NUL characters in request text.** Every request schema derives from
  `apps.common.schemas.InputSchema`, whose validator refuses `\x00` in any string
  field (422 `validation_error`); PostgreSQL text cannot store it.
- **Upload files are removed on any failure inside the intake service**, also after
  all pages were written (queueing the job). Left: a failure after the service
  returned, such as a lost connection at the request's commit.

## Open items

- Pin GitHub Actions and container images by digest: later hardening task.
- Two-factor authentication is not built in phase 1a; login is email + password only (with lockout).
- Device tokens never expire; a device stops working only when somebody revokes it. A lifetime (and refresh) is still to be decided.
- Server-side OCR (OCRmyPDF + Tesseract) for uploads without text: with web upload and the watch folder (1d).
- The extraction schema leaves out relations, VAT lines and identifiers (licence plate, assessment number); they arrive with business mode (phase 2), with IBAN mod-97 and the VAT sum check. Addressees and labels arrived in 1c.
- `listDocuments` returns the newest 200; the inbox counts are exact, the lists show the newest 200. Paging is an additive change for later (1d or after).
- Confirmed tasks have no reminders yet (local notifications, follow-up step), and calculated deadlines (NL rules) are not built: a client may only send `in_document` or `manual`.
- A document cannot be deleted or edited after confirmation yet.
- Encrypted page files can still stay behind when the request transaction fails after the intake service returned (a lost connection at commit); a storage sweep comes with back-ups (1d), which must also cover `BRIEVIE_MASTER_KEY`.
- AI settings are set with `manage.py configure_ai`; the web settings screen comes with the wizard (1d). The `external` mode was not run against a real service by the builder (no key): the owner checks it with their own Mistral key.
- The API key for an AI service lives in the database sealed with the workspace key; the worker therefore holds `BRIEVIE_MASTER_KEY` too.
- From phase 1c, for 1d and later:
  - Removing a member must unlink their person, and deleting a user must clear `Person.user` inside each workspace's context (row-level security).
  - `listDocuments` now documents 422, so the iOS app's generated switch gains a case at the next contract sync.
  - `updateLabel`'s body schema is now named `LabelUpdateIn`: the generated iOS type name changes at the next contract sync, the wire shape does not.
  - Events written in the same call can tie on `created_at` (the ordering falls back to the id).
  - There is no undo of a whole manual run.
  - Visibility per document and "Zorg private" wait for members (inviting members, 1d).
  - Folders, NL rules (calculated deadlines), full-text search, learned rule suggestions and paging of `listDocuments` are not built.
- From writing the install docs (2026-11-19), for a NAS:
  - PostgreSQL is published on `127.0.0.1:${POSTGRES_PORT:-5432}` of the host, which collides with a database a NAS often already runs on 5432. Only the host-side tools (`make test`, `make migrate`) use that port; the stack itself does not need it published.
  - `.env.example` says to change `BRIEVIE_HTTP_PORT`/`BRIEVIE_HTTPS_PORT` "only to run a second stack", while nearly every NAS already serves its own pages on 80/443; and the HTTPS port is part of the address in the pairing QR code, so changing it later means pairing every phone again.
  - The documents (and the database and Caddy's authority) live in Docker volumes, not in the Brievie folder: a NAS's own back-up of that folder does not hold them. Only `./brievie backup` does.

## 2026-11-16: more than one AI supplier

- **`AISettings.api`** (`openai` or `anthropic`, default `openai`) says what kind of API
  sits at `base_url`. `settings_service` has presets per supplier: `mistral` (openai API,
  `https://api.mistral.ai/v1`, `mistral-small-latest`), `openai` (`https://api.openai.com/v1`,
  no default model) and `claude` (anthropic API, `https://api.anthropic.com`,
  `claude-opus-5-5`). `configure_ai --supplier` picks one; `--base-url` and `--model`
  override the preset (a custom URL with mistral/openai stays the OpenAI-compatible API;
  `claude` is always the Messages API). `local` and `off` are always `openai`.
- **OpenAI has no default model on purpose**: model ids change often and we do not invent
  one. `--supplier openai` without `--model` is refused.
- **New dependency `anthropic` (2.8.0)**, built on `httpx2`. We chose httpx over the
  `openai` SDK before because the OpenAI-compatible call is one plain POST. Claude's
  Messages API is not OpenAI-compatible (other request and response shape, own error
  types), so here the official SDK is the smaller risk: it tracks the request shape
  (`output_config` with a JSON schema) and gives typed errors. The SDK retries nothing
  (`max_retries=0`): the job queue does. Its DEBUG logging includes request bodies, so
  the `anthropic` logger is held at WARNING.
- **Claude's default model is `claude-opus-5-5`**, overridable with `--model` (for
  example `claude-haiku-5-5` for a lower cost). No server-side refusal fallback is
  requested, so a letter only ever goes to the configured model. A refusal or a cut-off
  answer (`stop_reason` other than `end_turn`) is `ai_invalid_output`.
- Errors map as for the OpenAI provider: 401/403 `ai_key_rejected`; 429, 5xx (also 529),
  408, 409 and connection errors `ai_unavailable` (retry); other 4xx `ai_request_rejected`.
  Timeout is 120 s because the model may reason before it answers.
- Not run against the real Claude API by the builder (tests use a mock transport): the
  owner checks it with their own key.

## 2026-11-16: licence

- The host is licensed `AGPL-3.0-or-later` (`LICENSE`, `license` in `backend/pyproject.toml`
  and `web/package.json`). The spec (sections 11 and 14) says AGPL so nobody can build a
  closed cloud version; it does not say which variant. Chosen: "or later", the FSF default,
  so a future AGPL version can be adopted without asking every contributor. Dropped:
  `AGPL-3.0-only` (no gain while the owner holds all copyright).
- No licence header per source file: the `LICENSE` file and the README section cover the
  repository. The CLA from spec section 14 is still open and is needed before the first
  outside contribution.

## 2026-11-16: Phase 1c scope (owner decisions)

- **A person can be linked to a member.** Post is always on the name of a `Person`.
  Every member gets a person automatically; a person without an account can be linked
  later and keeps all their post.
- **Everyone in the workspace sees every document.** There is one account per workspace
  until members can be invited, so restricting does nothing yet. "Privé" and "Zorg is
  private by default" (spec 14) arrive with inviting members. `docs/PRIVACY.md` says it.
- **Proposal first, automatic per rule.** A label or addressee from the AI is always a
  proposal the user confirms. A rule starts in mode `propose`; the user can switch it to
  `auto`. Every change is in the document's history and can be undone.
- **Calendar through EventKit in the iPhone app.** Google and Outlook cannot fetch an ICS
  feed from a home server on a private address. Dropped: an ICS feed per member (it can
  follow once outside access exists).
- **Label presets**: ten labels for Thuis, nine for Zakelijk, created with the workspace
  and offered again for an existing one. Departments are not in a preset: the user adds
  several labels at once.
- **Scope**: persons, inbox, post in the web, labels, automatic labelling, rules,
  calendar, ticking off a task. Folders, NL rules, search, the Today screen and local
  notifications move to a follow-up step.

## 2026-11-16: Ordering post (phase 1c)

- **One mechanism for automatic labelling.** A preset label comes with a preset rule
  (for example the word "Belastingdienst" gives the label Belasting); applying a preset
  creates the rules. There is no second, hard-coded matching path. Dropped: keyword
  lists in code next to the rules (two places to explain and to switch off).
- **Only `apps.documents.organise` adds or removes a document's labels and addressees.**
  It writes the history event (`DocumentEvent`) in the same transaction, whoever made the
  change (user, rule, accepted suggestion, undo). Deleting a whole label writes a
  `label_removed` event on each of its documents first (since 2026-11-20, see "Fixes from
  the exploratory API test"); a person on a document cannot be deleted.
- **Suggestions have their own table.** A label or addressee proposal is a
  `DocumentSuggestion` (open, accepted, dismissed), not a field of the extraction.
  Dismissed is remembered: nothing automatic (AI, name matching, rules) proposes or
  applies a change the user dismissed on that document. An `Origin` of empty string is a
  dismissed marker for a change nothing proposed (the user removed it by hand).
  Dropped: storing proposals in the extraction JSON (no per-item state, no memory).
- **A rule takes over an open suggestion.** Strongest origin wins: rule, then name
  match, then AI. A proposal counts in a rule's counters (`proposed`) only when it is
  new or taken over, so a second run does not inflate them.
- **Rules run at three moments:** when processing ends, when a document is confirmed, and
  on "Toepassen op bestaande post". Never on a hand edit of labels or addressees: no
  loops and no surprises when someone removes a label.
- **Name matching is deterministic and runs on the server**, also with AI off. A match
  directly after an address word ("t.a.v.", "aan", "mevrouw", "familie", ...) scores
  0.95, one in the first 600 characters of the first page 0.85 (preselected from 0.85),
  any other 0.60. An address word counts only directly before the name on the same line,
  with nothing but whitespace or `.,:;` between. Why: a name far behind a word like "aan" or "beste", or on another line, says nothing about who the letter is addressed to, and a loose window made too many names look addressed.
- **"aanslag" is not a recognition word for Belastingdienst.** A municipal tax bill says
  it too (gemeentelijke belastingen), so it would label town hall post as Belastingdienst.
- **Closed lists as per-request aliases.** The AI is shown the workspace's labels and
  persons under short aliases for this request only; its answer is translated back and
  every id is checked against the workspace's own lists. Free text from the model is
  never stored as a label or addressee. What leaves the network: see
  `docs/PRIVACY.md` (names of labels, display names and name variants of persons; never
  ids, rules or page images). At most 50 persons and 100 labels (by name) are offered
  to the AI per letter.
- **A fixed colour palette for labels** (eight names). The CSP forbids inline styles, so
  the web has one CSS class per colour and the app maps each to its own; a free hex value
  would need inline styles.
- **Rule conditions are flat objects** (one condition type per object, no `anyOf`),
  because the contract test forbids `anyOf` (the iOS generator cannot read it). Limits
  on counts and lengths are checked in `rules.definition`.
- **The rules engine stays pure** (`apps.rules.engine`, no Django import): facts in,
  planned changes out. The executor does the database work.
- **No `remove_label` action.** Rules add labels and addressees only. Removal by a rule
  has no undo story that is simple to explain. Dropped until somebody needs it.
- **Undo is offered only while the rule's event is the latest event** for that label or
  person on the document. After a later change by hand the undo would reverse the wrong
  state; the user reverses it by hand (the same click). Undo also dismisses the matching
  suggestion, so the rule does not do it again.
- **No undo of a whole manual run.** The builder shows the match count before the run;
  undo is per document.
- **A manual run locks each document** (blocking `select_for_update`, not skip-locked,
  which could silently miss a document) and **one run per rule can be queued at a time**
  (queueing lock), so a double click does not run it twice.
- **The engine never sees a dismissed or missing target.** The document's dismissed
  labels and addressees are part of its facts and are never planned, so no later rule
  builds on them ("label X → label Y" does not fire after the user removed X). Actions on
  a deleted label or an archived person are dropped before evaluation for the same reason.
- **A failure while organising a processed letter keeps the AI's result.** Name
  matching, AI picks and rules run in a savepoint: when they fail, only their writes are
  rolled back, the proposals are stored and the letter goes to `needs_review`.
- **Rules respect the limits of 30 labels and 10 addressees per document.** The executor
  skips an applied or proposed change that would take the document over the limit;
  otherwise every later hand edit would be refused.
- **A manual run gives way to processing.** `apply_rule_to_existing` runs at priority -1
  (below `process_document`) and does one batch of 50 per job run, queueing the next
  batch with the last document id as cursor under the same queueing lock. When a fresh
  run of the rule is already waiting, the continuation is dropped: that run starts from
  the beginning.

## 2026-11-16: a new app must read an old server

People update the iPhone app before the server, and the iOS client is generated from
`docs/api/openapi.json`: a property under `required` makes the generated decoder refuse a
body without it. So a field added to an existing API schema always has a default in its
Pydantic schema and is never required (the server still always sends it). Request schemas
follow the same rule. `test_released_schemas_gain_no_required_properties` compares the
contract with `backend/tests/fixtures/openapi_1b.json`, the released 1b contract, and fails
when a schema that exists in both gains a required property. Schemas new in a phase are not
restricted. When a release ships, the fixture is replaced by that release's contract.
openapi-typescript makes list fields with a default factory optional but keeps scalar
fields with a literal default required; the web client is served by the same server, so it
only reads the optional lists with `?? []`.

## 2026-11-17: follow-ups to phase 1c

1. Migration `rules.0003` gives an unedited old Contracten or Belastingdienst preset rule the current definition; a rule the user changed is left alone.
2. A rule writes at most one `proposed` `RuleRun` per document, so processing a letter again does not count it twice.
3. A PostgreSQL deadlock or serialization failure is answered with 409 `try_again`, and the request transaction is rolled back.
4. A failure in the rules during `confirm_document` is caught in a savepoint (logged as type and document id); the user's review is stored. A deadlock or serialization failure is not caught: the whole confirm rolls back with 409 `try_again`, so the letter's rules still run on the retry.
5. `DocumentEventOut.actor_id` (additive); the web says "Jij" by id and compares names only when an old server sends no id.
6. `updateLabel` takes `LabelUpdateIn`; an empty or missing colour keeps the label's colour. The body schema's name changes (the generated iOS type at the next contract sync); the wire shape does not.
7. `testRule` reads the facts of all checked documents in bulk (`facts.facts_for_many`): a fixed number of queries (11).
8. `create_extra_labels` saves each label in a savepoint and skips a name PostgreSQL's `lower()` sees as taken.
9. The contract test also checks that released schemas and operations (with their `operationId`) still exist, that no released schema lost a property or changed a property's definition (all but `title`, `description` and `default`), that no released operation gained a required parameter, and that no released request body became required or changed its schema (one recorded rename: `updateLabel`).

## 2026-11-17: security review of phase 1c

1. `listRules` needs role `member`, as design 9.5 says (it was open to a viewer). Every other rules endpoint already needed `admin`. The web leaves Automatisering out of a viewer's navigation, and the rules pages do not ask a viewer's rules (a sentence instead of a 403 alert).
2. `testRule` runs inside the request, so it has a hard budget: at most the first 100,000 characters of each document (`TEST_TEXT_CHARS`) and 2,000,000 characters in all (`TEST_TEXT_BUDGET`, about 0.7 s of normalising). It stops before the document that would go over; `checked_count` is the number of documents it checked, and the web already says "N van de nieuwste M" when that is below the total. Only the first characters are loaded from the database. No contract change apart from the operation's description. Processing a letter still evaluates its whole text (at most 30 pages of 20,000 characters, in a background job), so for a document longer than 100,000 characters the test can miss a match, or report a "does not contain" match, that the real run would see differently.
3. Accepted, stays as is for now: before a document is confirmed, the sender and document type the AI proposed count as settled facts for rules, so a crafted letter can make an automatic rule on sender or type fire. The result is only a label or addressee from the workspace's own lists, and it can be undone. Label choices of the AI never trigger automatic rules.

## 2026-11-17: web interface moves to the Fluent 2 design language

The owner asked for a more enterprise look, "meer richting Microsoft". The web now follows
Fluent 2 (the calm, dense look of Microsoft 365 admin, Azure portal and Outlook on the
web) and keeps Brievie's own name, envelope-with-tick mark and navy. No Microsoft logo,
product name or icon is used. Behaviour, routes, API calls and roles are unchanged.

- **Tokens in one place.** Every colour, type size, radius, shadow and spacing step is a
  custom property on `:root` at the top of `web/src/styles.css`; components use only
  tokens. Type: the system stack led by `"Segoe UI Variable", "Segoe UI"`, no web font;
  Fluent ramp (caption 12/16, body 14/20, subtitle 16/22, title 20/28, large title 28/36),
  weights 400 and 600 only. 4 px spacing grid; radius 4 px (8 px on dialogs); Fluent
  shadows (`shadow2`/`shadow4` on surfaces, `shadow16` on menus, `shadow64` on dialogs);
  no gradients. Controls are 32 px high, 40 px on a touch screen (`pointer: coarse`).
- **Brand colour.** Interactive elements (primary buttons, links, selection, focus) use
  Fluent's brand blue ramp (`#0F6CBD`, hover `#115EA3`, pressed `#0C3B5E`, tint
  `#EBF3FC`). Its hue (208°) is that of Brievie's navy (212°), so it reads as Brievie, and
  white on it is 5.4:1. The navy `#16293F` stays for the app bar and the brand panel of the
  login and setup screens. Dropped: the navy as the button colour (on navy chrome a navy
  button disappears, and a second, lighter blue was needed for links and focus anyway).
  Every text/background pair is checked for WCAG AA (lowest: white on `#0F6CBD`, 5.4:1;
  secondary text `#616161` on `#F5F5F5`, 5.7:1; status, label and persona tints 5.9:1 or
  more). Disabled text (`#BDBDBD`) is exempt, as WCAG allows.
- **Status** in the Fluent way: success, warning, error and info as a tint, a border, a
  darker text colour and an icon, never colour alone. Message bars replace the ad hoc
  `alert` and `notice` blocks; badges replace `tag`.
- **Layout.** A 48 px app bar (menu button, mark and name, account menu with the name,
  e-mail address and "Uitloggen"), a navigation pane with icon and name and a brand bar on
  the selected page, then the page: title, optional breadcrumb (document and rule editor),
  a short description and a command bar (a plain row: main action first, the rest as
  subtle buttons).
  The pane folds to a 48 px icon rail with the menu button (remembered in `localStorage`,
  wrapped in try/catch); below 1200 px it is a rail by default and opens over the page;
  below 640 px it is hidden and opens as a drawer (Escape or a click on the backdrop
  closes it; it is not a focus trap). Tables are a details list: 36 px header, 40 px rows,
  row hover, no sort arrows (nothing sorts). On a phone (below 640 px) the post list is a
  card per letter (sender, status, kind, date, persons, labels) instead of a table that
  scrolls sideways.
- **Icons.** Our own line icons (`web/src/components/icons.tsx`): 20 px, 1.5 px stroke,
  inline SVG, always `aria-hidden` next to a text. Dropped: `@fluentui/react-icons`
  (a dependency for some twenty icons, and its look is Microsoft's own).
- **No component library.** React Aria Components stay for the accessible behaviour;
  Fluent UI React (`@fluentui/react-components`) is not adopted. It would bring a good
  data grid, combobox and dialog, but it styles through Griffel (CSS-in-JS that injects
  `<style>` elements at runtime), which the strict CSP (`style-src 'self'`) blocks unless
  styles are extracted at build time with an extra build plugin; it adds roughly 150 to
  250 kB gzip, and every screen and its tests would be rewritten. Revisit when the data
  grid needs sorting, column resizing or row selection.
- **CSP.** No inline style or script was added: the select chevron and checkbox tick are
  `data:` SVGs in the stylesheet (allowed by `img-src 'self' data:`). Checked in a browser
  against a build served with the production CSP: no violations.
- **Accessible names.** None changed. The account menu moves "Uitloggen" and the e-mail
  address one click deeper (behind the button "Account"); two tests open the menu first.
  New names: the button "Menu" (with `aria-expanded`), the dialog "Account" and the
  breadcrumb navigation "Kruimelpad".
- **Dark mode** is not built. The tokens are structured for it: a dark theme is one block
  that redefines them under `prefers-color-scheme: dark` and `[data-theme="dark"]`.
- **Dropped:** upper-case table headers and section labels (sentence case, as Fluent),
  44 px controls on desktop (32 px, enterprise density; 40 px on touch), the full-height
  navy sidebar, and an overflow menu in the command bar (the few commands wrap instead).
- Screenshots of the post list, a document, the inbox and the rule builder at 1440, 1024
  and 390 px are in `docs/ui/fluent/` (made-up letters and names).

## 2026-11-17: recorded consent for AI

Design: `docs/superpowers/specs/2026-11-17-ai-consent-design.md`. Choices 1 to 4 were
put to the owner and taken as proposed.

- **An append-only table, not columns on `AISettings`.** `AIConsentEvent`
  (`pipeline_aiconsentevent`, RLS enabled and forced): `event` (`granted`, `changed`,
  `switched_off`), `mode`, `supplier`, `host`, `model`, `actor` (user, null for the
  command line), `actor_label`; `created_at` is the moment. It is the history. The state in
  force is `AISettings.consented_base_url`, written in the same transaction under a row
  lock, so it depends neither on the order of rows nor on the server's clock (a Pi
  without a clock battery can start in the past). Only `settings_service.configure` writes it, in the transaction that writes
  the settings. A generic audit log was not built: its shape is not known yet.
- **Consent covers one address** (host, port and path; behind a gateway the path picks
  the service). `granted` is written when somebody agreed to an external service;
  another model or key at the same address is `changed` without a question; any other
  address needs a new consent. The event keeps only the host. Switching off or moving to `local` ends it.
- **`local` is recorded, not asked** (owner): nothing leaves the network.
- **The pipeline enforces it** (owner). `provider_for` raises `ai_not_configured` for
  `external` unless `consented_base_url` equals `base_url`. This also covers an
  address changed by hand. Consequence: a workspace already on `external` reads no
  letters with AI after this upgrade until `configure_ai` is run once; letters scanned
  in between fail (as with a wrong `local` address) and are retried by hand. The
  Claude SDK client no longer follows redirects, like the OpenAI-compatible one.
- **The supplier is `custom` when the address is not the preset's host**, so the name
  and country of Mistral are never shown for somebody else's server. For `custom` the
  country is "unknown": we do not guess it from a host name.
- **`configure_ai`**: types `akkoord` at a terminal, or `--accept-external-ai` in a
  script; the notice is printed in both cases and the settings are checked before the
  question. Without a terminal and without the flag it refuses. The API key is asked
  after the consent.
- **`AiStatusOut` gains `supplier`, `host`, `since`** (nullable, default null). This
  replaces "nothing about the service itself" from phase 1c: a member may know where
  the text of their post goes. Still no model, full address or key, and no host for
  `local`. `since` empty with mode `external` means: no consent, no letter is read.
- **One line for every member** (owner) on the Inbox and Post pages; no per-member
  "read" click until members can be invited (1d). The countries per supplier are
  repeated in `web/` (and will be in the iOS app): the API gives the supplier key, the
  client the sentence.
- Not done: consent in the web interface (comes with the AI settings screen, 1d; the
  service already takes an `Actor` with a user), a database rule that forbids UPDATE and
  DELETE on the table, a screen that lists the history.

## 2026-11-17: AI settings in the web interface

Design: `docs/superpowers/specs/2026-11-17-ai-settings-web-design.md`. The owner
delegated the choices.

- **One place for the rules.** `updateAiSettings` (`PUT /workspaces/{id}/ai`, role
  admin) calls `settings_service.configure`, as `configure_ai` does. `external_notice`
  moved from the command to the service; the command and `previewAiSettings` give the
  same text (its last line no longer names `--mode off`).
- **The client echoes what it agreed to.** `previewAiSettings` returns the notice and
  the `address` it is about; `updateAiSettings` takes `consent_address` and `configure`
  counts it as consent only when it equals the address it resolves itself, under the
  row lock. Otherwise 409 `ai_consent_required`. Rejected: a bare `consent: true` (says
  nothing about what was read) and a signed token (more machinery than the address
  comparison, which is exactly what a consent covers).
- **A tick box, not a typed word**, in the browser: the dialog, the tick and the
  server's comparison are the explicit act. The command keeps `akkoord`.
- **The key is write-only.** `AiStatusOut.has_api_key` (default false) is all that is
  read back. Without `api_key` the stored key stays when mode and address are the same;
  otherwise 422. A key that moved to a new address would be sent to another company.
- **Errors.** Settings that do not pass are `AiSettingsError` with a reason; the API
  answers 422 `ai_settings_invalid` with a Dutch sentence per reason. The command still
  prints the English message.
- **Throttled**: 10 per 10 minutes per caller and workspace
  (`CallerThrottle("ai_settings")`), not per client address as setup and pairing are:
  Ninja throttles before the role check, so with a shared address a viewer could use
  up the administrator's attempts and keep them from switching AI off.
  `previewAiSettings` writes nothing and is not throttled.
- **`listAiSuppliers`** gives name, country, host and default model per preset, so the
  form does not repeat them. (`AiStatusLine` still has its own sentence per supplier.)
- **No "Test de verbinding" button.** Possible without letter text (a fixed one-word
  prompt), but it needs a new provider method for both APIs and an outbound call from
  inside a web request, where the request transaction is open. A wrong key shows at the
  first letter as `ai_key_rejected`. Belongs with the wizard (1d).
- **The model is read back to an administrator only** (`AiStatusOut.model`), so that
  replacing the key does not silently put the model back to the preset's default. The
  full address is still never read back: a workspace on an own address is told to type
  it again, and with the same address is asked nothing and keeps its key.
- **An own address through the web interface is always OpenAI-compatible** (the form
  sends supplier `openai`, so a model is required). An own address that speaks Claude's
  API stays a job for `configure_ai`.
- **The notice names a preset only for the preset's exact address.** Another port or
  path at the same host is shown as the whole address with country unknown, because
  that address is what the consent is for.
- **`local` got stricter** now that it can be set from a browser: link-local addresses
  (where a cloud keeps its metadata service) and IPv6 forms that carry a public IPv4
  address (6to4, Teredo, IPv4-mapped) are refused. Accepted: an administrator of a
  workspace can make the server post letter text to a service on the server's own
  network; that is what `local` is. On a server shared by workspaces that do not trust
  each other's administrators this needs a server-wide switch; noted for the
  enterprise layer.
- **The actor label is `api`** for the web interface and the app alike (an
  administrator's paired device may call the endpoint too); the user is recorded.
- **The page "AI" is for every member**; only an administrator gets the form.
- Checked in a browser against a local server with a throwaway database and a made-up
  key: switching on with the dialog, the status line after it, the phone width. Not
  checked: a real AI service.

## 2026-11-17: Back-up and restore (B1)

1. The database role `brievie_backup` exists: `LOGIN`, `BYPASSRLS`, `NOSUPERUSER`, `NOCREATEDB`, `NOCREATEROLE`, `CONNECT` on `brievie`, member of `pg_read_all_data` (read-only). Only the back-up keeper and the storage sweep use it, for `pg_dump` without `--enable-row-security`. This changes the premise of the 1d design (`2026-11-17-fase-1d-leden-rollen-sso-design.md`, line 119, "deliberately do not have"): the **application's** roles (`brievie_owner`, `brievie_app`) still cannot bypass RLS. A new server gets the role from `deploy/postgres-init/01-roles.sh`; an existing one from `deploy/ensure-backup-role.sh` (`make dev`; idempotent), both through `backup-role.psql`. Its password is `BRIEVIE_BACKUP_DB_PASSWORD` in `deploy/.env`.
2. The default back-up password `BRIEVIE_BACKUP_PASSWORD` is derived from the master key: `HKDF-Expand(SHA-256, length 32, info "brievie/backup-password/v1")`, hex (`apps.common.crypto.derive_backup_password`). HKDF-Expand only, since the master key is already 32 uniformly random bytes (RFC 5869 section 3.3); it equals `HMAC-SHA256(master key, info || 0x01)`. So the master key alone opens the back-ups. It is derived only inside the container, by `manage.py derive_backup_password` (`./brievie` appends its output to `deploy/.env`), never on the host: there the only tools are openssl, which takes an HMAC key only as a command-line argument that any local user can read from the process list, and python3, which a NAS may not have. A secret is never an argument of a started process: `init-env.sh` substitutes passwords with shell built-ins, and `01-roles.sh` and `ensure-backup-role.sh` give psql the passwords on stdin (`\set`). The label and the construction never change: existing back-ups depend on them.
3. **restic 0.18.1** (BSD-2-Clause) makes and checks the back-ups: encrypted, deduplicated, incremental, with folder, S3, SFTP and REST destinations in one tool. The image copies its static binary from the official image `restic/restic:0.18.1`, pinned by version and index digest (amd64 and arm64 both published, so a NAS on ARM works). At least 0.17 is needed (`--stdin-from-command`, exit code 12 for a wrong password, `RESTIC_REST_*`). Rejected (spec D 4.1): age or tar plus age (every back-up a full copy; deduplication, retention and verification would be ours), borg (comparable, but no S3 backend confirmed) and volume tarballs (stack stopped, unencrypted, tied to the PostgreSQL major).
4. **postgresql-client-18** from the PostgreSQL apt repository (PGDG; the signing key is pinned by checksum in the Dockerfile) gives `pg_dump` and `pg_restore` in the image. Debian's own client lags the server; `pg_dump` refuses a server newer than itself. The client major equals the server image's major (`postgres:18`); `test_pg_client_major_matches_the_server` compares compose, CI and the Dockerfile, so client and server move together in one change.
5. Two containers run the back-up code, from the same image (`BRIEVIE_IMAGE`). The **keeper** is always on and serves only the Procrastinate queue `backup`, as uid 10001. It holds the app role (queue, `BackupRun`), the read-only back-up role, the back-up password and the destination credentials, and mounts the documents and the CA copy read-only. It holds no master key, no superuser or owner password: a compromised keeper reads what it backs up but can neither decrypt documents nor change the schema. The **admin** container (compose profile `admin`, `run --rm --no-deps`, root) is one-off: set-up, restore and deriving the back-up password. It alone holds both the master key and the superuser password, and mounts every volume writable. Caddy copies its CA (keys included) to the `caddy-authority` volume for the back-up (`caddy-publish-root.sh`, files 0600 owned by 10001); the api and worker never mount it. The image creates `/data/authority`, `/data/backup-config` and `/backup` owned by 10001, so a back-up before Caddy's first certificate still finds its source paths.
6. **The storage sweep** (`sweep_storage [--delete]`, run as uid 10001 through `./brievie sweep`; run by hand in the admin container it runs as root) closes the "orphan files" gap; the earlier notes about it (phase 1b, "Known gap: orphan files" and the open item about a lost connection at commit) are hereby closed. It lists, and only with `--delete` removes, `<uuid>/<uuid>/<n>.page` and `.tmp-*` files that no `documents_documentpage.storage_path` names. The known paths are read as the back-up role with `row_security = off`, so a role that cannot bypass RLS fails instead of listing live pages. The files on disk are listed before the database is read, files younger than 24 hours are skipped (and checked again just before the unlink), deleting goes through directory descriptors with no symlink followed, directories are never removed, and when any stored path is not in the page layout `<uuid>/<uuid>/<n>.page` (or none matches a page file on disk) it refuses (`internal_error`) instead of calling everything an orphan. Output is counts and total size, never paths. It holds the back-up lock, so it never runs beside a back-up or restore. When the database knows no pages at all while page files exist on disk (an empty or wrong database, for instance a back-up URL that points at another server), the sweep refuses and lists nothing; there is no override flag. Old `.tmp-*` files alone are still listed then.
7. **Destructive functions carry their own path guard** (`apps/backup/safepaths.py`), after a test once ran an unguarded "empty this directory" against `/` on a developer's Mac. Every function that deletes, empties, overwrites or chowns judges its own path first, whoever calls it, and refuses with `unsafe_path` ("Brievie weigert deze map te wissen of te overschrijven, omdat dat niet veilig is."). `_empty` (restore `--replace`) and `delete_orphans` (the sweep) accept only the configured directories (storage root, authority copy, config folder; the sweep only the storage root), and refuse a relative path, a path with `..`, `/` or one directory below it, the home directory and its ancestors, a real folder inside a home directory, and any path that is a link or is reached through one (except a system link directly below `/`, such as macOS's `/var`). So the sweep still lists through a linked storage root but no longer deletes through one. `_place_authority` accepts only an absolute `…/caddy/pki/authorities/local` without links, and removes only the leaf directory `…/caddy/certificates/local` derived from it. The restore output, the back-up marker and the chowned repository tree must resolve strictly inside their folder or mount (chown never follows a link), and `write_private` writes only a plain file name. Testing rule: a guard is built and tested as a pure function first; a refusal test of a destructive function replaces its primitives (`os.scandir`, `os.unlink`, `shutil.rmtree`, `os.replace`, `os.chown`, `Path.unlink`, …) by recorders that fail when called; destructive code never runs against a real path outside the test's `tmp_path` or a container.
8. **A fixed allow-list, a clean-up after every restore, and deletion through descriptors** (second safety round; supersedes the "configured directories" rule of item 7). What may be emptied, restored into or swept no longer comes from the settings that name it: only `/data/documents`, `/data/authority` and `/data/backup-config` (the sweep only `/data/documents`; Caddy's authority only `/caddy-data/caddy/pki/authorities/local` and its leaf `/caddy-data/caddy/certificates/local`; `/backup` never), or, for tests only, a path strictly inside a temporary folder that is itself proven safe (not `/`, not a home directory or near one, not above a production path, at least three components unless `/tmp`). A directory must also be the configured one, contain no glob character, and not equal, contain or lie inside the back-up mount or another of the three. Home directories are found by inode from `$HOME` and the password database, and `/Users`, `/home` and root's home are refused in any spelling. Settings paths are absolute or refused, so an empty `BRIEVIE_BACKUP_MOUNT` is never the current directory. Every restore (with or without `--replace`) judges the three directories before restic starts; restic gets exactly those as includes, with `--` before the snapshot id (8-64 hex characters). Afterwards the three trees are walked through descriptors: links, devices, fifos and sockets are removed (`restore_files` reports the count in one line), setuid, setgid and sticky bits cleared, and as root everything goes to uid 10001; a file with more than one hard link keeps its owner. All deleting, writing and chowning (`_empty`, the authority and its leaf, the repository hand-over, the marker, `write_private`, the restore output) opens the directory component by component with `O_NOFOLLOW`, checks it is the directory that was judged, and works relative to directory descriptors (`safepaths.open_dir_nofollow`, `empty_dir`, `clean_tree`, `chown_tree`); nothing crosses into another file system. The authority is read from the restored copy without following a link, regular files of at most 64 KiB only. Behaviour changes: a custom `BRIEVIE_STORAGE_ROOT` (or authority or config folder) outside the fixed list is refused for `restore_files` and for `sweep --delete` (`directory_not_allowed`, with a Dutch sentence that names the fixed folders; a bind mount still appears as `/data/documents` in the container, so normal installations are unaffected); `restore_fetch --out` must be a new or empty folder that is no data or back-up volume; the restored `.env` keeps none of the back-up's host lines (`BRIEVIE_BACKUP_DIR`, the repository and its credentials, `BRIEVIE_IMAGE`, `COMPOSE_PROJECT_NAME`, the ports): `./brievie` sets them from the installation that restores, and `restore_fetch` names what it left out in one line on stderr; `init-env.sh` links a fresh `.env` into place (never over one made meanwhile) and refuses a `.env` that is a link. In tests every directory setting and the restic binary point into the test's `tmp_path` by default, and one shared `Recorder` makes any destructive primitive or process start fail a refusal test.
9. **Third safety round (image side).** The test exception of the path guards no longer trusts `TMPDIR`: production accepts only the fixed list, and the test suite installs its own `tmp_path` as the one test root (`safepaths.allow_test_root`, from the autouse fixture; refused for `/`, a home directory or anything at, in or above one, a root shallower than three components, or one overlapping a production volume). Restore-side restic reads (`snapshots`, `dump`, `restore`, and `cat config` when used for reading) take `--no-lock`, so a restore source may be mounted read-only; the admin container's `/backup` mount takes its mode from `BRIEVIE_BACKUP_MOUNT_MODE` (default `rw`, `./brievie` sets `ro` for the restore steps; the keeper's mount is unchanged). Before restic writes, the three target directories are walked through descriptors and a restore is refused (`restore_target_unsafe`, with or without `--replace`) when they hold a link, a special file or a file with more than one hard link; after the restore such a hard-linked file is removed and counted rather than kept with a foreign owner. Placing Caddy's authority happens on a fresh machine before Caddy first starts (a Caddy that started would make an authority of its own and every paired phone would have to pair again), so it makes exactly the chain `caddy/pki/authorities/local` below the existing volume `/caddy-data` and nothing else: each component through its parent's descriptor (0700, no link followed, an existing link or non-directory refused with `unsafe_path`); only a missing `/caddy-data` (the volume is not mounted) is refused with `caddy_not_ready`. The leaf directory `…/caddy/certificates/local` is removed only when it exists and is never made. The restored `.env` also drops every `COMPOSE_*` and `DOCKER_*` key, and `make` reads `export COMPOSE_PROJECT_NAME=…` like the plain form.
10. **The CI job `restore` proves a back-up restores onto an empty machine, on every change.** On a GitHub runner it makes a server (`./brievie start`), seeds one user, two workspaces with a one-page letter and a recorded AI decision each, and a paired device (`.github/scripts/restore-seed.py`, through the app's own services; AI stays off), backs up to a folder under `$RUNNER_TEMP` (a named volume would go with `down --volumes`), then destroys everything: containers, every volume of the project, `deploy/.env`. It restores from the folder and the master key alone (key on stdin), and again over the running server with `--replace`. After each restore one request per seeded page (`.github/scripts/restore-check.sh`) must return the seeded bytes over a connection that trusts only the **old** authority (`--cacert`, never `--insecure`): that single hash proves the sealed page opens, the workspace is complete, the device token still signs in and the certificate chains to the old authority; the fresh authority is also byte-identical to the saved one, and `./brievie backup` and `./brievie status` work again without handwork. The job prints no logs (they hold the installation code) and keeps the token out of every process argument. It cannot prove: a real or slow disk, an external disk or a Synology (the runner's folder is a plain ext4 directory owned by uid 10001), a Linux host other than the runner's Ubuntu, a REST or S3 destination, a restore on another host name (pairing again), or an authority older than a week (D 4.7: a clock moved forward in the Caddy container or a pre-made authority fixture; belongs with the B2 restore test). Those stay with the manual proofs in `SELFHOST.md` and with B2.
11. **Retention, and the fourth safety round.** Retention is `forget --prune --group-by host,paths --keep-within 2d --keep-within-daily 7d --keep-within-weekly 1m --keep-within-monthly 1y`: every snapshot of the last two days (counted from the newest) stays, older ones thin out to one a day for a week, one a week for a month and one a month for a year. Why: the daily rule alone keeps one snapshot per day, so a second back-up on the same day removed the first; the CI restore job found the safety back-up made right before `restore --replace` pruning the run the user had chosen, after the database had already been dropped. The safety back-up of a restore (`backup --no-prune`, stored as `requested_by=restore`) applies no retention at all. Before its destructive step `./brievie` can check cheaply that the chosen run still has both snapshots (`restore_check --run <id>`, read with `--no-lock`, exit 0/1), and `backup_status --list` lists the runs on the destination (id, date, complete; no paths), which the `run_not_found` sentence points to. `restore_database` and `restore_files` find the run before they change anything. Also in this round: every path guard first judges the path's text (absolute, depth, home trees, the fixed list, the test root) and only then looks at its components, and nothing is listed, opened or created before a path is accepted, so the outcome is the same on Linux (where `/etc` is a directory) and macOS (where it is a link); the restore output folder is exactly `/out` (the staging bind mount) or a folder in the test root, the back-up mount exactly `/backup`, and the sweep judges its root before it lists it (a linked storage root is no longer listed either).
12. **Fifth safety round: what a back-up is, what retention may remove, and the restored queue.** Retention now runs, per installation, in this order: list the installation's own snapshots (`snapshots --host <host>`); stop, without removing anything, when one lies more than 24 hours ahead of now (the run is recorded as succeeded with the warning `clock_skew`, which `backup_status` shows: retention is relative to the newest snapshot, so one wrong clock would otherwise remove almost everything); remove runs that have only one of their two snapshots and are older than 48 hours, by their run tag; remove safety back-ups older than 30 days, counted from now, by their run tag (not with `forget --tag kind:safety --keep-within 30d`: restic counts `--keep-within` from the newest snapshot of the group, so a lone safety snapshot would never expire; checked against restic 0.18.1); then `forget --prune --host <host> --group-by host,paths --keep-tag kind:safety --keep-within 2d --keep-within-daily 7d --keep-within-weekly 1m --keep-within-monthly 1y`. Nothing of this on append-only. Both snapshots of a run carry the run's start as their time (`--time`, UTC; restic gets `TZ=UTC`), so retention can never keep one half of a run and drop the other (found by review: the halves lie in different groups). The safety back-up of a restore (`backup --safety`, older name `--no-prune`) is tagged `kind:safety` on both halves, applies no retention, is kept by ordinary retention and goes after 30 days; with `BRIEVIE_WRAPPER=1` the command prints `safety_run_id=<id>`, and an ordinary back-up that was already open is waited for first. The restic host is `brievie-<first 12 hex of the installation id>` when `/data/backup-config/installation-id` is a regular file holding 32 lowercase hex, else `brievie`; so two installations on one repository never prune each other. Snapshots made before this change stay under the host `brievie` and are no longer pruned automatically; `backup_status --list` (which, like `find_run` and `restore_check`, never filters by host) still shows and restores them. The installation id is never part of a back-up (excluded from the files snapshot), is never restored (restic restores of the config folder only `manifest.json` and `env`), and `--replace` keeps it when it empties the config folder. A back-up without the `.env` copy fails (`env_copy_missing`) before anything is made, because `fetch` needs that copy. The clean-up after a restore also runs when restic fails (a failing clean-up never hides restic's error), and a restore never restores extended attributes or ACLs (`--exclude-xattr '*'`). Every dump holds the job of the back-up that made it, fetched and holding the task lock `backup`: right after `pg_restore`, in the transaction that closes the restored runs, the restored queue is settled with Procrastinate's own SQL (back-up jobs that wait or run become `failed`, running jobs of other queues go back to `todo`, the old server's workers are unregistered), so a new back-up job is fetched at once instead of after the stalled-job retry (confirmed in a container of brievie-b1: before the fix the fetch returned nothing, after it the new job). The destructive commands (`restore_database`, `restore_files`, `sweep_storage --delete`, `backup_init`) refuse to run without `BRIEVIE_IN_CONTAINER=1`, which the image sets (`not_in_container`); the test suite is let through by the test root it installs. `backup_init` repairs an invalid marker when the repository opens with the configured password (else `wrong_password`). `init-env.sh` recognises its keys in every spelling (`export`, quotes, spaces), and `ensure-backup-role.sh` reads the project with the Makefile's own expression. CI's backend job runs the real-restic tests with the image's pinned restic (they fail there without it); the `restore` job only runs on a GitHub-hosted runner. Also: a restore reads a folder source with `--no-lock` (it is mounted read-only) but a remote one (`rest:`, `s3:`) with restic's shared lock, so another server pruning it cannot pull packs from under the restore; an abandoned run is removed only when it lacks one of its two snapshots (one that died after its files snapshot is a whole back-up); and `_empty` and the pre-restore check refuse a tree that holds the back-up mount itself (`BRIEVIE_BACKUP_DIR` inside a data volume's host path).

## 2026-11-18 — Phase 1d part A: members, roles, devices

Built from `docs/superpowers/specs/2026-11-17-fase-1d-leden-rollen-sso-design.md` (the
design) and `docs/superpowers/plans/2026-11-17-host-1d-a-leden-apparaten.md`. Private post
(part B), second factor and SSO (part C) and the external address (part D) are not built.
No new dependency was added in this part. This section supersedes two lines of "Open
items": "Device tokens never expire…" (they do now, item 8) and "Removing a member must
unlink their person…" (it does, item 11). The "database rule that forbids UPDATE and
DELETE" that "AI settings in the web interface" left open is done for the consent history
(item 1).

1. **The audit log.** Two tables, `audit_workspaceauditevent` (workspace rows, forced RLS)
   and `audit_serverauditevent` (sign-ins, devices, server admin; read by server admins).
   `apps.audit.services.record()` is the only writer. It accepts only a code of a closed
   list and, per code, only listed value keys with short text, numbers or flags. A name is
   stored only for people, devices, invitations and workspaces; labels, rules, documents,
   exports and sessions are an id only, because a label or rule name can hold what
   the log must never hold. The rows have no foreign keys (the workspace field is
   `db_constraint=False, on_delete=DO_NOTHING`), so deleting a user or workspace never
   needs a right the app role lacks. The migration REVOKEs UPDATE, DELETE and TRUNCATE for
   `brievie_app` on both audit tables **and on `pipeline_aiconsentevent`** (the AI consent
   history), and GRANTs SELECT and INSERT; the test checks the privileges of
   `brievie_app` itself, because the suite runs as the owner and would not notice a
   forgotten REVOKE. A restore keeps the privileges (`pg_restore` runs without
   `--no-privileges`). Old rows leave only through two `SECURITY DEFINER` functions owned
   by the schema owner (`brievie_purge_workspace_audit`, `brievie_purge_server_audit`)
   that refuse a cut-off younger than 90 days; the nightly job `purge_audit_events` calls
   them with `BRIEVIE_AUDIT_RETENTION_DAYS` (default 365, minimum 90, refused at start-up
   below that). Note on cascades: `AIConsentEvent.workspace` is `CASCADE` and its `actor`
   `SET_NULL`, so a workspace or user deletion as the app role now fails on that table.
   No code deletes either today; a later one must run as the owner, or keep the history
   some other way. The purge walks the workspaces that exist, so the rows of a workspace
   that was deleted later would stay until that deletion handles them on purpose.
2. **Server admin.** `User.is_server_admin`; a server admin reads the server log and may
   act on accounts that live in more than one workspace. There is always at least one
   active server admin: every path that takes the power away (`set_server_admin
   --remove`, deactivating the account) takes the row lock `lock_active_server_admins`
   and answers `last_admin` when it would be the last one. A second one is appointed on
   the server with `manage.py set_server_admin`; a web page for that belongs with part C.
   Workspace creation stays at `/setup`.
3. **`start_session` is the only sign-in.** `apps.accounts.sessions.start_session` is the
   only caller of Django's `login` (a test scans `apps` and `brievie`) and writes the
   `UserSession` row; a session without a row is signed out (the middleware fails
   closed). Part C hangs the second factor on this one place. The `user_logged_in`
   receiver that records sessions exists only in the test run (`conftest`), so a stray
   `login()` in production gives a session that does not work.
4. **Web sessions** end after 14 days idle or 90 days in all, whichever comes first
   (`BRIEVIE_SESSION_IDLE_DAYS`, `BRIEVIE_SESSION_MAX_DAYS`; the idle limit may not exceed
   the other). Enforced twice: by Django's own expiry and by an explicit check of both
   limits in `UserSessionMiddleware`; an hourly job removes sessions past a limit. The
   limits are environment settings until part C's server settings page; both the api and
   the worker read them, so compose passes them to both.
5. **People administration is web-session only.** Every endpoint that administers
   members, accounts, invitations, roles, devices or sessions of others, workspace
   settings, the log and the export uses `auth=session_auth`, never the default that also
   takes a device token (a test per endpoint: a device token gets 401). A stolen phone
   must not be able to manage people. `listMembers` is the one read the app may use. Every
   response that carries a secret once (reset link, invitation link, pairing code or
   link) sets `Cache-Control: no-store`.
6. **Reset links.** An admin makes one for a member ("Herstellink"); nobody makes one for
   their own account (403 `own_account`: a stolen admin session must not set a password
   without the current one). Only the SHA-256 of the token is stored. The link is
   `/wachtwoord-herstellen#<token>`: the token sits in the fragment, never in a query string, and goes
   to the server in a POST body. It works once and for 24 hours, and only while its maker
   still may act on the account: the authority rule is checked when the link is made and
   again when it is used. Every dead link (unknown, used, expired, revoked, maker no
   longer allowed) gets the same sentence. Completing a reset ends every session of the
   account, **revokes every paired device** (a reset is what one does after a suspected
   break-in; the member pairs the phone again) and every other open link, and leaves a
   notice. The same holds for `manage.py reset_password`. **Binding for private post
   (1d-B), decided by the owner on 2026-11-19:** once a member can have private letters,
   only the server admin makes a reset link for that member; a workspace admin gets 403.
   A reset link is a way into the account, so whoever makes one could read that member's
   private post. What the log cannot prove: the
   completion row has no asserted actor (nobody proved it was the member) and carries the
   maker's id as `reset_by`. Said plainly: after an admin's reset the admin can sign in
   with the new password and dismiss the notice from that later session. Only the session
   the reset itself started cannot dismiss it (`can_dismiss` is false there). So the
   notice does not stop an admin who misuses a reset; the audit log keeps the trace (the
   link's creation, its maker's id on the completion, the revoked devices), in rows
   the application cannot change or delete.
7. **Notices** ("meldingen") are composed in Dutch on the server (`AccountNotice`), so
   the web and a later iOS screen show the same sentence.
8. **Device tokens expire 180 days after their last use** (`TOKEN_IDLE_LIMIT`). The
   install moment of the update is one row (`TokenExpiryStart`), so no phone stops on the
   day of the update. An expired token is revoked when it is noticed (a conditional
   update, so the expiry is logged once) and the request gets the same `401
   not_authenticated` as a revoked token. The app sends `X-Brievie-App-Version`; the
   server stores it at most once a day (`app_version_at`). `Device.paired_via` records
   how a device was paired (part C reads it for SSO).
9. **Pairing by proof.** The link is `brievie://server#v=1&url=…&ca_sha256=…&pairing=…`
   (fragment, URL-encoded, no code in it). From a link the phone never sends the typed
   code: it asks `createPairingChallenge` for a one-time nonce (valid five minutes) and
   sends `proof = HMAC-SHA256(key, "brievie-pair-v1" || nonce || trust binding)`, with
   `key = PBKDF2-HMAC-SHA256(code, "brievie-pair-v1" || pairing id, 600,000 rounds)`.
   The code has only 40 bits, so a relay that saw one HMAC keyed with the bare code could
   try every code offline within its ten minutes; 600,000 rounds make that cost years of
   GPU time, while phone and server pay about half a second once per code. Salting with
   the pairing id lets the server keep the derived key (sealed with the master key) and
   not the code. The trust binding is the SHA-256 of the CA the phone pinned, or, for a
   publicly certified host, the lower-case host name it connected to; a relay with
   another CA or host cannot make a proof the server accepts. **Hosts no public authority
   certifies** (`.local`, `.lan`, `.home.arpa`, `.internal`, single-label names, private,
   loopback and link-local addresses) **get no host binding**: only the CA pin binds
   them. **The phone enforces the 43-character nonce**: the proof's fields are not
   length-prefixed, so a nonce of another length could shift bytes between nonce and
   binding. **A version 2 of the proof must length-prefix its fields**; version 1 stays
   as it is, because the shared test vector (`tests/fixtures/pairing_proof_v1.json`, the
   same bytes in the iOS app) pins it. Five wrong proofs burn the code; whoever learns
   the pairing id can burn it (the user makes a new code; accepted). The QR code and
   `pairDevice` stay exactly as in 1c. `createPairingCode` is limited to 10 per 10
   minutes per caller. An address that is not in `BRIEVIE_PUBLIC_URLS` makes proofs fail
   and burns codes. The app contacts nothing when a link is opened until the user taps
   "Server zoeken"; it then shows the address it found; without a certificate pin it
   keeps only that one proven address.
10. **Invitations** are server rows (`Invitation`), not workspace rows. Only the token's
    hash is stored; the link is `/uitnodiging#<token>` (fragment, POST body for the
    lookup and the accept, `history.replaceState` removes it from the address bar). Valid
    1, 7 or 30 days, default 7; works once. **The maker must still be an active admin of
    the workspace**: an admin who lost the role cannot hand out the role any more, and
    their open links end (demotion, removal or leaving of an admin revokes them, audited). The lookup
    and the accept answer one sentence for every dead link, and cost one query whatever
    the reason. Role, person and e-mail address are fixed by the maker; the invitee
    chooses none of them. **An e-mail address on an invitation proves only that the
    address the account stores equals it.** No mail is sent and nobody proves control of
    the mailbox, so it is not proof that the invitee owns that mailbox: part B must not
    rest private post on it (a person with private post accepts only with an existing
    account or with proof given outside the system). An existing account is found only by
    its address with its own password (the sign-in lockout applies); a new account whose
    address is taken answers 409 `account_exists`. The lookup shows the workspace's AI
    line, including the flag `has_api_key`, to anybody who holds the link (accepted: a
    flag, never the key; it closes the per-member "read" step 1c deferred). Reactivating a
    switched-off account happens only here, with the account's own password.
11. **Roles and members** change only in `apps.workspaces.members` (role change, removal,
    leaving), always under one lock on the workspace's memberships in id order. A
    workspace always keeps an admin (409 `last_admin`). Removal unlinks the person with a
    queryset `update()` before the membership goes (`Person.save` refuses a user who is no
    longer a member), revokes what an admin made when they lose the role (open invitations
    of this workspace, their reset links) and records the invitations. **Reset-link
    revocation on demotion is server-wide and not audited** (accepted): a reset link
    belongs to an account, not to a workspace, so it stops every open link the person
    made, also one made as admin of another workspace; no log row says so.
12. **Deactivation.** When the last membership goes (removal, leaving) the account is
    switched off in `accounts.account_state.deactivate_account`: web sessions end, devices
    are revoked, unused pairing codes are deleted, reset links for it and made by it stop.
    **It also takes the server-admin flag away** (and records `server_admin_revoked`), so
    an invitation that brings the account back can never bring server authority back; a
    returning server admin is appointed again with `set_server_admin`. A workspace admin
    never deactivates a server admin (403 `server_admin_only`), and nobody deactivates the
    last active one. The rule for acting on another account is one function,
    `account_authority`: 404 for strangers, 403 `server_admin_only` when the person also
    lives elsewhere, 403 `own_account` for oneself.
13. **Accepted races.** A member removed while their phone uploads or confirms: the
    request that passed the check a moment before the removal finishes as if it had
    finished a moment earlier (the letter belongs to the workspace and stays; a
    confirmation is a change an admin can see in the letter's history); every later
    request answers 401 or 404. A `FOR KEY SHARE` lock on every write would hold a row
    lock for the length of an upload. Task assignment, when it is built, must lock the
    assignee's membership (`select_for_share`); part B must lock the person link against
    the membership check, because `Person.save` does not.
14. **The member export** ("Mijn post exporteren"): the member's own post only (part A:
    documents with an addressee linked to the member; part B changes the one function
    `member_document_ids`), as a ZIP of page images (JPEG) and a JSON file with a
    `LEESMIJ.txt`, until part E adds PDFs. Asked for in the web interface, built by a job
    on the `pipeline` queue (so the worker needs the documents volume), sealed in 4 MiB
    AES-GCM chunks with index and last-chunk flag in the associated data (no plaintext on
    disk, bounded memory, truncation is detected), valid for 24 hours, one per member and
    workspace (a new one replaces the old). A workspace switches it off or on
    (`allow_member_export`): on in Thuis, off in Zakelijk. The files live in
    `<storage root>/exports/`, which is kept out of the back-up (a restore has no use for
    them) and swept when no row names a file and it is older than a day. Every removal
    goes through the guarded functions in `documents/export_files.py`, which accept only
    `<root>/<uuid>/<uuid>.sealed` under the export directory, and that directory must lie
    inside the safe-path list (`apps.backup.safepaths`: `/data/documents` in production).
    So an install whose storage root lies elsewhere cannot export. The download does its
    checks, key and audit row first and returns a plain iterator, because a generator
    would run after the request transaction is gone.
15. **The audit log is read** by a workspace's admins (their workspace) and by server
    admins (the server log), through web-session endpoints with filters and a CSV (at most
    10,000 rows, times in Amsterdam time, a cell that would start a formula gets an
    apostrophe).
16. **Login throttle:** 30 failed sign-ins per 10 minutes per client address (successful
    ones are not counted: an office shares one address); the lockout of `django-axes`
    (5 failures, 15 minutes per address and account) stays.
17. **Contract.** `openapi_1c.json` joins `openapi_1b.json` as a fixture: the contract
    test guards what an app built against 1c expects. Fields added in this part have
    defaults. `AcceptInvitationIn.account` is a validated `str`, so part C adds `"sso"`
    without changing a released enum.
18. **Invitations bound to an address end with that account's membership.** When a
    member is removed or leaves, the workspace's open invitations bound to their address
    are revoked (`reason: member_removed`); when somebody joins, the workspace's other
    open invitations bound to their address are revoked (`reason: superseded`). Without
    this, an admin link made for someone before they joined another way could bring them
    back after a removal. Unbound links cannot be attributed; the Leden page lists them.
    Removal revokes invitations before it locks the server admins and the user, so the
    lock order is the same everywhere (written at the top of `workspaces/members.py`).
19. **`mask_bsns` also masks a run of nine digits inside an id in a log line** (a uuid4
    holds one now and then, and the elfproef passes about one in eleven). Accepted: a log
    line then shows `[BSN]` inside the id; finding the row takes the other half of the id
    or the time. Tests compare with `mask_bsns(id)`. Skipping candidates inside a
    canonical UUID would change what the privacy filter lets through and is not done.
20. **A restore brings back sessions, devices and links as they were at the back-up.**
    A phone revoked after the back-up works again. Not changed in code in this part; the
    restore guide (SELFHOST section 8) says to check the devices and sessions afterwards.
21. **The export files stay on the documents volume for now.** The security reading asked
    for a read-only documents mount in the worker with a separate `exports` volume. A
    volume mounted inside a read-only mount needs its mount point to exist in the parent
    volume before the container starts; Docker can only create it through a writable
    mount (the api), so the worker's start would depend on the api having started first,
    on existing installs and after every restore. Kept read-write until that is solved
    and checked in a real stack (for example: the one-shot migrate step makes the
    directory, the image creates it for new volumes).

## 2026-11-19: CI hardening

Tools that only run in CI; none of them reaches the image or the running stack. Every
third-party action is pinned to a full commit SHA, the version in a trailing comment;
Dependabot (github-actions) proposes the updates.

- **CodeQL** (`github/codeql-action` v4.39.3, `.github/workflows/codeql.yml`): static analysis of Python, TypeScript and the workflows with the `security-extended` suite, on pushes and pull requests to `main` and weekly. Results go to the repository's code scanning alerts. Dropped: Semgrep (a second rule set to maintain), GitHub's default setup (configured in the repository settings, so not reviewable in a pull request).
- **pip-audit** pinned to 2.10.1 (it ran unpinned through `uvx` before) and fed the hashed `uv export` with `--disable-pip`: the lock is audited as it is, without a resolver run. Dropped for now: `uv audit` (present in the pinned uv 0.13.8, gives the same result on this lock, but uv marks it experimental and "may change without warning"); revisit when it is stable.
- **hadolint** (`hadolint/hadolint-action` v3.5.0, which runs hadolint 2.15.1 pinned by digest): lints `deploy/Dockerfile` in the CI job `image`; warnings fail. DL3008 (pin apt versions) is ignored with the reason in the workflow: PGDG keeps only the newest build per major. Dropped: Trivy's Dockerfile checks alone (fewer rules, no ShellCheck of `RUN` lines).
- **Trivy** (`aquasecurity/trivy-action` v0.36.0, Trivy v0.76.2 pinned in the step): scans the built backend image for known vulnerabilities in Debian packages, the virtualenv and the Go binaries; fails on HIGH or CRITICAL that have a fix (`ignore-unfixed`). Action and binary are both pinned, so neither a moved tag nor a new release changes what runs. No Trivy config scan of `deploy/`: Trivy has no scanner for Compose files, so it would only repeat the Dockerfile checks hadolint makes. Dropped: Grype (comparable; Trivy also covers misconfiguration should Kubernetes files come), Docker Scout (needs a Docker account).
- **OWASP ZAP** (`zaproxy/action-baseline` v0.15.0 and `zaproxy/action-api-scan` v0.10.0, running ZAP 2.17.0 from `ghcr.io/zaproxy/zaproxy`, pinned by digest in `docker_name`; Dependabot does not update that pin, so it is raised by hand with the actions): the CI job `dast` starts the stack as `stack` does and runs a passive baseline scan of the web interface and an active scan of every operation in `docs/api/openapi.json`, both unauthenticated. `.zap/rules.tsv` marks as FAIL the headers and cookie flags the stack already guarantees and the injection rules; other alerts are reported, not failing (`-I`); every ignored rule has its reason in the file. Reports are uploaded as the artifacts `zap-baseline` and `zap-api`; the actions never open issues. Known limits: the spider sees little of a single-page app (no AJAX spider: it needs a browser in the container), and nothing behind a login is scanned yet. Dropped: Nuclei (template checks for known products, not this application's own endpoints).
- **Workflow hygiene.** Every action is pinned to a commit SHA with the version as a comment (`test_every_action_in_the_workflows_is_pinned_to_a_commit` guards it); the restore job's `actions/checkout@v5` became the same v7.0.1 as the others. Every workflow has `permissions: contents: read` at the top and widens it only per job, with a comment; every checkout has `persist-credentials: false` (no job pushes); every job has `timeout-minutes`; superseded runs are cancelled on pull requests only, so every commit on `main` gets a complete run. The `postgres:18` service image stays a tag, like in `docker-compose.yml`: the major is what `test_pg_client_major_matches_the_server` compares, and Dependabot's docker updates cover `deploy/` only.
- **zizmor** (1.32.0, run with `uvx` in the CI job `workflows`): lints the workflows for template injection, unpinned or impostor actions, excessive permissions and credential persistence, with its online audits on the job's read-only token. Clean at the default persona; the pedantic persona's remaining notes (job names, the `postgres:18` tag) are not acted on. Dropped: the zizmor action (uploads SARIF, which needs `security-events: write`), actionlint in CI for now (run locally before this change; a syntax error in a workflow already shows as a failed run).
- **pytest-cov** (7.1.0, with coverage 7.17.3; dev only) and **@vitest/coverage-v8** (5.1.0, same version as Vitest; dev only): CI reports backend and web coverage in the job log and keeps `coverage.xml` and the Cobertura file as artifacts. Nothing is enforced yet: the minimum goes in one place per side (`[tool.coverage.report] fail_under` in `backend/pyproject.toml`, `test.coverage.thresholds` in `web/vite.config.ts`), set from the first CI run. `make test` and `pnpm test` do not measure. Dropped: an external coverage service (Codecov and the like: an upload token and a third party for a number the log already shows).
- **First CI run of the scans (image).** restic **0.19.1** (2026-07-05, the newest release) replaces 0.18.1, again from the official image pinned by index digest (amd64 and arm64 published), in the Dockerfile and in CI's backend job. Changes in 0.19 that touch our calls: `forget` now exits 3 when it could not remove a snapshot (it exited 0), which the retention now reports as the warning `retention_incomplete` instead of `files_incomplete`; `backup` exits 3 for a missing source path (ours always exist); SIGINT exits 130; flags, JSON output and repository format are unchanged for what we use. 0.19.1 still carries 25 HIGH Go findings that are fixed upstream but not in a restic release (Go 1.25.10, x/net, x/text, grpc, …): they are accepted in `.trivyignore.yaml`, only for `usr/local/bin/restic`, each with its reason and `expired_at: 2027-01-31`, after which the job fails again. The Python findings (msgpack, urllib3, setuptools) and Trivy's "third-party SBOM" warning came from the base image's pip, which vendors those packages and ships their CycloneDX file (`pip/_vendor/bom.cdx.json`); nothing runs pip in the image, so the runtime stage uninstalls it (our own lock has none of the three). The runtime stage also installs pending Debian security updates in its apt layer (`apt-get upgrade`), because the base image lags behind them (zlib1g).
- **ZAP needs a host name with a dot.** Java sends no SNI for `localhost`, so Caddy had no certificate to offer and every ZAP request failed the TLS handshake. The `dast` job serves its stack as `brievie.test` (in `deploy/.env` and the runner's `/etc/hosts`, which Docker copies into a `--network=host` container); real installations are unchanged. The scans run with `continue-on-error`, their reports are moved aside and uploaded as `zap-reports` in every case, and a last step fails the job when either scan did not succeed.
- **What the first ZAP findings changed.** No CORS grant on the web app's files (`WHITENOISE_ALLOW_ALL_ORIGINS = False`; WhiteNoise sent `Access-Control-Allow-Origin: *` on `/assets/*` and the favicon by default, and nothing needs it). Every `/api/` answer that sets no caching of its own is `Cache-Control: no-store` (`ApiCacheControlMiddleware`; endpoints with their own header keep it). Unknown `/api/` paths answer the JSON 404 for every method (`api_path_not_found` is CSRF-exempt; a POST got Django's HTML CSRF page). Caddy adds `Permissions-Policy` (camera, microphone, geolocation, sensors, payment and USB off; the clipboard stays, the app copies pairing details), `Cross-Origin-Resource-Policy: same-origin` and `Cross-Origin-Embedder-Policy: require-corp`; `Cross-Origin-Opener-Policy` stays Django's (one place per header). The iOS app is no browser and is not affected by any of these. Ignored with reasons in `.zap/rules.tsv`: the readable `csrftoken` cookie (10010, 10112), and caching and content-type rules outside `/api` (10015, 10049, 100001 out of scope for the web app's paths).

## 2026-11-19: Te doen and Alle post

The owner could not tell "Inbox" from "Post", nor "Nieuw" from "Te controleren". Wording
and presentation only; API, `inbox_state` values, database, route paths and the iOS app
are unchanged.

- **Renamed.** "Inbox" is **Te doen** (post that waits for you), "Post" is **Alle post**
  (every letter, also the finished ones), in the navigation, page and browser titles, aria
  labels, the breadcrumb of a letter and the empty states. The paths stay `/inbox` and
  `/post` so bookmarks keep working; the route files say so in a comment.
- **One list, a status per letter.** Te doen no longer has tabs per system state. Each row
  shows what it needs as a badge with words: `review` reads **Controleren**, `unassigned`
  **Naam kiezen**, `new` **Bezig…** with "Je hoeft niets te doen". Order: what needs the
  person (newest first), letters being read last. The list is three calls (one per
  `inbox_state`), merged in the browser, so nothing outside the newest 200 per state drops.
- **`new` is not "the same as review".** The request assumed it was; in the code `new` is
  a letter still being read (status `processing`): nothing to do yet. So it is the Bezig…
  row, not Controleren. The owner's point stands: for the person there is now one thing
  to check, not two tabs that looked alike. A letter that could not be read (`failed`)
  stays under Controleren with its own hint.
- **Counts kept** in a line under the title ("Controleren: 1 · Naam kiezen: 2 · Bezig: 3");
  the navigation count is still review plus unassigned ("Te doen, 3 brieven").
- **Dropped:** keeping the three groups (as tabs or as headed sections): they named the
  system's states, not the person's work, and two of them read as the same thing. The
  `?tab=` of an old bookmark is ignored. In Alle post the status column uses the same
  words (Controleren, Bezig…).
- **A sentence under every page title** says what the page is for; where pages are easily
  confused (Te doen and Alle post, Personen and Leden) it says how they differ. A person
  is someone letters are addressed to, also without an account (a child); a member is
  someone who logs in.

## 2026-11-19: Scheduled security audits

New CVEs, a new Debian update in the image or a new scanner rule do not need a code change, so a check that only runs on a change misses them. The checks that can find such a problem now also run **Monday, Wednesday and Friday** on `main` (ci.yml 03:23 UTC, codeql.yml 04:41 UTC; not on the hour, when many schedules start and GitHub delays or drops some), and on demand (`workflow_dispatch`).

- **One workflow, no copy.** `schedule` is on `ci.yml` itself. On a schedule or manual run the jobs that only judge the code (`backend`, `web`, `stack`, `restore`) are skipped by an `if`; `secrets` (gitleaks, over the whole history then), `workflows` (zizmor, online), `image` (Trivy on a fresh build) and `dast` (both ZAP scans) run unchanged. The two dependency audits are steps inside `backend` and `web`, which are required checks on pull requests; they also exist as the small jobs `audit-python` and `audit-node`, which only the schedule runs (a text test keeps the commands equal). Dropped: a separate `security-audit.yml` with `workflow_call`, because it needs ci.yml cut into reusable pieces or the same steps twice, and the required check names would have to survive the cut.
- **Concurrency** groups contain the event name, so a scheduled run neither cancels nor queues behind a push run (a queued run replaces an older queued one in the same group).
- **Issue on failure.** The job `audit-failed` (ci.yml and codeql.yml, both calling `.github/workflows/audit-failed.yml`) runs only when the event is `schedule` and a job failed. It opens an issue "Scheduled security audit failed" with the label `security-audit` (created if missing), or comments on the one that is still open. It has `issues: write` and nothing else, uses the `gh` CLI with the job token, and writes the run URL and the failed job ids only: no log lines, as a log can hold a finding that must be judged before it is public. It does not run on pull requests or pushes. The call uses `./`, with a zizmor ignore: zizmor 1.32 prefers `$/`, which actionlint 1.7.12 rejects.
- **The Trivy entries** in `.trivyignore.yaml` expire on 2027-01-31. From then on, unless restic has a release without those findings, `image` fails on the first scheduled run, which opens the issue: the intended reminder.
- **A failing audit does not block merging** by itself; the issue is the signal. Only the existing required checks gate a pull request.
- Limit: GitHub switches off scheduled workflows of a public repository after 60 days without repository activity. after a long pause, enable the workflow again in the Actions tab.
- **Dropped:** daily runs (three a week finds an advisory within days; a daily ZAP scan and image build costs runner time for little extra); an external monitoring or vulnerability service (an account and a third party that sees our dependency list, for what the OSV data in pip-audit, Trivy's database and GitHub's alerts already give); Dependabot security alerts are on GitHub's side and not changed here.

## 2026-11-19: `./brievie start` applies a changed Caddy configuration, and says when no phone can pair

- **Why Caddy stayed stale.** Caddy reads `deploy/Caddyfile` once, when `caddy-publish-root.sh` (also bind-mounted) runs `caddy run` without `--watch`. Compose recreates a service only when its definition (image, command, environment, labels, mount *paths*) changed, never because the content of a bind-mounted file changed; so after `git pull` the `up -d --build --wait` of `./brievie start` recreated api and worker (new image) and left caddy running with the old file. On Linux a single-file bind mount even keeps showing the old file after git replaced it (git writes a new file and renames it; the mount holds the old inode), so a `caddy reload` inside the container would not help either.
- **The fix.** `./brievie` exports `BRIEVIE_CADDY_CONFIG`, the POSIX `cksum` of `Caddyfile` plus `caddy-publish-root.sh`, for every command; the caddy service has it as the label `brievie.caddy-config`. A changed file changes caddy's definition, and compose's own comparison recreates caddy, and only caddy; an unchanged one changes nothing; a first start creates it with the label; an installation started by an older wrapper gets caddy recreated once. `restore` stops caddy and starts it with the current files anyway, and its `up` carries the label too. No other service reads a bind-mounted file at run time: `postgres-init` is a directory mount whose scripts run only on a new volume, and `backup-role.psql` is read fresh on each `\i`.
- **Dropped:** `caddy run --watch` (does not see a replaced single-file mount, and does not cover `caddy-publish-root.sh`); `docker compose exec caddy caddy reload` (same inode problem); always `--force-recreate caddy` or `restart caddy` (a restart and dropped connections on every start); a hash recorded in a file under `deploy/` with an explicit restart (one more state file, a special case for the first start and for installations without a record, and a stale record after a failed start; compose already keeps the comparison). A strong hash is not needed: `cksum` is in POSIX and only detects a change. A hand `docker compose up -d` (without the label value) recreates caddy once; the docs say `./brievie start` everywhere.
- **Loopback host name.** `./brievie start` still starts with `BRIEVIE_HOSTNAME` set to `localhost`, `*.localhost`, `127.*` or `::1` (a developer may want it), but the address line then says it works only in a browser on this computer, followed by one sentence with the fix. `deploy/init-env.sh` takes `BRIEVIE_HOSTNAME` from the environment for a new `deploy/.env` (letters, digits, dots and dashes only), so `BRIEVIE_HOSTNAME=mijnmac.local ./brievie start` on a hand install ends where `install.sh` ends; an existing file is never changed.

## 2026-11-19: Versions and updates (U1)

Design: `docs/superpowers/specs/2026-11-17-beheer-backup-export-updates-design.md` section 7; plan: `docs/superpowers/plans/2026-11-17-host-u1-versies-bijwerken.md` (adapted to what B1 built; the rulings are below).

1. **One version, in `backend/pyproject.toml`.** `apps.common.version.server_version()` reads it at run time (the image copies `backend/` to `/app`, so no build argument is needed); the health endpoint, the back-up manifest, `deploy/release.json` and `CHANGELOG.md` follow it, and tests hold them equal. `web/package.json` stays `0.0.0` and is not a version. The API's `api_version` (1.0.0) is the contract's version and moves separately. The first release is **0.2.0**, because every installation from before versions reports 0.1.0 (pyproject said so from the start).
2. **The health endpoint checks the database.** `getHealth` gains `server_version` (optional, default `""`) and answers 503 `database_unavailable` with a Dutch sentence when `SELECT 1` as the app role fails; nothing of the error (host, message) reaches the body or the log line, only the exception's type is logged. The route is taken out of `ATOMIC_REQUESTS` (`apps.common.health.mark_non_atomic`), because the request transaction opens the connection before the view runs and would otherwise turn an unreachable database into Django's 500. The compose healthcheck already calls the endpoint, so it now fails with the database, and `up --wait` in `./brievie start` and `update` waits for a server that works.
3. **The release rules are pure Python and run in the installed image.** `apps.updates.release` reads one `deploy/release.json` per tag (`{"format": 1, "version", "min_upgrade_from", "postgres_major"}`) and decides: an installation from before versions (0.1.0) moves once by hand; the same version is "already"; a downgrade is refused (the way back is a restore); a missing or unreadable release, a `format` it does not know (how a later release says "this updater cannot do it") and another PostgreSQL major are refused; a jump past `min_upgrade_from` names the furthest version to go to first (`./brievie update X`). `manage.py update_plan` runs it in the installed image under `docker run --network none`, with the releases on stdin, so the shell decides nothing itself. `min_upgrade_from` of 0.2.0 is 0.2.0: nothing older has the updater.
4. **The emergency back-up is a safety back-up, named after the update.** With a destination it is B1's `backup --safety`, through the running keeper, with `--label pre-update-X.Y.Z` (`requested_by="update"`): tagged `kind:safety` and `label:pre-update-X.Y.Z` on both snapshots, no retention in its own run, kept 30 days, its run id printed. Without a destination it goes to a **fallback**: the Docker volume `<project>_update-backup`, which `./brievie` mounts at `/backup` of the one-off admin container for that one call (the same mechanism as a restore's source), prepared by B1's own `backup_init --update-fallback` (its marker carries `"purpose": "update-fallback"`) and written by `manage.py update_backup --label …` in that container (the keeper is never involved). The two never cross: an ordinary run refuses a fallback folder, and the fallback run refuses anything else, because it keeps only its newest run (`forget --keep-last 1` grouped by host and paths); the keeper never mounts the volume, so no retention but the update's own reaches it. A fallback run is recorded with `destination_kind="update"` and never counts in `status` (no last back-up, failure or interruption). No new mount point and no new destructive file code: the fixed list of design 4.10 is unchanged. `find_run` (and so `./brievie restore`) accepts the label: the newest complete run whose database snapshot carries it. Deviation from design 7.3 ("in the installation folder"): a named volume instead of a host folder, so there is no uid 10001 ownership problem on the host and nothing for the user to remove as root; like the folder, it lies on the same machine as the data, which the texts say.
5. **Recorded twice, for two readers.** A finished update is a row of the server-level table `ServerUpdate` (from, to, the back-up's run id, when; in every back-up, for `./brievie status` and later the web), and what runs is pinned by `BRIEVIE_VERSION` in `deploy/.env`, which chooses the image before the database is up. `update_preflight` (free space on the documents' disk against a 4 GiB build reserve, plus the data's size when the fallback is used), `check_health` (inside the api container, up to a minute, 200 with the target's `server_version`) and `update_status` are management commands, so the shell decides nothing itself.
6. **Compose for updates, and PostgreSQL unpublished.** Every service of the backend image takes `${BRIEVIE_IMAGE:-brievie/backend:${BRIEVIE_VERSION:-local}}`; `./brievie` always sets `BRIEVIE_IMAGE` itself (below). The worker gets the keeper's minute to stop (`stop_grace_period: 1m`; a job cut off by an update is picked up by the stalled-job retry). The volume `update-backup` is declared but mounted by no service. **PostgreSQL is no longer published** on the host by `deploy/docker-compose.yml`: the stack reaches it on its own network, and on a NAS port 5432 is usually taken by the NAS's own database (the open point of the install docs). `deploy/docker-compose.dev.yml` publishes `127.0.0.1:${POSTGRES_PORT:-5432}`; `make` always adds it (make test and make migrate need it), `./brievie` only when `deploy/.env` sets `BRIEVIE_PUBLISH_POSTGRES=true` (a developer whose test server runs through `./brievie` and who runs `make test` against it). That key is a machine setting: a restore never takes it from a back-up. Cost: such a developer adds one line once.
7. **`./brievie update` follows design 7.3 with B1's rules.** It runs from a copy of itself (`deploy/.update/brievie-running`; `BRIEVIE_ROOT` is honoured only for a copy that lies in that folder), checks the typed version before any git or docker call, refuses a dirty tree, own commits, a custom `BRIEVIE_IMAGE`, a stopped server, a failed `git fetch --tags` (no `--force`: a moved tag fails it) and anything `update_plan` refuses, builds the target from `git archive vX.Y.Z` while the server runs, and asks for the typed word `BIJWERKEN` (or `BRIEVIE_UPDATE_CONFIRM`; piped input never) before it stops api, worker and caddy. The keeper makes the safety back-up and stops after it. Every exit before the switch starts the old version again (the exit trap). After `git checkout` the **new** checkout's wrapper finishes (`_update-finish`, the lock handed over with `BRIEVIE_LOCK_HANDOVER`), reading only the marker `deploy/.update/in-progress` (keys `from`, `to`, `previous_ref`, `run_id`, `kind`, `step`, `time`, which later wrappers keep reading). A failure there, or an interruption, prints the way back and leaves Brievie stopped (exit 3). Whether the switch happened is decided by HEAD against `previous_ref` and by the step together (item 15), so a kill between the switch and the marker write is still seen.
8. **The image name carries the version and the installation.** `brievie/backend:<version>` for the project `brievie`, `brievie/backend:<version>-<project>` for any other (two checkouts never build over each other's image). `start` and `restore` run the checkout's version and pin it in `deploy/.env`; the other commands take the pin. A restore never takes `BRIEVIE_VERSION` from a back-up: the code that runs is the checkout's, and the way back is checkout plus restore.
9. **The way back is a restore by name.** `./brievie restore pre-update-X.Y.Z` takes the back-up of an update; without a destination it opens the volume `update-backup` by itself (B1's `volume:` source). While an update is unfinished a restore runs only for that update's own back-up and only once HEAD is back at `previous_ref`, and it removes the marker when it succeeds (design 7.3: no restore during an update, except the way back out of it). `./brievie status` adds `update_status` after `backup_status`.
10. **A release workflow already (`.github/workflows/release.yml`), though publishing images is U2's.** On a tag `vX.Y.Z` it checks that tag, pyproject, `release.json` and the newest `CHANGELOG.md` entry agree (`.github/scripts/release-check.py`), builds amd64 and arm64, scans (Trivy, as the `image` job), pushes `ghcr.io/<owner>/brievie-backend:X.Y.Z` with a provenance attestation, and makes the GitHub release from the CHANGELOG section. A run by hand checks, builds and scans and publishes nothing. No pull_request trigger; no rights by default, each job its own (`packages: write` and `id-token: write` only in `publish`, `contents: write` only in `release`); no persisted credentials; actions pinned by commit. Installations keep building locally until compose moves to these images. The package is private until the owner makes it public (the owner's step, as is the first tag). Cost: images nobody pulls until U2.
11. **The update's real proof runs in CI (job `update`), not on the development machine**, which runs the owner's test server: it makes throwaway tags in a clone on the runner (`.github/scripts/update-releases.py`: the commit under test, a next patch, and one with a failing migration), installs the first, seeds it, updates, fails an update and types the printed way back; pages, the paired device and the certificate authority must survive. Nothing is pushed.
12. **Left open (NAS):** the installer asking for the HTTP/HTTPS ports (they are part of the pairing QR code and needed on almost every NAS; an update keeps them), and documents outside Docker volumes. Both belong to the installer and storage designs, not to updating.
13. **Local builds carry no default provenance attestation.** BuildKit adds a `mode=min` provenance attestation by default, and min mode records the build's start and finish time and an invocation id (docs.docker.com/build/metadata/attestations/slsa-provenance). With Docker's containerd image store that attestation is part of the image index, so every build of unchanged code got a new image id and every `./brievie start` recreated migrate, api, worker and keeper (found on the owner's Mac). The wrapper exports `BUILDX_NO_DEFAULT_ATTESTATIONS=1` (Buildx 0.10.4 and later; an older one ignores it) unless the caller set it: an environment variable rather than `build.provenance: false` in compose, because an older Compose on a NAS refuses a key it does not know. The release workflow keeps its attestation (`actions/attest-build-provenance`). With published images (U2) a normal installation no longer builds at all and the problem is gone for it. Not proven here: that the image id is then stable on the owner's Docker Desktop (two `./brievie start` in a row, `docker image inspect` before and after) and that every Compose version passes the variable to its builder.
14. **A Docker step that hangs gets one honest line, and is never killed.** A build or start that still runs after `BRIEVIE_SLOW_SECONDS` (600) prints once that Docker is slow, that this is usually Docker itself (a credential helper or registry), and what Ctrl-C means at that moment; the wrapper never stops Docker by itself, because killing a build half-way helps nobody and a timeout would have to guess. In `./brievie update` the build is before the stop (design 7.3), so a hang there leaves the old version running. The watchdog's `sleep` holds none of the wrapper's output, so a caller reading it is not kept waiting. Each wait is a loop of one-second sleeps, so stopping the watchdog leaves at most one second of sleep behind (a single long `sleep` outlived the wrapper; review of U1).
15. **After the review of U1: an update that may have touched the data is never "nothing changed".** Withdrawn: the earlier rule that read an unfinished update as untouched whenever HEAD was back at `previous_ref` (a user who typed only `git checkout` after a failed migration then started the old code on the new schema, and the way back was gone). Now an unfinished update counts as untouched only with `step` `stopped` or `backed-up`, HEAD at `previous_ref` AND no tracked file changed; the finish writes `step=migrating` before `migrate`. In every other state `start`, `status`, `backup`, `sweep --delete` and `recovery-sheet` print the way back (or, when untouched, "typ ./brievie start") and refuse, until the update is finished (`./brievie update`, only with HEAD exactly at the target and a clean tree) or the way back is taken. The exit trap restarts the old version only under the same rule, so Ctrl-C right after the checkout keeps the update resumable; that path, like every failure or interruption after the switch, exits 3. A restore of the update's own back-up insists on `--replace --skip-safety-backup` (a new safety back-up cannot be made while the update is unfinished) and says so first. Half-switched files make the way back say `git checkout --force` (the update refuses a tree with changes at step 1, so nothing of the user's is in them). Cost if wrong: a user whose update stopped in a harmless spot after the switch must finish it or take the way back.
16. **A second guard: `./brievie start` refuses code older than its database.** It starts PostgreSQL when it does not run (a first start then gets its empty database, with nothing to compare), reads `django_migrations` over the socket (read-only; a missing table is a new database), refuses with a sentence when the database cannot be read, and refuses when the database has applied a migration of one of Brievie's own apps (`backend/apps/<app>/migrations`) that the checkout does not have. Cheap, and it stops exactly C1's outcome whatever the marker says. Not in the image's own start (`migrate` would need to compare too); the wrapper is how installations start. Cost: a developer who switches back to a branch without a migration they applied sees the refusal, which is right.
17. **A stale lock is recognised, conservatively.** The lock folder holds the process id (the update's `exec` hand-overs keep it) and, on Linux only, the boot id of `/proc/sys/kernel/random/boot_id` (fixed for one boot). macOS's `kern.boottime` is NOT used: XNU recomputes it when the clock is stepped (after sleep), which would hand a running update's lock to a second `./brievie` (re-review of U1). It is taken over only when the Linux boot differs, or the process surely does not exist: `/proc/<pid>` absent where `/proc` exists (Linux, busybox), else `ps -p` (macOS) only when `ps -p` works for the wrapper itself. Unsure counts as alive. A lock without a process id (older wrappers) keeps the twelve-hour rule and the typed `JA`. A takeover is atomic: the lock is renamed to a name of this process and judged again there; holding another process id by then (another `./brievie` took it over first), it is put back. Cost if wrong: a reused process id of another program (on a Mac after a restart) keeps the lock for twelve hours or until `JA`.
18. **A dropped terminal does not abandon an update.** On SIGHUP while an update or its finish runs, the wrapper moves its output to `deploy/.brievie-last.log` and goes on; the step that the hang-up cut off fails and the normal path follows (old version restarted, or the way back), into the log. The exit trap runs with `set +e` and writes that cannot fail. Other commands still stop on SIGHUP.
19. **The finish opens Caddy last.** `up -d --wait api worker keeper`, then `check_health`, then `up -d --wait caddy`: nobody writes to the new version before it answers well, so the way back (`--skip-safety-backup`) loses nothing.
20. **`./brievie update` without a version lets the installed image choose.** `update_plan` without `--target` takes the newest release whose `release.json` it can read and says on stderr which newer one it skipped; it prints `target=X.Y.Z` before `allowed`/`already`.
21. **The release workflow publishes what it scanned.** `publish` pushes both architectures by digest only, scans the amd64 and the arm64 image of that digest from the registry (Trivy, `TRIVY_PLATFORM`), and only then tags the digest `X.Y.Z` and attests it. Not proven before the first tag: that Trivy reads the private package with the login of `docker/login-action`. `.github/scripts/release-check.py` applies the rules of `apps.updates.release.parse_release` (a test holds them equal; it refuses the `v` prefix, which is stricter).

## 2026-11-19: Example rules on Automatisering ("Begin met een voorbeeld")

- **What.** Seven example rules (`apps/rules/templates.py`), listed by `GET /workspaces/{id}/rules/templates` (`listRuleTemplates`, member and up, as `listRules`). The page shows them under the rules and instead of an empty list, with "Zo werkt een regel" (wanneer, als, dan). "Gebruik dit voorbeeld" opens the existing editor at `automatisering/nieuw?voorbeeld=<key>`, filled in; nothing is stored until Opslaan. The existing "Test op bestaande post" already takes an unsaved rule, so trying an example before saving needs no new endpoint; its result now says "Er is nog niets opgeslagen."
- **Only what the engine does today.** No new condition, action or trigger. Every example proposes (mode `propose`, as a new rule) and its sentence starts with "Stelt … voor".
- **Blanks, never a silent choice.** The words or name to type are empty with an `example` placeholder and a hint, marked "Vul in"; the label or person is always picked by the user (marked "Kies"; the selects now start with "Kies een label/persoon" instead of looking as if the first entry was chosen). An example may suggest a start-set label (`label_key`, matched on `system_key`, then on name); when the workspace lacks it, the editor offers a button "Label … maken" (the existing `createLabel`), never creates it unasked.
- **Server list, not frontend data.** The iOS app can show the same gallery without a copy, the texts go through `gettext` like every other server sentence, and `tests/test_rules_templates.py` fills every blank and checks the result against `RuleIn`, `definition.validate`, `to_spec` and the engine (a letter it must match, one it must not), without a database. Dropped: the list as TypeScript data in `web/` (no check against the real definition, and a second copy for iOS).
- **Honest texts.** Word examples say the word may appear anywhere in the letter (a letter that only mentions it gets the proposal) and point to the sender example; sender and type examples say these are known once the AI read the letter or the user confirmed it (without AI, a sender rule acts on confirming).

## 2026-11-19: AI autopilot

Design: `docs/superpowers/specs/2026-11-19-ai-autopilot-design.md`. Opt-in per workspace; the product principle ("AI proposes, the user confirms") stays the default.

1. **The autopilot rulings.**
   - The server-wide prohibition is an environment variable (`BRIEVIE_AI_AUTOPILOT_ALLOWED`, default true), not a table: there is no server settings model, and a switch that forbids must not be changeable by the people it restrains. A per-workspace prohibition waits for the enterprise layer.
   - Name matches are applied too, and recorded as `name_match`, not `ai`: it is not the AI that found them. The badge still reads "Automatisch door AI".
   - A rule's proposal is never applied automatically, also not when the AI proposed the same change. A rule in mode `auto` is unchanged.
   - A task without a deadline is confirmed only when the letter names no term (`mentions_term`, which blocks rather than guesses). Working out a relative term ("binnen 14 dagen na dagtekening") is a follow-up. A needless task cannot be told apart: a stated limit.
   - When processing fails, or the organise step (`organise_processed`) fails, nothing is applied and nothing is confirmed, whatever the switches. When applying fails, nothing is confirmed.
   - No `AIConsentEvent` row: that table records where text goes. The switches are settings and go to the audit log (`workspace_setting_changed`, `ai_auto_organise` / `ai_auto_confirm`).
   - The web has no review screen: "Zelf controleren" reopens the document and says the check is done in the app.
   - The summary is stored with `summary_by_ai`, as a person's confirm of the AI's text does.
   - Trial with a real key (12 made-up letters, three Claude models): the weak spot was the keyword rule, not the AI (a letter with an embedded instruction got a rule's label at 1.000), hence the rule above. Generated fields always carry 0.500 and are no signal. `AUTO_APPLY_FROM` went from 0.700 to 0.800.
   - Only an addressee proposal that is still open stops the automatic confirm (with `auto_organise` on), not a label proposal. The trial through real containers showed why: a preset rule in mode "propose" matches a word in most letters, so a rule's label proposal would have kept nearly every letter from being confirmed. A label that is still a question says nothing about the sender, the date or the tasks; it stays a proposal on the confirmed letter.
   - Guard rails the build added to the spec: a letter date after today, and no confirmation when the organise step failed. A reopened document does not get its tasks twice.
   - Guard rails after the final reviews: a letter that names a term is confirmed only with actions that all have a deadline (zero actions included); an invoice due date needs a pay action, and every pay action a deadline; `mentions_term` also reads bracketed counts ("veertien (14) dagen"), terms in weeks "na" a date, "vervaldag", short dates, "tot <datum>" and a few English forms, still linear-time; an AI pick of a person is applied only when its quote names that person. A confirm refused by its own validation is logged at INFO, not ERROR.
   - A person's confirm with `sets_organisation` clears `automatic` on the links that stay, and the autopilot's change is then no longer undoable (`can_undo` and `undo_event` both check the link's flag). A hand edit leaves the flag. A person's review of an AI-confirmed letter is never a repeat (409 `document_not_reviewable`).
   - The switches reset when AI goes off, the mode changes (external <-> local), or consent is granted for another address. Another model in the same mode at the same address keeps them.
   - `TaskItemOut.created_by_ai` (additive, default false), so a task list can show the mark.
   - Follow-ups: work out relative terms with `needs_attention`; a per-workspace prohibition for the enterprise layer; ~~`updateAiSettings` still accepts a device token~~ (closed 2026-11-20, security review: session only). Before private post (phase 1d part B) an automatic addressee link must not by itself grant reading rights. iOS must show the mark "Automatisch door AI" (documents and tasks) before the autopilot is used in households that only use the app.

## 2026-11-20: Who sees which letter: measured before it is built (merge point M0)

Plan: `docs/superpowers/plans/2026-11-20-host-wie-ziet-wat.md`, Tasks 1–2; spec S 4.9.5.

- **P1, the threshold.** Pass = every list's median <= 250 ms, per state (E on; E off with D on; E off with D off), actor (plain member, department member, flagged sorter, admin) and list (`list_documents` plain, unassigned and on the actor's own person, `inbox_counts`, `list_persons`, `list_labels`, `list_tasks(status="all")`), on a GitHub-hosted `ubuntu-latest` runner with the `postgres:18` service at default settings, 5 timed runs after one first run; the preview's letter count <= 2 s. A list that fails with the plain policy but passes with the member filter passes (the filter ships in Task 11). A fail stops the plan before Task 3, with the options of Task 2 for the owner.
- **P2, a scratch schema.** The measurement adds the columns, tables, functions and policies of S 4.9 to the migrated test database itself (`tests/perf/scratch_schema.py`), from the SQL that ships (`apps.workspaces.visibility_sql`, the real `enable_rls`), so a failed measurement costs no product migration. Task 11 measures again on the real schema and removes it.
- **Its own workflow, not a job of ci.yml** (`.github/workflows/visibility-perf.yml`, job `visibility-perf`). It runs when what it measures changes (the visibility SQL, `rls.py`, `context.py`, the list services, any migration, `tests/perf`, `uv.lock`, the workflow), on demand, and on Sunday 05:37 UTC (a new `postgres:18` image can change a plan). Not a required check: a path-filtered required check would block every pull request that skips it. Dropped: the plan's "every pull request" (minutes of runner time for changes that cannot move the numbers) and a per-job path filter inside ci.yml (needs a third-party action). A scheduled failure opens no issue (`audit-failed` is about security audits); GitHub mails the failure.
- **How it measures.** One committed world per run (`tests/perf/data.py`: a fixed seed, every `NOT NULL` column explicit, about 1,500 characters of OCR text a letter, `VACUUM (ANALYZE)` afterwards); each measurement inside the test's transaction as the non-superuser owner role with `brievie.workspace_id` and `brievie.actor` set transaction-local, as a request has them; the real service functions, timed in Python (ORM included). "First" is the first run of that query in the measurement: a new plan, but caches are not emptied, so it is no cold-cache number. p95 is by nearest rank over the five runs (the slowest). Every query of one extra run is explained with `ANALYZE, BUFFERS` (artifact), and the report names the node with the most time of its own (JIT counts as a node).
- **The member filter as measured** is not the plan's "addressee arm only", which would hide the scanner's and the reader rows' letters, but `addressee_ids && my_post_ids OR scanned_by_id = actor OR id = ANY(the actor's reader rows)`: a superset of what the policy shows such a member (checked: the result must equal the plain run), with every arm on an index so PostgreSQL can combine them in a BitmapOr. Task 11's `Q(visibility="restricted")` arm has no index and would turn the filter into a scan again.
- **The reader-row arm last.** `document_condition` writes S 4.9.3's arms with the reader-row `EXISTS` at the end: PostgreSQL evaluates an OR's arms in the order written, and that arm is the only one that probes a table per row. Same truth table (Task 7's tests check it); the probe then runs only for letters the cheap arms leave out. Matters most for `inbox_counts` and the preview, which read every letter.
- **Measured, first run** (run 44655424309, PR #26, `ubuntu-latest` 4 CPUs, PostgreSQL 18.7 at defaults, so `jit = on`): FAIL, 56 of 84 lists over 250 ms, preview 97.6 s. Planning took <= 2.7 ms everywhere; the time is JIT compilation and execution:

  | list | median | JIT | execution without JIT | what dominates |
  |---|---:|---:|---:|---|
  | `inbox_counts` | 1,415–1,959 ms | 1,290–1,365 ms | 110 ms (own post) to 620 ms (E, sorter) | three correlated subqueries per letter; "has an addressee" checks the letter's policy once more per link |
  | `list_persons` | 828–879 ms | 300–310 ms | ~550 ms | 105,000 addressee links, each checked against its letter's policy (`document_count`) |
  | `list_labels` | 509–638 ms | 350–375 ms | ~180 ms | a hashed subplan over every visible letter |
  | `list_tasks(all)` | 462–498 ms | 380–400 ms | ~70–100 ms | the per-task letter check |
  | `list_documents` | 216–258 ms | none (cost under the threshold) | same | `has_addressee` annotated on every list: a hashed subplan over all addressee links and all visible letters (~190 ms) for 200 rows |
  | `list_documents` unassigned, E off, not sorting | 286–331 ms | none | same | walking all letters newest first, each through the policy and an anti-join, to find 22–24 |
  | `list_documents` on own person | 37–39 ms | none | same | |

  The member filter did not use the GIN index and made the unassigned list slower (419–469 ms). The data build rewrote every letter once (`UPDATE … addressee_ids`), which left `documents_document` twice its size (49,000 pages); a server that grows letter by letter does not look like that. Fixed: `addressee_ids` is written with the letter.
- **JIT off, for the app's connections** (`brievie/settings.py`: libpq `options = -c jit=off` on the default connection, so requests, the worker, `migrate` and the tests). PostgreSQL compiles a query once its estimated cost passes `jit_above_cost` (100,000); the policies are inlined into every list query and their estimates pass that by far (cost 0.5–11 million for queries that run in 70–600 ms), so every list paid 0.3–1.3 s of compilation for nothing. JIT pays for long analytical queries, which Brievie does not run on a request; OLTP installations commonly switch it off (not verified here against a named project). Set at connection start: no extra round trip, no server or database setting to migrate on existing installations, nothing for the back-up role (`pg_dump` is not affected either way). Dropped: `ALTER DATABASE … SET jit` (the owner role does not own the database on every installation, and only new sessions see it), `ALTER ROLE` (needs a superuser), a per-request `SET` (a round trip), raising `jit_above_cost` (the estimates grow with the data; a threshold is a guess). The measurement checks it runs with `jit = off` and reports every list with `jit = on` beside it.
- **Proposed forms, measured beside the services** (`tests/perf/candidates.py`, same result required on every run): lists annotate `has_addressee`/`has_automatic` only where a filter reads them; "unassigned" is `status = 'done' AND addressee_ids = '{}'` on a partial index `documents_document_unassigned (workspace_id, created_at DESC)`; a person filter is `addressee_ids @> ARRAY[person]` (GIN); `inbox_counts` is one pass with `addressee_ids = '{}'` and the automatic links as two uncorrelated `IN` subqueries; `list_persons` counts with one grouped query over the visible letters (`unnest(addressee_ids)`), not per link. If they pass, Task 11 makes them the services and Task 7 adds the index.
- **The preview, grouped** (`visibility_sql.viewer_groups_sql`): without reader rows, a non-private letter's viewers depend only on its `addressee_ids` and, while not done, its scanner. The condition runs once per group of alike letters and member, not per letter and member; letters with an unexpired reader row are asked one by one (`viewer_pairs_sql`), private letters never change with the switches. The measurement checks the grouped count equals the per-letter count of the first design.
- **Ruling: proceed after measurement run 44747946602** (commit db14375, JIT off): every list passes with the candidate forms except `inbox_counts` (candidate 259–291 ms on a slow runner; 213 ms the run before). The plan adopts the `MATERIALIZED` member CTEs of `visibility_sql` (Tasks 8, 37), the grouped `list_labels` count (Task 11) and the forms of `tests/perf/candidates.py` wherever a list is built; `inbox_counts` takes the candidate form plus one cheap improvement in its task. The 250 ms threshold stays.

## 2026-11-20: Fixes from the exploratory API test of 19 November

- **A BSN is never a match value.** Text is masked before it is stored, so a BSN typed into a name, a name variant or a rule's text or sender value became `[BSN]` and found every letter with any BSN. `apps.common.text` now treats the mask token as opaque (a phrase holding it matches nothing; the token in a letter is no text), which neutralises existing rows without a migration, and persons and rules refuse a BSN or the token with a Dutch sentence. A masked value already stored on the same row may stay, so an old person or rule stays editable and can be switched off. A text or sender condition whose value holds the token never holds, for every operator (also "does not contain", which would otherwise hold for every letter). Dropped: a data migration removing masked variants or conditions (removing a condition widens a rule; the rules above already make such a condition false and such a name match nothing).
- **One name, one person.** Names and variants are stored NFC with single spaces; a name or variant another active person has is refused (409 `name_variant_taken`, naming that person), compared as name matching compares (case, accents, whitespace). Existing duplicates stay; matching caps a person whose match overlaps an equal or longer match of another person at 0.700, below preselection. Not locked against two requests at once: a race gives a duplicate, which matching then treats as ambiguous.
- **Omitted is not false.** `updatePerson` keeps `archived` and `name_variants` when the client leaves them out (`model_fields_set`); the fields stay plain with their defaults, because the released contract fixes their definition and nullable inputs need a default anyway.
- **Rules and archived persons.** A rule keeps naming an archived person it already named; newly naming one is refused ("gearchiveerd"); `testRule` accepts any person. The web learns a person is archived from `listPersons`, as it already did.
- **Password rules in Dutch.** Django 6 changed the minimum length msgid and its Dutch catalogue lags, so `AUTH_PASSWORD_VALIDATORS` now names subclasses in `apps.accounts.passwords` with their own `gettext` sentences. Dropped: a project `.po` overriding Django's catalogue (needs compiled messages in the image, and breaks again at the next msgid change).
- **Page images.** The intake walks a JPEG's segments to the first scan header (frame size 1-20,000 px a side, at most 100 Mpx) with no image library; the picture data is not decoded. Dropped: Pillow (a new dependency and a decoder of untrusted input on the server, for a check the AI service and the app already do on the pixels).
- **Messages.** A schema refusal (422) now gets a sentence from Pydantic's error type and limit (`apps.common.input_errors`), so a limit's own number reaches the user; a CSRF failure is 403 `csrf_failed`; a letter date lies between 1900-01-01 and a year after today; deleting a label writes `label_removed` (with its name) in each letter's history.
- **The autopilot follows the same rules.** An AI pick of a person is applied on its own only when its quote names that person with a name that holds no (masked) BSN, and names no other active person with an equal or longer name; name matches are applied only from the preselection threshold, which an ambiguous match no longer reaches.
- **Proposals in the list.** `DocumentSummaryOut` gains `proposed_sender`, `proposed_document_type` and `proposed_letter_date` (additive, with defaults): the strongest pipeline proposal (confidence above 0 for sender and date) while a letter is not confirmed and the field is empty, read with one prefetch per list. The web shows them italic with "(voorstel)"; the iOS app picks them up separately.
- **Names.** A label or person name needs one character that draws something and may not hold an embedding, override or isolate (U+202A-202E, U+2066-2069); the plain marks LRM, RLM and ALM stay allowed, because Hebrew and Arabic keyboards type them in real names. A member's own person gets their name as a variant only when no other active person has it; it is never linked to an existing person by name.
- **Open items, left for later.**
  - `apps.common.privacy.find_bsns` does not see `/` or `_` as separators between the groups of a BSN (`123/456/789`).
  - Folder names and device names are stored without BSN masking.
  - 409 `name_variant_taken` names the other person; once persons can be private (1d-B), the sentence must become generic for someone who may not see that person.
  - Finding #14: a dismissed proposal can still be accepted by its id (a stale screen on another device undoes a "no"); decide whether accept of a dismissed row is 409.
  - Finding #15: archiving a person deletes their open proposals for good; restoring does not bring them back (intended in the code, not yet a product decision).
  - Finding #18: the audit log keeps a deleted person's name but only the id of a deleted label or rule; choose one line.
  - Also skipped from the same test: #12 (task events do not say which task: needs a field and a migration), #13 (rule counts after undo or "no"), #16 (an admin may archive their own linked person), #17 (folders differing only in case: needs a constraint and existing duplicates), #22 (a malformed id in a path is 422, not 404).

## 2026-11-20: Who sees which letter: the actor (plan Tasks 3–4)

- **A second transaction-local setting, `brievie.actor`**, beside `brievie.workspace_id`: a user's id, `system` or empty (`NOBODY`). `public.brievie_actor_id()` gives a user actor's id (NULL for anything else, never an error) and `public.brievie_is_system()` the exact word `system`; both `LANGUAGE sql STABLE`, `search_path` pinned, owned by the schema owner, executable by `brievie_app` (migration `workspaces/0005`). Every `workspace_context` names its actor; a request runs as its user (session or device token), never as the system. Audit rows, AI settings and invitation lookups run as `NOBODY`: their tables have no letter policy, and an empty actor sees no letter (R6).
- **Running as the system is a closed list of files** (`tests/test_actor_callers.py`: `system_actor(` or `actor=SYSTEM`), and only `apps/workspaces/context.py` writes the setting. A new caller is added there in the same commit.
- **A manual rule run runs as the member who started it** (R7): the job carries `user_id`, checks the membership first ("Rule run skipped: the member left") and queues its next batch with the same id. A job queued before this change has no `user_id` and does nothing ("Rule run skipped: started before the update", P5): failing closed costs one click of the admin, guessing an actor could show letters to nobody's account. `run_rule` refuses an actor without a user.
- **One lock order (plan Task 5, PB T6).** A request that organises a letter first holds its own membership row `FOR SHARE` (`apps.workspaces.locks.lock_own_membership`; 404 `workspace_not_found` when the member was removed meanwhile), then the document. A membership change takes the workspace's memberships `FOR UPDATE`, so it waits for the request or the request finds the member gone; the two never hold each other's rows. Every row lock on a `Document` or `Person` is `FOR NO KEY UPDATE` (a source scan checks it): a foreign-key check on the letter (`FOR KEY SHARE`, every event, label link or task insert) then never waits for it. `Workspace` locks in `create_rule` and `apply_presets` stay `FOR UPDATE`: those transactions take no other row lock afterwards, so they cannot be part of a cycle. The organising services read the member from the actor (`current_actor()`); the system has no membership to hold.
- **Switches as fields first (plan Task 6).** `Workspace.everyone_sees_all`, `departments_enabled`, `admins_read_all`, `care_is_private`, `emergency_access`, `Membership.sorts_post`, `Person.kind` with `DepartmentMember` (forced RLS) and `Task.created_by`, none with `db_default` (Django would insert with `RETURNING`, which reads the new row through the SELECT policies). Every existing workspace migrates to E on, D off, A on whatever its kind (S 4.16), and until the presets of Task 40 every new workspace starts that way too, also an Organisatie (P8): no installation can reach "own post" before every path that could show a hidden letter is closed. The kind's labels become "Organisatie" and the viewer role "Alleen lezen" (R29).
- **Two policy layers (plan Task 7, S 4.9.3).** The permissive `workspace_isolation` stays the only permissive policy on every workspace table; on top, restrictive `document_visibility`, `document_visibility_update` and `document_visibility_delete` narrow reads, updates and deletes alike (an `UPDATE`/`DELETE` without a `WHERE` that reads a column applies no SELECT policy, so they need their own). The letter's condition is `visibility_sql.POLICY_CONDITION`, rendered from the one template that `viewers()` will use too. No `WITH CHECK`: PostgreSQL then checks an update's new row with the same condition, so a write that would hide the letter from the actor fails with 42501 rather than passing ("whoever makes it private stays a reader"). Every table with a letter key follows its letter (`EXISTS` on the document, the coverage test compares the database's foreign keys with Django's); a task without a letter is seen by the system, whoever sees all post, its assignee and its maker (R39). `documents_documentreader` has only `reader_scope` (a user sees their own rows, only the system writes them) and no child policy: the document policy reads it, a policy back would be a circle.
- **An empty actor and a non-member see no letter (R6)**, also with "Iedereen ziet alle post" on; PB's "an empty actor sees public letters" is gone. Reader rows stay for private letters, shares and emergency access only (R33); `addressee_ids` (GIN) makes non-private sight an array overlap and is filled from the links by the migration. The partial index `documents_document_unassigned` (letters `done` without an addressee, newest first) ships here, as measured.
- **`WorkspaceModel.save` reads a hidden related letter's workspace as the system**: a user who may write a history row on a letter they cannot see (the write order of Task 9) would otherwise be refused as "another workspace". The workspace policy still binds, so a row of another workspace stays invisible and is refused as before.
- **The scratch schema goes with Task 7, not Task 11**: once the migrations carry the functions, policies and indexes, installing them a second time fails; the measurement now runs on the migrated schema. Task 11 keeps the member filter and the new table.
- **One writer for `addressee_ids` and reader rows (plan Task 8, P3).** `apps.documents.visibility.addressee_scope(document)` wraps every outermost service that may change a letter's addressees (set, accept, undo, confirm, the pipeline's organising with the autopilot, a rule run, the autopilot on its own); `organise` raises `RuntimeError` for an addressee change outside a scope, so no caller can forget the sync. The outermost scope syncs once at its end, as the system, in the same transaction; a scope that raises syncs nothing. Reader rows: `share` for the shares of every letter (R44), `addressee` and `department` for private letters only, current members only, emergency rows untouched. `viewers()` runs the policy's own condition per member (`viewer_pairs_sql`, its `MATERIALIZED` member CTEs as measured), so there is no second copy of the rule. The nightly `verify_visibility` (03:40) repairs every `addressee_ids` that differs from the links and every reader set, and logs two counts only.
- **`django.contrib.postgres` in `INSTALLED_APPS`**: `ArrayField` and `GinIndex` need it (system check `postgres.E005`). Part of Django, not a new dependency.
- **The write order (plan Task 9, S 4.9.3).** A confirm first looks the letter up as the system after the membership lock, so a repeat by someone who handed it over answers the same (200, `can_read: false`); any other review of a letter the caller cannot see is 404 like an unknown id. Then, as the caller: every field but the status, labels and addressees, the dismissals, the history, the tasks (`created_by`) and the rules at confirm; the scope's end syncs `addressee_ids` and the readers as the system; last, as the system, `status`, `confirmed_by` and `confirmed_at`. Every organising endpoint (confirm, addressees, labels, accept, dismiss, undo, reopen) answers through `document_out_for`: `can_read` is computed last, as the caller, and when false the answer holds the id, `client_id`, `created_at` and status only. `DocumentOut.can_read` is additive (default true). The web does not act on `can_read: false` yet (Task 44 adds the hand-over sentence before the tap); with every workspace still on "Iedereen ziet alle post" no answer has it.
- **A repeat confirm is recognised for its confirmer only** (security review of Task 9): the lookup as the system answers the repeat only when `confirmed_by` is the caller, so it is no oracle for a letter someone else confirmed; anyone else gets the caller-scoped lookup (404 when hidden). The `can_read: false` answer keeps `client_id` and `created_at`: only the member who just handed the letter over gets it, and both came from that member's own upload or screen.
- **Writes the user cannot see (plan Task 10, PB T9).** Deleting a person, label or rule and archiving a person authorise as the caller, then reach links on letters the caller cannot see as the system (a hidden link would otherwise fail the commit at its deferred foreign key). `deletePerson` is admin-only, so "in use" because of hidden post is told only to admins; the web shows the delete button to admins only. A repeated upload is looked up as the system and returned only when the uploader can still read it; otherwise the same 409 `document_conflict` as a different upload, with no data.
- **The member filter and the list forms (plan Task 11, S 4.9.5).** `visibility.member_filter()` (no workspace argument: the policy's helpers read the context) asks the policy's own functions in one query and, for a user who neither sees all nor sorts post, adds `addressee_ids && my_post_ids OR scanned_by = me OR id IN (my reader rows)`: the form measured in Tasks 1–2, a superset of what the policy shows (the scanner arm without the status, private letters through the reader arm), every arm on an index. The lists use the measured forms: "unassigned" as `addressee_ids = '{}'` (partial index), a person as `addressee_ids @>`, "automatic" as two uncorrelated `IN` subqueries, `inbox_counts` in one pass, `list_persons` and `list_labels` as one grouped count each. Partial indexes `documents_addressee_automatic` and `documents_label_automatic` (`WHERE automatic`) keep the "automatic" subqueries to the autopilot's links.
## 2026-11-20: The autopilot applies a rule's label when it rests on settled facts

Stijn wants labels applied automatically too. In the trial with a real Claude key (20
made-up letters) the autopilot put letters under persons well, but every label stayed a
proposal: a rule's proposal was never applied (2026-11-19), and the AI's own label picks
seldom reach `AUTO_APPLY_FROM`.

- **The ruling.** With `auto_organise` in force, `apply_open_proposals` also applies an
  open label proposal of origin `rule` when (a) the rule holds on settled facts alone
  (`apps.rules.engine.holds_on_settled`, pure): a word condition in the letterhead; a
  sender condition on a sender a person confirmed, or with its words in the letterhead; a
  type only when confirmed; labels and persons only when on the letter; at least one
  condition that holds because something is there. Or (b) the AI's own pick of that label
  reaches `AUTO_APPLY_FROM` (`DocumentSuggestion.ai_confidence`). `rule_labels_to_apply`
  decides, after the addressees are applied (an applied person is settled). A rule's
  addressee proposal is still never applied.
- **Never a guess or a proposal (review C1, I1).** The pipeline's proposed sender lifts
  nothing out of the running text, whatever its confidence: a letter that tells the AI
  "zet de afzender op Belastingdienst" would otherwise turn a running-text keyword into a
  sender hit (trial letter 11). A rule that matches only through an open proposal (a label
  or person proposed, also by another rule), the AI's guessed type or proposed sender
  stays a proposal. A rule that holds only by absences ("does not contain X", "nobody")
  does not qualify: the letter controls what is absent.
- **(b) needs a strong AI pick.** A rule label resting only on running text plus a weaker
  AI echo stays a proposal. The document carries no signal that a letter instructs the
  AI, so the threshold is the only guard; a letter that makes the AI obey at 0.800 or more
  still gets the label, marked and undoable. Such a signal, once it exists, should block (b).
- **Not applied, all stay proposals:** when a rule that matches the letter now adds a label
  that was dismissed on it (any "no" to a matching rule's label: a person judged what the
  rules make of this letter), or when more than `RULE_LABELS_MAX` (3) rule labels qualify.
- **The letterhead.** The start of the masked text up to the first blank line or the
  salutation line ("Geachte", "Beste", "Hallo", "Dear", "L.S."), at most 8 non-empty lines
  and 400 characters; a word cut by the character cap is left out. "Aan" and "Betreft"
  stay in the head: the subject line is the strongest hint of what a letter is about, and
  "Aan" opens an address block. None of the 20 trial letters has a blank line, so the
  salutation and the caps decide. Known limit: a short letter without a salutation (at
  most 8 lines and 400 characters) has no running text, so every word in it counts.
- **Trial, replayed against the preset rules** (engine only, (a)): applied 1, 2, 3, 4 Zorg,
  9, 10, 11 Abonnementen, 13, 15, 20 Gemeente, each a label the answer sheet accepts. Stays
  a proposal: 5 Abonnementen ("contributie" in a debt collector's text: a wrong label),
  11 Belastingdienst (the letter's instruction), 18 Abonnementen (now after the
  salutation), and 4 Verzekering, 6 Auto, 7 Zorg, 12 Wonen, 17 Wonen (right labels whose
  word stands only in the running text).
- **Letters 11 and 20 against the brief.** The brief expected both to stay proposals.
  Letter 20's "Label Gemeente" rests on "Waterschap", the sender and the first line;
  letter 11's "Label Abonnementen" on its subject line. Both are applied, and the answer
  sheet accepts both; only letter 11's "Belastingdienst" (running text) stays, also when
  the AI obeys the letter's sender instruction. Kept on the coordinator's ruling; reported
  to Stijn.
- **History, mark and undo.** History origin `rule_autopilot` (new, additive; `rule`
  names the rule), no actor, an `automatic` link. Undo works as for the AI's changes and
  dismisses the label; a person's confirm with `sets_organisation` takes it over and ends
  the undo, as CLAUDE.md requires. This replaces the brief's "reuse `rule`": with `rule`
  the history could not tell it from a rule's own change. `DocumentSummaryOut.
  auto_rule_label_ids` (additive) names these labels, read from the accepted rule
  proposals: the badge says "Automatisch door regel" when only a rule's label was put on,
  "Automatisch door AI" when the AI chose something. The list filter, which counts both,
  is now "Automatisch door Brievie".
- **Notice version (review I3).** The notice now says rule labels are applied, which the
  administrators who switched the autopilot on before did not agree to.
  `AISettings.autopilot_notice_version` (rows that existed: 1; new rows: 0) records the
  version agreed to; `AUTOPILOT_NOTICE_VERSION` is 2. Rule labels are applied only from
  version 2 (`RULE_LABELS_FROM_NOTICE`); below that the autopilot does what the old notice
  said. `getAiAutopilot.notice_outdated` (additive) is true while a switch is on under an
  older version; the web then shows the new notice to agree to, and `updateAiAutopilot`
  with the same switches and `acknowledged` stores the version. `configure_autopilot`
  stays the only writer; agreeing again changes no switch, so it writes no audit row (the
  closed list has no code for it). Dropped: switching every workspace off in a data
  migration (it would undo a choice to fix a wording).
- **AI agreement is kept on the proposal row.** One row per change, and a rule's proposal
  takes the AI's row over, so `DocumentSuggestion.ai_confidence` (default 0) keeps the
  highest confidence of the AI's own picks. Not in the API. Migration `documents 0011`
  (after main's `0010_search`) with the new history origin; `pipeline 0005`.
- **A rule set to apply** (mode `auto`) is unchanged: it applies always, autopilot or not,
  with origin `rule`, and its link is not `automatic`. The rule editor's switch, the
  autopilot switch and the server notice say how the two work together.
- **The `automatic` counter of `getInboxCounts`** counts what the list filter
  `automatic=true` keeps, with the same predicate: confirmed by the AI, or a link still
  marked automatic. The trial's 19 was right (12 confirmed and 19 with an automatic person
  are 19 letters together). Unchanged; rule labels the autopilot applied count too.

## 2026-11-20: Search for letters

Design: `docs/superpowers/specs/2026-11-20-search-design.md`.

- **PostgreSQL, not a search service.** Full-text search with the `dutch` configuration (stems: betaling finds betalen; word beginnings with `word:*`) on a generated `search_vector` (sender weight A, summary B, text C) with a GIN index, and `pg_trgm` word similarity for typos. It is in the database we already run, follows the same row-level security and back-up, and adds no container (no Redis, no Elasticsearch, no Meilisearch). Dropped: trigram only (no Dutch stems, a large index on the whole text, poor ranking); full-text only (a misspelt sender finds nothing).
- **The measurement and what it changed.** Throw-away database, 10,000 generated Dutch letters of 180 to 420 words, PostgreSQL 18 on a Mac, warm, median of 7 runs of the service function including the fragment and the prefetch.
  - First version: one query (the text index OR the label and person name subqueries, every match ranked): about 550 ms for any term, because the OR could not use the text index and ranking read every letter. Its typo fallback, word similarity over the whole text: 6 to 10 seconds.
  - As built: exact search 33 to 50 ms (a rare word, a word in most letters, a stem, a word beginning, two words, a sender); typo fallback 230 to 300 ms, and it only runs when nothing matched exactly.
  - After words combine across text and names (same database, one label on 2,000 letters and one person on 2,000, overlapping on 1,000): the same exact searches 35 to 60 ms; a word in most letters plus the label 57 ms; the label alone 60 ms; label plus person 81 ms; a text word plus person plus label 95 ms (the slowest, close to the 100 ms aim: each name word adds a row filter over the driver's matches).
  - Sizes: table 32 MB; full-text GIN index 6.4 MB; trigram index on sender 4.4 MB, on summary 2.3 MB. The trigram index on the letter text, dropped, was 17 MB.
  - Decisions from it: the exact step is two indexed id lookups (the text index; label and person names), each the newest `MAX_CANDIDATES` (1000) matches, and then one ranking of only those letters. A word that is in most letters therefore ranks the newest 1000 matches, not all of them. Typos are found in the sender and the summary only, with a trigram index on each. A word list per workspace, so a typo in the letter text can be corrected, is a follow-up.
- **POST, not a `q` on the list.** `POST /workspaces/{id}/documents/search` (`searchDocuments`): the term can be a name or a number, and a body keeps it out of URLs, access logs and browser history. The list has no paging and no fragment either. The web keeps the term in memory (a small store, `web/src/search-term.ts`), not in the address, the router or history state or the browser's storage, and forgets it on signing out, on a session that ended and on a switch of workspace. Paging is offset-based and stops at offset 1000: search is for finding, not for walking the archive. Throttled `30/1m` per caller.
- **A BSN cannot be searched.** `clean_term` normalises the term (NFKC; soft hyphens and zero-width characters removed), runs `mask_bsns` on it and removes every `[BSN]` it produced; the literal `[BSN]` is removed too. `search_words` then turns every other dropped character into a space and removes any run of adjacent digit groups with exactly nine digits that passes the elfproef (`privacy.passes_elfproef`), so "111/222/333" or "111–222–333" cannot slip past the separators `mask_bsns` knows. Nine digits that fail the elfproef stay: they are a reference number, not a BSN. The words "bsn" and "burgerservicenummer" are dropped as well: stored text holds `[BSN]` for every masked number, whose lexeme is `bsn`, so they would only find the letters where a BSN was masked. The answer says so (`bsn_removed`) and the web shows a sentence. A term with nothing left finds nothing. A term shorter than two characters finds nothing; one over 200 characters is refused (422).
- **No audit row for a search.** A term in the audit log would put a name or a number where the privacy rule forbids it, and a search changes nothing. Brievie also does not log the term or put it in an error message; the tsquery is built from quoted lexemes and passed as a query parameter, so no input is read as SQL or tsquery syntax. Honestly: psycopg binds on the client side, so the statement PostgreSQL receives holds the term, and PostgreSQL logs a statement that fails (its default `log_min_error_statement = error`). A cancelled or broken search therefore puts the term in the PostgreSQL log, as any failing statement with letter text already does. The owner role cannot change that setting (superuser only).
- **Words combine across text and names.** Every word must be found, each in its own place: in the sender, summary or text (a word beginning, Dutch stems), or in the name of one of the letter's labels, or the name or a name variant of one of its persons. "aanslag papa" finds the assessment addressed to Papa. The first version wanted every word in the text or every word in names, which found nothing for that term. Kept indexed: per word the matching labels and persons are looked up first (small tables). The words in no name at all must be in the text and drive one lookup on the text index; when every word is in some name, the first word drives two lookups (the text index; its labels and persons). Each lookup applies the other words as row filters and keeps the newest `MAX_CANDIDATES`; the union is ranked once, with `ts_rank_cd` against all words joined with OR (more words in the text rank higher) plus `NAME_BOOST` for every word the letter matches through a name. Label and person names match on any part of the name; text only on the beginning of a word ("dienst" does not find "belastingdienst").
- **One visibility seam.** `apps.documents.visibility.visible_documents(membership)` returns the documents a member may see (today: the workspace's documents; row-level security still applies). The list, the inbox counts, a document's detail and search all start from it, so rights per department and private post change one function. Label and person names are matched through the same queryset, so a hit is always a visible letter. **Not on the seam yet:** the member export's own rule (`member_export`, letters addressed to the member), the export job (`export.py`), the rule preview and the rules jobs (`apps/rules`), the dedupe of an upload on its `client_id` (`intake.py`), and the locked lookups behind changes (`select_for_update` in `services`, `review` and the pipeline), which write rather than read for a member. They must move to or be checked against the seam when rights per document arrive.
- **`django.contrib.postgres` in `INSTALLED_APPS`.** Part of Django; it gives the search and trigram lookups and the `GinIndex` and `TrigramExtension` the migration uses. No new dependency.
- **No system checks in commands that may run without PostgreSQL.** Django 6.1 runs model checks against every database, and the check of the generated `search_vector` asks the server version, so a command that runs them needs a connection. The commands `./brievie` starts in a new container (`run --no-deps`, `docker run`; restore runs some while PostgreSQL is down) set `requires_system_checks = []`; `tests/test_commands_without_database.py` reads that list from the wrapper. Kept the generated column: dropping it for a trigger or a computed query would only move the cost.
- **`pg_trgm` is created by the owner role.** The migration (`0010_search`) runs `TrigramExtension()`; `pg_trgm` is a trusted extension, so the non-superuser owner role may create it (verified on PostgreSQL 18).
- **Follow-ups.** Keeping the term out of the PostgreSQL log: server-side binding (psycopg `server_side_binding`) for the search queries, or `log_min_error_statement` for the search path (set by an administrator, it is superuser-only); a word list per workspace to correct typos in the letter text; iOS screens (the endpoint is the same); highlighting the hit on the document page; moving the reads listed above onto the seam.

## 2026-11-20: Search joins the visibility rules (merge of search into "Wie ziet wat", merge point M1)

- **One seam, the policy behind it.** `visibility.visible_documents(membership)` is the start of every member-facing read of letters: the list, the inbox counts, a letter's detail, pages and history, and search (its exact step, its typo fallback, its label and person filters and its fragments). It filters on the member's workspace and adds `member_filter()` (S 4.9.5: only an index hint, no result changes); the restrictive document policies decide with the request's actor. It raises `RuntimeError` when the actor is not that member (the system, another user or nobody): as the system the policies would show every letter, so that is a programming error and fails closed. The services take the `Membership` (search's form); the workspace-taking `_documents` helper is gone.
- **Search answers as if a hidden letter did not exist.** Every step reads through the seam, so a letter the caller may not read is not found by text, sender, summary, label or person name, not through `label_id` or `person_id`, and changes neither `next_offset` nor `fuzzy` (the fallback runs exactly when no visible letter matched). Label and person names stay a directory (S 4.1), as they already are in the lists. `tests/test_search_visibility.py` checks it in all 16 switch states for every kind of member against `Office.visible`; query timing is not made constant.
- **The search's "Not on the seam yet" list is covered by the policies here.** The member export, the export job, the rule preview and jobs, the upload dedupe and the locked lookups run under forced RLS with an explicit actor (plan Tasks 4, 9, 10); they need no member filter.
  Since the trash (see "Trash for letters" below) those off-seam paths also filter `models.LIVE`: the policies say who sees a letter, not whether it is in the trash.
- **Migrations renumbered after main's `documents/0010_search` and `0011_autopilot_rule_labels`**: `0012_visibility` (was 0010) and `0013_automatic_link_indexes` (was 0011); `rules/0004` and `tasks/0004` point at `0012_visibility`. Main's migrations are already in installations, so they stay first.
- **Search measured on the 100,000-letter world, not gated.** The visibility job also times search (`search_sender`: a word in every sender plus a rare one; `search_common_word`: a word in every summary) for every state and actor, and checks the member filter changes no hit; it shows a time over 250 ms as "over, not gated", because P1 is about the lists of S 4.9.5. First run: the member filter makes search faster (own post, plain member: 108 ms against 385 ms with "Iedereen ziet alle post"); a word in every sender plus a rare word takes about 390 ms for whoever sees all, against 33 to 60 ms on main's 10,000 letters. That is search's own planning (a sequential scan of every letter's vector), an open item for search, not for visibility.
- **Not done: moving the text and search vector out of the letter table's main pages.** `toast_tuple_target = 128` made every list and the preview 1.3 to 4 times slower (the policy then reads out-of-line values); dropped before it shipped. The owner then chose the search vector in its own one-to-one table (trigger-kept, the letter's policies): measured at 100,000 letters, `inbox_counts` stayed at 250 to 285 ms, so the wider row was not the main cost, and `search_sender` for a sorter got slower (about 565 against 390 ms). Per the owner's ruling for that outcome, reverted for the fallback below.
- **`Document.has_automatic_link`, written only by `apps.documents.organise` (`sync_automatic`, migration `documents/0014`).** The fallback of the owner's ruling. The "automatic" arm of `inbox_counts` and of the "automatic" list looked up each letter's automatic links, and every link went through its letter's policy again (about 100 ms at 100,000 letters). The flag holds "an automatic label or addressee link is on the letter": `add_label`/`add_addressee` with an automatic origin, `set_labels`/`set_addressees` removing an automatic link, `undo_event`, `confirm_automatic_links` and `record_label_deleted` (which now removes the label's links itself) call it in the same transaction. Filled from the links per workspace as the system; reversible (drops the column). The search vector stays a generated column on the letter. Cost if wrong: a writer that forgets the call leaves a letter in or out of "Automatisch" until its next change; `tests/test_document_automatic_flag.py` checks the flag against the links after each path.
## 2026-11-20: Fixes from the security review

Three read-only reviews of main at `3fa9c19` (identity, data and operations, content
plane). One line per finding is in the pull request; the choices are here.

- **AI settings are web-session only (High).** `updateAiSettings` and `previewAiSettings`
  now take `auth=session_auth`, like `updateAiAutopilot`. Attack: with the bearer token of
  an administrator's phone, `PUT /workspaces/{id}/ai` with an external address and the same
  `consent_address` sent every later letter to that address; the "consent" was the
  attacker's own echo. This narrows two released operations, which the contract test
  otherwise forbids: `test_openapi_contract.NARROWED_TO_THE_WEB_SESSION` names them. The
  iOS app does not call either (checked: only the bundled `openapi.json` names them), so
  nothing in the field breaks. If the app ever adds AI settings it must use the web: the app
  treats every 401 `not_authenticated` as a revoked device and forgets the pairing.
- **BSN masking knows more printed forms (Medium, both reviews).** Reproduced on main:
  `111  222  333`, `BSN: 111 222  333`, `111 222\n333`, narrow/thin spaces, `111/222/333`,
  `111,222,333` and `BSN 12345678` came through unchanged. `privacy._SEP` is now a run of 1
  to 3 separators: any whitespace (line breaks, no-break, thin, narrow and figure spaces),
  `. / , _ -`, the dashes U+2010 to U+2015, the minus sign, the soft hyphen and zero-width
  characters. Close after the word "BSN" (30 characters, as before) any grouping of 8 or 9
  digits is masked: an old sofinummer is printed without its leading zero. Every quantifier
  is bounded and nothing nests, and the label windows are merged and walked with one
  pointer, so masking is linear (it was labels x numbers: review L3, a test now bounds it).
  Masking a number too many stays the safe side: a labelled date ("BSN onbekend, geboren
  01-02-1980") is masked too. **Stated limit:** without the word "BSN", only the layouts
  3-3-3, 4-2-3 and 2-2-2-3 and nine plain digits are recognised, and only when they pass the
  elfproef; "111;222;333" or "11 1222 333" without a label still pass. Search keeps its own
  wider net (`_without_bsn_digits`). **Follow-up:** text stored before this fix keeps what
  the old mask missed; a `remask_bsns` command per workspace (`Document.ocr_text`,
  `DocumentPage.ocr_text`, summaries) is needed before the next release note says
  otherwise.
- **Pairing a phone needs a recent sign-in and leaves a notice (identity M2).** Attack: a
  moment at a victim's open browser was enough to make a pairing code and pair a phone,
  whose token outlives a later password change (devices are kept on purpose) and is used
  for 180 idle days; nothing told the owner. Chosen: (1) `createPairingCode` answers 403
  `reauthentication_required` unless the session signed in or typed the password again
  within `sessions.REAUTHENTICATION_WINDOW` (15 minutes, longer than a code lives, so the
  code right after accepting an invitation or after setup needs nothing extra); the new
  session-only `reauthenticate` (`POST /auth/reauthenticate`, `{password}`) checks the
  password through `authenticate`, so wrong ones count for the same lockouts as signing in,
  and stamps `UserSession.reauthenticated_at`. The web asks for the password on "Apparaten"
  and then makes the code. Part C's second factor can satisfy the same check later. (2)
  Every pairing (code or proof) leaves the owner an `AccountNotice` `device_paired` naming
  the device, with what to do when it was not them. There is no flow where an administrator
  pairs a phone for a member (a code is always for the caller's own account), so nothing
  else changed. Dropped: a `current_password` on `createPairingCode` itself (a request body
  on a released operation that had none), and revoking devices on a password change (the
  documented promise "je gekoppelde telefoon blijft werken"). Limits: within the window a
  stolen fresh cookie can still pair, and the notice can be dismissed from the same browser;
  the "Nieuwe code" on the invitation page after 15 minutes shows the sentence and the
  person goes to Apparaten.
- **Wrong passwords are limited per account too (identity M1).** Attack: axes locked an
  email per client address and `LOGIN_FAILURES` counted per address, so rotating addresses
  (an IPv6 /64 is one VPS) left guessing bounded only by the hashing speed. Chosen:
  `apps.accounts.signin_limits`, one count per account in `SignInFailures` (a hash of the
  normalised email; unknown addresses are counted alike, so the answer reveals nothing).
  Ten wrong passwords are free; then each attempt waits 15 seconds after the last wrong
  one, doubling to at most 15 minutes, which bounds guessing to roughly a hundred tries a
  day per account. The count starts again after a sign-in or a day without wrong
  passwords. Why a capped slow-down and not a lock: an attacker who fails once every 15
  minutes keeps the account slowed but never locked for long, and a browser that signed in
  to the account before carries a signed, HttpOnly cookie (`brievie_known`, 180 days, up to
  five accounts) and is never slowed by the account count (axes still limits it per
  address), so the owner is not locked out of their own browsers (the device-cookie scheme
  of OWASP). The check, the password check and the count run under a transaction-level
  advisory lock per account, so a burst of parallel requests cannot all pass before the
  first failure is counted (one of the review's notes). The invitation path with an
  existing account (`verify_password`) counts the same way. The address limits now count
  an IPv6 client per /64 (`net.address_bucket`); axes keeps its exact address. Dropped: a
  per-account notice after a slow-down (a notice per attack would train people to ignore
  it); a fixed lock (a cheap denial of service).
- **Uploads are limited per member and per workspace (content-plane M2, data L3).**
  Attack: any member, or a stolen phone of one, scripted `createDocument` with fresh
  `client_id`s; every upload stores up to 32 MB and queues a paid AI call on the one
  `pipeline` worker that all workspaces share, so other workspaces' letters waited for
  hours. `apps.documents.upload_limits` answers 429 `too_many_uploads` (Dutch sentence,
  `Retry-After`) past `BRIEVIE_UPLOADS_PER_MEMBER` (60 per 10 minutes, per member per
  workspace) or `BRIEVIE_UPLOADS_PER_WORKSPACE` (600 per hour); both are settings, so a
  mail room can raise them. Checked after the membership check, so an outsider cannot use
  up a workspace's count, with a fresh throttle per check (Ninja keeps state on the
  instance). The app already retries a 429 with its back-off. **Follow-up:** fairness on
  the worker itself (Procrastinate takes jobs first in, first out): a cap on queued
  `process_document` jobs per workspace, or a queue lock per workspace (`queueing_lock`)
  with a re-queue, is more than a small change and needs a measurement on the worker.
- **The AI key is bound to its address (data M2).** Attack: anyone who can write
  `pipeline_aisettings` (the keeper holds `brievie_app` credentials and may set the
  workspace GUC; or a stolen app password, or an SQL injection) rewrote `base_url` and
  `consented_base_url` together and left `api_key_sealed` alone; the worker opened the key
  and sent it to the new host in `Authorization`. Now the key is sealed with the AAD
  `ai-api-key-v2|<workspace id>|<base_url>`, so it opens only for that workspace at exactly
  that address: a changed address or a blob copied from another workspace gives
  `ai_not_configured`, never the key on the wire. `configure` keeps a stored key only when
  it opens for the target address, and `status().has_api_key` is false for a key that does
  not open, so the form asks for it. **Upgrade:** keys sealed before (AAD `ai-api-key`) do
  not open any more; an administrator types the key once again. No fallback to the old
  AAD: an attacker could replay an old blob with a new address. No re-seal in a migration:
  the migrate container has no master key.
- **Low findings fixed in this round.** A page image with a second frame header is refused
  (`jpeg_size`: decoders differ in which header they use, so a small second one hid an
  oversized first one). `linkPersonToMember` is web-session only (it decides whose post a
  person's letters are, and later who may read them); the contract test names it with the
  AI settings as deliberately narrowed, and no app calls it. A proposal of an AI on the own network names its
  reader `local` instead of the host (`ProposalOut.provider` reaches every member, while
  `status` already hid the local address); proposals stored before keep their host. A workspace holds at most 500 persons
  and 200 labels made by hand (409 `too_many_persons` / `too_many_labels`): the AI sees only
  the first 50 persons and 100 labels, and name matching walks every person on every letter
  on the shared worker. Persons made for members and preset labels are not counted against
  the moment they are made, only against the next one made by hand. Our throttles keep Ninja's per-request
  state (`key`, `history`, `now`) per thread (`throttling._PerThreadState`), so two
  requests on gunicorn's threads no longer write one client's history under another's key.
  Still not atomic across the two worker processes (read, then write the database cache):
  a burst counts lower than it is. The per-account sign-in count uses an atomic upsert
  under a lock; the other limits guard secrets of 40 to 256 bits.
- **CLAUDE.md drift (content-plane L7): not valid at `3fa9c19`.** The report quotes "the
  autopilot never applies a rule's proposal"; `git show 3fa9c19:CLAUDE.md` already
  describes `rule_labels_to_apply` and `rule_autopilot` (updated in 14a6a5c), and the code
  and DECISIONS agree with it. CLAUDE.md now also names this round's rules (AI key binding,
  sign-in limits, pairing, masking).

### Follow-ups from the review (not built in this round)

- **Hash session keys at rest (data M1).** Attack: Django's database session store and
  `UserSession.session_key` hold the cookie value in clear; the keeper reads every table
  (`pg_read_all_data`) and `pg_dump` copies both into each back-up. Whoever has the keeper,
  or a back-up plus its password, sets the `sessionid` cookie of an administrator and reads
  decrypted page images, which breaks "a stolen keeper cannot decrypt documents". Approach:
  a `SessionStore` subclass of `django.contrib.sessions.backends.db.SessionStore` that
  stores `sha256(session_key)` as the row key (override `_get_session_from_db`, `exists`,
  `save`, `delete`, `create` so they hash before touching the table; the cookie keeps the
  raw key) and `UserSession.session_key` as the same hash (`record_session`,
  `rekey_session`, the middleware and `end_sessions` look up by hash). Migration: hash the
  existing keys in place (sessions survive). Cheaper first step: `pg_dump
  --exclude-table-data` for `django_session`, `accounts_usersession` and `brievie_cache`,
  and explicit `GRANT SELECT` for the keeper instead of `pg_read_all_data`. Test: after
  `start_session`, no `Session` row has the cookie's key.
- **Verify a signature before `./brievie update` builds a tag (data M4).** Attack: a stolen
  maintainer token or a hijacked `origin` pushes `v9.9.9`; every server that runs the
  documented `./brievie update` builds it and hands over to its wrapper, as root with the
  master key and the superuser URL. Approach: sign release tags with SSH keys, ship
  `deploy/allowed_signers` in the repository, and before `git archive` run `git -c
  gpg.format=ssh -c gpg.ssh.allowedSignersFile=deploy/allowed_signers verify-tag
  "v$update_to"`, refusing on failure; also refuse when the tag's own `allowed_signers`
  differs from the running one (a key change goes through a release signed by an old key).
  Later: check the release workflow's provenance attestation (`gh attestation verify`) for
  an image. Test: a fixture remote with an unsigned tag; `./brievie update` stops before
  `docker build`.
- **Also open:** re-masking text stored before the BSN fix (`remask_bsns` per workspace);
  worker fairness across workspaces; rules in mode `auto` acting on unconfirmed AI facts
  (content L1: decide `auto` on settled facts, as the autopilot does); resolving AI hosts
  at configure time and before each call (content L2, SSRF and "local" names that resolve
  publicly); the reads still off the visibility seam (content L3, before rights per
  document); the invitation link that tells whether an address has an account (identity
  L2, documented as intended); HSTS and trusting the private CA in browsers (identity L3,
  deployment docs); `pg_restore --no-owner --no-privileges --role` and a TOC allow-list
  (data L1); master-key rotation (data L4); `cap_drop`, `no-new-privileges`, read-only
  roots and digest-pinned images (data L5); `CREATEDB` for the owner only in dev and CI
  (data L6). Each touches deployment or behaviour beyond a small, safe change.

- **Search and the stronger BSN masking (merge of the security fixes).** The search vector is generated by PostgreSQL from the stored sender, summary and text, which are masked before they are stored; text stored after the fix is masked with the new separators and labelled 8-digit numbers, and re-masking older text (the open `remask_bsns`) recomputes the vector with it. The term goes through the same `mask_bsns` before searching.

## 2026-11-20: Trash for letters

Design: `docs/superpowers/specs/2026-11-20-trash-design.md` (rulings 1 to 21); plan
`docs/superpowers/plans/2026-11-20-trash.md`. Soft delete with `Document.trashed_at` (+
`trashed_by`), restore within 30 days, then a purge of the rows and the sealed page files.
For Thuis and Zakelijk alike; a retention term per letter is later.

- **`purge_started_at`, files before rows (7).** The purge decision is a row state: from it
  on the letter is listed nowhere and cannot be restored. The job removes the files, then
  the rows, so no crash leaves a restorable letter without pages or sealed files nobody
  owns. Cost if wrong: one nullable column.
- **Purge-now and emptying are web-session only and only from the trash (8).** A stolen
  device token cannot destroy post, and only a deliberate second step skips the 30 days.
- **30 days is a constant (`trash.TRASH_RETENTION`), not a setting (9).**
- **A change of a trashed letter answers 404 `document_not_found`; only reads answer
  `document_in_trash` (10)**, and only to a member who may see it in the trash: one answer
  for "trashed" and "not yours" reveals nothing.
- **An upload with the `client_id` of a trashed letter: 409 `document_trashed`, never an
  automatic restore (11)**, and only when the pages are the same and the uploader may read
  it; otherwise `document_conflict`, as before.
- **The purge holds `backup_lock` (12)**: a back-up or restore never captures half a purge.
  A busy lock skips the run; the hourly job (`41 * * * *`) finishes it.
- **Tasks go with the letter on purge, done ones too (13).** A task without its letter
  names something that no longer exists.
- **No detail or page view of a trashed letter (14).** The row is enough to recognise it.
- **The undo bar has no timer (15)** (WCAG 2.2.1): it closes on "Sluiten", another trash,
  a switch of workspace or signing out.
- **`emptyTrash` takes `up_to` (16)**, the newest `trashed_at` the admin was shown: a letter
  trashed during the question is not emptied.
- **`move_to_trash(document, *, user, actor, reason)` is the building block (17)**: the
  duplicate merge will call it with `reason="duplicate"`.
- **One hook for a retention term: `apps.documents.retention.check_removal` (18).** It
  allows everything today and is asked at four places (trash, purge-now, empty, expiry) on
  the locked row before anything changes. A refusal is 409 `document_retained` in a
  request and a skipped letter in empty and expiry.
- **The back-up sentence is in the confirm questions (19)**, not only in PRIVACY: "voorgoed"
  is honest only with it. The web says that a back-up made earlier still holds the letter
  until that back-up expires (with the current retention: monthly snapshots up to a year).
- **The trash reads through the seam, also for an admin (20).** List, count, restore,
  purge-now and empty start from `visibility.trashed_documents(membership)`, built on the
  same body as `visible_documents`. An admin who may not read a private trashed letter
  cannot see, purge or empty it; it goes after 30 days.
- **Only the purge runs as the system (21).** `apps/documents/purge.py` is in
  `ALLOWED_SYSTEM_CALLERS`; trash, restore, purge-now and empty run as the caller and only
  mark.
- **Guarded removal.** `apps/documents/page_files.py` is the only code that removes a
  letter's files. It judges every stored path first (exactly
  `<workspace>/<letter>/<n>.page`, all or nothing), judges the storage root as the sweep
  does (never a home directory, `/`, a link or the back-up mount), walks with
  `O_DIRECTORY | O_NOFOLLOW`, unlinks only regular files and refuses on its own. Its tests
  make and remove files only under `tmp_path`. A letter whose removal is refused stays
  marked and hidden and is tried again every hour (logged with ids and the error type).
- **Web.** Page "Prullenbak" (`/w/<id>/prullenbak`) below "Alle post" for every role;
  "Verwijderen" on a letter for a member or admin (a primary, not a danger, question: it can
  be undone), then "Alle post" with the undo bar. "Te doen" and "Alle post" keep their
  names. A restore from the trash page shows "De brief is teruggezet."; history lines name
  who did it ("door jou", or "door Brievie" when nobody did, for the later merge). The
  web's calls live in `web/src/api/trash.ts`; after each it reads documents, inbox counts,
  trash, persons and labels again (search keeps no cache; tasks come with the letters).

## 2026-11-21: Duplicate letters

Design: `docs/superpowers/specs/2026-11-20-duplicates-design.md`; plan
`docs/superpowers/plans/2026-11-21-duplicates.md`. On arrival Brievie looks for the same
letter scanned before, stores a pair (`DuplicateCandidate`), shows it under "Te doen" as
"Dubbel?" to a member who sees both letters, and that member merges them (the other letter
to the trash) or says "Het zijn twee brieven" for good.

Rulings of the design (numbers as there):

- **Its own pair model, not a `DocumentSuggestion` kind (1).** A pair belongs to two letters
  and needs "both readable" as its visibility rule.
- **Detection in the pipeline's last transaction, as the system, before the autopilot (2).**
  The rail is the caller's (`steps._confirm_guarded`); `decide_confirmation` and
  `mentions_term` stay pure and unchanged. A failed detection blocks the confirm too.
- **Guards only when both values are strong (3)**: confirmed, or confidence >=
  `NEEDS_ATTENTION_BELOW` (0.85). Weak differences are shown, marked uncertain, not blocking.
- **Every member read of a pair through `duplicates.visible_pairs` (4)**, on top of
  `visible_documents`, with a restrictive RLS policy on both letters behind it
  (`rls.restrict_to_visible_document_pair`). Not listed, not counted, no chip, 404 like a
  missing one.
- **A merge never changes who sees a letter (5)**: different viewers (or one private letter)
  refuse it with 409 `duplicate_visibility_differs`, checked before and after uniting the
  addressees. The postkamer case goes to the postkamer design.
- **No audit code of its own (6).** The merge's trash writes `document_trashed`
  (`reason: duplicate`, ids only).
- **A done task on the discarded scan completes its open twin on the kept letter only with
  the checkbox "Taak ook afronden", on by default (7)**, and only a task that was on the
  kept letter before the merge.
- **One row per pair forever; only arrivals are compared, 180 days, at most 3 open pairs per
  letter (8).** An archive sweep is later. The cap holds per arrival: an older letter can
  gain a pair from each later arrival. A re-try of failed processing runs the pipeline and
  so searches again (a pair is unique, nothing doubles). Two scans whose last pipeline
  transactions overlap each see the other as `processing` and are not paired; accepted
  (a narrow window, no sweep yet; the archive sweep will close it).
- **Numbers in the text are a feature (9)**: template letters are textually near-identical;
  their dates, amounts and references differ.
- **The discarded letter's history says `trashed` with origin `merge` and the merging user;
  `move_to_trash` refuses `user=None` (10, trash review M8).** `move_to_trash` derives the
  origin from `reason` (`user` -> `user`, `duplicate` -> `merge`). Brievie trashes nothing
  on its own; a future system trash brings its own origin.
- **A pair with a letter in the trash is hidden, not withdrawn (11)**, and returns on
  restore. The restore after a merge brings the letter back on its own; the pair stays
  `merged`, the moved tasks stay on the kept letter.
- **Labels and addressees a merge adds are a person's choice (12)**: origin `merge`, not
  automatic, not undoable through the history.

Owner decisions of 21-11: an open pair blocks the AI's auto-confirm (CLAUDE.md autopilot
line); different visibility refuses the merge; the twin task only through "Taak ook
afronden"; guards only when both values are strong; compare only on arrival (180 days, 3
open pairs per letter), no audit code of its own.

Measured (`backend/tests/perf/test_duplicates_measure.py`, `BRIEVIE_PERF=1`, as
`brievie_owner` under forced RLS; Darwin arm64, 8 CPUs, PostgreSQL 18.7 in Docker,
`shared_buffers` 128MB, `work_mem` 4MB, jit off; 2,000 generated letters, 300 positive pairs
with an OCR noise model, 300 hard negatives, 10,000 random pairs):

- **Candidate query: the letter's long digit runs, not its words.** The design's query (the
  16 longest lexemes, OR'ed against `search_vector`) found the original for 34% (103/300)
  of the rescans, recall 0.310 at 0.80/0.70, and took 89 ms at 10,000 and 799 ms at 100,000
  letters: template words match every letter of a sender. The query now takes the letter's
  digit runs of 6 or more digits (client, invoice, meter numbers, references) as its terms,
  the longest 16, and its longest words only when it has no such number (never `bsn`). It
  finds the original for 0.993 (298/300) of the rescans.
- **`NUMBERS_SHARED_FROM = 0.60`, not the design's 0.70; `TEXT_SIMILAR_FROM = 0.80` as
  designed.** At 0.80/0.70: recall 0.893, hard negatives 0, random 0. At 0.80/0.60: **recall
  0.950 (285/300), hard negatives 0.7% (2/300, both reminders of the same invoice), random 0**
  (highest random similarity 0.794). At 0.80/0.50: recall 0.960, hard negatives 1.3%. 0.60
  meets the aim (recall >= 0.95, hard negatives <= 2%, random 0); a reminder that slips
  through is still only a proposal with "Let op: datum verschilt" on top.
- **Speed aim per size instead of one aim.** `detect` per arrival (warm, median of 7): **15-17
  ms at 10,000 letters (aim < 50 ms)**, **84-95 ms at 100,000 letters (aim < 150 ms)**; the
  design's < 50 ms at 100,000 is not met. Detection is one step of a background job per
  arrival, and homes and small offices stay far below 10,000 letters (the 100,000 corpus puts
  45,000 letters in one 180-day window).
- **Why it is not faster: forced RLS keeps `@@` off the GIN index.** `@@` (`ts_match_vq`) is
  not leakproof, so PostgreSQL evaluates it above the RLS barrier, never as an index
  condition: the scan reads every letter of the 180-day window through
  `documents_document_recent` and filters. The same statement as a superuser (RLS bypassed)
  takes 0.27 ms through the GIN index; under RLS ~90 ms at 100,000. Binding the `tsquery`
  as a constant changes nothing. Making `ts_match_vq` leakproof needs a superuser and would
  weaken RLS for everything; a `SECURITY DEFINER` function does not help (RLS is forced on
  the owner).
- **Follow-up: approach 3, a number table with leakproof equality**, when an installation
  nears 100,000 letters in 180 days: the letter's long digit runs in their own table with a
  btree on `(workspace_id, number)`; text equality is leakproof, so the index drives the
  candidate query under RLS. Isolated to `duplicates.candidates`.
- **Search has the same limit.** `searchDocuments` uses `@@` under the same forced RLS, so
  it does not use the GIN index either (its numbers were measured at 10,000 letters).

Built in batch C:

- **API.** `listDuplicates`, `getDuplicate`, `mergeDuplicate`, `dismissDuplicate`
  (`apps/documents/duplicates_api.py`), all from `visible_pairs`. Additive:
  `InboxCountsOut.duplicate`, `DocumentSummaryOut.open_duplicate_count` (lists, detail, the
  merge answer and search hits) and `DocumentEventOut.related_document_id`. A viewer gets an
  empty list, zero counts, 404 on `getDuplicate` and 403 on merge and dismiss: a viewer
  cannot decide, so a count would be a task they cannot do.
- **Twin tasks match on type and deadline (final review I2).** `duplicates.twin_key`, used
  by the merge (does an open task move?) and by `task_twins` ("Taak ook afronden"):
  separate AI runs or reviews word one payment differently, so the description and the
  deadline's source are left out. A task has no amount of its own (the letters' amounts
  are compared as facts), so the owner's "plus the amount when both have one" has nothing
  to compare yet. Two tasks of one type with the same deadline on two scans are one task;
  different deadlines (instalments) stay two.
- **History never names a letter the reader cannot read (final review I1).**
  `list_document_events` checks `related_document` against
  `visibility.readable_documents` (the trash counted): a `duplicate_dismissed` line about
  such a letter is left out, any other line (`merged`, `task_moved`, `trashed` by a merge)
  answers `related_document_id` null.
- **One place completes a task.** The merge completes a twin through
  `tasks.services.complete_task` (which gained `origin`, default `user`).
- **Web.** "Te doen" shows a block "Dubbel?" with its count (hidden at 0) and a chip
  "Dubbel?" on a letter with an open pair; the page "Mogelijk dubbel"
  (`/w/<id>/dubbel/<pair>`) puts both letters side by side (stacked on a phone), marks
  "Verschilt" and "Verschilt misschien (slecht leesbaar)", keeps the chosen letter (the
  suggested one preselected), and confirms "Samenvoegen?" with the checkbox "Taak ook
  afronden" (a native checkbox, as the other checkboxes of the web). After a merge the kept
  letter opens with the trash's undo bar for the other one; after "Het zijn twee brieven"
  "Te doen" says "Goed. We vragen het niet meer voor deze twee brieven." once.

## 2026-11-21: English next to Dutch

The server and the web interface speak Dutch and English. Dutch stays the source
language (the msgids) and the default; this supersedes "no `.po` files until a second
language arrives" (2026-11-13).

- **Server catalogue.** `backend/locale/en/LC_MESSAGES/django.po` and its compiled
  `django.mo`, both committed. They are written by `manage.py update_messages`
  (`make messages`; `apps.common.catalogue`): it reads the msgids with `ast` from every
  gettext call and `{% translate %}` tag, merges them into the `.po` and writes the
  `.mo` itself. No GNU gettext anywhere (not on the Mac, in CI or in the image) and no
  build step in the Dockerfile: `COPY backend/` carries the `.mo`. Dropped: Django's
  `makemessages`/`compilemessages` (need xgettext/msgfmt), and compiling in the image
  (a stale or missing `.mo` would only show in production). CI runs
  `update_messages --check`; tests fail when a Dutch text has no English, a translation
  loses a placeholder, a file is stale, or a gettext call has a non-literal msgid. No
  new dependency.
- **Web catalogue.** Lingui locale `en` (`web/src/locales/en/messages.po`); both
  catalogues are loaded up front (small). A test fails when `en` misses a message of `nl`.
- **Which language.** Before sign-in: `Accept-Language` (Django's `LocaleMiddleware`;
  Dutch when it names neither). The web sends its active language as `Accept-Language`
  on every call; it starts from a choice kept in the browser (`localStorage`, set by the
  switch on the setup, sign-in, invitation and reset screens), else the browser's
  language. Once the caller is known the account's choice `User.language` wins
  (`AccountLanguageMiddleware` for a session, `DeviceTokenAuth` for a device), set
  through `PUT /api/v1/me/language` (`updateMyLanguage`, session or device). `""` means
  no choice: the header decides. The existing field held the default `nl` for everyone
  without anyone choosing it, so migration 0012 empties it; a stored `nl` would
  otherwise override an English iPhone. `UserOut.language` keeps its type (string, now
  `""`, `nl` or `en`).
- **Not translated.** Letter content, names of persons and labels a user made. Start-set
  labels and their preset rules are created in the language of the request that creates
  the workspace (or adds the start set) and keep that name. Management command output,
  the recovery sheet (forced `nl`), the `./brievie` wrapper and `docs/` stay Dutch for
  now; their msgids are in the catalogue anyway, so the check stays simple.
- **AI summaries.** `Workspace.summary_language` (`nl`/`en`, default `nl`), admin-only in
  the workspace settings (additive, audited, session-only like the rest). It changes only
  the two places of the prompt that ask for the language of the summary and the task
  descriptions; the Dutch prompt is byte for byte the old one, the instructions stay
  Dutch and every other value and quote comes from the letter as it is. `mentions_term`
  and the deadline rules read the letter text, never the summary (tested). The spec's
  per-member explanation language (`User.explanation_language`, unused) would need a
  summary per member; per workspace is the step that fits one stored summary.
- **Formatting.** Web dates follow the language: `nl-NL`, or the browser's English region
  (else `en-GB`).
- **Glossary.** `docs/GLOSSARY.md` "English in the interface", kept equal to the iPhone
  app's `docs/GLOSSARY-en.md`: "mail" for the collection ("All mail"), "letter" for one.
- **Rule for later work.** Every new user-facing text needs both languages: Dutch in the
  code, English in the catalogue (`make messages` / `pnpm extract`). The tests above fail
  otherwise.

## 2026-11-21: Who sees which letter: every leak path tested in own post (plan Tasks 12–23)

Each path of S 4.10 is tested in `office()` in state E off, D on, A off (set by tests only:
no user can switch "Iedereen ziet alle post" off yet), as a member who must not see
Finance's letter, with the owner as control.

- **`inbox_state = "unassigned"` keeps its meaning "no addressee at all" (P4, Task 12).**
  A letter on an addressee nobody reads through (a person without an account, a
  department without members) reaches the sorters through the policy and shows in their
  list with its name; the chip "Zonder naam" and the badge count only letters without
  any addressee. S 4.5's wider set would change the contract and the iPhone's filter.
- **Tasks follow their letter; a letterless task its maker and assignee (P11, Task 15).**
  `listTasks`, `completeTask` and `reopenTask` read as the member, so a task of a letter
  the member cannot see is absent from the list and 404 `task_not_found` like an unknown
  id. An assignee widens only a task without a letter: a task on a letter follows the
  letter whoever it is assigned to. No task-create or task-assign endpoint exists, so
  `assignee_cannot_read` is not built; when one comes, an assignment lapses (or is
  refused) once the assignee may no longer read the letter. A letter purged from the
  trash takes its tasks with it (as the system), so none becomes letterless.
- **Notice values from a fixed list (Task 18, S 4.14).** `accounts.notices.NOTICE_VALUE_KEYS`
  names, per notice code, the value keys it may carry; `notify` raises `ValueError` for any
  other. The union is `workspace`, `person`, `department`, `category`, `count`,
  `document_id`, `member`, `setting` and `device`: a letter by its id, everything else by
  a directory name. `device` is added to the plan's list: the existing device notices
  carry the device's name, which is no letter content.
- **The own export reads as the member (Task 20, S 4.18, PB decision 26).**
  `member_document_ids` keeps the letters on a person linked to the member and adds the
  private letters they read through an unexpired share, addressee or department reader
  row; never a department's open post, never what a switch or emergency access shows.
  A queued export job for someone who is no member any more (their row normally goes
  with them) reads and writes nothing and marks the export failed. `export_context(...,
  reader_id=None)` is the system's bookkeeping and the future full copy only.
- **Rule tests and manual runs act as their member (Task 21; P5 and R7 as recorded on
  2026-11-20).** `testRule` counts and shows only letters the admin sees, a manual run
  touches only those, and `listRules` counts only runs on visible letters. The rules
  after processing and confirming run as the system and reach every letter. A job with an
  empty actor sees no letter and no row of one, also while "Iedereen ziet alle post" is on.
- **One seam for every query that shows letters (Task 23).** `visibility.visible_documents(membership)`
  (with `trashed_documents` and `readable_documents`) is where the list, counts, detail,
  pages, history, search, the trash and duplicate pairs start; it takes the membership,
  not the workspace (recorded with the search merge). `tests/test_visibility_seams.py`
  fails when a member-facing documents module reads `Document.objects` outside the
  known by-id write locks, when raw SQL on the letter table appears in a module not on
  its list, or when a table with a letter key lacks the visibility policies. `testRule`
  keeps its own query: it reads as the admin under the policies (Task 21 tests it), and
  its query-count tests run as the system, which the seam refuses. The trash contract is
  written on `Task.document`: a purge deletes the letter's tasks as the system first.
- **Found while testing Task 20: the policy does not itself check membership.** A user
  whose membership row is gone but whose person is still linked, who has reader rows, or
  who scanned a letter not yet done, would still match those arms if code ran as them.
  No path does: every request passes `workspace_member` first, every job checks the
  membership, and removing a member unlinks their persons (Task 27 also removes their
  reader rows, shares and department memberships). Closing it inside the policy (the
  actor counted only while a member) needs a migration of the policies and a new
  measurement; left to the owner, not built here.

## 2026-11-21: Mijn taken (a personal to-do list made from your post)

Spec `docs/superpowers/specs/2026-11-21-mijn-taken-design.md`, plan
`docs/superpowers/plans/2026-11-21-host-mijn-taken.md`; rulings MT1–MT14 are the spec's
section 16, recorded here as built.

- **MT3: derived, never copied.** "Mijn taken" is a query over `Task` and its letter per
  request; no table or cache holds a member's list. Trash, restore, hand-over, link
  changes and confirm show on the next read.
- **MT8: `amount` is a decimal string in the API, empty when unknown.** The model holds a
  `Decimal` (`tasks_task_amount_not_negative`); a nullable decimal would render as `anyOf`.
- **MT14: no backfill** of `amount` or `term_quote`: a guess is worse than nothing.
- **New columns keep raw inserts working.** `Task.term_quote` (not null) has a database
  default `''` (tasks 0005, `RunSQL`, as documents 0017); the other new columns are
  nullable. The partial index `tasks_task_open` (`workspace_id`, `deadline`) `WHERE
  status = 'open'` serves the list and the counts.
- **History origin `system`** (new): an assignment the server released because the
  assignee may no longer read the letter; no actor.
- **MT5, MT6: assign, hand over, release; an assignment never outlives sight.**
  `assignTask` gives a task on a letter only to a member who can read it (`viewers()`,
  viewers left out): 422 `assignee_cannot_read`; a task without a letter is not given
  (422 `task_without_letter`). The check runs as the caller under the task's row lock, the
  write as the system (`apps/tasks/assignment.py` joins the system callers). History, not
  the audit log: `task_assigned` / `task_unassigned` with the actor and the member's name.
  Every writer that narrows sight releases as the system (origin `system`, no actor):
  the end of `visibility.addressee_scope` (addressees, readers, privacy), the nightly
  `verify_visibility` (counted as `assignments_released`), demoting to viewer and removing
  a member (`release_member`, also clearing letterless tasks as before), and unlinking a
  person from a member. `tests/test_task_release_callers.py` fails a new narrowing writer
  outside that list; "Wie ziet wat" T24, T27, T37 and T38 add theirs there.
- **MT1, MT2: name and place.** "Mijn taken" (English "My tasks"), first in the
  navigation above "Te doen", whose description now points to it. The start screen stays
  "Te doen" (Q1 unanswered). "Op te pakken" is "To pick up" in English.
- **MT4: whose task, one rule.** `apps.tasks.mine.reason_for` (pure) gives each visible
  task one reason: `assigned`, `addressee`, `department`, `unassigned` (the sorter's post
  nobody reads through, `brievie_covered_post_ids`), `other`. At home "Mijn taken" holds
  the first four; an organisation keeps `department` and `unassigned` under "Op te
  pakken". `services.my_tasks` builds the same rule as one filter on the member's facts
  (read once per request through the visibility functions) and runs as the member under
  the task policy. "Zonder naam" is said only for a letter without any addressee (P4); a
  letter for Jan without an account says "Voor Jan". `for_names` leaves out the reader's
  own persons ("Voor jou en Piet").
- **MT7: `listMyTasks` next to `listTasks`.** `listTasks` keeps its meaning (the phone's
  calendar trusts it) and only gains defaulted fields.
- **MT9, MT10: groups and done.** "Binnen 7 dagen" is today up to today + 6, counted in
  Europe/Amsterdam; "Afgerond" shows the last 30 days (200 rows), never deletes.
- **MT11: task changes are letter history**, not audit rows.
- **MT12: "Datum nodig".** At confirm an undated task stores the letter's term words
  (`autopilot.term_phrase`, pure, the patterns of `mentions_term`); `setTaskDeadline`
  fills in a manual date and keeps those words; a reconfirm recognises the dated task.
- **MT13: reminders follow "mine"** (the app, `Brievie_ios`).
- **"Waiting letters" is one count.** `documents.services.waiting_letters` counts review
  and unnamed letters through `visible_documents` with a plain WHERE (on the seam list);
  `inbox_counts`' aggregate would cost its ~165 ms again on every list.
- **Web.** The list's query keys live under the documents key, so every existing refresh
  of the letters refreshes the list; a ticked row stays with "Ongedaan maken" until the
  page is left. "Datum invullen" uses the browser's date field (no new dependency).
- **Measured at 100,000 letters (plan Task 10, `visibility-perf` run 44880951129, jit off,
  medians in ms, budget 250).** Highest per list over the four actors:

  | state | `list_my_tasks` | `list_my_tasks_to_pick_up` | `count_my_tasks` | `list_tasks_all` |
  |---|---|---|---|---|
  | E | 97 | 25 | 30 | 123 |
  | own+D | 90 | 38 | 28 | 117 |
  | own | 95 | 24 | 31 | 122 |

  The first runs (up to 228 ms for a sorter's list, and `list_tasks_all` at 253 ms on a
  slow runner) showed two costs, both fixed: the names around a task (three user joins
  before the sort, now fetched for the shown rows only) and the waiting-letter count (an
  OR the policy had to check on every letter, now two counts on partial indexes, with the
  new `documents_document_review`). No `visible_documents` hint was needed.

## 2026-11-21: Wie ziet wat, slice A2b (private letters, departments, emergency access)

Plan `docs/superpowers/plans/2026-11-20-host-wie-ziet-wat.md`, Tasks 24-34 (merge point
M3). "Iedereen ziet alle post" stays on everywhere until Task 40, so departments only sort.

- **Private letters and shares (Task 24, PB T7/T8).** `documents.visibility` is the one
  writer of visibility, shares and readers: `make_private` (history line, then shares and
  readers as the system, then the flip as the current actor), `make_public`, `set_shares`
  (on every letter, R44), `keep_own_access`, the grants and `forget_member`. A letter an
  automatic action would make private without any reader stays visible: a
  `private_skipped` line (its kind is the explanation; no stored sentence, so it follows
  the reader's language) and one admin notice per letter. `keep_own_access` wraps the
  `addressee_scope` in the services (addressees, undo, confirm), because the readers are
  recomputed only at the scope's end; it answers 409 `would_lose_access` for a private
  letter only. "Reads private post" counts reader rows on private letters only: a share of
  a letter that is not private is no private post.
- **Departments (Task 25, PB T11, S 4.5).** `addDepartmentMember`, `updateDepartmentMember`
  (the lead, audit `department_lead_changed`) and `removeDepartmentMember` are admin and
  web-session only (they administer people). Only an admin makes a department
  (`createPerson` with `kind` "department"; a member gets 403); `kind` is read on create
  only. A department with letters is in use; an empty one is deleted with its
  memberships (S 4.5 over PB's "in use while it has members"). Name matching and the AI
  are offered departments only while departments are on; an archived department is not
  offered and cannot be newly put on a letter, its members keep its letters.
- **Labels of a department (Task 26, S 4.6).** `Label.department` (nullable, `SET_NULL`).
  `listLabels` leaves out, for a member who is no admin, the labels of departments they
  are not in while departments are on; a label on a letter they see still comes with it.
  Creating, renaming, moving or deleting a department's label needs an admin or a lead of
  that department (403 `forbidden`); members keep managing labels for everyone.
  `updateLabel` moves a label only when `department_id` is sent, so an older client never
  moves one.
- **Linking is a grant; removing a member (Task 27, PB T12/T13).** Linking a person needs
  an admin, never to the admin's own account when the person has private post (409
  `cannot_grant_self`); a linked person is unlinked or relinked only by its own member
  (409 `cannot_unlink`; `linkPersonToMember` is now open to every role, the service
  decides), or by removing the member. Every link or unlink recomputes the readers and
  writes `access_granted` on the person's private letters. Removing a member removes
  their reader rows, shares and department memberships (`access.on_member_removed`); an
  invitation accepted for a person makes the new member its reader with the inviting
  admin as the grant's actor, and one for a person with private post needs an address.
  The removal itself is not wrapped in `system_actor()`: every step that must reach
  letters the admin cannot see (releasing tasks, forgetting the member) runs as the system
  on its own.
- **Account actions follow who reads which post (Task 28, S 4.19; implements "6. Reset
  links" above).** A workspace admin makes a reset link, ends sessions or lists and
  revokes devices of a member only when the member belongs to that workspace alone and is
  no server admin, reads no private letter there (emergency rows do not count), and the
  admin reads all post there that is not private ("Iedereen ziet alle post", or
  "Beheerders lezen alle post"). Otherwise 403 `server_admin_only` ("Dit kan alleen de
  serverbeheerder."). A link made before the member became a reader stops working (the
  rule is checked again at completion). `listMembers.can_manage_account` applies the
  same conditions with one query for the readers.
- **Zorg and the emergency-access switch (Task 29, PB T15).** `care_is_private` is a
  workspace setting (`bool | None = None` in `updateWorkspaceSettings`, so an older client
  never changes it; a change is audited and changes no existing letter).
  `setEmergencyAccess` is the server admin's own operation (web session, no membership
  needed, 403 `forbidden` for anyone else): audit `emergency_access_switched_on`/`_off` and
  a notice to every member; the same value writes nothing. Both are shown to every member
  in `getWorkspaceSettings`.
- **Private by hand, by Zorg and at confirm (Task 30, PB T17/T18).** The Zorg label
  arriving (by hand, by accepting it, by a rule, by the autopilot) makes a letter private
  where `care_is_private` is on; a proposal of it does not. `setDocumentVisibility`:
  `restricted` makes a letter private (or, already private, sets its shares);
  `workspace` makes it visible again and then sets its shares to exactly the list sent,
  on any letter (R44: an empty list removes them). `ConfirmIn.visibility` ("" leaves it)
  runs inside the confirm's write order, before the rules; a refused `workspace`
  (403 `cannot_make_public`) stores nothing. Lists and the letter carry `visibility` and
  `reader_names`; search, the trash and duplicate pairs carry `visibility` only.
- **Rules and private letters (Task 32, PB T10/T19).** An automatic rule never widens a
  private letter's readers: its addressee action becomes a proposal there (the engine
  knows `DocumentFacts.restricted`, the executor checks the letter again), which only the
  letter's readers see; labels are still added. "Privé maken" is a new action type in
  format version 1 (no version bump; an older server reads such a rule as unreadable and
  skips it whole), only in a rule with mode `auto` (422 otherwise, also when such a rule
  is switched to proposing). It makes the letter private as the system with the current
  user as a reader: the confirmer at confirm, the starter of a manual run. With nobody to
  read it, nothing changes and no run is recorded (ruling d). The web offers it only
  while "Automatisch toepassen" is on and sends an action type it does not know back
  unchanged.
- **Emergency access (Task 33, PB T20, S 4.7).** `openEmergencyAccess` (admin, web session)
  opens for one hour every letter on the person's or department's name the admin cannot
  see at that moment, private or not (the set is asked of `viewers()`, so letters the
  admin already sees are not counted or touched). It is read-only everywhere a letter or
  its task changes: organising, sharing and visibility, confirm, reopen, retry (never
  sent to the AI again), trash, merge, and completing, reopening, dating or assigning a
  task answer 403 `emergency_read_only`. Whether someone sees a letter only through
  emergency access is asked by taking their emergency rows away inside a savepoint that is
  rolled back and running the policy's condition (`viewers`), so the answer is the
  policy's own. The notice to the people concerned now says "brieven" (not only private
  ones).
- **The back-up check knows the visibility rules (Task 34, PB T21).** `verify_restore`
  also checks, in its one read transaction, that the six helper functions exist, that the
  letter table and every table with a letter key (the task table included) have their
  three restrictive visibility policies, and that the reader table has `reader_scope`. A
  back-up from before `workspaces.0007_visibility_functions` is not checked for them. A
  problem names a table, never a person or a letter. **Merge point M3:** private letters,
  shares, departments, department labels and emergency access are in; every workspace
  still has "Iedereen ziet alle post" on, so departments only sort.
- **Still open: the letter policy arms do not check membership** (recorded above with
  Task 20). No task of this slice changes the policy; removing a member now also removes
  their reader rows, shares and department memberships (Task 27), which closes the
  reader-row arm in practice. Closing it inside the policy stays the owner's call (a
  policy migration and a new measurement).

### Security review of PR #38 (2026-11-21)

- **The autopilot never widens a private letter's readers (finding 1).** On a letter that
  is private (read from the database before every addressee: a rule of the same run may
  have made it private, and a letter processed again may be private already) an AI or
  name-match addressee stays a proposal. So does it when an open Zorg proposal could be
  applied in the same run where Zorg makes letters private: addressees are applied before
  labels, and a rule's Zorg proposal counts even though `rule_labels_to_apply` decides
  only later (the autopilot blocks rather than guesses).
- **Emergency access and the trash (finding 2).** A letter in the trash that the admin
  sees through emergency access is listed in their trash read-only, as everywhere else
  (the trash has the seam's rights, S 4.10); restoring it or purging it now answers 403
  `emergency_read_only`, and emptying the trash leaves it where it is.
- **Names that steer post are an admin's (finding 3).** `updatePerson` on a department,
  or on a person linked to an account (the member's own included), needs an admin (403
  `forbidden`). A name or variant decides through name matching, and with the autopilot
  on through applying, whose post a letter becomes; a member adding "Belastingdienst" to
  their own person would steer that post to themselves once "Iedereen ziet alle post" is
  off. Members keep creating and editing persons without an account (that sends post to
  whoever sorts it, not to themselves). Cost: a member asks an admin to correct the
  spelling of their own name.
- **Off ends the hour (finding 4).** Switching emergency access off removes every running
  emergency reader row of the workspace in the same transaction and releases any
  assignment that rested on it.
- **No permission parameter fails open (finding 3, follow-up).** `actor_role` of
  `create_person`, `update_person` and `link_member` and the label services' membership
  are required arguments; a call on nobody's behalf states it (`ACTING_AS_SYSTEM`, or
  `None` for the label services). A caller that forgets its role does not compile under
  mypy instead of acting as an admin.
- **The "nobody could read it" notice names the letter only to who may see it (finding
  5).** Admins who see the letter (it stays visible by the switches) get
  `private_without_readers` with its id. When no admin sees it, every admin gets the new
  `private_without_readers_unnamed`: that it happened in the workspace, no id; its readers
  see the `private_skipped` line on the letter itself.
- **Emergency access never makes an assignee (finding 6).** Assignment candidates and the
  release ask `visibility.regular_viewers` (sight without emergency rows, the policy's own
  condition with those rows rolled back), so a task is never given to whoever holds
  emergency access, and any assignment that rested on it is released by the next release
  (the nightly check, switching emergency access off). The same helper decides what an
  emergency opening counts as already seen (an hour running is renewed) and which admins
  the "nobody could read it" notice may name.

## 2026-11-21: Wie ziet wat, slice A2c (switching safely)

Plan `docs/superpowers/plans/2026-11-20-host-wie-ziet-wat.md`, Tasks 35-41 (merge point
M4). From here on "Iedereen ziet alle post" can be switched off, and an Organisatie
installation starts in own post.

- **The rule as data (Task 35, S 4.4).** `apps.workspaces.visibility_rules` is pure (no
  Django, like the rules engine): a `PolicySnapshot` (switches, members with role, sorting
  flag, persons and departments, the names of persons and departments) and per member the
  kinds of post they see: `all`, `all_as_admin`, `person:<id>`, `department:<id>`,
  `unassigned`. `all` and `all_as_admin` hold every other kind, so a member who sees all
  gains nothing by a department and loses only the whole. `diff` says who gains and who
  loses; `expected_sight` states S 4.1 per letter for the tests. The table of S 4.4 is test
  data (`tests/visibility_transitions.py`), each row with its gains, losses and
  confirmation written from the table's words, in every state where it applies.
- **R34: post nobody reads goes to the sorters.** A letter with no person with an account
  and no department with members (departments counting only while on) goes to the flagged
  sorters, or to the admins when nobody is flagged; never to everyone, never to nobody.
- **R35: departments switched off are kept.** Members, leads, labels, links and rules stay;
  for sight a department then counts as nobody, so a letter only on departments goes to the
  sorters; private letters keep their readers.
- **R37: confirmation when anyone gains.** A change in which anybody gains sight needs
  `confirmed` and the preview's token; a narrowing needs neither. Admins gaining all post
  tells every member.
- **P6: a sorter's fallback gain needs no confirmation after a narrowing.** An unlink, a
  department member removed, a member removed, a demotion or an unflagged sorter sends post
  nobody reads any more to the sorters; when every gain is such post going to a member who
  sorted already, nobody confirms. When the admins become the sorters (the last flagged
  sorter goes) and do not read all post, they gain new post: confirm ("yes when an admin
  gains").
- **P7: the last reader leaving sends the post to the sorters.** Removing the last member
  of a department, or unlinking the last account of a person, makes its letters post
  nobody reads: the sorters get them, with a `sight_widened` notice, though S 4.4's row
  says "Gains: nobody".
- **Changing the switches (Task 37, S 4.4, 4.13).** `apps.workspaces.visibility_settings`:
  `change` (admin by the locked memberships, then the workspace row), `preview` (any
  change of sight inside a savepoint that is rolled back, with confirmations and notices
  left out), `require_confirmation`, `notify_changes`, and `sight_change`, the steps every
  writer of sight runs around its own write: snapshot, write, snapshot, diff, confirm,
  notices, and the release of every assignment whose assignee lost sight (switching off
  ends what ran under it). The preview token is the SHA-256 of the switches and every
  member's role, flag, persons and departments; a confirmation with another token is 409
  `preview_outdated`, so it never covers another admin's change. The preview's letter
  count groups alike letters (ruling M0) and asks letters with reader rows one by one,
  private letters included (a member change can change their readers).
  `lock_memberships` moved to the leaf module `workspaces.locks` (people's writers take it
  too, Task 38).
- **P9: every member reads the switches.** `getVisibility` answers any member with the
  switches, who sorts post and their own "Wie kan mijn post zien?" sentences; those
  sentences state the switches, so hiding the booleans protects nothing. An admin also
  gets `summary`, the sentences of "Zo is Brievie nu ingesteld" (an added field: the head
  of the "Wie ziet wat" page needs them, the member sentences say something else).
- **P10: notices name the kind of post.** `sight_widened` and `sight_narrowed` carry
  `category` (`all`, `all_as_admin`, `department`, `person`, `unassigned`) and the
  department's or person's name (a directory, like department names; no letter content).
  One notice per member and change, for the broadest kind. `admins_read_all_on` goes to
  every member when admins gain all post. The sentence follows the category
  (`accounts.notices.CATEGORY_MESSAGES`). A member who left is not told.
- **The preset switch.** `apply_preset` takes the target kind's values (`presets.PRESETS`)
  for every value the request leaves out, except emergency access (the server admin's);
  one `workspace_setting_changed` row per value that really changed, `kind` included, and
  one `workspace_preset_applied`. `add_start_set` adds the kind's labels and rules and
  removes nothing.
- **Every member change through the same steps (Task 38, S 4.4, 4.7).** `change_role`,
  `remove_member`, the new `set_sorts_post` ("Verdeelt post", `updateMemberSortsPost`,
  422 `viewer_cannot_sort`, audit `post_sorter_changed`), `add_department_member`,
  `remove_department_member` and `link_member` take `lock_memberships` first (people's
  writers too, instead of the caller's own membership: every change of sight in one lock
  order, memberships before persons) and run `sight_change`. Making a sorter a viewer
  clears the flag in the same transaction (with its own `post_sorter_changed` row). A
  self-grant (an admin adding themselves to a department, linking themselves, flagging
  themselves) tells the department's other members or the other admins (`self_grant`);
  the audit rows stay the usual ones.
- **Removing a member never asks for a confirmation.** S 4.4's row says "no", also when
  the member was the only flagged sorter and the admins become the sorters; the sorters
  who gain are told, the member who left is not. `previewVisibilityChange` with
  `member_change` "remove" runs the removal's sight-moving part only (unlink, forget,
  membership gone), not the account's deactivation.
- **Unlinking and leaving a department end the member's own export.** Their export held
  that person's letters and the department's private letters; it goes with the sight
  (switching something off ends what ran under it).
- **Accepting an invitation for a person asks nothing and tells nobody.** The admin
  confirmed when making the invitation; taking `lock_memberships` there would invert the
  lock order (invitation before memberships). Cost: the sorters are not told that a
  person's post is the new member's now.
- **Confirmation fields on old endpoints.** `updateMemberRole`, `linkPersonToMember` and
  `addDepartmentMember` take `confirmed` and `preview_token` (defaults `false`, `""`): an
  older client keeps working wherever nobody gains, and gets 409
  `confirmation_required` where somebody would. The web sends both as defaults until its
  preview dialog (Task 42); in an organisation a link or a department member then answers
  with the 409's sentence. `MemberOut` gains `sorts_post` and `department_ids`.
- **R40: one choice at installation, P8 ended (Task 40).** `create_workspace` gives a new
  workspace its kind's values through `apps.workspaces.presets.apply_preset_settings`:
  Thuis E on, D off, A on, Zorg private, no emergency access, own export allowed;
  Organisatie E off, D on, A off, Zorg not private, emergency access on, no own export. An
  Organisatie installation now starts in own post (P8 is over: every leak path was closed
  in Tasks 12-34). Existing workspaces keep what they have. `setupServer` is unchanged and
  still accepts `extra_labels`.
- **A second workspace on the command line (S 5.2).** `manage.py create_workspace --name
  --kind home|business --admin-email`: with an account for that address it becomes the
  admin; without one the workspace has no member yet and its first admin is invited
  (`Invitation` role admin, for that address only, 7 days, `created_by` empty,
  "Serverbeheerder"), the link printed once. An invitation without a maker is usable only
  as long as the workspace has no admin at all, so a maker-less invitation (a deleted
  account's) never revives elsewhere. Server audit `workspace_created` (`kind`), actor the
  system. An unknown kind: exit code 2 with a Dutch sentence. The link uses the first
  `BRIEVIE_PUBLIC_URLS` entry (a path only without one).
- **R41: a preset is a starting point.** Switching the kind later goes through
  `updateVisibility` with `apply_preset` (Task 37); labels and rules are never removed.
- **Who can see my post, and this letter (Task 41, S 4.15).** `getMyPostAccess` answers
  any member (also with the app) with the sentences of "Wie kan mijn post zien?" for
  them; `getVisibility` gives the same sentences beside the switches. `getDocumentAccess`
  answers, for a letter the caller sees (through the seam; 404 `document_not_found` like
  an unknown id otherwise, a case in the leak suite), every viewer by name with the
  reasons that hold (`documents.visibility.viewer_reasons`, the S 4.1 arms on the snapshot
  and the reader rows): "op naam van", "afdeling", "verdeelt post", "heeft de brief
  gescand", "is gedeeld", "ziet alle post", "beheerder, leest alle post", "noodtoegang tot
  14:30" (Dutch time). A member who sees the letter learns who else does: that is the
  page's purpose. Merge point M4.

### Security review of PR #39 (2026-11-21)

- **A confirmation token is bound to its change (finding 2).** The preview token was the
  SHA-256 of the switches and the members' facts, which a member can work out from
  `listMembers` and `getVisibility`, and a preview of a narrowing then confirmed a
  widening. It is now an HMAC (`django.utils.crypto.salted_hmac`, the key derived from
  `SECRET_KEY` with a fixed purpose label, no new setting) over the workspace, the admin
  who previewed, the change itself (the proposal, or the member change with only the
  fields its kind uses) and the state's fingerprint. Another proposal, another admin,
  another workspace or a changed state: 409 `preview_outdated`.
- **An invitation moves sight like a member change (finding 1).** `createInvitation` with
  role admin takes the memberships' lock (before the invitation, the lock order) and,
  when the admin role would give the newcomer more sight than the same invitation as a
  member (all post while "Beheerders lezen alle post" is on, or the unnamed post as a
  fallback sorter while nobody is flagged), needs the confirmed preview of
  `member_change` "invite_admin" (`confirmed`, `preview_token`, defaults `false`, `""`;
  the preview lets a placeholder join as a member and shows what making it admin
  changes, all rolled back; the token is over the state without the placeholder). A
  Thuis workspace never asks. Consequence: in a fresh Organisatie (admins do not read
  all post, nobody flagged) every admin invitation needs the confirmation, as a
  promotion to admin does since Task 38; the web answers 409 there until its dialog
  (Task 42).
  Accepting an invitation now tells, inside its transaction, everybody whose sight changed
  at that moment (`admins_read_all_on` to every member when an admin joins while admins
  read all post; `sight_narrowed` to the sorters who no longer get the person's post);
  the newcomer is not told. This replaces "accepting an invitation tells nobody" above;
  still no memberships lock there. `on_member_added` releases the sorters' assignments on
  the person's letters at once, and `test_task_release_callers` now checks per function
  that every writer of sight releases (or runs `sight_change`). (A renewed admin
  invitation asked nothing again; replaced in slice A2d below.)
- **The people writers use the locked role (finding 3).** `link_member` and the
  department writers (`add_`, `remove_`, `update_department_member`) read the acting
  member's role from the memberships they lock, not from the endpoint's earlier read: an
  admin demoted in between gets 403 `forbidden`. `actor_role` of `link_member` counts only
  for a call without a user, and only `ACTING_AS_SYSTEM` with a system actor is the
  system; anything else without a user acts with no rights.
- **The preview counts only what its admin can read (finding 4).** `letter_count` counted,
  as the system, every letter whose viewers change, private ones included: an admin who
  does not read all post learned exact counts per person or department by previewing a
  link or a department member. Now it counts only letters the previewing admin can read
  through the policy at that moment (for one who reads all post: every letter that is not
  private, plus the private ones they read). The count says "letters you can read whose
  viewers change"; it may be 0 where others' post moves.
- **"Wie kan mijn post zien?" says only what is true (finding 5).** "Je post zien alleen
  jij ..." left out other addressees, the scanner until the letter is done and shares;
  "Privé-post zie alleen jij ..." left out the members of a department a private letter
  is on, and who scanned it or made it private (they keep a share). The sentences now
  list exactly those (Thuis: "groep"), and `tests/test_policy_text_truth.py` checks in all
  16 states that every viewer `viewers()` gives for a member's letter is someone their
  sentences name. S 4.15's example sentences are amended by these.
- **Presets and `create_workspace` widen nobody's sight (checked with the review).** The
  kind's values are applied only to a new workspace (no member but its first admin, or
  none); a later preset switch goes through `updateVisibility` (preview, confirmation,
  notices); the migration left existing workspaces as they were.

## 2026-11-21: Wie ziet wat, slice A2d (the web)

Plan `docs/superpowers/plans/2026-11-20-host-wie-ziet-wat.md`, Tasks 42-47 (merge point
M5), and two gaps the server still had.

- **An invitation for a person is confirmed when it is made (S 4.4).** The table's row
  "person linked to a member" says confirm "yes (the link dialog; an accepted invitation
  was confirmed when it was made)", but `createInvitation` with a `person_id` asked
  nothing. Now every invitation is compared with a plain member invitation without a
  person (`visibility_settings.invitation_changes`, pure): the admin role adds all post
  while admins read all post, or the unnamed post as a fallback sorter; a person adds
  that person's post (in own post, not in Thuis). When the newcomer gains, it needs the
  confirmed preview of the new `member_change` "invite" with `member_role` and
  `member_person_id` (409 `confirmation_required`, `preview_outdated`); the token is over
  the role and the person, so a preview for Kees never confirms an invitation for Jan.
  "invite_admin" stays and is the same change as "invite" with role admin and no person.
  The preview lets a placeholder ("Nieuw lid" or "Nieuwe beheerder") join and links the
  person as accepting would, all rolled back. A linked or archived person answers its
  usual 404/409 at preview and at creation, before any question. Accepting still asks
  nothing. Cost if wrong: an admin confirms one extra dialog per person invitation in an
  Organisatie.
- **A renewal asks again when it widens at that moment.** `renewInvitation` re-issues the
  link with the role and person it had, so it goes through the same check with the
  state of now: an admin invitation made while admins did not read all post, renewed
  after "Beheerders lezen alle post" went on, needs the confirmation. Its body
  (`RenewInvitationIn`: `confirmed`, `preview_token`) is optional, so a client that sends
  none keeps working wherever nobody gains. The invitation is read without a lock to learn
  its role and person, then the memberships are locked, then the invitation (the lock
  order). This replaces "a renewed admin invitation asks nothing again".

- **One way for every change of sight in the web (Task 42).** `useSightChange` serves the
  switches, the kind, roles, sorters, links, department members and invitations. The
  switches and the kind preview first (the admin also sees who sees less); member changes
  write at once and preview only when the server answers 409 `confirmation_required`, so
  nothing asks where nobody gains. The preview and the write send the same fields (the
  token binds them); in the preset dialog an unticked value is sent with its current value,
  because a value left out takes the preset's. A 409 `preview_outdated` makes the preview
  again and shows the server's sentence.
- **Leaving a department asks nothing (Task 43).** Its post falls to whoever sorts already
  (P6), so the web removes a department member without a preview.
- **The hand-over sentence sits on the letter's addressee picker (Task 44).** The web has no
  confirm step (that is the app's), so "Na opslaan kun je deze brief niet meer zien. Hij
  gaat naar Jan." (or "Na toewijzen ziet HR ...") is asked before an addressee change on the
  letter or in the Inbox that takes the letter out of your sight. It is the web's own
  estimate from the switches, your person, your departments and the sorters; it does not
  know the scanner or shares, so it can warn where you keep the letter. The server's
  `can_read` decides: false returns to the Inbox with "Je kunt deze brief niet meer openen."
  The Inbox chips (Voor mij, your departments, Zonder naam) show only while not everyone
  sees all post.
- **Words (Tasks 45-47).** The role `viewer` is "Alleen lezen" everywhere (R29); "Zakelijk"
  is "Organisatie" on every screen. PB's "Voor iedereen zichtbaar maken" is "Niet meer
  privé": in own post a letter that is no longer private is not seen by everyone. The
  notice codes of this part are shown with the server's own sentence (no text in the web).
- **Privé settings live on Instellingen (Task 46).** "Zorg-post is privé" for admins and
  emergency access (a switch for the server admin, a sentence for others) in the section
  "Privé", linked from "Wie ziet wat".
- **Installation (Task 47).** Setup asks no departments: they are made on Afdelingen
  afterwards. The typed client still sends `extra_labels: []` (the field is required in the
  generated types; empty means none). "Zo is Brievie nu ingesteld" lists the next steps;
  "Back-up instellen" is a server command (`./brievie backup --init`), not a link.
  **Merge point M5.**

### Security review of PR #40 (2026-11-21)

- **Making a letter private keeps only the shares sent (point 1).** `setDocumentVisibility`
  with `restricted` on a letter that is not private now sets its shares to exactly those
  sent, besides the scanner and the caller (who stay readers, as the dialog says); an
  earlier share not sent again ends with a `share_removed` line. The dialog starts from
  the current shares and names the scanner and the members of the letter's departments.
  Cost: an older client that makes a shared letter private with an empty list ends those
  shares, which is what it asked for.
- **Lock order of the invitation writers (point 2):** memberships, then the invitation,
  then the person (accepting: the invitation, the account, the person). The confirmation
  check of a person invitation reads the person without a lock; `_issue` locks it.
- **A department is never invited (point 3).** `invitable_person` and `_link_person` take
  `kind` person only (404 `person_not_found`); the dialog leaves departments out. Accepting
  such an invitation would have failed with a server error (a department has no account),
  not linked the department as the review assumed.
- **A change of sight refreshes the workspace's cache (point 4).** `useSightChange` and the
  Afdelingen dialogs invalidate everything under `["workspaces", id]`. Wie ziet wat already
  did through the workspaces list key, a prefix of every workspace query.
- **Texts (point 5).** A department's label is offered to its members and the admins; whoever
  sees a letter with it sees the label. Admins who do not read all post still get unnamed
  post while nobody sorts it, and other post through emergency access.

## 2026-11-21: Mensen (one list of people, each with an account state)

Spec `docs/superpowers/specs/2026-11-21-mensen-en-accounts-design.md`, plan
`docs/superpowers/plans/2026-11-21-host-mensen-en-accounts.md`.

### The owner's answers to "Besluiten voor Stijn"

- **B1: an inactive account is a person without a login** (the advised option, spec M1). No
  `User` and no `Membership` exist before activation, so nobody's sight of any letter
  changes. In the interface the user sees no difference: everyone is under Mensen with a
  status.
- **B2: "Beheerd door" is built now**, as the last part (slice C, plan Tasks 11–15).
- **B3: the AI may propose an unknown name as a new person now.** Only as a proposal (a
  `DocumentSuggestion` of its own kind), never created automatically, never repeated once
  dismissed, behind the same consent and BSN rules as every other AI output (see "Mensen,
  the AI proposes a new person" below).
- **B4: ordinary members see name, state (active or not) and departments**; address, last
  sign-in and invitations only admins (spec M5).
- **B5: every non-archived person gets a scan subfolder**, inactive ones too. This belongs
  to the intake plan (`docs/intake-and-2fa-design`); that branch is not touched here, and
  whoever builds it applies the ruling.
- Also decided by the owner: "Mensen" is its own item in the main menu, replacing Leden and
  Personen; on the row of anyone who never signed in "Uitnodigen"; for a pending invitation
  "Opnieuw versturen" and "Intrekken". A navigation redesign (`docs/navigation-design`)
  moves menu items later, so "Mensen" sits where Leden and Personen were.

### Slice A: the server (plan Tasks 1–5)

- **Rulings M1–M5, M7 of the spec hold.** The account slot is the Person row (M1);
  departments stay `kind = department` and are never in `listPeople` (M2); activation goes
  only through `createInvitation` → `acceptInvitation` (M3, a test lists every writer of
  `Person.user`); `Person.former_user` and `deactivated_at` are display only, read by no
  visibility, lock or permission code (M4, a test reads `visibility_sql` for them); account
  facts are for admins (M5); nothing automatic makes a person (M7).
- **New audit code `person_created`** (target person, no values), written by `createPerson`
  for a person and a department alike.
- **Switched off is a mark, not a state of sight.** Removing a member or leaving sets
  `deactivated_at` and `former_user` in the same statement that unlinks the person;
  accepting an invitation for the person (and the old `linkPersonToMember`) clears both. A
  member who unlinks themselves leaves a person "niet actief", not switched off. — Why:
  only a removal ends an account. — Cost if wrong: a self-unlinked person shows "Nooit
  ingelogd" instead of the former account's sign-in.
- **M8, every member keeps exactly one person, is maintained.** An unlink by
  `linkPersonToMember` gives the former member a fresh person at once, after the change of
  sight is settled (`sight_change` has already diffed and told): a fresh person is on no
  letter, so it gains nobody anything, and inside the block it would have counted as a gain
  and asked a confirmation for a self-unlink. — Cost if wrong: none for sight; the member
  has a second name ("Anne (2)") until an admin renames or archives one.
- **Relinking takes the member's unused own person away.** With M8 every member has their
  own person, so a link of a member to another person would always answer 409
  `member_already_linked` (a self-unlink could never be undone). Now the member's own
  person makes way when it looks exactly as Brievie made it (the member's name, or
  "name (2)", at most that name as variant) and nothing uses it (no letter, also in the
  trash, no proposal, no rule, no open invitation): it is deleted inside the change of
  sight (`person_deleted` in the log). Any other person stays and the link is refused as
  before. — Why: undoing an unlink must work; a person somebody named by hand is never
  deleted behind the admin's back. — Cost if wrong: an unused auto-made person is gone; it
  held nothing.
- **The post of a person made or deleted by a change counts for nobody** (`visibility_rules
  .diff`). A person that exists in only one of the two snapshots of a change of sight is on
  no letter (a person on a letter is never deleted, a new one is on none yet), so the
  fresh person of an unlink and the deleted one of a relink are no gain or loss: no
  confirmation, no notice, nothing in the preview. A person in both snapshots counts as
  before, also one without letters (its future post is a kind of post). — Cost if wrong:
  none for letters; only the wording of a change.
- **Archiving a person withdraws its open invitations** (`invitation_revoked`, reason
  `person_archived`). Lock order of `updatePerson` when it archives: the person's open
  invitations first, then the person (memberships, invitations, person: as renewing and
  accepting take them). — Cost if wrong: none; a deadlock would be the alternative.
- **An invitation without a person never reuses a switched-off person.** A former member
  invited without a person gets a fresh person, as before: linking their old one would give
  them its post (private letters included) without the confirmed preview of an invitation
  with that person. "Opnieuw uitnodigen" on Mensen always sends the person.
- **`listPeople`** (`GET /workspaces/{id}/people`, web session, every member) and
  **`getPersonHasPrivatePost`** (`GET /workspaces/{id}/persons/{person_id}/private-post`, web
  session, admins, a boolean only). The list never carries a private-post field (PR #39
  findings 4 and 5). For somebody who is no admin `state` is `active` or `inactive` and the
  filter accepts only those two (422 otherwise); role and "Verdeelt post" are left empty for
  them too (B4 names only name, state and departments).
- **Last sign-in** is `User.last_login` (set by `start_session` through Django's signal);
  **last app use** the newest `last_seen_at` of an unrevoked device. A switched-off person
  shows the former account's; both are the account's, the same in every workspace.
- **Measured, not assumed:** `listPeople` runs a constant number of queries whatever the
  number of people (test with 8 more people and invitations).

### Slice B: the web (plan Tasks 6–10)

- **Tasks 6, 7 and 8 are one commit.** The row actions and the person's page share
  `components/person-actions.tsx`; built apart the steps would not have type-checked.
- **One query, filtered in the browser.** Mensen asks `listPeople` once (no `state`) and
  filters on status and name locally, so the filter is instant and the counts agree; the
  server's `state` filter stays for other clients. — Cost if wrong: none below the 500
  persons a workspace may have.
- **"Verdeelt post" stays a switch on the row** for admins (as on Leden), and is on the
  person's page too; both go through the preview (`useSightChange`).
- **Invitations without a person stay in their own panel** ("Open uitnodigingen") under
  the list; an invitation with a person shows on that person's row.
- **"Uitschakelen" asks `getPersonHasPrivatePost` when its dialog opens**, never for the
  list (PR #39 findings 4 and 5). The invite dialog for a person asks it too, to say the
  address is required before the server answers `email_required`; that sentence is shown at
  the address field.
- **Linking a person to a member is not offered on Mensen** (M3: an account comes only by
  invitation); `linkPersonToMember` stays for old clients.
- **Departments while departments are off.** They are not people (M2), so Mensen never
  lists them; the old Personen page showed them as names while departments were off.
  Switching departments on shows them on Afdelingen again. — Cost if wrong: an admin who
  wants to rename such a name switches departments on for a moment.
- **"Nieuwe persoon" is a button beside the picker, not an option in its list.** The
  picker is a select without free text; the button opens a small dialog with a name field
  that offers a person who already has the name or writes it so first ("Bedoel je ...?
  (ook: ...)"). Names compare as the server's `normalise` (`web/src/names.ts`).
- **Words** in `docs/GLOSSARY.md`: Mensen/People and the account states; "persoon" stays
  "person". `docs/SPEC.md` names Mensen where Leden stood.

### Mensen, the AI proposes a new person (Besluit B3, plan Tasks B3a and B3b)

- **Its own table, `documents.NewPersonSuggestion`.** CLAUDE.md's "A label or addressee
  proposal is a `DocumentSuggestion`" stays true: this proposes to *make* a person, not to
  put an existing one on a letter. In `DocumentSuggestion` it would have needed a third
  action with neither label nor person, which `SuggestionOut` (read by the iPhone app),
  the autopilot, the review and `target_id_of` cannot read; in its own table none of them
  can ever apply or show it (M7 by construction). Same shape: one row per name per
  letter, `dismissed` is the letter's memory of "no", nothing automatic repeats it.
- **What the AI's name must be.** In the letter itself (the name, not only its quote),
  no BSN (raw or masked), 2–100 characters, at most three per letter, confidence capped at
  0.8 like every pick. Known is any person or department of the workspace, archived ones
  included (a name taken there would be refused on accept), not only the 50 the AI sees.
- **An open new-person proposal stops the autopilot's confirm**, like an open duplicate
  pair: the AI itself says the letter is for somebody nobody knows. — Cost if wrong: such
  a letter waits for a person instead of being confirmed.
- **Accepting is a human's act by the normal paths.** `create_person` (its rules, the BSN
  refusal, `person_created` with the member as actor) and `organise.add_addressee` (origin
  suggestion) in one transaction, after the letter is locked for organising
  (`require_editable`). A person who has the name by now is taken instead of a second one.
  Members only (403 for read-only), device tokens as for the other proposals.
- **Consent and privacy as for every AI output.** The name comes only from a run under the
  recorded consent (`provider_for`); the text sent is BSN-masked; the stored name and quote
  are masked again; the row is readable only with its letter (child policy).

### Mensen, slice C: "Beheerd door" (plan Tasks 11–15)

- **A manager reads the post of a person without an active account as their own**
  (`Person.managed_by`, only kind person, only while `user_id` is null, only a member; a
  check constraint and `save()`). `brievie_my_post_ids` gains those persons and
  `brievie_covered_post_ids` counts them as read through, so the sorters stop getting that
  post. A private letter on the person gets a reader row with reason `managed`.
- **Migrations replay their own text.** `workspaces/0007` now reads the helpers frozen as
  they were (`visibility_functions_sql_0007`); `0009` replaces the two helpers in place
  (`CREATE OR REPLACE`: the policies call them) and its reverse restores the 0007 text, never
  a drop. Without a manager no letter changes viewers (migration test, all four switch
  combinations, both kinds of workspace).
- **The snapshot is the same rule as the SQL.** `MemberFacts.person_ids` holds the managed
  persons (`managed_ids` only for the wording "beheert de post van"), so previews, tokens,
  notices, `expected_sight` and "Wie kan deze brief zien?" agree with the helpers.
- **Setting it is a widening with a confirmation** (`setPersonManager`, member_change
  `manage`); never the admin themself while private post is on the person
  (`cannot_grant_self`); not a department, not a person with an account. Lock order:
  memberships, then the person.
- **M6: what ends it.** Accepting an invitation for the person (an adult with an account
  decides who reads their post) — the manager is told by `notify_join`, their export goes,
  `person_manager_cleared` is logged; the old link endpoint likewise; the manager leaving
  or being removed (P7, the post goes back to the sorters, logged per person). Switching
  it off ends what ran under it: the former manager's assignments there and their own
  export.
- **Audit codes** `person_manager_set` and `person_manager_cleared` (value `member`: the
  manager's name; a member is a person, so a name is allowed).
- **The web:** "Beheerd door" on the page of a person without an active account (admins),
  with the preview on a gain; the "Uitschakelen" dialog of a person with private post
  offers "Laat de post beheren door" and sets it, with its own preview, right after.
- **Measured:** the perf world manages 2 % of its persons (one of fifty, by a member in a
  department); `visibility-perf` stays within the M0 budget with no threshold raised (the
  numbers are in the run's summary of the PR's last commit).

### Security review of PR #43 (2026-11-21)

- **Point 1 (high), fixed: one way in per person.** A new invitation for a person revokes
  the earlier open ones (reason `replaced`); accepting revokes every other open invitation
  for the person, whatever its address (`superseded`, skip-locked as for the address);
  switching off (`member_removed`) and unlinking (`person_unlinked`) revoke every open
  invitation naming the person. Accepting refuses (the one answer of a dead link) an
  invitation without an address once private post is on the person: the rule of making one
  holds at accepting too. Lock order stays memberships, invitations, person (unlink locks
  the invitations before the person). Mensen shows an open invitation on any row ("Open
  uitnodiging", Intrekken), also an active person's. — Ruling: replace, not refuse, a
  second invitation: the admin's newest intent wins and "Opnieuw uitnodigen" keeps
  working; the older link is logged as revoked.
- **Point 2 (medium), fixed: no activity from elsewhere.** Sign-ins and devices are per
  account, not per workspace, so the list shows them only for an account whose only
  workspace this is; for any other `activity_hidden` is true and the times are null ("Niet
  te zien"). A switched-off person shows the address and times frozen at switch-off
  (`former_email`, `former_last_sign_in_at`, `former_last_app_use_at`); the former account
  is never read live. — Cost: an admin no longer sees when a member of two workspaces was
  last active; showing that "it is not shown" tells them the account is elsewhere too,
  which listMembers' `can_manage_account` already implied.
- **Point 3, fixed:** the switch-off warning says what stays true: the person no longer
  reads them, shares still do, an admin can open them with emergency access when it is on.
- **Point 4 (decided): managing private post needs its own yes.** With private post on the
  person, `setPersonManager` answers 409 `private_post_acknowledgement_required` until
  `private_post_acknowledged` is sent; the web asks "Privébrieven laten lezen?" naming the
  manager, apart from the preview. Not refused: after a switch-off it is the one way (with
  re-inviting) for anybody to read those letters, and every step is logged and told. Two
  admins naming each other stays possible, each act audited and notified (S 4.7 rules out
  only naming oneself). — Cost if wrong: an admin who agrees twice can read a former
  colleague's private post through a colleague; the audit log shows both acts.
- **Point 5, fixed generally:** every change of sight now removes the own export of
  everybody who loses sight (`sight_change`), not only in the writers that did it by hand.
- **Point 6 (decided): narrowed.** `getPersonHasPrivatePost` answers only for a person with
  an active account (409 `person_has_no_account` otherwise): the switch-off dialog needs it
  before the act; inviting relies on `email_required` and managing on its own refusal. Not
  removed: a warning after the switch-off would come too late. It tells an admin no more
  than `email_required` does for one invitation.
- **Point 7, fixed, not as proposed:** a `FOR UPDATE` on a person breaks the rule "documents
  and persons are locked without their key" (`test_privacy_locks`: a running upload must
  not wait on a person lock). Instead the deletion of the unused own person runs in a
  savepoint and looks again after the DELETE, which waits for a transaction that put the
  person on a letter meanwhile: a link or proposal found then undoes the deletion with a
  clean 409 `member_already_linked`. No test: it needs two transactions racing.
- **Re-review, point 7 again:** a proposal naming the person is deleted with it
  (`DocumentSuggestion.person` cascades), so looking after the DELETE could miss it. The
  per-model counts of `person.delete()` decide now: anything deleted besides the person
  (or a `ProtectedError`) undoes the deletion with 409 `member_already_linked`.
- **Re-review: deleting a person stops its open invitations** (reason `person_deleted`,
  locked after the rules and before the person): with the person gone an old link would
  have worked as a plain invitation with its role.
