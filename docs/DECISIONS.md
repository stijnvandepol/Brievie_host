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
  change (user, rule, accepted suggestion, undo). Deleting a whole label or person
  cascades without events.
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
2. The default back-up password `BRIEVIE_BACKUP_PASSWORD` is derived from the master key: `HKDF-Expand(SHA-256, length 32, info "brievie/backup-password/v1")`, hex (`apps.common.crypto.derive_backup_password`). HKDF-Expand only, since the master key is already 32 uniformly random bytes (RFC 5869 section 3.3); it equals `HMAC-SHA256(master key, info || 0x01)`, So the master key alone opens the back-ups. It is derived only inside the container, by `manage.py derive_backup_password` (`./brievie` appends its output to `deploy/.env`), never on the host: there the only tools are openssl, which takes an HMAC key only as a command-line argument that any local user can read from the process list, and python3, which a NAS may not have. A secret is never an argument of a started process: `init-env.sh` substitutes passwords with shell built-ins, and `01-roles.sh` and `ensure-backup-role.sh` give psql the passwords on stdin (`\set`). The label and the construction never change: existing back-ups depend on them.
3. **restic 0.18.1** (BSD-2-Clause) makes and checks the back-ups: encrypted, deduplicated, incremental, with folder, S3, SFTP and REST destinations in one tool. The image copies its static binary from the official image `restic/restic:0.18.1`, pinned by version and index digest (amd64 and arm64 both published, so a NAS on ARM works). At least 0.17 is needed (`--stdin-from-command`, exit code 12 for a wrong password, `RESTIC_REST_*`). Rejected (spec D 4.1): age or tar plus age (every back-up a full copy; deduplication, retention and verification would be ours), borg (comparable, but no S3 backend confirmed) and volume tarballs (stack stopped, unencrypted, tied to the PostgreSQL major).
4. **postgresql-client-18** from the PostgreSQL apt repository (PGDG; the signing key is pinned by checksum in the Dockerfile) gives `pg_dump` and `pg_restore` in the image. Debian's own client lags the server; `pg_dump` refuses a server newer than itself. The client major equals the server image's major (`postgres:18`); `test_pg_client_major_matches_the_server` compares compose, CI and the Dockerfile, so client and server move together in one change.
5. Two containers run the back-up code, from the same image (`BRIEVIE_IMAGE`). The **keeper** is always on and serves only the Procrastinate queue `backup`, as uid 10001. It holds the app role (queue, `BackupRun`), the read-only back-up role, the back-up password and the destination credentials, and mounts the documents and the CA copy read-only. It holds no master key, no superuser or owner password: a compromised keeper reads what it backs up but can neither decrypt documents nor change the schema. The **admin** container (compose profile `admin`, `run --rm --no-deps`, root) is one-off: set-up, restore and deriving the back-up password. It alone holds both the master key and the superuser password, and mounts every volume writable. Caddy copies its CA (keys included) to the `caddy-authority` volume for the back-up (`caddy-publish-root.sh`, files 0600 owned by 10001); the api and worker never mount it. The image creates `/data/authority`, `/data/backup-config` and `/backup` owned by 10001, so a back-up before Caddy's first certificate still finds its source paths.
6. **The storage sweep** (`sweep_storage [--delete]`, run as uid 10001 through `./brievie sweep`; run by hand in the admin container it runs as root) closes the "orphan files" gap; the earlier notes about it (phase 1b, "Known gap: orphan files" and the open item about a lost connection at commit) are hereby closed. It lists, and only with `--delete` removes, `<uuid>/<uuid>/<n>.page` and `.tmp-*` files that no `documents_documentpage.storage_path` names. The known paths are read as the back-up role with `row_security = off`, so a role that cannot bypass RLS fails instead of listing live pages. The files on disk are listed before the database is read, files younger than 24 hours are skipped (and checked again just before the unlink), deleting goes through directory descriptors with no symlink followed, directories are never removed, and when any stored path is not in the page layout `<uuid>/<uuid>/<n>.page` (or none matches a page file on disk) it refuses (`internal_error`) instead of calling everything an orphan. Output is counts and total size, never paths. It holds the back-up lock, so it never runs beside a back-up or restore. When the database knows no pages at all while page files exist on disk (an empty or wrong database, for instance a back-up URL that points at another server), the sweep refuses and lists nothing; there is no override flag. Old `.tmp-*` files alone are still listed then.
7. **Destructive functions carry their own path guard** (`apps/backup/safepaths.py`), after a test once ran an unguarded "empty this directory" against `/` on a developer's Mac. Every function that deletes, empties, overwrites or chowns judges its own path first, whoever calls it, and refuses with `unsafe_path` ("Brievie weigert deze map te wissen of te overschrijven, omdat dat niet veilig is."). `_empty` (restore `--replace`) and `delete_orphans` (the sweep) accept only the configured directories (storage root, authority copy, config folder; the sweep only the storage root), and refuse a relative path, a path with `..`, `/` or one directory below it, the home directory and its ancestors, a real folder inside a home directory, and any path that is a link or is reached through one (except a system link directly below `/`, such as macOS's `/var`). So the sweep still lists through a linked storage root but no longer deletes through one. `_place_authority` accepts only an absolute `…/caddy/pki/authorities/local` without links, and removes only the leaf directory `…/caddy/certificates/local` derived from it. The restore output, the back-up marker and the chowned repository tree must resolve strictly inside their folder or mount (chown never follows a link), and `write_private` writes only a plain file name. Testing rule: a guard is built and tested as a pure function first; a refusal test of a destructive function replaces its primitives (`os.scandir`, `os.unlink`, `shutil.rmtree`, `os.replace`, `os.chown`, `Path.unlink`, …) by recorders that fail when called; destructive code never runs against a real path outside the test's `tmp_path` or a container.
8. **A fixed allow-list, a clean-up after every restore, and deletion through descriptors** (second safety round; supersedes the "configured directories" rule of item 7). What may be emptied, restored into or swept no longer comes from the settings that name it: only `/data/documents`, `/data/authority` and `/data/backup-config` (the sweep only `/data/documents`; Caddy's authority only `/caddy-data/caddy/pki/authorities/local` and its leaf `/caddy-data/caddy/certificates/local`; `/backup` never), or, for tests only, a path strictly inside a temporary folder that is itself proven safe (not `/`, not a home directory or near one, not above a production path, at least three components unless `/tmp`). A directory must also be the configured one, contain no glob character, and not equal, contain or lie inside the back-up mount or another of the three. Home directories are found by inode from `$HOME` and the password database, and `/Users`, `/home` and root's home are refused in any spelling. Settings paths are absolute or refused, so an empty `BRIEVIE_BACKUP_MOUNT` is never the current directory. Every restore (with or without `--replace`) judges the three directories before restic starts; restic gets exactly those as includes, with `--` before the snapshot id (8-64 hex characters). Afterwards the three trees are walked through descriptors: links, devices, fifos and sockets are removed (`restore_files` reports the count in one line), setuid, setgid and sticky bits cleared, and as root everything goes to uid 10001; a file with more than one hard link keeps its owner. All deleting, writing and chowning (`_empty`, the authority and its leaf, the repository hand-over, the marker, `write_private`, the restore output) opens the directory component by component with `O_NOFOLLOW`, checks it is the directory that was judged, and works relative to directory descriptors (`safepaths.open_dir_nofollow`, `empty_dir`, `clean_tree`, `chown_tree`); nothing crosses into another file system. The authority is read from the restored copy without following a link, regular files of at most 64 KiB only. Behaviour changes: a custom `BRIEVIE_STORAGE_ROOT` (or authority or config folder) outside the fixed list is refused for `restore_files` and for `sweep --delete` (`directory_not_allowed`, with a Dutch sentence that names the fixed folders; a bind mount still appears as `/data/documents` in the container, so normal installations are unaffected); `restore_fetch --out` must be a new or empty folder that is no data or back-up volume; the restored `.env` keeps none of the back-up's host lines (`BRIEVIE_BACKUP_DIR`, the repository and its credentials, `BRIEVIE_IMAGE`, `COMPOSE_PROJECT_NAME`, the ports): `./brievie` sets them from the installation that restores, and `restore_fetch` names what it left out in one line on stderr; `init-env.sh` links a fresh `.env` into place (never over one made meanwhile) and refuses a `.env` that is a link. In tests every directory setting and the restic binary point into the test's `tmp_path` by default, and one shared `Recorder` makes any destructive primitive or process start fail a refusal test.
9. **Third safety round (image side).** The test exception of the path guards no longer trusts `TMPDIR`: production accepts only the fixed list, and the test suite installs its own `tmp_path` as the one test root (`safepaths.allow_test_root`, from the autouse fixture; refused for `/`, a home directory or anything at, in or above one, a root shallower than three components, or one overlapping a production volume). Restore-side restic reads (`snapshots`, `dump`, `restore`, and `cat config` when used for reading) take `--no-lock`, so a restore source may be mounted read-only; the admin container's `/backup` mount takes its mode from `BRIEVIE_BACKUP_MOUNT_MODE` (default `rw`, `./brievie` sets `ro` for the restore steps; the keeper's mount is unchanged). Before restic writes, the three target directories are walked through descriptors and a restore is refused (`restore_target_unsafe`, with or without `--replace`) when they hold a link, a special file or a file with more than one hard link; after the restore such a hard-linked file is removed and counted rather than kept with a foreign owner. Placing Caddy's authority happens on a fresh machine before Caddy first starts (a Caddy that started would make an authority of its own and every paired phone would have to pair again), so it makes exactly the chain `caddy/pki/authorities/local` below the existing volume `/caddy-data` and nothing else: each component through its parent's descriptor (0700, no link followed, an existing link or non-directory refused with `unsafe_path`); only a missing `/caddy-data` (the volume is not mounted) is refused with `caddy_not_ready`. The leaf directory `…/caddy/certificates/local` is removed only when it exists and is never made. The restored `.env` also drops every `COMPOSE_*` and `DOCKER_*` key, and `make` reads `export COMPOSE_PROJECT_NAME=…` like the plain form.
