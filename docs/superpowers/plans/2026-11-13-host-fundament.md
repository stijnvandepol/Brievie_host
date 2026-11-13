# Host Fundament (fase 1a) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Brievie server that starts with one `docker compose up`, serves HTTPS with its own certificate authority, lets a first admin set up a workspace, log in on the web, and pair a phone via a QR code, with workspace isolation enforced by PostgreSQL row-level security.

**Architecture:** One Django project (`backend/brievie`) with one app per domain under `backend/apps/`. Django Ninja exposes `/api/v1`; its OpenAPI schema is exported to `docs/api/openapi.json` and is the contract with `Brievie_ios`. Workspace content tables are protected by RLS, keyed on a per-transaction setting. A static React app (`web/`) is built into the API image and served by WhiteNoise; Caddy terminates TLS in front.

**Tech Stack:** Python 3.13, Django 6.1, Django Ninja, psycopg 3, PostgreSQL 18, uv, ruff, mypy + django-stubs, pytest + pytest-django; React 19, Vite, TypeScript strict, Biome, Vitest, TanStack Query + Router, React Aria Components, Lingui, openapi-typescript + openapi-fetch; Caddy 2; Docker Compose.

**Spec:** `docs/SPEC.md` (sections 11, 12, 14, 16 step 1a, 18, 19)

## Global Constraints

- Code, comments, commits, table and field names in English. User-facing text in Dutch (B1), always through i18n (`gettext` on the backend, Lingui on the web). Glossary in `docs/GLOSSARY.md`.
- Every table has `id` (UUID), `created_at`, `updated_at`. Every workspace content table has `workspace_id` and RLS enabled **and forced**.
- `mypy --strict` and `ruff check` clean; `tsc --noEmit` and `biome check` clean. No `Any`, no bare `except`.
- Money is `Decimal`; datetimes are timezone-aware (`USE_TZ = True`, `TIME_ZONE = "Europe/Amsterdam"`).
- API under `/api/v1` only. Additive changes only within v1. Every endpoint has typed request and response schemas and an explicit `operation_id`.
- Error body for every non-2xx: `{"code": "<snake_case>", "message": "<Dutch sentence>"}`.
- Thin endpoints: validate, call one service function, return. Business logic lives in `services.py` per app.
- No document content, tokens, passwords or BSNs in logs.
- No new dependency without a line in `docs/DECISIONS.md`.
- No Redis, no Celery, no Node runtime container.
- Work on branch `feat/fundament`. Conventional commits. Do not push.

## Review Focus

