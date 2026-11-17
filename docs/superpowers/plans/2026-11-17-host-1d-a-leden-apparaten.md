# Host Leden en apparaten (fase 1d, part A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Several people use one Brievie server: an admin invites a member with a link or QR code, the invitee chooses a password and opens the app; admins change roles and remove members; every user sees their devices and web sessions; device tokens stop when they are not used; a member can export their own post; and an append-only audit log records who did what.

**Architecture:** One new app, `audit` (two append-only tables, a closed list of codes, one `record()` helper that every service calls, a purge function owned by the schema owner). `accounts` gains the server admin flag, `UserSession` (recorded by a `user_logged_in` receiver, enforced by a middleware), `start_session()` as the one place a sign-in creates a session, password change and reset links, account notices and the rule for account actions. `workspaces` gains `Invitation` (a server table like `PairingCode`) and the member services (role, remove, leave). `devices` gains the app version, the token expiry and the pairing link. `documents` gains the export building block and `MemberExport`. The web gets the pages Leden, Apparaten, Waar je bent ingelogd, the invitation and reset pages, Instellingen with the log, all with the existing Fluent 2 building blocks.

**Tech Stack:** As in 1c (Python 3.13, Django 6.1, Django Ninja, PostgreSQL 18 with forced RLS, Procrastinate, django-axes, pytest; React 19, TanStack Router and Query, React Aria Components, Lingui, Vitest, MSW, `qrcode.react`). No new dependency (`zipfile`, `secrets`, `hashlib` are standard library).

**Spec:** `docs/superpowers/specs/2026-11-17-fase-1d-leden-rollen-sso-design.md` (cited as "D 6.2"). Part A is the first row of D 13. Binding: D 6, 7, 11, 14, 15 and the rulings of D 20. Related: `docs/superpowers/specs/2026-11-17-beheer-backup-export-updates-design.md` section 5 (the export building block, cited as "Beheer 5"). Previous plan for house style: `docs/superpowers/plans/2026-11-16-host-fase-1c.md`.

## Global Constraints

