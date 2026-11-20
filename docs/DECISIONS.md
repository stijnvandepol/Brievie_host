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