1. Code that runs without a workspace context (management command, future worker task) must see **zero** workspace rows, never all rows. → Task 3.
2. The RLS setting must not leak from one request to the next on a reused connection. → Task 3.
3. A workspace id in the URL that the user is not a member of returns 404 with the same body as a non-existent id (no existence leak). → Task 4.
4. A pairing code that is expired, already used, or mistyped returns 400 `pairing_code_invalid` and never a token; a revoked device token returns 401 on the next call. → Task 5.
5. The CA certificate file is missing (Caddy not started yet, or Let's Encrypt/tunnel mode): pairing still works and the payload carries `"ca_sha256": null`. → Task 6.
6. `POST /setup` after a user exists returns 409 and creates nothing, also when two requests race. → Task 4.

## File Structure

```
backend/
  pyproject.toml                # uv project, ruff, mypy, pytest config
  manage.py
  brievie/                      # settings.py, urls.py, api.py (NinjaAPI instance), wsgi.py
  apps/
    common/                     # models.py (BaseModel, WorkspaceModel), errors.py, schemas.py
    accounts/                   # User model, login/logout/me, setup
    workspaces/                 # Workspace, Membership, rls.py, context.py, deps.py
    documents/                  # Folder (first RLS-protected table)
    devices/                    # PairingCode, Device, auth.py, pki.py
  tests/                        # mirrors apps/: test_<app>_<topic>.py, conftest.py, factories.py
web/                            # src/api (generated types), src/routes, src/locales
deploy/
  docker-compose.yml  Dockerfile  Caddyfile  postgres-init/01-roles.sh  .env.example
docs/
  SPEC.md  DECISIONS.md  GLOSSARY.md  api/openapi.json
.github/workflows/ci.yml
CLAUDE.md  README.md  Makefile
```

---

### Task 1: Project skeleton, tooling, health endpoint, CI

**Files:**
- Create: `backend/pyproject.toml`, `backend/manage.py`, `backend/brievie/{__init__,settings,urls,api,wsgi}.py`, `backend/apps/__init__.py`, `backend/apps/common/{__init__,errors,schemas}.py`, `backend/tests/{conftest.py,test_health.py}`
- Create: `deploy/docker-compose.yml` (services `postgres`, `api`), `deploy/Dockerfile`, `deploy/postgres-init/01-roles.sh`, `deploy/.env.example`
- Create: `Makefile`, `.gitignore`, `.github/workflows/ci.yml`, `CLAUDE.md`, `README.md`, `docs/DECISIONS.md`, `docs/GLOSSARY.md`

**Interfaces:**
- Produces: `brievie.api.api` (the single `NinjaAPI(title="Brievie", version="1.0.0", urls_namespace="api-v1")`), mounted at `/api/v1/`.
- Produces: `apps.common.errors.ApiError(status: int, code: str, message: str)` exception plus a registered handler that renders the error body from Global Constraints. Ninja validation errors render as 422 `validation_error`.
- Produces: `apps.common.schemas.ErrorOut` (`code: str`, `message: str`).
- Produces: DB roles `brievie_owner` (owns schema, `CREATEDB`, **not** superuser) and `brievie_app` (DML only, via `ALTER DEFAULT PRIVILEGES`). Settings read `DATABASE_URL` (app role) and `DATABASE_OWNER_URL`; `manage.py migrate` and the test run use the owner URL.
- Produces: Make targets `make dev` (compose up postgres), `make test`, `make lint`, `make typecheck`, `make openapi`.

- [ ] **Step 1:** Install tooling: `brew install uv`; `cd backend && uv init --python 3.13`; add `django~=6.1`, `django-ninja`, `psycopg[binary]`, `django-environ`, `whitenoise`, `gunicorn`; dev: `ruff`, `mypy`, `django-stubs`, `pytest`, `pytest-django`, `factory-boy`. Record each in `docs/DECISIONS.md`.
- [ ] **Step 2:** Write the failing test `tests/test_health.py`:

```python
def test_health_returns_status_and_api_version(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "api_version": "1.0.0"}

def test_unknown_api_path_uses_error_shape(client):
    r = client.get("/api/v1/does-not-exist")
    assert r.status_code == 404
    assert set(r.json()) == {"code", "message"}
```

- [ ] **Step 3:** Run `make test`; expected: FAIL (no project).
- [ ] **Step 4:** Implement settings (env-driven, `ATOMIC_REQUESTS = True`, `LANGUAGE_CODE = "nl"`, `USE_TZ = True`), the `NinjaAPI` instance, `GET /health` (`operation_id="getHealth"`, no auth), the error handler, Compose with `postgres:18` and the role init script.
- [ ] **Step 5:** Run `make test lint typecheck`; expected: all pass.
- [ ] **Step 6:** CI workflow: job `backend` (postgres 18 service; ruff check, `ruff format --check`, mypy, pytest, `pip-audit`), job `secrets` (gitleaks). `CLAUDE.md`: pointer to `docs/SPEC.md`, the Make targets, and the Global Constraints in short form. `docs/GLOSSARY.md`: werkruimte=workspace, lid=member, meekijker=viewer, beheerder=admin, persoon=person, relatie=relation, map=folder, regel=rule, taak=task, post=entry, op naam van=addressee, thuis=home, zakelijk=business.
- [ ] **Step 7:** Commit `chore: project skeleton with health endpoint and CI`.

### Task 2: Base models and User

**Files:**
- Create: `backend/apps/common/models.py`, `backend/apps/accounts/{__init__,apps,models,admin}.py`, `backend/apps/accounts/migrations/0001_initial.py`, `backend/tests/factories.py`, `backend/tests/test_accounts_user.py`

**Interfaces:**
- Produces: `BaseModel` (abstract; `id: UUIDField(primary_key, default=uuid4)`, `created_at`, `updated_at`).
- Produces: `accounts.User(AbstractBaseUser, PermissionsMixin, BaseModel)`: `email` (unique, case-insensitive, `USERNAME_FIELD`), `name`, `language` (`"nl"` default), `explanation_language` (`"nl"` default; choices `nl`, `en`). `AUTH_USER_MODEL = "accounts.User"`.
- Produces: `tests.factories.UserFactory`.

- [ ] **Step 1:** Failing tests: creating a user normalises the email to lower case; a second user with the same email in different case raises `IntegrityError`; `id` is a `UUID`.
- [ ] **Step 2:** Run; expected FAIL.
- [ ] **Step 3:** Implement the models and migration.
- [ ] **Step 4:** Run; expected PASS. mypy clean.
- [ ] **Step 5:** Commit `feat(accounts): user model with email login`.

### Task 3: Workspaces, membership and row-level security

**Files:**
- Create: `backend/apps/workspaces/{__init__,apps,models,rls,context,services}.py` + migration
- Create: `backend/apps/documents/{__init__,apps,models}.py` + migration (Folder only)
- Test: `backend/tests/test_workspaces_rls.py`, `backend/tests/test_rls_coverage.py`

**Interfaces:**
- Produces: `Workspace(BaseModel)`: `name`, `kind` (`"home"` | `"business"`), `modules: JSONField(default=dict)`.
- Produces: `Membership(BaseModel)`: `user`, `workspace`, `role` (`"admin"` | `"member"` | `"viewer"`); unique `(user, workspace)`.
- Produces: `apps.common.models.WorkspaceModel(BaseModel)` (abstract, `workspace = ForeignKey(Workspace, CASCADE)`, indexed).
- Produces: `documents.Folder(WorkspaceModel)`: `parent` (self FK, nullable), `name`, `path` (e.g. `/Auto/APK`, computed on save from parent path + name); unique `(workspace, path)`.
- Produces: `apps.workspaces.rls.enable_rls(table: str) -> migrations.RunSQL`: enables and **forces** RLS and creates policy `workspace_isolation` with `USING` and `WITH CHECK` `workspace_id = NULLIF(current_setting('brievie.workspace_id', true), '')::uuid`. Reversible.
- Produces: `apps.workspaces.context.workspace_context(workspace_id: UUID) -> ContextManager[None]`: opens `transaction.atomic()`, reads the current value, runs `SELECT set_config('brievie.workspace_id', %s, true)`, and **restores the previous value on exit** (also on exceptions). The restore is required: a transaction-local setting survives the release of a savepoint, so inside an outer transaction (`ATOMIC_REQUESTS`, pytest's test transaction) it would otherwise stay set until that outer transaction ends. Nested contexts therefore work and unwind correctly.
- Produces: `apps.workspaces.services.create_workspace(*, name: str, kind: str, admin: User) -> Workspace` (creates the workspace and the admin membership in one transaction).
- Decision to record in `DECISIONS.md`: `Workspace` and `Membership` themselves are not under RLS in phase 1a (they are looked up before a workspace is known); they are guarded by the API dependency in Task 4 and its tests.

- [ ] **Step 1:** Failing tests in `test_workspaces_rls.py` (all run as the non-superuser owner role, which is subject to forced RLS):

```python
def test_folder_visible_only_inside_its_workspace(ws_a, ws_b):
    with workspace_context(ws_a.id):
        Folder.objects.create(workspace=ws_a, name="Auto")
    with workspace_context(ws_b.id):
        assert Folder.objects.count() == 0
    with workspace_context(ws_a.id):
        assert Folder.objects.count() == 1

def test_no_context_sees_zero_rows(ws_a):            # Review Focus 1
    with workspace_context(ws_a.id):
        Folder.objects.create(workspace=ws_a, name="Auto")
    assert Folder.objects.count() == 0

def test_cannot_insert_into_other_workspace(ws_a, ws_b):
    with workspace_context(ws_a.id), pytest.raises(DatabaseError):
        Folder.objects.create(workspace=ws_b, name="Stiekem")

def test_setting_does_not_leak_after_context(ws_a):  # Review Focus 2
    with workspace_context(ws_a.id):
        pass
    with connection.cursor() as c:
        c.execute("SELECT current_setting('brievie.workspace_id', true)")
        assert c.fetchone()[0] in (None, "")

def test_folder_path_is_built_from_parent(ws_a): ...   # "/Auto" then child "APK" -> "/Auto/APK"
```

- [ ] **Step 2:** Failing test in `test_rls_coverage.py`: for every concrete model subclassing `WorkspaceModel`, `pg_class.relrowsecurity` and `relforcerowsecurity` are both true and a policy named `workspace_isolation` exists. This test guards every future table.
- [ ] **Step 3:** Run; expected FAIL.
- [ ] **Step 4:** Implement models, `rls.py`, `context.py`, `services.py`, migrations (the Folder migration ends with `enable_rls("documents_folder")`).
- [ ] **Step 5:** Run; expected PASS. Confirm the test database user is not a superuser (`SELECT rolsuper FROM pg_roles WHERE rolname = current_user` is false) in a session-scoped fixture that fails loudly otherwise.
- [ ] **Step 6:** Commit `feat(workspaces): workspaces, membership and row-level security`.

### Task 4: Setup, login and workspace-scoped API

**Files:**
- Create: `backend/apps/accounts/{api,schemas,services}.py`, `backend/apps/workspaces/{api,schemas,deps}.py`, `backend/apps/documents/{api,schemas,services}.py`
- Modify: `backend/brievie/api.py` (add routers), `backend/brievie/settings.py` (django-axes)
- Test: `backend/tests/test_accounts_api.py`, `backend/tests/test_workspaces_api.py`, `backend/tests/test_documents_folders_api.py`

**Interfaces:**
- Produces endpoints (operation ids in brackets):
  - `POST /setup` [`setupServer`], no auth. Body `{email, password, name, workspace_name, workspace_kind}` → 201 `{user: UserOut, workspace: WorkspaceOut}` and a logged-in session. 409 `already_set_up` if any user exists.
  - `GET /setup` [`getSetupStatus`], no auth → `{needs_setup: bool}`.
  - `POST /auth/login` [`login`] `{email, password}` → 200 `UserOut` + session cookie; 401 `invalid_credentials`; 429 `too_many_attempts` after 5 failures within 15 minutes for the same email + IP (django-axes).
  - `POST /auth/logout` [`logout`] → 204. `GET /me` [`getMe`] → `UserOut`.
  - `GET /workspaces` [`listWorkspaces`] → `list[WorkspaceOut]` (only the caller's; includes `role`).
  - `GET /workspaces/{workspace_id}/folders` [`listFolders`] → `list[FolderOut]`; `POST` same path [`createFolder`] `{name, parent_id?}` → 201 `FolderOut`; 409 `folder_exists`.
- Produces: `apps.workspaces.deps.workspace_member(request, workspace_id: UUID, *, min_role: str = "viewer") -> Membership`: raises `ApiError(404, "workspace_not_found", …)` when the caller has no membership (same response for unknown ids), 403 `forbidden` when the role is too low, and sets the RLS setting for the rest of the request transaction (`ATOMIC_REQUESTS` makes `set_config(..., true)` last exactly that long). `createFolder` requires `member`.
- Produces: schemas `UserOut {id, email, name, language, explanation_language}`, `WorkspaceOut {id, name, kind, role}`, `FolderOut {id, name, path, parent_id}`.
- Auth: session cookie with CSRF for the web. Password validation: Django validators, minimum length 12.

- [ ] **Step 1:** Failing tests. Must include:
  - setup creates user + workspace + admin membership and logs in; second `POST /setup` → 409 and user count stays 1; two concurrent setup calls create exactly one user (Review Focus 6; serialise with `pg_advisory_xact_lock`).
  - login success, wrong password 401, sixth wrong attempt 429.
  - `listWorkspaces` returns only the caller's workspaces.
  - user of workspace A calling `listFolders` and `createFolder` on workspace B gets 404 `workspace_not_found`, identical body to a random UUID (Review Focus 3).
  - a `viewer` gets 403 on `createFolder`.
  - unauthenticated calls return 401 `not_authenticated`.
- [ ] **Step 2:** Run; expected FAIL.
- [ ] **Step 3:** Implement services, deps, routers. Add `django-axes` (record in DECISIONS).
- [ ] **Step 4:** Run; expected PASS; mypy clean.
- [ ] **Step 5:** Commit `feat(api): setup, login and workspace-scoped folders`.

### Task 5: Device pairing and bearer tokens

**Files:**
- Create: `backend/apps/devices/{__init__,apps,models,services,api,schemas,auth}.py` + migration
- Modify: `backend/brievie/api.py` (global auth becomes `[DeviceTokenAuth(), django_auth]`)
- Test: `backend/tests/test_devices_pairing.py`, `backend/tests/test_devices_auth.py`

**Interfaces:**
- Produces: `PairingCode(BaseModel)`: `user`, `code_hash` (sha256 hex), `expires_at`, `used_at`. `Device(BaseModel)`: `user`, `name`, `token_hash` (sha256 hex, unique), `last_seen_at`, `revoked_at`.
- Produces: `services.create_pairing_code(user) -> tuple[PairingCode, str]`: code is 8 characters from `ABCDEFGHJKLMNPQRSTUVWXYZ23456789`, shown as `XXXX-XXXX`, valid 10 minutes, single use. Creating a new code invalidates the user's unused older codes.
- Produces: `services.pair_device(*, code: str, device_name: str) -> tuple[Device, str]`: token is `secrets.token_urlsafe(32)`; only the hash is stored. Comparison is constant-time; input is normalised (upper case, dash optional).
- Produces: `auth.DeviceTokenAuth(HttpBearer)`: resolves `Authorization: Bearer <token>` to the user, sets `request.user` and `request.device`, updates `last_seen_at` at most once per minute; revoked or unknown → 401 `not_authenticated`.
- Produces endpoints:
  - `POST /devices/pairing-codes` [`createPairingCode`], session auth only → 201 `PairingCodeOut {code, expires_at, qr_payload}`.
  - `POST /devices/pair` [`pairDevice`], no auth, `{code, device_name}` → 201 `{device_id, token, user: UserOut}`; 400 `pairing_code_invalid`. Rate-limited to 10 attempts per IP per 10 minutes → 429 `too_many_attempts`.
  - `GET /devices` [`listDevices`] → the caller's devices (never a token or hash). `DELETE /devices/{device_id}` [`revokeDevice`] → 204; another user's device → 404.
- `qr_payload` is a JSON **string**: `{"v":1,"urls":[...],"code":"XXXX-XXXX","ca_sha256":<hex string or null>}`. `urls` comes from setting `BRIEVIE_PUBLIC_URLS` (comma-separated, local address first, each must be `https://`). `ca_sha256` comes from Task 6 (`None` until then).

- [ ] **Step 1:** Failing tests: happy path pair → token works on `GET /me`; code reuse → 400; expired code (freeze time +11 min) → 400; lower-case and dash-less code accepted; wrong code → 400 with no token key in the body; revoked device → 401 on next call (Review Focus 4); `listDevices` body contains no `token`; the database contains neither the plain code nor the plain token; `createPairingCode` with a bearer token (not a session) → 401.
- [ ] **Step 2:** Run; expected FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run; expected PASS.
- [ ] **Step 5:** Commit `feat(devices): pair a device with a one-time code`.

### Task 6: HTTPS with Caddy and the certificate pin

**Files:**
- Create: `deploy/Caddyfile`, `backend/apps/devices/pki.py`
- Modify: `deploy/docker-compose.yml` (add `caddy`, share its data volume read-only with `api`), `backend/apps/devices/{services,api}.py`, `deploy/.env.example`
- Test: `backend/tests/test_devices_pki.py`

**Interfaces:**
- Produces: `pki.load_ca_certificate_pem() -> str | None`: reads `settings.BRIEVIE_CA_CERT_PATH` (default `/caddy-data/caddy/pki/authorities/local/root.crt`); returns `None` if the setting is empty or the file is missing.
- Produces: `pki.certificate_sha256(pem: str) -> str`: lower-case hex SHA-256 of the certificate's DER bytes.
- Produces endpoint `GET /server/ca-certificate` [`getCaCertificate`], no auth → 200 `{pem: str}`; 404 `no_private_ca` when there is none.
- `qr_payload.ca_sha256` now carries `certificate_sha256(...)` or `null`.
- Caddyfile: site `{$BRIEVIE_HOSTNAME}` with `tls internal`, `reverse_proxy api:8000`, security headers (HSTS, `X-Content-Type-Options`, `Referrer-Policy`, frame-ancestors none), TLS 1.2 minimum. Only Caddy publishes ports (443, and 80 for redirect).

- [ ] **Step 1:** Failing tests: `certificate_sha256` of a fixture certificate equals `openssl x509 -outform der | sha256sum` (commit the fixture and the expected value); missing file → `load_ca_certificate_pem()` is `None`, the endpoint returns 404, and `createPairingCode` still returns a payload with `"ca_sha256": null` (Review Focus 5); present file → payload hash equals the hash of the PEM served by the endpoint.
- [ ] **Step 2:** Run; expected FAIL. Implement. Run; expected PASS.
- [ ] **Step 3:** Manual check, recorded in the task report: `docker compose up -d`, then `curl --cacert <root.crt copied from the caddy volume> https://localhost/api/v1/health` returns the health JSON, and plain `curl https://localhost/api/v1/health` fails certificate verification.
- [ ] **Step 4:** Commit `feat(deploy): HTTPS via Caddy with a pinned private CA`.

### Task 7: OpenAPI export as the contract

**Files:**
- Create: `backend/apps/common/management/commands/export_openapi.py`, `docs/api/openapi.json`
- Test: `backend/tests/test_openapi_contract.py`
- Modify: `Makefile`, `.github/workflows/ci.yml`

**Interfaces:**
- Produces: `python manage.py export_openapi [--check]`: writes the schema with sorted keys and 2-space indent to `docs/api/openapi.json`; `--check` exits 1 when the file differs.
- Produces: `make openapi`. CI runs `--check`.

- [ ] **Step 1:** Failing tests: the committed file equals the generated schema; every operation has an `operationId` in camelCase; every operation documents at least one error response using `ErrorOut`; `info.version == "1.0.0"`.
- [ ] **Step 2:** Run; FAIL. Implement and generate. Run; PASS.
- [ ] **Step 3:** Commit `feat(api): export OpenAPI schema as the app contract`.

### Task 8: Web interface: setup, login, pair the app

**Files:**
- Create: `web/` (Vite project): `package.json`, `biome.json`, `tsconfig.json`, `vite.config.ts`, `lingui.config.ts`, `src/main.tsx`, `src/api/{schema.d.ts,client.ts}`, `src/routes/{setup,login,home,pair-app}.tsx`, `src/locales/nl/messages.po`, tests next to each route as `*.test.tsx`
- Modify: `deploy/Dockerfile` (build stage for `web/`, copy `dist` into the API image), `backend/brievie/{settings,urls}.py` (WhiteNoise + SPA fallback for non-`/api` paths), `Makefile`, CI (job `web`: biome, `tsc --noEmit`, vitest, build)

**Interfaces:**
- Consumes: `docs/api/openapi.json` → `pnpm gen:api` writes `src/api/schema.d.ts` with openapi-typescript; `client.ts` wraps openapi-fetch with `credentials: "include"` and the CSRF header. No hand-written API types.
- Routes: `/setup` (shown when `getSetupStatus.needs_setup`): two large cards Thuis / Zakelijk, workspace name, admin account. `/login`. `/` lists workspaces. `/koppel-app`: button creates a pairing code, shows the QR of `qr_payload` (`qrcode.react`), the code as text, and a countdown; at 0 it offers "Nieuwe code".
- All visible text through Lingui, Dutch source strings, B1 level, je-vorm.

- [ ] **Step 1:** Failing Vitest tests (API mocked with MSW): setup form submits `workspace_kind: "home"` when the Thuis card is chosen; login shows the server's `message` on 401; pair page renders a QR whose value equals `qr_payload` and shows "Nieuwe code" once expired; every form control has an accessible name (`getByRole` queries only).
- [ ] **Step 2:** Run `pnpm test`; FAIL. Implement. Run; PASS. `pnpm build` succeeds.
- [ ] **Step 3:** Backend test `tests/test_spa_fallback.py`: `GET /koppel-app` returns the SPA index; `GET /api/v1/nope` still returns the JSON 404.
- [ ] **Step 4:** Manual check in a browser against `docker compose up`: complete setup, log out, log in, open "App koppelen", see a QR. Record a screenshot path in the task report.
- [ ] **Step 5:** Commit `feat(web): setup, login and app pairing screens`.

---

## Out of scope for 1a (later steps of phase 1)

Worker and Procrastinate, document upload, OCR, AI pipeline, rules, tasks, search, 2FA, Bonjour advertisement, cloudflared wizard step, backups, image signing, container scan and SAST. Record 2FA and token lifetime as open items in `docs/DECISIONS.md`.