- From `CLAUDE.md`, verbatim where it binds this plan:
  - "Code, comments, commits, table and field names in English. User-facing text in Dutch (B1): in the backend always through `gettext`, in `web/` always through Lingui (`t` macros; run `pnpm extract` afterwards)."
  - "Every table: UUID `id`, `created_at`, `updated_at`. Every workspace content table: `workspace_id` (subclass `WorkspaceModel`) and RLS enabled **and forced**."
  - "`mypy --strict` and `ruff check` clean. No `Any`, no bare `except`." "Money is `Decimal`; datetimes are timezone-aware."
  - "API only under `/api/v1`, additive changes only. Every endpoint: typed schemas and an explicit camelCase `operation_id`. Endpoints without auth say `auth=None`."
  - "Every non-2xx body: `{"code": "<snake_case>", "message": "<Dutch sentence>"}` via `apps.common.errors.ApiError`."
  - "Thin endpoints: validate, call one function in the app's `services.py`, return." (Here the app's service modules are `services.py` plus the named modules below; the rule is one service call per endpoint.)
  - "Every mutating service function is wrapped in `transaction.atomic`, so raising inside it rolls back its own writes. An error response (`ApiError`, Ninja `HttpError`, `Http404`) does not roll back the request transaction; only a 500 does."
  - "No document content, tokens, passwords or BSNs in logs." "No new dependency without a line in `docs/DECISIONS.md`."
  - "Background jobs: Procrastinate, in an app's `jobs.py`; a job takes ids only and enters `workspace_context` itself, in short transactions (none open during an external call)."
  - "Text is BSN-masked (`apps.common.privacy.mask_bsns`) before it is stored, logged or sent to an AI service. Document files are sealed (`apps.common.crypto`) before they touch disk."
  - "A field added to an existing API schema has a default (never newly required; the contract test compares with `backend/tests/fixtures/openapi_1b.json`)." "Nullable fields in API schemas are primitives only, and nullable inputs have a default (the iOS generator needs both); the contract test forbids `anyOf`."
  - "Tests first for logic. Conventional commits."
- From the design, D 17: "Not left to the builder: the rules of sections 6 to 11, the policies of 8.3, and the list of what is logged." Left to the builder: names of private helpers, the Dutch sentences not fixed here (B1, je-form), the layout within the existing design, test file names.
- Fixed sentences (D 6.2, D 6.3): a used, expired, revoked or unknown invitation answers "Deze uitnodiging werkt niet meer. Vraag een nieuwe."; the reset link the same shape: "Deze herstellink werkt niet meer. Vraag een nieuwe."; the notice after a reset: "Je wachtwoord is op 3 mei opnieuw ingesteld door Anne." (date and name filled in).
- Secrets: invitation and reset tokens are `secrets.token_urlsafe(32)`, stored only as SHA-256 hex, shown once, carried in a URL fragment or a request body, never in a path or query string, never logged.
- Every new endpoint documents its errors (`AUTH_ERRORS`, `SESSION_WRITE_ERRORS`, `BODY_ERRORS`); input schemas derive from `InputSchema`. After every API change: `make openapi`, then `cd web && pnpm gen:api`; commit `docs/api/openapi.json` and `web/src/api/schema.d.ts` with the change.
- Every service that changes something in the list of D 11 calls `apps.audit.services.record(...)` inside its own transaction; audit rows never hold a letter's sender, summary, text or label names, only ids and the names of people.
- Web: no CSP change, no inline styles, every page has `usePageTitle`, works by keyboard, tests query by role; a test that navigates waits for the visible result (`findBy…`), as `test-utils.tsx` and `test-setup.ts` expect.
- Branch `feat/1d-a-leden` from `main` (if `feat/b1-backup` has been merged by then, from that `main`; nothing here touches `apps/backup/` or `./brievie`). Conventional commits ending with the trailer `Co-Authored-By: Claude Code <noreply@anthropic.com>`. Never push. iCloud conflict copies (" 2", " 3" in a name) are never committed.

## Review Focus

1. **An invitation accepted twice at the same moment** (two taps, two phones): exactly one membership, one person link, the second gets 400 `invitation_invalid`. → Task 11 (`test_concurrent_accepts_make_one_membership`, `test_accept_locks_the_invitation_row`).
2. **A token in a log line or a URL query**: no operation takes a token in a path or query parameter, and neither the token nor its hash appears in any log record of create, lookup, accept, reset. → Task 6 (`test_no_operation_takes_a_token_in_path_or_query`, `test_reset_token_never_logged`), Task 11 (`test_invitation_token_never_logged`), Task 19 (`test_token_goes_in_the_body_not_the_url`).
3. **The last admin removing themselves** (by "Verwijderen" on their own row, by "Werkruimte verlaten", or by demoting themselves), and the last server admin losing their last membership: 409 `last_admin`, nothing changed. → Task 12 (`test_last_admin_cannot_demote_themselves`), Task 13 (`test_last_admin_cannot_leave_or_remove_themselves`, `test_last_server_admin_cannot_be_deactivated`).
4. **A workspace admin acting on an account that is also a member elsewhere** (reset link, ending sessions, devices): 403 `server_admin_only`, nothing changed, no notice. → Task 6 (`test_authority_matrix`), Task 9 (`test_workspace_admin_cannot_touch_a_member_of_two_workspaces`).
5. **An expired device token answering differently from a revoked one** (status, code, message, headers): a 1c app only treats `401 not_authenticated` as revocation. → Task 8 (`test_expired_token_answers_exactly_like_a_revoked_one`).
6. **An audit row that can be updated or deleted by the app role** (a forgotten REVOKE is invisible to a suite that runs as the owner): → Task 1 (`test_app_role_may_insert_and_read_audit_rows_but_not_change_them`), Task 2 (`test_purge_refuses_a_cut_off_younger_than_90_days`).

---

## File Structure

```
backend/apps/
  audit/                         # new app
    codes.py                     # AuditCode, ActorKind, TargetKind, ALLOWED_VALUES (closed list, D 11)
    models.py                    # WorkspaceAuditEvent (RLS), ServerAuditEvent
    services.py                  # AuditActor, AuditTarget, actor_from_request, record, list/csv queries
    jobs.py                      # purge_audit_events (periodic)
    api.py  schemas.py
    migrations/0001_initial.py   # tables, RLS, REVOKE/GRANT
    migrations/0002_purge_functions.py
  accounts/
    models.py                    # + User.is_server_admin, UserSession, PasswordReset, AccountNotice
    sessions.py                  # start_session, end_sessions, session_policy, describe_user_agent, receivers
    middleware.py                # UserSessionMiddleware
    authority.py                 # account_authority (D 6.3 rule)
    resets.py                    # create/complete password reset
    notices.py                   # notify, list, mark seen, message text
    services.py  api.py  schemas.py
    management/commands/reset_password.py
  common/
    tokens.py                    # new_token, token_hash
    links.py                     # public_base_url, fragment_link, home_network_only
    sealed_stream.py             # SealedWriter, iter_opened (chunked AES-GCM)
  devices/
    models.py                    # + Device.app_version, app_version_at; TokenExpiryStart
    services.py  auth.py  api.py  schemas.py
  workspaces/
    models.py                    # + Invitation, Workspace.allow_member_export
    invitations.py               # create/list/revoke/renew/lookup/accept
    members.py                   # change_role, remove_member, lock helpers
    access.py                    # seams for part B: on_member_added, on_member_removed, person_requires_email
    settings_service.py          # workspace settings (allow_member_export)
    services.py  api.py  schemas.py
  documents/
    export.py                    # write_export: the building block of Beheer 5
    member_export.py             # MemberExport services
    jobs.py                      # build_member_export, remove_expired_exports
    models.py  api.py  schemas.py
  rules/ labels/ people/ pipeline/   # record() calls only (Task 14)
backend/tests/                   # test_<app>_<topic>.py; fixtures/openapi_1c.json
web/src/
  routes/members.tsx  invitation.tsx  password-reset.tsx  sessions.tsx  password.tsx
  routes/member-devices.tsx  settings.tsx  audit-log.tsx  server-audit-log.tsx
  components/invite-dialog.tsx  role-dialog.tsx  notice-bar.tsx  audit-table.tsx  export-panel.tsx
  routes/pair-app.tsx  components/device-table.tsx  components/page.tsx  components/icons.tsx
  api/queries.ts  router.tsx  locales/nl/messages.po
docs/  DECISIONS.md  GLOSSARY.md  PRIVACY.md  api/openapi.json
```

Imports: `audit` imports nothing from other domain apps (it takes ids and names). `workspaces.members` imports `accounts`, `people`, `tasks`, `devices`; `accounts.authority` imports `workspaces.models`. `workspaces.access` is a leaf module that part B fills in.

---

## Slice A1: the audit log

### Task 1: Audit tables, codes and `record()`

**Files:**
- Create: `backend/apps/audit/{__init__,apps,codes,models,services}.py`, `backend/apps/audit/migrations/{__init__,0001_initial}.py`, `backend/tests/test_audit_record.py`, `backend/tests/test_audit_privileges.py`, `backend/tests/fixtures/openapi_1c.json`
- Modify: `backend/brievie/settings.py` (`INSTALLED_APPS` + `"apps.audit"`), `backend/tests/test_openapi_contract.py`

**Interfaces:**
- Produces (`apps/audit/codes.py`), the closed list of D 11 as `AuditCode(models.TextChoices)` with Dutch `gettext_lazy` labels: server codes `sign_in_succeeded`, `sign_in_failed`, `sign_in_locked_out`, `second_factor_added`, `second_factor_removed`, `second_factor_cleared`, `password_changed`, `password_reset_created`, `session_ended`, `device_paired`, `device_revoked`, `device_expired`, `sso_settings_changed`; workspace codes `invitation_created`, `invitation_revoked`, `invitation_accepted`, `member_role_changed`, `member_removed`, `member_left`, `workspace_setting_changed`, `rule_created`, `rule_changed`, `rule_deleted`, `rule_run`, `label_deleted`, `person_deleted`, `visibility_changed`, `person_linked`, `person_unlinked`, `department_member_added`, `department_member_removed`, `emergency_access_switched_on`, `emergency_access_switched_off`, `emergency_access_used`, `export_created`, `export_downloaded`. Codes of parts B and C are in the list now; nothing in part A writes them.
- Produces: `ActorKind` (`user`, `device`, `system`, `sso`), `TargetKind` (`user`, `invitation`, `device`, `session`, `rule`, `label`, `person`, `document`, `workspace`, `export`); `ALLOWED_VALUES: dict[AuditCode, frozenset[str]]`, e.g. `member_role_changed: {"from", "to"}`, `member_removed`/`member_left: {"account_deactivated"}`, `invitation_created: {"role", "expires_in_days"}`, `invitation_accepted: {"role", "new_account", "account_reactivated"}`, `password_changed: {"via"}`, `session_ended: {"count", "reason"}`, `sign_in_succeeded: {"via"}`, `workspace_setting_changed: {"setting", "value"}`, `export_created: {"document_count"}`, `emergency_access_used: {"reason"}`; every other code `frozenset()`.
- Produces (`apps/audit/models.py`): `WorkspaceAuditEvent(WorkspaceModel)` and `ServerAuditEvent(BaseModel)`, both with `actor_kind` (CharField 10), `actor_id` (UUIDField null), `actor_name` (CharField 150, blank), `actor_device_id` (UUIDField null), `code` (CharField 50, choices `AuditCode`), `target_kind` (CharField 20, blank), `target_id` (UUIDField null), `target_name` (CharField 150, blank), `client_ip` (GenericIPAddressField null), `values` (JSONField default dict). `WorkspaceAuditEvent` overrides the inherited field: `workspace = models.ForeignKey("workspaces.Workspace", on_delete=models.DO_NOTHING, db_constraint=False, related_name="+")`, so the table has no foreign key (D 11) and `test_rls_coverage.py` still sees it. Index `(workspace, -created_at)` / `(-created_at)`; `ordering = ["-created_at", "-id"]`.
- Produces (`apps/audit/services.py`):
  - `@dataclass(frozen=True) class AuditActor: kind: str; user_id: UUID | None; name: str; device_id: UUID | None = None; client_ip: str | None = None` with `@classmethod system() -> AuditActor` (kind `system`, name `"Brievie"`) and `@classmethod for_user(user: User, *, client_ip: str | None = None) -> AuditActor`.
  - `actor_from_request(request: HttpRequest) -> AuditActor`: kind `device` with `device_id` when `request.device` is set (bearer), else `user`; `client_ip` from `apps.common.net.client_ip`.
  - `@dataclass(frozen=True) class AuditTarget: kind: str; id: UUID | None; name: str = ""`.
  - `record(code: AuditCode, *, actor: AuditActor, target: AuditTarget | None = None, workspace_id: UUID | None = None, values: Mapping[str, str | int | bool] | None = None) -> None`: `workspace_id` set → a `WorkspaceAuditEvent` written inside `workspace_context(workspace_id)` (it enters the context itself, nesting is fine); None → a `ServerAuditEvent`. Raises `ValueError` for a value key outside `ALLOWED_VALUES[code]` or a value that is not `str | int | bool`; strings longer than 200 characters are cut; names pass through `mask_bsns`.
- Migration `0001_initial`: both tables, `enable_rls("audit_workspaceauditevent")`, then one `RunSQL`:

```sql
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'brievie_app') THEN
    REVOKE UPDATE, DELETE, TRUNCATE ON audit_workspaceauditevent, audit_serverauditevent FROM brievie_app;
    GRANT SELECT, INSERT ON audit_workspaceauditevent, audit_serverauditevent TO brievie_app;
  END IF;
END $$;
```

(The production database grants the app role everything through default privileges, so the REVOKE matters there; the test database has no default privileges, so the GRANT makes the positive half of the test meaningful.)
- Contract fixture: copy `docs/api/openapi.json` as it is on `main` at branch start to `backend/tests/fixtures/openapi_1c.json`; `released_contract()` becomes `released_contracts()` returning both fixtures, and each `test_released_*` test is parametrized over them (`ids=["1b", "1c"]`). The 1c fixture includes `updateLabel` with `LabelUpdateIn`, so `RENAMED_REQUEST_BODIES` needs no entry for it.

- [ ] **Step 1: Write the failing tests**

```python
# test_audit_privileges.py
def test_app_role_exists() -> None:                       # SELECT 1 FROM pg_roles WHERE rolname='brievie_app' -> a row
@pytest.mark.parametrize("table", ["audit_workspaceauditevent", "audit_serverauditevent"])
def test_app_role_may_insert_and_read_audit_rows_but_not_change_them(table: str) -> None:
    # has_table_privilege('brievie_app', table, p) for p in SELECT, INSERT -> True
    # for p in UPDATE, DELETE, TRUNCATE -> False
def test_audit_tables_have_no_foreign_keys() -> None:     # pg_constraint contype='f' for both tables -> []

# test_audit_record.py
def test_record_writes_a_workspace_row_in_its_own_context(ws_a, ws_b) -> None:
    # called with no context; visible in workspace_context(ws_a.id), not in ws_b's; code, actor, target as given
def test_record_without_workspace_writes_a_server_row() -> None
def test_record_rejects_a_value_key_not_listed_for_the_code(ws_a) -> None:
    # record(AuditCode.LABEL_DELETED, ..., values={"name": "Zorg"}) -> ValueError; no row
def test_record_masks_a_bsn_in_a_name(ws_a) -> None:     # target name "Jan 111222333" -> "Jan [BSN]"
def test_actor_from_request_names_the_device(rf) -> None: # request.device set -> kind "device", device_id, user_id
def test_every_code_has_an_allowed_values_entry() -> None:# set(ALLOWED_VALUES) == set(AuditCode)
```

and the parametrized contract tests (`test_released_schemas_still_exist[1c]` etc.).

- [ ] **Step 2: Run** `cd backend && uv run pytest tests/test_audit_record.py tests/test_audit_privileges.py tests/test_openapi_contract.py -v` → FAIL (no app `audit`; the 1c fixture is missing).
- [ ] **Step 3: Implement** the app, the migration (`uv run python manage.py makemigrations audit`, then append the `enable_rls` and the SQL above), the fixture and the parametrization.
- [ ] **Step 4: Run** the same command plus `tests/test_rls_coverage.py` → PASS. `make lint typecheck` → clean.
- [ ] **Step 5: Commit** `feat(audit): append-only audit tables with a closed list of codes`

### Task 2: Purging old audit rows

**Files:**
- Create: `backend/apps/audit/migrations/0002_purge_functions.py`, `backend/apps/audit/jobs.py`, `backend/tests/test_audit_purge.py`
- Modify: `backend/brievie/settings.py` (`BRIEVIE_AUDIT_RETENTION_DAYS`)

**Interfaces:**
- Consumes: Task 1 tables.
- Produces: SQL functions owned by the schema owner, `SECURITY DEFINER`, `SET search_path = pg_catalog, public`, `REVOKE ALL ... FROM PUBLIC`, `GRANT EXECUTE ... TO brievie_app` (inside the same `IF EXISTS` guard as Task 1):
  - `brievie_purge_workspace_audit(p_workspace uuid, p_cutoff timestamptz) RETURNS integer`
  - `brievie_purge_server_audit(p_cutoff timestamptz) RETURNS integer`
  Both raise (`ERRCODE '22023'`) when `p_cutoff > now() - interval '90 days'`. The workspace function enters the context itself, because the owner is bound by forced RLS too (D 20, last ruling):

```sql
previous := current_setting('brievie.workspace_id', true);
PERFORM set_config('brievie.workspace_id', p_workspace::text, true);
DELETE FROM audit_workspaceauditevent WHERE workspace_id = p_workspace AND created_at < p_cutoff;
GET DIAGNOSTICS removed = ROW_COUNT;
PERFORM set_config('brievie.workspace_id', coalesce(previous, ''), true);
RETURN removed;
```

- Produces: setting `BRIEVIE_AUDIT_RETENTION_DAYS: int` (env, default 365; `ImproperlyConfigured` below 90). `apps/audit/jobs.py`: `purge_audit_events()` (`@app.periodic(cron="17 3 * * *")`, queue `pipeline` from `apps.pipeline.jobs.QUEUE`): for each workspace id, in its own short transaction, `SELECT brievie_purge_workspace_audit(%s, %s)`; then the server function; logs the two totals only.

- [ ] **Step 1: Failing tests**

```python
def test_purge_refuses_a_cut_off_younger_than_90_days(ws_a) -> None:
    # cutoff = now - 89 days -> django.db.DataError; rows untouched
def test_purge_removes_only_older_rows_of_that_workspace(ws_a, ws_b) -> None:
    # rows in both workspaces aged 400 and 10 days (age set with .update(created_at=...) as the owner)
    # purge(ws_a, now - 365d) -> 1; ws_a keeps the 10-day row; ws_b keeps both
def test_purge_restores_the_previous_workspace_setting(ws_a, ws_b) -> None:
    # inside workspace_context(ws_b.id): call for ws_a -> current_setting is ws_b afterwards
def test_server_purge_removes_rows_older_than_the_cut_off() -> None
def test_app_role_may_execute_the_purge_functions_and_public_may_not() -> None:
    # has_function_privilege('brievie_app', 'brievie_purge_workspace_audit(uuid, timestamptz)', 'EXECUTE') -> True
    # has_function_privilege('public', ...) -> False; both functions; prosecdef is true
def test_purge_job_uses_the_retention_setting(settings, ws_a) -> None:
    # settings.BRIEVIE_AUDIT_RETENTION_DAYS = 120; a 130-day row goes, a 100-day row stays
```

- [ ] **Step 2: Run** `cd backend && uv run pytest tests/test_audit_purge.py -v` → FAIL.
- [ ] **Step 3: Implement** the migration (`RunSQL` with a reverse that drops both functions), the setting and the job.
- [ ] **Step 4: Run** the tests → PASS; `make lint typecheck` → clean.
- [ ] **Step 5: Commit** `feat(audit): purge old audit rows through an owner function that refuses young cut-offs`

---

## Slice A2: accounts, sessions and passwords

### Task 3: The server admin

**Files:**
- Create: `backend/apps/accounts/migrations/0003_user_is_server_admin.py`, `backend/apps/accounts/migrations/0004_first_server_admin.py`, `backend/tests/test_accounts_server_admin.py`
- Modify: `backend/apps/accounts/models.py`, `backend/apps/accounts/services.py` (`setup_server`), `backend/apps/accounts/schemas.py` (`UserOut`), `docs/api/openapi.json`, `web/src/api/schema.d.ts`

**Interfaces:**
- Produces: `User.is_server_admin = models.BooleanField(default=False)`; `UserOut.is_server_admin: bool = False`.
- Produces: `0004_first_server_admin` with module-level `def mark_first_server_admin(apps: StateApps, schema_editor: object) -> None` (depends on `workspaces` latest migration): when any user exists and none is server admin, the admin membership with the oldest `created_at` in the oldest workspace (by `created_at`, then `id`) makes its user server admin; with no workspace, the oldest active user. Reverse: no-op.
- Produces: `setup_server` creates the first user with `is_server_admin=True`.
- Produces (`apps/accounts/services.py`): `other_active_server_admin_exists(user: User) -> bool` (used by Task 13).

- [ ] **Step 1: Failing tests** (migration tested the way `test_rules_preset_migration.py` does: import the module, call the function with the global `apps`):

```python
def test_migration_marks_the_first_admin_of_the_oldest_workspace() -> None:
    # two workspaces, the older one with two admins (membership created_at apart) -> only the first is server admin
def test_migration_changes_nothing_when_a_server_admin_exists() -> None
def test_migration_on_an_empty_server_does_nothing() -> None
def test_setup_makes_the_first_user_server_admin(client) -> None       # POST /setup -> body user.is_server_admin True
def test_me_reports_is_server_admin(client, ws_a) -> None              # false for a plain admin
def test_other_active_server_admin_exists_ignores_inactive_admins() -> None
```

- [ ] **Step 2: Run** `cd backend && uv run pytest tests/test_accounts_server_admin.py -v` → FAIL.
- [ ] **Step 3: Implement**; `make openapi && cd web && pnpm gen:api`.
- [ ] **Step 4: Run** `make test` → PASS (the contract test passes: `UserOut` only gained a defaulted field).
- [ ] **Step 5: Commit** `feat(accounts): the server admin, given to the first admin on existing servers`

### Task 4: Recorded sessions, one place to sign in, and their lifetime

**Files:**
- Create: `backend/apps/accounts/sessions.py`, `backend/apps/accounts/middleware.py`, `backend/apps/accounts/migrations/0005_usersession.py`, `backend/tests/test_accounts_sessions.py`
- Modify: `backend/apps/accounts/models.py`, `backend/apps/accounts/apps.py` (`ready()` connects receivers), `backend/apps/accounts/services.py` (`login`, `setup_server`, `logout`), `backend/brievie/settings.py`

**Interfaces:**
- Consumes: `record`, `AuditActor`, `AuditTarget`, `AuditCode` (Task 1).
- Produces: `UserSession(BaseModel)`: `user` (FK `User`, CASCADE, `related_name="web_sessions"`), `session_key` (CharField 40, unique; plain, as Django's own table holds it, D 7.2), `method` (`Method(TextChoices)`: `password`, `sso`), `last_used_at` (DateTimeField), `client_ip` (GenericIPAddressField null), `browser` (CharField 100: a description, never the raw user agent). `created_at` is the start.
- Produces (`apps/accounts/sessions.py`):
  - `start_session(request: HttpRequest, user: User, *, method: str = UserSession.Method.PASSWORD, via: str) -> None`: **the one function that signs somebody in** (login, setup, accepting an invitation, completing a reset). Calls `django.contrib.auth.login`, then `record(SIGN_IN_SUCCEEDED, values={"via": via})`. `via` is one of `password`, `setup`, `invitation`, `reset`. Part C inserts the second-factor step here.
  - `@dataclass(frozen=True) class SessionPolicy: idle: timedelta; maximum: timedelta` and `session_policy(method: str) -> SessionPolicy` (from settings; part C caps `sso` at 12 hours here).
  - `on_user_logged_in(sender: object, request: HttpRequest, user: User, **kwargs: object) -> None`: the `user_logged_in` receiver that creates the `UserSession` (method from `request._brievie_session_method`, default `password`) and sets the session expiry. A receiver, not code in `start_session`, so `Client.force_login` in the 66 existing tests records a session too.
  - `end_sessions(user: User, *, except_key: str | None = None) -> int`: deletes the user's `UserSession` rows and the matching `django.contrib.sessions.models.Session` rows; returns the count.
  - `describe_user_agent(user_agent: str) -> str`: pure; "Firefox op Windows", "Safari op iPhone", "Chrome op Android", "Edge op Windows", "Onbekende browser" (Dutch via `gettext`).
- Produces: `UserSessionMiddleware` (after `AuthenticationMiddleware` in `MIDDLEWARE`). For a request whose session holds an authenticated user: no `UserSession` row → `request.session.flush()` and `request.user = AnonymousUser()` (fails closed); `now - created_at > policy.maximum` or `now - last_used_at > policy.idle` → the same plus delete the row; otherwise, when `now - last_used_at >= 1 minute`, update `last_used_at` and `client_ip` and call `request.session.set_expiry(min(now + idle, created_at + maximum))`, so Django's own `expire_date` and cookie enforce both limits. Its writes run in their own `transaction.atomic()` (middleware is outside the request transaction).
- Produces: settings `BRIEVIE_SESSION_IDLE_DAYS: int = 14`, `BRIEVIE_SESSION_MAX_DAYS: int = 90` (env; `ImproperlyConfigured` when idle > max or either < 1), `SESSION_COOKIE_AGE = idle in seconds`.
- Produces: migration `0005_usersession` with a `RunPython` that creates a `UserSession` for every unexpired Django session holding `_auth_user_id` (method `password`, `last_used_at` now, browser ""), so nobody is signed out by the upgrade and the middleware can fail closed.
- Produces: `login` failures write `sign_in_failed` (server log; `actor` kind `user` with `user_id` of the account the email belongs to, or None; `target` that account; never the email typed for an unknown account, never the password); an `axes.signals.user_locked_out` receiver writes `sign_in_locked_out` once per lockout. Both are written before the 401/429, which commits the request transaction. `logout` deletes the `UserSession` row before `django_logout`.

- [ ] **Step 1: Failing tests**

```python
def test_login_records_a_session_with_browser_and_address(client) -> None:
    # User-Agent of Firefox on Windows, REMOTE_ADDR 192.0.2.7 -> one UserSession: browser "Firefox op Windows", client_ip "192.0.2.7", method "password"
def test_force_login_records_a_session(client) -> None
def test_a_session_without_record_is_signed_out(client) -> None:  # delete the row -> GET /me -> 401
def test_idle_session_ends_after_the_idle_limit(client) -> None:  # last_used_at = now - 15 days -> 401; row gone
def test_session_ends_after_the_maximum_even_when_used(client) -> None:  # created_at = now - 91 days, last_used now -> 401
def test_last_used_is_written_at_most_once_a_minute(client, django_assert_num_queries) -> None
def test_expiry_is_the_earlier_of_idle_and_maximum(client) -> None:
    # created_at = now - 85 days -> session expire_date == created_at + 90 days (± 1 s)
def test_logout_deletes_the_record(client) -> None
def test_failed_sign_in_is_logged_without_the_password(client, caplog) -> None:
    # ServerAuditEvent code sign_in_failed, target the account; the password not in caplog.text
def test_failed_sign_in_for_an_unknown_address_names_nobody(client) -> None  # target_id None, target_name ""
def test_lockout_is_logged_once(client) -> None                               # 6 failures -> one sign_in_locked_out
def test_successful_sign_in_is_logged_with_via(client) -> None                # values {"via": "password"}
def test_start_session_is_the_only_caller_of_django_login() -> None:
    # scan backend/apps/**/*.py: `login(` from django.contrib.auth is imported only in apps/accounts/sessions.py
@pytest.mark.parametrize(("agent", "text"), [...])
def test_describe_user_agent(agent: str, text: str) -> None
def test_migration_records_existing_sessions() -> None
```

- [ ] **Step 2: Run** `cd backend && uv run pytest tests/test_accounts_sessions.py -v` → FAIL.
- [ ] **Step 3: Implement**; replace `django_login` in `login` and `setup_server` by `start_session(..., via="password" | "setup")`.
- [ ] **Step 4: Run** `make test` → PASS (all tests that use `force_login` still pass through the receiver).
- [ ] **Step 5: Commit** `feat(accounts): record web sessions, sign in through one function, enforce 14 days idle and 90 days at most`

### Task 5: My sessions, my password and notices

**Files:**
- Create: `backend/apps/accounts/notices.py`, `backend/apps/accounts/migrations/0006_accountnotice.py`, `backend/tests/test_accounts_my_sessions_api.py`, `backend/tests/test_accounts_password_api.py`, `backend/tests/test_accounts_notices.py`
- Modify: `backend/apps/accounts/{models,services,api,schemas}.py`, `docs/api/openapi.json`, `web/src/api/schema.d.ts`

**Interfaces:**
- Consumes: `UserSession`, `end_sessions` (Task 4), `record` (Task 1).
- Produces: `AccountNotice(BaseModel)`: `user` (FK CASCADE), `code` (`Code(TextChoices)`: `password_reset_created`, `password_reset_completed`, `sessions_ended`, `device_revoked`, `second_factor_cleared`), `actor_name` (CharField 150), `values` (JSONField default dict), `seen_at` (null).
- Produces (`apps/accounts/notices.py`): `notify(user: User, code: str, *, actor_name: str, values: Mapping[str, str] | None = None) -> None`; `notice_message(notice: AccountNotice) -> str` (Dutch, date as "3 mei" in `Europe/Amsterdam`): `password_reset_created` "%(name)s heeft op %(date)s een herstellink voor je wachtwoord gemaakt.", `password_reset_completed` "Je wachtwoord is op %(date)s opnieuw ingesteld door %(name)s." (D 6.3), `sessions_ended` "%(name)s heeft op %(date)s je sessies beëindigd.", `device_revoked` "%(name)s heeft op %(date)s je apparaat ‘%(device)s’ ingetrokken."
- Produces endpoints (all `auth=session_auth`: they are about the web session; a bearer token gets 401):

| Operation | Method, path | In → Out | Errors |
|---|---|---|---|
| `listMySessions` | GET `/me/sessions` | → `list[SessionOut]` | 401 |
| `endMySession` | DELETE `/me/sessions/{session_id}` | → 204 | 404 `session_not_found`, 401, 403 |
| `changePassword` | POST `/me/password` | `PasswordChangeIn{current_password: str, new_password: str}` (max 128) → 204 | 400 `wrong_password`, 422, 429, 401, 403 |
| `listMyNotices` | GET `/me/notices` | → `list[NoticeOut]` (unseen, oldest first) | 401 |
| `markNoticesSeen` | POST `/me/notices/seen` | `NoticesSeenIn{notice_ids: list[UUID]}` (max 50) → 204 | 401, 403, body errors |

  `SessionOut{id: UUID, method: str, started_at: datetime, last_used_at: datetime, client_ip: str = "", browser: str, current: bool}`; `NoticeOut{id: UUID, code: str, message: str, created_at: datetime}`.
- Produces: `change_password(request: HttpRequest, *, current_password: str, new_password: str) -> None` (throttle `CallerThrottle("password", "10/10m")`): checks the current password, `validate_password`, `set_password`, `update_session_auth_hash`, `end_sessions(user, except_key=current)`, `record(PASSWORD_CHANGED, values={"via": "self"})` and, when sessions ended, `record(SESSION_ENDED, values={"count": n, "reason": "password_changed"})`. Ending the current session through `endMySession` signs this browser out (204, the next request 401).

- [ ] **Step 1: Failing tests**

```python
def test_list_my_sessions_marks_the_current_one(client) -> None      # two logins of the same user, two clients
def test_ending_another_session_signs_that_browser_out(client) -> None # other client's GET /me -> 401; both rows gone
def test_ending_somebody_elses_session_is_404(client) -> None
def test_my_sessions_refuse_a_bearer_token(client) -> None             # 401 not_authenticated
def test_change_password_ends_the_other_sessions_and_keeps_this_one(client) -> None
def test_change_password_with_a_wrong_current_password_is_400(client) -> None   # code wrong_password; password unchanged
def test_change_password_runs_the_password_rules(client) -> None       # "kort" -> 422
def test_change_password_is_logged(client) -> None                     # password_changed {"via": "self"}, session_ended {"count": 1, ...}
def test_notices_are_listed_once_and_marked_seen(client) -> None
def test_notice_message_reads_like_the_design() -> None
    # password_reset_completed by "Anne" on 2026-05-03 -> "Je wachtwoord is op 3 mei opnieuw ingesteld door Anne."
def test_marking_another_users_notice_changes_nothing(client) -> None
```

- [ ] **Step 2: Run** the three test files → FAIL.
- [ ] **Step 3: Implement**; `make openapi && cd web && pnpm gen:api`.
- [ ] **Step 4: Run** `make test` → PASS.
- [ ] **Step 5: Commit** `feat(accounts): see and end my sessions, change my password, read notices`

### Task 6: Account authority and reset links

**Files:**
- Create: `backend/apps/common/tokens.py`, `backend/apps/common/links.py`, `backend/apps/accounts/authority.py`, `backend/apps/accounts/resets.py`, `backend/apps/accounts/management/commands/reset_password.py`, `backend/apps/accounts/migrations/0007_passwordreset.py`, `backend/tests/test_accounts_authority.py`, `backend/tests/test_accounts_reset_api.py`, `backend/tests/test_api_token_placement.py`, `backend/tests/test_common_links.py`
- Modify: `backend/apps/accounts/{models,api,schemas}.py`, `docs/api/openapi.json`, `web/src/api/schema.d.ts`

**Interfaces:**
- Produces (`apps/common/tokens.py`): `new_token() -> tuple[str, str]` (`secrets.token_urlsafe(32)` and its SHA-256 hex); `token_hash(token: str) -> str` (strips surrounding whitespace first).
- Produces (`apps/common/links.py`): `public_base_url(request: HttpRequest) -> str` (first `BRIEVIE_PUBLIC_URLS` entry without a trailing slash; without one, `request.build_absolute_uri("/")` without the slash); `fragment_link(request: HttpRequest, path: str, token: str) -> str` → `f"{base}{path}#{token}"`; `home_network_only(request: HttpRequest) -> bool`: every public URL's host is `localhost`, ends in `.local`, `.lan` or `.home.arpa`, or is a private, loopback or link-local address (`ipaddress`). Part D adds external addresses here.
- Produces (`apps/accounts/authority.py`): `@dataclass(frozen=True) class AccountAuthority: target: User; workspace_id: UUID | None` and `account_authority(actor: User, target_id: UUID) -> AccountAuthority` implementing D 6.3: a server admin may act for anyone (`workspace_id` = the target's only workspace when they have exactly one, else None); otherwise the actor must be `admin` of the target's **only** membership and the target must not be a server admin. Errors: target unknown, or no shared workspace in which the actor is admin → 404 `user_not_found` ("We kunnen dit account niet vinden."); the actor is admin of a workspace of the target but the target has another membership or is a server admin → 403 `server_admin_only` ("Dit kan alleen de serverbeheerder, omdat dit account ook buiten deze werkruimte bestaat.").
- Produces: `PasswordReset(BaseModel)`: `user` (FK CASCADE), `token_hash` (CharField 64, unique), `expires_at`, `created_by` (FK User, SET_NULL, null), `created_by_name` (CharField 150), `used_at` (null), `revoked_at` (null). Validity 24 hours.
- Produces (`apps/accounts/resets.py`):
  - `create_password_reset(request: HttpRequest, *, target_id: UUID) -> IssuedReset` (`IssuedReset(link: str, expires_at: datetime)`): authority check; target inactive → 409 `account_inactive`; earlier unused links of the target get `revoked_at`; `notify(target, "password_reset_created")`; `record(PASSWORD_RESET_CREATED)` in the server log and, when `authority.workspace_id`, in that workspace's log. Link path `/wachtwoord-herstellen`.
  - `complete_password_reset(request: HttpRequest, *, token: str, password: str) -> User`: row lock on the reset; unknown, used, expired, revoked, or the account inactive → 400 `reset_link_invalid` "Deze herstellink werkt niet meer. Vraag een nieuwe." (one sentence for all); `validate_password` → 422 (link stays unused); sets the password, marks used, `end_sessions(user)`, `notify(user, "password_reset_completed", actor_name=created_by_name)`, `record(PASSWORD_CHANGED, values={"via": "reset"})`, then `start_session(request, user, via="reset")`.
- Produces endpoints:

| Operation | Method, path | Auth | In → Out | Errors |
|---|---|---|---|---|
| `createPasswordReset` | POST `/users/{user_id}/password-reset` | default | → 201 `PasswordResetOut{link: str, expires_at: datetime}` | 403 `server_admin_only`, 404 `user_not_found`, 409 `account_inactive`, 401 |
| `completePasswordReset` | POST `/auth/password-reset` | `auth=None`, `ClientAddressThrottle("password_reset", "10/10m")` | `PasswordResetIn{token: str (max 100), password: str (max 128)}` → 200 `UserOut` | 400 `reset_link_invalid`, 422, 429, body errors |

- Produces: `manage.py reset_password --email <address>`: asks the new password twice with `getpass`, runs the validators, sets it, ends every session, writes `password_changed {"via": "command"}` with `AuditActor.system()`. Never takes the password as an argument. For a server admin who lost theirs (D 6.3).

- [ ] **Step 1: Failing tests**

```python
@pytest.mark.parametrize(...)  # rows: (actor, target, expected)
def test_authority_matrix(...) -> None:
    # server admin -> anyone: ok; ws admin -> member whose only membership is that ws: ok, workspace_id set;
    # ws admin -> member of two workspaces: 403 server_admin_only; ws admin -> server admin member: 403;
    # plain member -> other member: 404 user_not_found; admin -> user of another workspace only: 404
def test_reset_link_is_a_fragment_link_and_only_the_hash_is_stored(client, settings) -> None:
    # settings.BRIEVIE_PUBLIC_URLS = ["https://brievie.local"]; link matches r"^https://brievie\.local/wachtwoord-herstellen#[A-Za-z0-9_-]{43}$"
    # PasswordReset.token_hash == sha256 of the fragment; the fragment is in no column
def test_a_new_link_stops_the_older_one(client) -> None
def test_completing_sets_the_password_ends_sessions_and_signs_in(client) -> None
    # old session client -> 401; response client -> GET /me 200; login with the new password works
@pytest.mark.parametrize("state", ["used", "expired", "revoked", "unknown", "inactive_account"])
def test_every_dead_link_answers_the_same_sentence(client, state) -> None:
    # 400, {"code": "reset_link_invalid", "message": "Deze herstellink werkt niet meer. Vraag een nieuwe."}
def test_a_weak_password_keeps_the_link_usable(client) -> None
def test_reset_writes_notices_and_audit_rows(client) -> None   # both notices; server rows; workspace row for a single-membership target
def test_reset_token_never_logged(client, caplog) -> None      # caplog.set_level(DEBUG); token and its hash not in caplog.text
def test_reset_password_command_sets_the_password(monkeypatch) -> None
# test_api_token_placement.py
def test_no_operation_takes_a_token_in_path_or_query() -> None:
    # every parameter of every operation in api.get_openapi_schema(): if "token" in name.lower() -> fail
# test_common_links.py
@pytest.mark.parametrize(("urls", "expected"), [(["https://brievie.local"], True), (["https://192.168.1.4"], True),
    (["https://brievie.local", "https://post.example.nl"], False), ([], True)])
def test_home_network_only(rf, settings, urls, expected) -> None
```

(With no public URL, `home_network_only` judges the request's own host; the test sends `HTTP_HOST="brievie.local"`.)

- [ ] **Step 2: Run** the four test files → FAIL.
- [ ] **Step 3: Implement**; `make openapi && cd web && pnpm gen:api`.
- [ ] **Step 4: Run** `make test` → PASS.
- [ ] **Step 5: Commit** `feat(accounts): reset links under the account-action rule, with notices and a server command`

---

## Slice A3: devices

### Task 7: App version and the pairing link

**Files:**
- Create: `backend/apps/devices/migrations/0002_device_app_version.py`, `backend/tests/test_devices_app_version.py`, `backend/tests/test_devices_server_link.py`
- Modify: `backend/apps/devices/{models,services,auth,api,schemas}.py`, `docs/api/openapi.json`, `web/src/api/schema.d.ts`

**Interfaces:**
- Produces: `Device.app_version = CharField(max_length=50, blank=True, default="")`, `Device.app_version_at = DateTimeField(null=True, blank=True)`.
- Produces: header name `APP_VERSION_HEADER = "X-Brievie-App-Version"` in `apps/devices/services.py`; `clean_app_version(raw: str) -> str` (accepts `^[0-9A-Za-z.+()\- ]{1,50}$` after trimming, else ""); `device_for_token(token: str, *, app_version: str = "") -> Device | None` refreshes `app_version` when the cleaned value is non-empty, differs from the stored one, and `app_version_at` is None or at least 24 hours old (D 7.1: at most once a day), in the same `update()` as `last_seen_at` when both are due. `DeviceTokenAuth.authenticate` passes the header.
- Produces: `PairIn.app_version: str = Field(default="", max_length=50)`; `DeviceOut.app_version: str = ""`; `pair_device(*, code: str, device_name: str, app_version: str = "", client_ip: str | None = None)`; it writes `device_paired` (server log; actor the code's user with the client address; target the device) and `revoke_device` writes `device_revoked`.
- Produces: `server_link() -> str` and `PairingCodeOut.server_link: str = ""`: `brievie://server#` + `urllib.parse.urlencode([("v", "1"), *(("url", u) for u in BRIEVIE_PUBLIC_URLS), ("ca_sha256", pin)])`, the `ca_sha256` pair left out without a private CA. No code, no token, nothing secret (D 6.2, D 20). Part D adds `addresses` here.

- [ ] **Step 1: Failing tests**

```python
def test_pairing_stores_the_app_version(client) -> None                 # PairIn app_version "1.4.0 (12)" -> device.app_version
def test_header_refreshes_the_version_at_most_once_a_day(client) -> None
    # header "1.5.0" -> stored; header "1.5.1" an hour later -> unchanged; app_version_at set back 25 h -> "1.5.1"
def test_a_malformed_header_is_ignored(client) -> None                  # "<script>" and 51 chars -> unchanged
def test_list_devices_shows_the_app_version(client) -> None
def test_server_link_carries_addresses_and_pin_only(settings, monkeypatch) -> None:
    # URLs ["https://brievie.local", "https://192.168.1.4"], pin "ab"*32 ->
    # "brievie://server#v=1&url=https%3A%2F%2Fbrievie.local&url=https%3A%2F%2F192.168.1.4&ca_sha256=abab..."
def test_server_link_has_no_pairing_code(client) -> None                # the code (without dash) not in server_link
def test_pairing_and_revoking_are_logged(client) -> None
```

- [ ] **Step 2: Run** both test files → FAIL.
- [ ] **Step 3: Implement**; `make openapi && cd web && pnpm gen:api`.
- [ ] **Step 4: Run** `make test` → PASS.
- [ ] **Step 5: Commit** `feat(devices): the app version and a pairing link without secrets`

### Task 8: Device tokens that stop when unused

**Files:**
- Create: `backend/apps/devices/migrations/0003_tokenexpirystart.py`, `backend/tests/test_devices_expiry.py`
- Modify: `backend/apps/devices/{models,services,auth}.py`

**Interfaces:**
- Produces: `TokenExpiryStart(BaseModel)` with `started_at` (DateTimeField) and the singleton constraint pattern of `SetupToken`; the migration's `RunPython` creates the row with `timezone.now()`: the moment the update was installed (D 14). There is no extra column on `Device` and no extra write per request (D 7.1).
- Produces: `TOKEN_IDLE_LIMIT = timedelta(days=180)`; pure `token_expired(device: Device, *, baseline: datetime | None, now: datetime) -> bool`: the reference is the later of `device.last_seen_at or device.created_at` and `baseline`; expired when `now - reference >= TOKEN_IDLE_LIMIT`. Part C adds the SSO confirmation rule (D 9.4) to this one function.
- Produces: `device_for_token` loads the device by hash with the baseline in the same query (`Subquery` on `TokenExpiryStart`); revoked, unknown, inactive user → None; expired → in its own `transaction.atomic()` sets `revoked_at = now` and writes `device_expired` (server log, `AuditActor.system()`, target the device) → None. The caller raises exactly the existing `ApiError(401, "not_authenticated", _("Je bent niet ingelogd."))`. `list_devices` and `listUserDevices` leave out expired devices.

- [ ] **Step 1: Failing tests**

```python
@pytest.mark.parametrize(("last_seen_days", "created_days", "baseline_days", "expired"), [
    (179, 400, 400, False), (180, 400, 400, True), (None, 181, 400, True), (None, 100, 400, False),
    (400, 400, 10, False),   # used long ago, but the update was installed 10 days ago
])
def test_token_expired(...) -> None
def test_expired_token_answers_exactly_like_a_revoked_one(client) -> None:
    # two devices, one revoked, one expired; GET /workspaces with each bearer token:
    # equal status (401), equal JSON body, equal headers apart from Date
def test_expiry_is_logged_once(client) -> None              # two requests -> one device_expired row; device.revoked_at set
def test_a_used_token_keeps_working(client) -> None         # last_seen 1 day ago -> 200
def test_expired_devices_are_not_listed(client) -> None
def test_migration_records_the_install_moment() -> None
```

- [ ] **Step 2: Run** `cd backend && uv run pytest tests/test_devices_expiry.py tests/test_devices_auth.py -v` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `make test` → PASS.
- [ ] **Step 5: Commit** `feat(devices): a token stops 180 days after its last use and answers like a revoked one`

### Task 9: An admin over a member's account: devices and sessions

**Files:**
- Create: `backend/tests/test_accounts_user_actions_api.py`
- Modify: `backend/apps/accounts/{api,services,schemas}.py`, `backend/apps/devices/services.py`, `backend/apps/workspaces/{services,schemas,api}.py`, `docs/api/openapi.json`, `web/src/api/schema.d.ts`

**Interfaces:**
- Consumes: `account_authority` (Task 6), `end_sessions` (Task 4), `notify` (Task 5), `token_expired` (Task 8).
- Produces endpoints (default auth):

| Operation | Method, path | Out | Errors |
|---|---|---|---|
| `endUserSessions` | DELETE `/users/{user_id}/sessions` | 204 | 403 `server_admin_only`, 404 `user_not_found`, 401, 403 |
| `listUserDevices` | GET `/users/{user_id}/devices` | `list[DeviceOut]` | 403, 404, 401 |
| `revokeUserDevice` | DELETE `/users/{user_id}/devices/{device_id}` | 204 | 403, 404 `user_not_found` / `device_not_found`, 401 |

- Produces services: `end_user_sessions(request, *, target_id) -> None` (notice `sessions_ended`, `session_ended {"count": n, "reason": "admin"}`), `list_user_devices(request, *, target_id) -> list[Device]`, `revoke_user_device(request, *, target_id, device_id) -> None` (notice `device_revoked` with `{"device": name}`, `device_revoked`). Every audit row goes to the server log and, when `authority.workspace_id`, also to that workspace's log.
- Produces: `MemberOut.can_manage_account: bool = False`, computed for the caller by `list_members(workspace: Workspace, *, viewer: User) -> list[MemberOut]` with the same rule as `account_authority` (one query for the members' membership counts).

- [ ] **Step 1: Failing tests**

```python
def test_workspace_admin_ends_a_members_sessions(client) -> None     # member's client -> 401; notice; server + workspace rows
def test_workspace_admin_cannot_touch_a_member_of_two_workspaces(client) -> None:
    # all three operations -> 403 server_admin_only; member's session alive; device not revoked; no notice
def test_server_admin_lists_and_revokes_anyones_device(client) -> None
def test_revoking_another_users_device_id_is_404(client) -> None    # device of user B under /users/A/... -> device_not_found
def test_a_plain_member_gets_404(client) -> None
def test_list_members_says_which_accounts_the_caller_may_manage(client) -> None
```

- [ ] **Step 2: Run** the new test file plus `tests/test_workspaces_members_api.py` → FAIL.
- [ ] **Step 3: Implement**; update `test_list_members_names_their_person` for the new field; `make openapi && cd web && pnpm gen:api`.
- [ ] **Step 4: Run** `make test` → PASS.
- [ ] **Step 5: Commit** `feat(accounts): admins see and revoke a member's devices and end their sessions`

---

## Slice A4: invitations and members

### Task 10: Invitations for admins

**Files:**
- Create: `backend/apps/workspaces/invitations.py`, `backend/apps/workspaces/access.py`, `backend/apps/workspaces/migrations/0003_invitation.py`, `backend/tests/test_workspaces_invitations_api.py`
- Modify: `backend/apps/workspaces/{models,api,schemas}.py`, `docs/api/openapi.json`, `web/src/api/schema.d.ts`

**Interfaces:**
- Produces: `Invitation(BaseModel)`, a server table like `PairingCode` (not a `WorkspaceModel`, so no RLS and outside `test_rls_coverage.py`): `workspace` (FK CASCADE, `related_name="invitations"`), `role` (CharField 10, `Membership.Role`, default `member`), `person` (FK `people.Person`, SET_NULL, null), `email` (EmailField 254, blank: lower case), `token_hash` (CharField 64, unique), `expires_at`, `created_by` (FK User, SET_NULL, null), `created_by_name` (CharField 150), `accepted_by` (FK User, SET_NULL, null), `accepted_at` (null), `revoked_at` (null), `note` (CharField 100, blank, BSN-masked; shown to admins only).
- Produces (`apps/workspaces/access.py`, part B seams; no-ops in A, each with a docstring naming the part-B work): `person_requires_email(person: Person) -> bool` (returns False; B: True when the person has restricted documents), `on_member_added(workspace_id: UUID, user_id: UUID) -> None`, `on_member_removed(workspace_id: UUID, user_id: UUID) -> None` (B: reader rows, shares, department memberships, as the system).
- Produces services (`apps/workspaces/invitations.py`): `create_invitation(*, membership: Membership, actor: AuditActor, request: HttpRequest, role: str, expires_in_days: int, email: str, person_id: UUID | None, note: str) -> IssuedInvitation` (`IssuedInvitation(invitation: Invitation, link: str, home_network_only: bool)`); `list_invitations(workspace: Workspace) -> list[Invitation]` (not accepted, not revoked; expired ones included with status `expired`; newest first); `revoke_invitation(*, workspace, invitation_id, actor) -> None`; `renew_invitation(*, membership, actor, request, invitation_id) -> IssuedInvitation` (revokes the old one, creates a new one with the same role, person, email and note and the original validity in days).
- Rules: role in `admin|member|viewer`; `expires_in_days` in 1, 7, 30 (D 6.2); the person must belong to the workspace (404 `person_not_found`), not be archived, and have no account link (409 `person_already_linked`, "Deze persoon is al gekoppeld aan een lid."); `person_requires_email(person)` and no email → 422 `email_required`; an email that belongs to a member of this workspace → 409 `already_member`. Audit `invitation_created {"role", "expires_in_days"}`, `invitation_revoked`; target kind `invitation`, name = the note or the email (never the token).
- Produces endpoints (role admin; `SESSION_WRITE_ERRORS` on writes):

| Operation | Method, path | In → Out |
|---|---|---|
| `createInvitation` | POST `/workspaces/{workspace_id}/invitations` | `InvitationIn{role: Literal["admin","member","viewer"] = "member", expires_in_days: Literal[1, 7, 30] = 7, email: str = "" (max 254), person_id: UUID \| None = None, note: str = "" (max 100)}` → 201 `CreatedInvitationOut{invitation: InvitationOut, link: str, home_network_only: bool}` |
| `listInvitations` | GET `/workspaces/{workspace_id}/invitations` | → `list[InvitationOut]` |
| `revokeInvitation` | DELETE `/workspaces/{workspace_id}/invitations/{invitation_id}` | → 204; 404 `invitation_not_found` |
| `renewInvitation` | POST `/workspaces/{workspace_id}/invitations/{invitation_id}/renew` | → 201 `CreatedInvitationOut`; 404 |

  `InvitationOut{id: UUID, role: str, email: str, note: str, person_id: UUID | None, person_name: str = "", created_by_name: str, created_at: datetime, expires_at: datetime, status: str}` (`open` | `expired`). The token is never read back. The link path is `/uitnodiging`.

- [ ] **Step 1: Failing tests**

```python
def test_create_invitation_returns_a_fragment_link_once(client, ws_a, settings) -> None:
    # link r"^https://brievie\.local/uitnodiging#[A-Za-z0-9_-]{43}$"; only the hash stored; listInvitations has no link/token
def test_default_role_is_member_and_validity_seven_days(client, ws_a) -> None
def test_validity_must_be_one_seven_or_thirty_days(client, ws_a) -> None      # 3 -> 422
def test_a_linked_person_cannot_be_invited_again(client, ws_a) -> None       # admin's own person -> 409 person_already_linked
def test_email_of_a_member_is_refused(client, ws_a) -> None                  # 409 already_member
def test_members_and_viewers_cannot_invite(client, ws_a) -> None             # 403 forbidden
def test_revoke_and_renew(client, ws_a) -> None                              # renew: new link, old row revoked, same role/person
def test_invitations_of_another_workspace_are_404(client, ws_a, ws_b) -> None
def test_home_network_only_is_reported(client, ws_a, settings) -> None
def test_invitation_events_are_logged(client, ws_a) -> None
def test_invitation_is_not_a_workspace_table() -> None                       # not issubclass(Invitation, WorkspaceModel)
```

- [ ] **Step 2: Run** `cd backend && uv run pytest tests/test_workspaces_invitations_api.py -v` → FAIL.
- [ ] **Step 3: Implement**; `make openapi && cd web && pnpm gen:api`.
- [ ] **Step 4: Run** `make test` → PASS.
- [ ] **Step 5: Commit** `feat(workspaces): invitations by link, with expiry, revoke and renew`

### Task 11: Opening and accepting an invitation

**Files:**
- Create: `backend/tests/test_workspaces_accept_invitation.py`
- Modify: `backend/apps/workspaces/{invitations,api,schemas}.py`, `backend/apps/accounts/services.py`, `docs/api/openapi.json`, `web/src/api/schema.d.ts`

**Interfaces:**
- Consumes: `Invitation` (Task 10), `start_session` (Task 4), `ensure_person_for_member` (1c), `access.on_member_added`.
- Produces (`apps/accounts/services.py`): `verify_password(request: HttpRequest, *, email: str, password: str) -> User`: the lockout check of `login` (429 `too_many_attempts`), then the account by normalised email **whether active or not**, `check_password`; a wrong password or an unknown address (with a dummy hash run for equal timing) sends `user_login_failed` so axes counts it, and raises 401 `invalid_credentials`. Plain `login` keeps refusing a deactivated account (Django's `ModelBackend`), so a deactivated account comes back only through an invitation and its own password (D 6.2).
- Produces endpoints (`auth=None`, both `ClientAddressThrottle("invitation", "20/10m")`, sharing one count per address):

| Operation | Method, path | In → Out | Errors |
|---|---|---|---|
| `getInvitation` | POST `/invitations/lookup` | `InvitationTokenIn{token: str (max 100)}` → `InvitationInfoOut{workspace_name: str, invited_by: str, role: str, email: str, person_name: str = ""}` | 400 `invitation_invalid`, 429, body errors |
| `acceptInvitation` | POST `/invitations/accept` | `AcceptInvitationIn{token: str, account: Literal["new", "existing"], name: str = "" (max 150), email: str (max 254), password: str (max 128)}` → 200 `AcceptedInvitationOut{user: UserOut, workspace: WorkspaceOut}` | 400 `invitation_invalid`, 401 `invalid_credentials`, 403 `invitation_email_mismatch`, 409 `account_exists` / `already_member` / `invitation_person_taken`, 422, 429, body errors |

- Produces: `accept_invitation(request: HttpRequest, *, token: str, account: str, name: str, email: str, password: str) -> Membership`, in this order:
  1. Look the invitation up by hash (no lock); unknown, accepted, revoked or expired → 400 `invitation_invalid`, "Deze uitnodiging werkt niet meer. Vraag een nieuwe." for every case.
  2. `existing`: `verify_password` **outside** the transaction (a failure must keep axes' record). `new`: an account with this email exists (active or not) → 409 `account_exists` ("Er is al een account met dit e-mailadres. Kies ‘Ik heb al een account’."); otherwise validate email, name (required) and password with the existing rules.
  3. `invitation.email` set and different from the account's → 403 `invitation_email_mismatch`.
  4. `transaction.atomic()`: `select_for_update()` on the invitation and the same validity check again (the second of two simultaneous accepts stops here); already a member → 409 `already_member` (invitation stays unused); create the user for `new`; reactivate an inactive `existing` account; create the membership; in `workspace_context`: with `invitation.person`, lock it, a person that got linked meanwhile → 409 `invitation_person_taken`, else `person.user = user`; without, `ensure_person_for_member`; `on_member_added`; mark accepted; `record(INVITATION_ACCEPTED, values={"role", "new_account", "account_reactivated"})`; `start_session(request, user, via="invitation")`.
  Part C adds `account: "sso"` and the SSO hand-off here (D 6.2).

- [ ] **Step 1: Failing tests**

```python
def test_lookup_shows_workspace_inviter_and_role(client, ws_a) -> None
@pytest.mark.parametrize("state", ["accepted", "revoked", "expired", "unknown"])
def test_dead_invitation_answers_one_sentence(client, ws_a, state) -> None:
    # lookup and accept: 400 {"code": "invitation_invalid", "message": "Deze uitnodiging werkt niet meer. Vraag een nieuwe."}
def test_accept_with_a_new_account_signs_in_and_links_the_person(client, ws_a) -> None
    # invitation with person "Papa": Person.user == new user; GET /me 200; role member; sign_in_succeeded via invitation
def test_accept_without_person_creates_one(client, ws_a) -> None
def test_new_account_with_a_taken_address_is_409(client, ws_a) -> None          # account_exists; invitation unused
def test_existing_account_joins_a_second_workspace(client, ws_a, ws_b) -> None
def test_wrong_password_counts_towards_the_lockout(client, ws_a) -> None         # 5 x 401 -> 6th 429
def test_deactivated_account_comes_back_only_with_its_password(client, ws_a) -> None:
    # inactive user: plain login 401; accept with wrong password 401 and still inactive; right password -> active, member
def test_email_bound_invitation_refuses_another_address(client, ws_a) -> None   # 403 invitation_email_mismatch
def test_already_member_is_409_and_keeps_the_invitation(client, ws_a) -> None
def test_person_linked_meanwhile_is_409(client, ws_a) -> None                    # invitation_person_taken
def test_accept_locks_the_invitation_row(ws_a) -> None:
    # CaptureQueriesContext: the SELECT on workspaces_invitation inside the transaction ends in "FOR UPDATE"
@pytest.mark.django_db(transaction=True)
def test_concurrent_accepts_make_one_membership() -> None:
    # two threads, threading.Barrier(2) before accept_invitation (own RequestFactory requests with sessions);
    # exactly one returns, the other raises ApiError("invitation_invalid"); Membership count for the workspace +1;
    # close each thread's connection; clean up created rows at the end
def test_invitation_token_never_logged(client, ws_a, caplog) -> None            # DEBUG level; lookup, accept, failures
def test_twenty_tries_per_address(client) -> None                               # 21st -> 429
```

- [ ] **Step 2: Run** `cd backend && uv run pytest tests/test_workspaces_accept_invitation.py -v` → FAIL.
- [ ] **Step 3: Implement**; `make openapi && cd web && pnpm gen:api`.
- [ ] **Step 4: Run** `make test` → PASS.
- [ ] **Step 5: Commit** `feat(workspaces): open and accept an invitation, once, signing in through the same function`

### Task 12: Changing a member's role

**Files:**
- Create: `backend/apps/workspaces/members.py`, `backend/tests/test_workspaces_member_role_api.py`
- Modify: `backend/apps/workspaces/{api,schemas}.py`, `docs/api/openapi.json`, `web/src/api/schema.d.ts`

**Interfaces:**
- Produces (`apps/workspaces/members.py`): `lock_memberships(workspace: Workspace) -> list[Membership]` (`select_for_update()`, ordered by id: the one lock order for every member change, so two admins demoting each other cannot both pass); `change_role(*, workspace: Workspace, user_id: UUID, role: str, actor: AuditActor) -> Membership`: unknown member → 404 `member_not_found`; demoting the only admin → 409 `last_admin` ("Een werkruimte heeft altijd minstens één beheerder. Maak eerst iemand anders beheerder."); same role → no write, no event; else `member_role_changed {"from", "to"}` with the member's person name as target name.
- Produces endpoint `updateMemberRole`: PUT `/workspaces/{workspace_id}/members/{user_id}/role`, admin, `MemberRoleIn{role: Literal["admin","member","viewer"]}` → `MemberOut`; errors 404, 409, `SESSION_WRITE_ERRORS`, `BODY_ERRORS`. The member sees the new role at their next request (no session change needed: `workspace_member` reads the membership each time).

- [ ] **Step 1: Failing tests**

```python
def test_admin_changes_a_members_role(client, ws_a) -> None          # member -> viewer; member's next POST to a member endpoint -> 403
def test_last_admin_cannot_demote_themselves(client, ws_a) -> None   # 409 last_admin; role unchanged; no audit row
def test_an_admin_may_step_down_when_another_admin_exists(client, ws_a) -> None
def test_members_cannot_change_roles(client, ws_a) -> None           # 403
def test_unknown_member_is_404(client, ws_a) -> None
def test_role_change_is_logged_with_from_and_to(client, ws_a) -> None
def test_member_changes_take_the_memberships_lock_in_id_order(ws_a) -> None   # captured SQL: one SELECT ... ORDER BY id ... FOR UPDATE
```

- [ ] **Step 2: Run** the test file → FAIL.
- [ ] **Step 3: Implement**; `make openapi && cd web && pnpm gen:api`.
- [ ] **Step 4: Run** `make test` → PASS.
- [ ] **Step 5: Commit** `feat(workspaces): change a member's role, never below one admin`

### Task 13: Removing a member and leaving

**Files:**
- Create: `backend/tests/test_workspaces_remove_member.py`
- Modify: `backend/apps/workspaces/{members,api}.py`, `backend/apps/accounts/services.py`, `docs/api/openapi.json`, `web/src/api/schema.d.ts`

**Interfaces:**
- Consumes: `lock_memberships` (Task 12), `end_sessions` (Task 4), `other_active_server_admin_exists` (Task 3), `access.on_member_removed` (Task 10).
- Produces (`apps/accounts/services.py`): `deactivate_account(user: User, *, actor: AuditActor) -> None`: `is_active = False`, `end_sessions(user)`, every unrevoked device `revoked_at = now`, unused pairing codes deleted, unused reset links revoked.
- Produces (`apps/workspaces/members.py`): `remove_member(*, workspace: Workspace, user_id: UUID, actor: AuditActor, left: bool) -> None`, one `transaction.atomic()` (D 6.3):
  1. `lock_memberships`; unknown → 404 `member_not_found`; the only admin → 409 `last_admin` (also when they remove or leave themselves).
  2. No other membership on the server and the user is a server admin without another active one → 409 `last_admin` ("Er is altijd minstens één serverbeheerder nodig. Dit account kan daarom niet weg.").
  3. In `workspace_context`: `Person.objects.filter(workspace=…, user_id=…).update(user=None)` (an `update()`, before the membership goes, because `Person.save` refuses a non-member), `Task.objects.filter(workspace=…, assigned_to_id=…).update(assigned_to=None)`, `on_member_removed(workspace.id, user_id)`; the member's exports are removed by Task 16's hook.
  4. Delete the membership; with none left, `deactivate_account`.
  5. `record(MEMBER_LEFT if left else MEMBER_REMOVED, values={"account_deactivated": …})`, and `person_unlinked` when a person was unlinked.
  Writes "as the system" (D 6.3) need nothing extra in part A: without part B every row in the workspace is visible to the admin; part B wraps steps 3 and 4 in `system_actor()`.
- Produces endpoints: `removeMember` DELETE `/workspaces/{workspace_id}/members/{user_id}` (admin) → 204; `leaveWorkspace` POST `/workspaces/{workspace_id}/leave` (any role, no body) → 204, then the caller's session ends when the account was deactivated. Errors 404, 409, `SESSION_WRITE_ERRORS`.

- [ ] **Step 1: Failing tests**

```python
def test_last_admin_cannot_leave_or_remove_themselves(client, ws_a) -> None:  # DELETE own id and POST leave -> 409; nothing changed
def test_removal_unlinks_the_person_and_keeps_it(client, ws_a) -> None
def test_removal_unassigns_the_members_tasks(client, ws_a) -> None            # tasks of ws_a only; a task in ws_b stays assigned
def test_removing_the_last_membership_deactivates_the_account(client, ws_a) -> None:
    # is_active False; their web client -> 401; their device token -> 401 not_authenticated; pairing code unusable
def test_member_of_two_workspaces_removed_from_one_keeps_account_and_devices(client, ws_a, ws_b) -> None:
    # device token: GET /workspaces lists only ws_b; GET ws_a's documents -> 404 workspace_not_found
def test_removed_member_cannot_upload_or_confirm(client, ws_a) -> None       # upload with token -> 401; confirm with session -> 401
def test_last_server_admin_cannot_be_deactivated(client, ws_a) -> None
def test_member_leaves(client, ws_a) -> None                                 # member_left logged; person unlinked
def test_re_invited_member_gets_their_person_back(client, ws_a) -> None      # invitation with the old person -> linked again
def test_removal_calls_the_part_b_seam(client, ws_a, monkeypatch) -> None    # on_member_removed called once with (ws id, user id)
```

- [ ] **Step 2: Run** the test file → FAIL.
- [ ] **Step 3: Implement**; `make openapi && cd web && pnpm gen:api`.
- [ ] **Step 4: Run** `make test` → PASS.
- [ ] **Step 5: Commit** `feat(workspaces): remove a member or leave, with devices and sessions ending with the last membership`

---

## Slice A5: what is logged, reading the log, the own export

### Task 14: Audit rows from the existing services, and workspace settings

**Files:**
- Create: `backend/apps/workspaces/settings_service.py`, `backend/apps/workspaces/migrations/0004_workspace_allow_member_export.py`, `backend/tests/test_audit_existing_services.py`, `backend/tests/test_workspaces_settings_api.py`
- Modify: `backend/apps/rules/{services,api}.py`, `backend/apps/labels/{services,api}.py`, `backend/apps/people/{services,api}.py`, `backend/apps/pipeline/{settings_service,api}.py`, `backend/apps/pipeline/management/commands/configure_ai.py`, `backend/apps/workspaces/{models,services,api,schemas}.py`, the tests that call these services directly, `docs/api/openapi.json`, `web/src/api/schema.d.ts`

**Interfaces:**
- Produces: a required keyword `actor: AuditActor` on `create_rule`, `update_rule`, `delete_rule`, `run_rule`, `delete_label`, `delete_person`, `link_member` (callers pass `actor_from_request(request)`; tests pass `AuditActor.system()` or a user actor). They write `rule_created`, `rule_changed`, `rule_deleted`, `rule_run`, `label_deleted`, `person_deleted`, `person_linked` / `person_unlinked`. Rule and label rows carry the id only, with an empty target name (D 11: never label names; a rule name can hold one). Person rows carry the person's display name.
- Produces: `settings_service.Actor` gains `client_ip: str | None = None`; `configure(...)` writes `workspace_setting_changed {"setting": "ai", "value": mode}` in the same transaction as the `AIConsentEvent` it already writes, with the user from `Actor.user_id` (or `AuditActor.system()` for the command). The consent table, its events and the rule "updating AI settings needs role admin" stay as they are (DECISIONS 2026-11-17).
- Produces: `Workspace.allow_member_export = BooleanField(default=True)`; the migration sets it False for `business` workspaces; `create_workspace` sets `kind == "home"`. D 6.3: off by default in Zakelijk.
- Produces endpoints: `getWorkspaceSettings` GET `/workspaces/{workspace_id}/settings` (any member) → `WorkspaceSettingsOut{allow_member_export: bool}`; `updateWorkspaceSettings` PUT same path (admin), `WorkspaceSettingsIn{allow_member_export: bool}` → `WorkspaceSettingsOut`, writes `workspace_setting_changed {"setting": "allow_member_export", "value": …}` when it changes.

- [ ] **Step 1: Failing tests**

```python
@pytest.mark.parametrize(("call", "code"), [...])   # one row per service above
def test_service_writes_its_audit_code(ws_a, call, code) -> None
def test_label_and_rule_rows_carry_no_name(ws_a) -> None
def test_ai_settings_change_is_logged_beside_the_consent_event(client, ws_a) -> None
def test_configure_ai_command_is_logged_as_the_system(ws_a) -> None
def test_member_export_is_on_for_home_and_off_for_business(ws_a, ws_b) -> None
def test_only_an_admin_changes_workspace_settings(client, ws_a) -> None
```

- [ ] **Step 2: Run** both test files → FAIL.
- [ ] **Step 3: Implement**; update the direct callers in existing tests (`grep -rn "delete_label(\|delete_person(\|create_rule(\|update_rule(\|delete_rule(\|run_rule(\|link_member(" backend/tests`); `make openapi && cd web && pnpm gen:api`.
- [ ] **Step 4: Run** `make test` → PASS.
- [ ] **Step 5: Commit** `feat(audit): rules, labels, persons and settings write the audit log`

### Task 15: Reading the log, with filters and CSV

**Files:**
- Create: `backend/apps/audit/{api,schemas}.py`, `backend/tests/test_audit_api.py`
- Modify: `backend/apps/audit/services.py`, `backend/brievie/api.py`, `docs/api/openapi.json`, `web/src/api/schema.d.ts`

**Interfaces:**
- Produces: `AuditFilter` (dataclass: `person_id: UUID | None`, `code: str`, `since: date | None`, `until: date | None`, `before: datetime | None`, `limit: int`); `list_workspace_events(workspace: Workspace, f: AuditFilter) -> list[WorkspaceAuditEvent]`, `list_server_events(f: AuditFilter) -> list[ServerAuditEvent]`; `person_id` matches `actor_id` or `target_id`; `since`/`until` are whole days in `Europe/Amsterdam`, both inclusive; `before` pages backwards; `limit` 1–500, default 100. `csv_rows(events) -> Iterator[list[str]]` with the header `Tijdstip, Wie, Gebeurtenis, Code, Om wie of wat, Adres, Details`; every cell that starts with `=`, `+`, `-`, `@`, tab or carriage return gets a leading `'` (spreadsheet formula injection).
- Produces endpoints (query parameters all optional with defaults: `person_id: UUID | None = None`, `code: str = ""`, `since: date | None = None`, `until: date | None = None`, `before: datetime | None = None`, `limit: int = 100`):

| Operation | Method, path | Who | Out |
|---|---|---|---|
| `listWorkspaceAuditEvents` | GET `/workspaces/{workspace_id}/audit-events` | workspace admin | `list[AuditEventOut]` |
| `exportWorkspaceAuditEvents` | GET `/workspaces/{workspace_id}/audit-events/csv` | workspace admin | `text/csv` (`openapi_extra` like `getDocumentPage`), `Content-Disposition: attachment; filename="brievie-logboek-<yyyy-mm-dd>.csv"`, up to 10,000 rows |
| `listServerAuditEvents` | GET `/server/audit-events` | server admin (else 403 `forbidden`) | `list[AuditEventOut]` |
| `exportServerAuditEvents` | GET `/server/audit-events/csv` | server admin | `text/csv` |

  `AuditEventOut{id: UUID, created_at: datetime, actor_kind: str, actor_id: UUID | None, actor_name: str, actor_device_id: UUID | None, code: str, label: str, target_kind: str, target_id: UUID | None, target_name: str, client_ip: str = "", values: dict[str, str]}` (`label` is the code's Dutch label; values as strings, so no `anyOf`).

- [ ] **Step 1: Failing tests**

```python
def test_only_admins_read_the_workspace_log(client, ws_a) -> None          # member 403, admin 200
def test_a_workspace_log_never_shows_another_workspace(client, ws_a, ws_b) -> None
def test_filters_on_person_code_and_days(client, ws_a) -> None
def test_before_pages_backwards(client, ws_a) -> None
def test_server_log_is_for_server_admins(client, ws_a) -> None
def test_csv_has_the_header_and_guards_formulas(client, ws_a) -> None      # person named "=HYPERLINK(...)" -> cell "'=HYPERLINK(...)"
def test_csv_is_an_attachment(client, ws_a) -> None
```

- [ ] **Step 2: Run** the test file → FAIL.
- [ ] **Step 3: Implement**; `make openapi && cd web && pnpm gen:api`.
- [ ] **Step 4: Run** `make test` → PASS.
- [ ] **Step 5: Commit** `feat(audit): read the workspace and server logs with filters and a CSV download`

### Task 16: The member's own export

**Files:**
- Create: `backend/apps/common/sealed_stream.py`, `backend/apps/documents/export.py`, `backend/apps/documents/member_export.py`, `backend/apps/documents/jobs.py`, `backend/apps/documents/migrations/<next number>_memberexport.py` (from `makemigrations documents`), `backend/tests/test_common_sealed_stream.py`, `backend/tests/test_documents_export.py`, `backend/tests/test_documents_member_export_api.py`
- Modify: `backend/apps/documents/{models,api,schemas}.py`, `backend/apps/workspaces/members.py` (removal deletes the member's exports), `docs/api/openapi.json`, `web/src/api/schema.d.ts`

**Interfaces:**
- Produces (`apps/common/sealed_stream.py`), so a large ZIP never exists in plaintext on disk or whole in memory: `class SealedWriter(io.RawIOBase)` with `__init__(self, target: BinaryIO, key: bytes, *, aad: bytes)`: buffers 4 MiB, writes each chunk as a 4-byte big-endian length plus `crypto.seal(key, chunk, aad=aad + b"|" + index + b"|" + (b"1" if last else b"0"))`; `close()` writes the last chunk (possibly empty). `iter_opened(source: BinaryIO, key: bytes, *, aad: bytes) -> Iterator[bytes]`: raises `DecryptionError` on a changed, reordered or missing chunk, and when the stream ends without a last chunk.
- Produces (`apps/documents/export.py`), the building block of Beheer 5 (part E adds the PDF per letter and the workspace export to it): `@dataclass(frozen=True) class ExportCounts: documents: int; pages: int`; `write_export(*, workspace_id: UUID, document_ids: Sequence[UUID], target: BinaryIO, title: str) -> ExportCounts`: a ZIP (`zipfile`, streamed, no seek) with `pagina's/<document id>/<n>.jpg` (each page opened from its sealed file), `brievie-export.json` (`{"version": 1, "documents": [{"id", "status", "sender", "document_type", "letter_date", "summary", "summary_by_ai", "created_at", "persons": [names], "labels": [names], "tasks": [{"type", "description", "deadline", "status"}], "pages": [paths]}]}`) and `LEESMIJ.txt` (Dutch: what is in it; the BSN is masked in the text but visible on the page images; the file is not encrypted once downloaded). It loads each document in its own short query inside the caller's `workspace_context`.
- Produces: `MemberExport(WorkspaceModel)` with `enable_rls`: `user` (FK User, PROTECT), `status` (`pending`, `ready`, `failed`), `storage_path` (CharField 300, blank; `exports/<workspace id>/<export id>.sealed` under `BRIEVIE_STORAGE_ROOT`, outside the sweep's `<uuid>/<uuid>/<n>.page` layout), `document_count` (int 0), `size` (BigInteger 0), `expires_at` (null). Validity 24 hours.
- Produces services (`apps/documents/member_export.py`): `member_document_ids(workspace_id: UUID, user_id: UUID) -> list[UUID]`, the part-A limit to "every document on the member's name": documents with a `DocumentAddressee` whose person's `user_id` is the member. **Seam for part B:** the job enters `workspace_context(workspace_id)` here; part B passes `actor=user_id` to it, so row-level security also leaves out private letters the member may not read. `create_my_export(*, membership: Membership, actor: AuditActor) -> MemberExport` (`allow_member_export` off → 403 `export_not_allowed`, "In deze werkruimte staat exporteren uit. Vraag de beheerder."; a pending one → 409 `export_running`; an older ready one is removed; defers the job; `export_created`); `my_export(membership) -> MemberExport | None`; `open_my_export(*, membership, actor) -> Iterator[bytes]` (ready, own, not expired, else 404 `export_not_found`; `export_downloaded`); `remove_member_exports(workspace_id: UUID, user_id: UUID) -> None` (rows now, files `transaction.on_commit`).
- Produces jobs (`apps/documents/jobs.py`, queue `pipeline`): `build_member_export(*, workspace_id: str, export_id: str)`: ids in a short transaction, the sealed file written outside any open transaction (pages read one at a time), then `ready` with `expires_at = now + 24 h`, `document_count`, `size` in a short transaction; any failure → `failed`, the partial file removed, the type of the exception logged only. `remove_expired_exports` (`@app.periodic(cron="*/30 * * * *")`): per workspace, files and rows past `expires_at`, and `pending` older than 2 hours become `failed`.
- Produces endpoints (any role, own export only):

| Operation | Method, path | Out | Errors |
|---|---|---|---|
| `createMyExport` | POST `/workspaces/{workspace_id}/my-export` | 202 `MyExportOut` | 403 `export_not_allowed`, 409 `export_running`, 404, 401, 403 |
| `getMyExport` | GET `/workspaces/{workspace_id}/my-export` | `MyExportOut` | 404 `workspace_not_found`, 401 |
| `downloadMyExport` | GET `/workspaces/{workspace_id}/my-export/file` | `application/zip`, streamed (`StreamingHttpResponse`), `Cache-Control: private, no-store`, `Content-Disposition: attachment; filename="brievie-mijn-post-<yyyy-mm-dd>.zip"` | 404 `export_not_found`, 401 |

  `MyExportOut{allowed: bool, status: str ("none" | "pending" | "ready" | "failed"), id: UUID | None = None, created_at: datetime | None = None, expires_at: datetime | None = None, document_count: int = 0, size: int = 0}` (flat, primitives only).

- [ ] **Step 1: Failing tests**

```python
# test_common_sealed_stream.py
def test_round_trip_over_several_chunks() -> None                  # 9 MiB of random bytes
def test_a_missing_last_chunk_fails() -> None                      # cut after the first chunk -> DecryptionError
def test_reordered_chunks_fail() -> None
def test_another_aad_fails() -> None
# test_documents_export.py
def test_export_holds_only_the_given_documents_pages_and_data(ws_a) -> None
def test_export_json_names_persons_labels_and_tasks(ws_a) -> None
def test_member_document_ids_are_the_documents_on_their_name(ws_a) -> None   # not the documents of another person
# test_documents_member_export_api.py
def test_member_exports_and_downloads_their_post(client, ws_a) -> None:
    # POST -> 202 pending; run the job inline; GET -> ready; download -> a valid ZIP with only their letters
def test_the_file_on_disk_is_sealed(client, ws_a, settings) -> None   # no b"PK\x03\x04" and no page bytes in the stored file
def test_another_member_cannot_download_it(client, ws_a) -> None      # 404 export_not_found
def test_business_workspace_refuses_until_allowed(client, ws_b) -> None
def test_expired_export_is_removed(ws_a) -> None                      # expires_at in the past -> remove_expired_exports deletes file and row
def test_removing_the_member_removes_their_export(client, ws_a) -> None
def test_export_is_logged(client, ws_a) -> None                       # export_created {"document_count"}, export_downloaded
```

- [ ] **Step 2: Run** the three test files → FAIL.
- [ ] **Step 3: Implement**; the migration ends with `enable_rls("documents_memberexport")`; `make openapi && cd web && pnpm gen:api`.
- [ ] **Step 4: Run** `make test` → PASS (`test_rls_coverage.py` covers the new table).
- [ ] **Step 5: Commit** `feat(documents): a member exports their own post as a sealed ZIP for 24 hours`

---

## Slice A6: the web interface

Every web task: add queries to `web/src/api/queries.ts`, routes to `router.tsx`, strings through `t`, then `cd web && pnpm extract && pnpm test && pnpm lint && pnpm typecheck && pnpm build`. Tests use MSW handlers and `renderApp`, query by role, and wait for the visible result. New icons in `icons.tsx` (20 px, 1.5 px stroke, `aria-hidden`): `personAdd`, `key`, `shield`, `history`, `settings`, `laptop`, `qr`.

### Task 17: The page "Leden"

**Files:**
- Create: `web/src/routes/members.tsx`, `web/src/routes/members.test.tsx`, `web/src/components/role-dialog.tsx`
- Modify: `web/src/router.tsx`, `web/src/components/page.tsx` (nav item "Leden", icon `personAdd`, for every role), `web/src/components/icons.tsx`, `web/src/api/queries.ts`, `web/src/styles.css`, `web/src/locales/nl/messages.po`

**Interfaces:**
- Consumes: `listMembers` (with `can_manage_account`), `updateMemberRole`, `removeMember`, `leaveWorkspace`.
- Produces: route `/w/$workspaceId/leden` (`membersRoute`, child of `workspaceRoute`); `membersQuery` exists. A details list: Persona, name, role as a badge ("Beheerder", "Lid", "Meekijker"); for an admin per row "Rol wijzigen" (a dialog with three radios, each with one plain sentence: Beheerder "Kan leden uitnodigen en alles instellen.", Lid "Kan post scannen, controleren en ordenen.", Meekijker "Kan alles lezen, maar niets wijzigen.") and "Verwijderen" (alertdialog: "<naam> kan daarna niet meer bij deze werkruimte. De post blijft hier. Taken van <naam> hebben daarna niemand."); on the caller's own row "Werkruimte verlaten" (alertdialog; Task 21 adds the export offer). At home nothing about roles shows unless somebody opens "Rol wijzigen" (D 6.1). A 409 `last_admin` shows the server's sentence in the dialog.

- [ ] **Step 1: Failing tests**

```tsx
test("lists members with their role")                     // getByRole("row", {name: /Anna.*Beheerder/})
test("an admin changes a role with three plain choices")   // radios "Beheerder", "Lid", "Meekijker"; PUT body {role: "viewer"}
test("a member sees no admin actions")                     // queryByRole("button", {name: /Rol wijzigen/}) null
test("removing asks first and explains what stays")
test("the last admin sees why they cannot leave")          // 409 last_admin -> its message in the dialog
test("the navigation links Leden")
```

- [ ] **Step 2: Run** `cd web && pnpm test src/routes/members.test.tsx` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full web check → PASS.
- [ ] **Step 5: Commit** `feat(web): the Leden page with roles, removing and leaving`

### Task 18: Inviting with a link and a QR code

**Files:**
- Create: `web/src/components/invite-dialog.tsx`, `web/src/components/invite-dialog.test.tsx`
- Modify: `web/src/routes/members.tsx`, `web/src/routes/members.test.tsx`, `web/src/api/queries.ts`, `web/src/locales/nl/messages.po`

**Interfaces:**
- Consumes: `createInvitation`, `listInvitations`, `revokeInvitation`, `renewInvitation`, `listPersons`.
- Produces: on Leden for admins, the command "Uitnodigen" opens `InviteDialog`: "Rol" (radios as in Task 17, default Lid), "Wie is het?" (optional: persons without an account link, as in `person-picker`; "Iemand nieuw" default), "E-mailadres" (optional, with the description "Alleen dit adres kan de uitnodiging gebruiken."), "Geldig" (select: 1 dag, 7 dagen (default), 30 dagen), "Notitie voor jezelf" (optional). The result view: the link in a read-only field with "Kopieer link", `QRCodeSVG` of the same link with the accessible name "QR-code met de uitnodiging", the expiry, and with `home_network_only` the message bar "Deze link werkt alleen op je thuisnetwerk." The link is shown once; closing the dialog forgets it. Below the members, "Open uitnodigingen" lists `listInvitations` (note or email, role, "Verloopt op …" or badge "Verlopen", actions "Nieuwe link" (opens the result view with the new link) and "Intrekken" (alertdialog)).

- [ ] **Step 1: Failing tests**

```tsx
test("inviting shows the link once with a QR code")       // getByRole("img", {name: "QR-code met de uitnodiging"}); link text shown
test("the default invitation is a member for seven days")  // POST body {role: "member", expires_in_days: 7, email: "", person_id: null, note: ""}
test("a person can be chosen as who it is")                // only persons without user_id offered
test("a home-network-only link says so")
test("open invitations can be renewed and revoked")
test("a member does not see Uitnodigen")
```

- [ ] **Step 2: Run** the two test files → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full web check → PASS.
- [ ] **Step 5: Commit** `feat(web): invite with a link or QR code and manage open invitations`

### Task 19: Accepting an invitation, then the app

**Files:**
- Create: `web/src/routes/invitation.tsx`, `web/src/routes/invitation.test.tsx`
- Modify: `web/src/router.tsx`, `web/src/api/queries.ts`, `web/src/locales/nl/messages.po`

**Interfaces:**
- Consumes: `getInvitation`, `acceptInvitation`, `createPairingCode` (`server_link`, `code`, `qr_payload`).
- Produces: route `/uitnodiging` (child of `rootRoute`, no guard: it works signed in or not). The token is read from `useLocation().hash` (the URL fragment); it goes only into the JSON bodies of `POST /invitations/lookup` and `/invitations/accept`, never into a URL. Without a fragment, or on 400: the server's sentence and nothing else. Otherwise "<invited_by> nodigt je uit voor <workspace_name>" with the role in a plain sentence, then a choice "Nieuw account" (Naam, E-mailadres prefilled and read-only when the invitation has one, Wachtwoord with the rule hint of setup) or "Ik heb al een account" (E-mailadres, Wachtwoord). After success the page calls `createPairingCode` and shows: on a touch device (`useMediaQuery("(pointer: coarse)")`) a link "Open de Brievie-app" (`href={server_link}`) and the eight-character code to type in the app, with its countdown as on `/koppel-app`; otherwise the pairing QR code (`qr_payload`) with "Scan deze code met de Brievie-app". Both show "Naar Brievie" to `/`.

- [ ] **Step 1: Failing tests**

```tsx
test("the token goes in the body, not the url")            // MSW: new URL(request.url).search === "" and no token in the path; body.token === "abc"
test("a dead invitation shows the server's sentence")      // "Deze uitnodiging werkt niet meer. Vraag een nieuwe."
test("a new account is made and the app link is offered on a phone")   // matchMedia stub (pointer: coarse) -> link "Open de Brievie-app" with href brievie://server#...; code "ABCD-2345"
test("on a computer the pairing QR code is shown")
test("an existing account signs in to accept")             // POST body account "existing"
test("a wrong password shows the server's sentence and keeps the form")
```

- [ ] **Step 2: Run** `cd web && pnpm test src/routes/invitation.test.tsx` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full web check → PASS.
- [ ] **Step 5: Commit** `feat(web): accept an invitation and continue in the app`

### Task 20: Devices, sessions, password and notices

**Files:**
- Create: `web/src/routes/sessions.tsx`, `web/src/routes/password.tsx`, `web/src/routes/password-reset.tsx`, `web/src/routes/member-devices.tsx`, `web/src/components/notice-bar.tsx`, tests next to each
- Modify: `web/src/routes/pair-app.tsx` (title "Apparaten"), `web/src/components/device-table.tsx` (column "App-versie"; optional `userId` prop for the admin view), `web/src/components/page.tsx` (account menu: "Apparaten", "Waar je bent ingelogd", "Wachtwoord wijzigen"; `NoticeBar` in `Shell`), `web/src/routes/members.tsx` (per row with `can_manage_account`: "Apparaten en sessies", "Herstellink maken"), `web/src/router.tsx`, `web/src/api/queries.ts`, `web/src/locales/nl/messages.po`

**Interfaces:**
- Consumes: `listDevices` (`app_version`), `listUserDevices`, `revokeUserDevice`, `endUserSessions`, `listMySessions`, `endMySession`, `changePassword`, `createPasswordReset`, `completePasswordReset`, `listMyNotices`, `markNoticesSeen`.
- Produces routes: `/account/sessies` ("Waar je bent ingelogd": browser, method, address, started, last used, badge "Deze browser", "Afmelden" per row); `/account/wachtwoord` (current, new, repeat; success message "Je wachtwoord is gewijzigd. Je andere sessies zijn beëindigd."); `/wachtwoord-herstellen` (root child, no guard; token from the fragment into the body of `POST /auth/password-reset`; then to `/`); `/w/$workspaceId/leden/$userId` (admin, "Apparaten en sessies van <naam>": the device table with revoke, the button "Alle sessies beëindigen" with an alertdialog). "Herstellink maken" on Leden opens a dialog with the link once and the sentence "Geef deze link aan <naam>. Hij werkt 24 uur en één keer." `NoticeBar`: one info message bar per unseen notice with the server's message and a button "Gezien" (marks it seen).

- [ ] **Step 1: Failing tests**

```tsx
test("devices show the app version")
test("sessions mark this browser and end another")          // DELETE /me/sessions/{id}; the row disappears
test("changing the password reports that other sessions ended")
test("the reset page sends the token in the body")         // as in Task 19
test("an admin ends a member's sessions after confirming")
test("a reset link is shown once with whom to give it")
test("a notice shows until it is marked seen")             // "Je wachtwoord is op 3 mei opnieuw ingesteld door Anne."
```

- [ ] **Step 2: Run** the new test files → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full web check → PASS.
- [ ] **Step 5: Commit** `feat(web): devices with app versions, sessions, password change and reset, notices`

### Task 21: Instellingen, the log and "Mijn post exporteren"

**Files:**
- Create: `web/src/routes/settings.tsx`, `web/src/routes/audit-log.tsx`, `web/src/routes/server-audit-log.tsx`, `web/src/components/audit-table.tsx`, `web/src/components/export-panel.tsx`, tests next to each
- Modify: `web/src/components/page.tsx` (nav item "Instellingen", icon `settings`, admins only; account menu "Serverlogboek" for `is_server_admin`), `web/src/routes/members.tsx` (the export panel and the leave dialog's export offer), `web/src/router.tsx`, `web/src/api/queries.ts`, `web/src/locales/nl/messages.po`

**Interfaces:**
- Consumes: `getWorkspaceSettings`, `updateWorkspaceSettings`, `listWorkspaceAuditEvents`, `exportWorkspaceAuditEvents`, `listServerAuditEvents`, `exportServerAuditEvents`, `createMyExport`, `getMyExport`, `downloadMyExport`.
- Produces routes: `/w/$workspaceId/instellingen` (admin): switch "Leden mogen hun eigen post exporteren" and a link "Logboek"; `/w/$workspaceId/instellingen/logboek`: filters "Persoon" (members), "Gebeurtenis" (the closed list with Dutch labels, through Lingui in the web), "Van" and "Tot" (dates), a details list (Tijdstip, Wie, Gebeurtenis, Om wie of wat, Adres), "Meer laden" (`before` = the last row's time), and a link "Download als CSV" whose `href` is the CSV endpoint with the same query (a GET with the session cookie; no token involved); `/server/logboek` the same for the server log. `ExportPanel` on Leden for the caller: "Mijn post exporteren", then "Wordt gemaakt…" (polls `getMyExport` every 3 s while pending), then "Download (<n> brieven, geldig tot <tijd>)" as a link to `/my-export/file`; hidden with a sentence when `allowed` is false. The leave dialog first offers the export when allowed: "Exporteer eerst je post: na het verlaten kun je niet meer downloaden."

- [ ] **Step 1: Failing tests**

```tsx
test("an admin switches member export")                   // PUT body {allow_member_export: false}
test("the log filters by person and kind and loads more")  // query string carries person_id and code; second page uses before
test("the csv link carries the same filters")              // getByRole("link", {name: "Download als CSV"}) href
test("the server log is only in a server admin's menu")
test("a member exports, waits and downloads")
test("the leave dialog offers the export first")
test("export is hidden when the workspace does not allow it")
```

- [ ] **Step 2: Run** the new test files → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full web check → PASS.
- [ ] **Step 5: Commit** `feat(web): settings, the audit log with CSV, and the own export`

### Task 22: Documentation and the last checks

**Files:**
- Modify: `docs/DECISIONS.md` (one new section at the end), `docs/GLOSSARY.md`, `docs/PRIVACY.md`, `docs/SELFHOST.md` (the `reset_password` command; the two new environment settings)

**Interfaces:**
- Produces: `## 2026-11-17: Members and devices (phase 1d, part A)` at the end of `DECISIONS.md`, one line each: the audit tables (no foreign keys, the overridden `workspace` field, the REVOKE in the migration and why the test checks `brievie_app` itself; that a restore keeps the privileges because `pg_restore` runs without `--no-privileges`); the purge functions and the retention setting; `start_session` as the only sign-in, `UserSession` through the `user_logged_in` receiver, the middleware failing closed and the migration for existing sessions; session lifetime through `BRIEVIE_SESSION_IDLE_DAYS`/`MAX_DAYS` until part C's server settings page; the account-action rule and its two error codes; notices composed on the server; invitation and reset tokens (fragment, body, hash, one sentence for every dead link, `POST` for the lookup); `Invitation` as a server table; reactivation only through an invitation and the own password; `last_admin` for workspaces and for the server; device expiry (180 days, `TokenExpiryStart`, revoked on detection, same 401); `X-Brievie-App-Version` at most once a day; the pairing link `brievie://server#v=1&url=…&ca_sha256=…`; the own export (part-A scope "documents with an addressee linked to the member", chunked sealing in `sealed_stream`, `documents.export` as the shared building block, ZIP of JPEGs plus JSON until part E adds PDFs, off in Zakelijk); the 1c contract fixture. In "Open items", strike "Two-factor authentication is not built" only after part C, and replace "Device tokens never expire…" and "Removing a member must unlink their person…" with a pointer to this section.
- Produces GLOSSARY rows: serverbeheerder | server admin; uitnodiging | invitation; herstellink | password reset link; sessie, "Waar je bent ingelogd" | session (`UserSession`); logboek | audit log; melding (aan een account) | notice (`AccountNotice`); intrekken (apparaat) | revoke; verlopen (koppeling) | expired; werkruimte verlaten | leave; lid verwijderen | remove member; mijn post exporteren | member export; koppellink | server link; app-versie | app version.
- Produces PRIVACY: the audit log (what it holds, client addresses, one year by default), sessions (address and browser description), the export (unencrypted once downloaded; the BSN visible on page images).

- [ ] **Step 1:** Write the three documents.
- [ ] **Step 2: Run** `make lint typecheck test openapi` and confirm `git diff --exit-code docs/api/openapi.json`; `cd web && pnpm gen:api && git diff --exit-code src/api/schema.d.ts && pnpm extract && pnpm test && pnpm lint && pnpm build` → all pass, nothing changed.
- [ ] **Step 3: Manual check** on a local stack with a throwaway database: invite a second user in one browser profile, accept in a private window, open the app link on a phone or the QR on a computer, remove the member and see their session end; record the result in the task report (not verified by tests: a real phone following `brievie://`).
- [ ] **Step 4: Commit** `docs: members and devices (phase 1d, part A)`

---

## Plan decisions

1. `WorkspaceAuditEvent` subclasses `WorkspaceModel` but overrides `workspace` with `db_constraint=False, on_delete=DO_NOTHING`: no foreign key (D 11) while the RLS coverage test and the house rule still apply.
2. The migration both REVOKEs and GRANTs on the audit tables, guarded by the role's existence: the test database has no default privileges, so a test that only checked "UPDATE is false" would pass with a forgotten REVOKE.
3. `start_session` is the only caller of Django's `login` (a test enforces it), and a `user_logged_in` receiver records `UserSession`: one hook for part C, and `force_login` in existing tests keeps working.
4. A session without a `UserSession` row is signed out (fail closed); a data migration records existing sessions, so the upgrade signs nobody out.
5. Session lifetime is enforced twice: Django's own `expire_date` and cookie (set by the middleware) and an explicit check of both limits; the limits are environment settings until part C's server settings page.
6. Account actions use one function, `account_authority`, with 404 for strangers and 403 `server_admin_only` for a member who also lives elsewhere: the reviewer's ruling of D 20 in one testable place.
7. Notices are composed in Dutch on the server: the web and a later iOS screen show the same sentence without repeating the list.
8. `Invitation` and `PasswordReset` are server tables found by token hash; the lookup is a `POST` so the token is in a body; every dead link answers one sentence with a 400, like `pairing_code_invalid`.
9. Accepting checks an existing account's password before taking the invitation's row lock, so a failed password keeps axes' record while the lock still decides the race.
10. Reactivation happens only in `accept_invitation` with the account's own password; plain sign-in still refuses a deactivated account, as Django's backend does.
11. Removal unlinks the person with a queryset `update()` before deleting the membership, because `Person.save` refuses a user who is no longer a member.
12. The install moment for device expiry lives in a one-row table (`TokenExpiryStart`) read in the same query as the device: no column per device, no extra write (D 7.1, D 14).
13. An expired token is revoked when it is detected, so the expiry is logged once and every later answer comes from the revoked path; the 401 body is the existing one.
14. The app version is refreshed when it differs and the stored one is at least a day old (`app_version_at`): the "at most once a day" of D 7.1 without a write per request.
15. The pairing link is URL-encoded in the fragment (`v`, repeated `url`, `ca_sha256`): `URLComponents` parses it on iOS, and it carries no code.
16. The own export in part A covers documents with an addressee linked to the member; part B adds the actor to the job's context, which is the only change needed there.
17. The export is sealed in 4 MiB AES-GCM chunks with index and last-chunk flag in the associated data: no plaintext on disk, bounded memory, and truncation is detected.
18. `allow_member_export` is a column with a kind-dependent default, set by a data migration: D 6.3's "off in Zakelijk unless allowed" without a settings framework.
19. A `openapi_1c.json` fixture joins `openapi_1b.json`, so the contract test guards what a 1c app was built against (D 14).
20. Rule and label audit rows carry ids only: D 11 forbids label names, and a rule name can contain one.

## Seams left for parts B, C, D

- **B, `apps/workspaces/access.py`:** `person_requires_email(person)` (True when the person has restricted documents: then an invitation for them needs an email), `on_member_added(workspace_id, user_id)` (recompute readers when a person gets an account), `on_member_removed(workspace_id, user_id)` (delete reader rows, shares and department memberships as the system). All no-ops in A, each called from exactly one place.
- **B, removal and the export:** `remove_member` steps 3 and 4 get wrapped in `system_actor()`; `build_member_export` passes `actor=user_id` to `workspace_context`; `link_member` gets the `cannot_grant_self` rule. The audit codes `visibility_changed`, `department_member_*` and `emergency_access_*` already exist.
- **C, `apps/accounts/sessions.py`:** `start_session` gains the second-factor step and the required set-up (D 10: "where a session is created"); `session_policy("sso")` caps an SSO session at 12 hours; the session lifetime moves from environment settings to the server settings page; changing to a second factor calls `end_sessions(user, except_key=...)`.
- **C, accounts:** `clearSecondFactor` (DELETE `/users/{user_id}/second-factor`) uses `account_authority` and `notify(..., "second_factor_cleared")`, both ready; `acceptInvitation` gains `account: "sso"`; `UserSession.Method.SSO` exists.
- **C, devices:** `token_expired` gains the rule for devices paired from an SSO session (D 9.4).
- **D:** `apps/common/links.py` (`public_base_url`, `home_network_only`) and `devices.services.server_link` / `qr_payload` gain the external addresses and the `addresses` list (D 19.3).

## What the iOS plan for part A must cover

- Register the `brievie` URL scheme and parse `brievie://server#v=1&url=…&url=…&ca_sha256=…` (ignore unknown keys; https only; a pin of 64 hex characters or none); show the server's host and ask for the eight-character pairing code typed by hand; pair with the pin from the link exactly as from a QR code. The link never holds a code; never pair without the typed code.
- Send `X-Brievie-App-Version: <CFBundleShortVersionString> (<CFBundleVersion>)` on every request and `app_version` in `pairDevice`.
- Confirm (open point of D 20) that the 1c app treats `401 not_authenticated` on any request as "this device is revoked" and shows pairing; part A's expired token answers exactly that.
- Confirm what the app does with calendar events of tasks that disappear from the list (a member removed elsewhere, a workspace that disappears): D 20 asks; part B makes it common.
- A member removed from one of several workspaces: `listWorkspaces` no longer has it and its endpoints answer 404 `workspace_not_found`; the app drops it from the picker without signing out.
- Contract sync: new optional fields (`UserOut.is_server_admin`, `DeviceOut.app_version`, `PairingCodeOut.server_link`, `MemberOut.can_manage_account`) and new operations; nothing is required of the app beyond the items above. Account notices and the export stay web-only in part A.
- Keep the token in the Keychain as now; nothing in part A changes the bearer token's format.
