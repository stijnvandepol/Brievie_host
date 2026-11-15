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
  A storage sweep comes with back-ups (1d).
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

## Open items

- Pin GitHub Actions and container images by digest: later hardening task.
- Two-factor authentication is not built in phase 1a; login is email + password only (with lockout).
- Device tokens never expire; a device stops working only when somebody revokes it. A lifetime (and refresh) is still to be decided.
- Server-side OCR (OCRmyPDF + Tesseract) for uploads without text: with web upload and the watch folder (1d).
- The extraction schema leaves out addressees, relations, labels, VAT lines and identifiers (licence plate, assessment number); they arrive with persons and labels (1c) and business mode (phase 2), with IBAN mod-97 and the VAT sum check.
- `listDocuments` returns the newest 200; paging is an additive change for later.
- Confirmed tasks have no reminders yet (1c), and calculated deadlines (NL rules) are not built: a client may only send `in_document` or `manual`.
- A document cannot be deleted or edited after confirmation yet.
- Encrypted page files of a rolled-back upload can stay behind; a storage sweep comes with back-ups (1d), which must also cover `BRIEVIE_MASTER_KEY`.
- AI settings are set with `manage.py configure_ai`; the web settings screen comes with the wizard (1d). The `external` mode was not run against a real service by the builder (no key): the owner checks it with their own Mistral key.
- The API key for an AI service lives in the database sealed with the workspace key; the worker therefore holds `BRIEVIE_MASTER_KEY` too.
- From the review, not fixed: `local` accepts a host made of digits only (`http://134744072`, read as an IP address by a resolver; only the owner can set it); a provider host name over 50 characters fails every document; a NUL character in page text gives 500 instead of 422; repeating a confirm after a lost answer gives 409 (the app reloads the document); an `in_document` deadline is checked for its quote, not for the date inside the quote.
