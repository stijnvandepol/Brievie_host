# Host Leden en apparaten (fase 1d, part A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Several people use one Brievie server: an admin invites a member with a link or QR code, the invitee chooses a password and opens the app; admins change roles and remove members; every user sees their devices and web sessions; device tokens stop when they are not used; a phone pairs from a link without ever sending the pairing code; a member can export their own post; and an append-only audit log records who did what.

**Architecture:** One new app, `audit` (two append-only tables, a closed list of codes, one `record()` helper that every service calls, a purge function owned by the schema owner). `accounts` gains the server admin flag (and a command to appoint another), `UserSession` (recorded by `record_session`, called by a `user_logged_in` receiver and by `start_session`, enforced by a middleware, cleaned by a periodic job), `start_session()` as the one place a sign-in creates a session, password change and reset links, account notices and the rule for account actions. `workspaces` gains `Invitation` (a server table like `PairingCode`) and the member services (role, remove, leave). `devices` gains the app version, the token expiry, the per-code pairing link and pairing by proof. `documents` gains the export building block and `MemberExport`. The web gets the pages Leden, Apparaten, Waar je bent ingelogd, the invitation and reset pages, Instellingen with the log, all with the existing Fluent 2 building blocks.

**Tech Stack:** As in 1c (Python 3.13, Django 6.1, Django Ninja, PostgreSQL 18 with forced RLS, Procrastinate, django-axes, pytest; React 19, TanStack Router and Query, React Aria Components, Lingui, Vitest, MSW, `qrcode.react`). No new dependency (`zipfile`, `secrets`, `hashlib`, `hmac` are standard library).

**Spec:** `docs/superpowers/specs/2026-11-17-fase-1d-leden-rollen-sso-design.md` (cited as "D 6.2"). Part A is the first row of D 13. Binding: D 6, 7, 11, 14, 15 and the rulings of D 20 and D 21. Related: `docs/superpowers/specs/2026-11-17-beheer-backup-export-updates-design.md` section 5 (the export building block, cited as "Beheer 5"). Previous plan for house style: `docs/superpowers/plans/2026-11-16-host-fase-1c.md`. Check report processed into this version: `plan-1d-a-controle.md` (session notes); every item is either in a task or in "Plan decisions".

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
- Secrets: invitation and reset tokens are `secrets.token_urlsafe(32)`, stored only as SHA-256 hex, shown once, carried in a URL fragment or a request body, never in a path or query string, never logged. A pairing code typed after following a link never leaves the phone (D 6.2): the app sends a proof (Task 8).
- A failure count or a burned code that must survive the error answer is written in its own `transaction.atomic()` block that ends **before** the `ApiError` is raised (the service's own transaction would otherwise roll it back).
- Client addresses go into `inet` columns only through `apps.common.net.valid_client_ip(request)` (Task 1), never `client_ip()` directly.
- Periodic Procrastinate tasks take `timestamp: int` (as `pipeline/jobs.py` does).
- Every new endpoint documents its errors (`AUTH_ERRORS`, `SESSION_WRITE_ERRORS`, `BODY_ERRORS`); input schemas derive from `InputSchema`. After every API change: `make openapi`, then `cd web && pnpm gen:api`; commit `docs/api/openapi.json` and `web/src/api/schema.d.ts` with the change.
- Every service that changes something in the list of D 11 calls `apps.audit.services.record(...)` inside its own transaction; audit rows never hold a letter's sender, summary, text or label names, only ids and the names of people.
- Web: no CSP change, no inline styles, every page has `usePageTitle`, works by keyboard, tests query by role; a test that navigates waits for the visible result (`findBy…`), as `test-utils.tsx` and `test-setup.ts` expect. A page that reads a token from the fragment calls `history.replaceState(null, "", location.pathname)` right after reading it.
- Branch `feat/1d-a-leden` from `main` (if `feat/b1-backup` has been merged by then, from that `main`; nothing here touches `apps/backup/` or `./brievie`). Conventional commits ending with the trailer `Co-Authored-By: Claude Code <noreply@anthropic.com>`. Never push. iCloud conflict copies (" 2", " 3" in a name) are never committed.

## Review Focus

1. **An invitation accepted twice at the same moment** (two taps, two phones): exactly one membership, one person link, the second gets 400 `invitation_invalid`; a unique-constraint race answers 409, never 500. → Task 12 (`test_concurrent_accepts_make_one_membership`, `test_accept_locks_the_invitation_row`, `test_a_unique_violation_on_accept_answers_409`).
2. **A token in a log line or a URL query**: no operation takes a token in a path or query parameter, and neither the token nor its hash appears in any log record of create, lookup, accept, reset. → Task 6 (`test_no_operation_takes_a_token_in_path_or_query`, `test_reset_token_never_logged`), Task 12 (`test_invitation_token_never_logged`), Task 22 (`test_token_goes_in_the_body_not_the_url`).
3. **The last admin removing themselves** (by "Verwijderen" on their own row, by "Werkruimte verlaten", or by demoting themselves), and the last server admin losing their last membership or the flag: 409 `last_admin`, nothing changed; two admins acting on each other at once never leave zero. → Task 13 (`test_last_admin_cannot_demote_themselves`, `test_a_demoted_actor_cannot_finish_a_change`), Task 14 (`test_last_admin_cannot_leave_or_remove_themselves`, `test_last_server_admin_cannot_be_deactivated`, `test_two_server_admins_leaving_at_once_keep_one`), Task 15 (`test_the_last_server_admin_cannot_be_removed`).
4. **A workspace admin acting on an account that is also a member elsewhere, or on a server admin** (reset link, ending sessions, devices, removing their last membership): 403 `server_admin_only`, nothing changed, no notice. An admin who lost the role cannot use what they made before. → Task 6 (`test_authority_matrix`, `test_completion_rechecks_the_authority`), Task 10 (`test_workspace_admin_cannot_touch_a_member_of_two_workspaces`), Task 14 (`test_workspace_admin_cannot_deactivate_a_server_admin`, `test_removal_revokes_what_the_admin_made`), Task 13 (`test_demotion_revokes_open_invitations_and_reset_links`).
5. **An expired device token answering differently from a revoked one** (status, code, message, headers): a 1c app only treats `401 not_authenticated` as revocation. → Task 9 (`test_expired_token_answers_exactly_like_a_revoked_one`).
6. **An audit row that can be updated or deleted by the app role** (a forgotten REVOKE is invisible to a suite that runs as the owner), and the same for the AI consent history: → Task 1 (`test_app_role_may_insert_and_read_audit_rows_but_not_change_them`), Task 2 (`test_purge_refuses_a_cut_off_younger_than_90_days`).
7. **A pairing code harvested by a hostile link** (`brievie://server#url=https://attacker…`): the app never sends the code, a proof bound to another CA or host is refused, a nonce works once, five wrong proofs burn the code. → Task 8 (`test_a_proof_bound_to_another_ca_is_refused`, `test_a_replayed_nonce_is_refused`, `test_five_wrong_proofs_burn_the_code`, `test_proof_matches_the_shared_vector`).
8. **The caller's own session ended inside the request** (end my current session, leave, self-removal, changing the password): a clean 204 and a recorded new key, never Django's `SessionInterrupted` 400 or a signed-out user. → Task 4, Task 5, Task 14.

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
    migrations/0001_initial.py   # tables, RLS, REVOKE/GRANT (also on pipeline_aiconsentevent)
    migrations/0002_purge_functions.py
  accounts/
    models.py                    # + User.is_server_admin, UserSession, PasswordReset, AccountNotice
    sessions.py                  # start_session, record_session, rekey_session, end_sessions, session_policy, describe_user_agent, receivers
    middleware.py                # UserSessionMiddleware
    authority.py                 # account_authority (D 6.3 rule)
    resets.py                    # create/complete password reset, revoke_resets_made_by
    notices.py                   # notify, list, mark seen, message text
    jobs.py                      # remove_stale_sessions (periodic)
    services.py  api.py  schemas.py
    management/commands/reset_password.py
    management/commands/set_server_admin.py
  common/
    tokens.py                    # new_token, token_hash
    links.py                     # public_base_url, fragment_link, home_network_only
    net.py                       # + valid_client_ip
    sealed_stream.py             # SealedWriter, iter_opened (chunked AES-GCM)
  devices/
    models.py                    # + Device.app_version, app_version_at, paired_via; PairingCode.paired_via, proof_key_sealed, failed_proofs; PairingChallenge; TokenExpiryStart
    proof.py                     # derive_proof_key, pairing_proof, trust_bindings (pure apart from settings/pki)
    services.py  auth.py  api.py  schemas.py
  workspaces/
    models.py                    # + Invitation, Workspace.allow_member_export
    invitations.py               # create/list/revoke/renew/lookup/accept, revoke_invitations_made_by
    members.py                   # change_role, remove_member, lock helpers
    access.py                    # seams for part B: on_member_added, on_member_removed, person_requires_email
    settings_service.py          # workspace settings (allow_member_export)
    services.py  api.py  schemas.py
  documents/
    export.py                    # export_context, write_export: the building block of Beheer 5
    member_export.py             # MemberExport services
    jobs.py                      # build_member_export, remove_expired_exports
    models.py  api.py  schemas.py
  rules/ labels/ people/ pipeline/   # record() calls only (Task 16)
backend/tests/                   # test_<app>_<topic>.py; fixtures/openapi_1c.json, fixtures/pairing_proof_v1.json
web/src/
  routes/members.tsx  invitation.tsx  password-reset.tsx  sessions.tsx  password.tsx
  routes/member-devices.tsx  settings.tsx  audit-log.tsx  server-audit-log.tsx
  components/invite-dialog.tsx  role-dialog.tsx  notice-bar.tsx  audit-table.tsx  export-panel.tsx
  components/ai-status-line.tsx (AiStatusSentence split out)
  routes/pair-app.tsx  components/device-table.tsx  components/page.tsx  components/icons.tsx
  api/queries.ts  router.tsx  locales/nl/messages.po
docs/  DECISIONS.md  GLOSSARY.md  PRIVACY.md  SELFHOST.md  api/openapi.json
```

Imports: `audit` imports nothing from other domain apps (it takes ids and names). `workspaces.members` imports `accounts`, `people`, `tasks`, `devices`; `accounts.authority` imports `workspaces.models`. `workspaces.access` is a leaf module that part B fills in. `devices.proof` imports only `apps.common.crypto`, `apps.devices.pki` and settings.

---

## Slice A1: the audit log

### Task 1: Audit tables, codes and `record()`

**Files:**
- Create: `backend/apps/audit/{__init__,apps,codes,models,services}.py`, `backend/apps/audit/migrations/{__init__,0001_initial}.py`, `backend/tests/test_audit_record.py`, `backend/tests/test_audit_privileges.py`, `backend/tests/test_common_net.py` (or extend the existing net test), `backend/tests/fixtures/openapi_1c.json`
- Modify: `backend/brievie/settings.py` (`INSTALLED_APPS` + `"apps.audit"`), `backend/apps/common/net.py`, `backend/tests/test_openapi_contract.py`

**Interfaces:**
- Produces (`apps/audit/codes.py`), the closed list of D 11 as `AuditCode(models.TextChoices)` with Dutch `gettext_lazy` labels: server codes `sign_in_succeeded`, `sign_in_failed`, `sign_in_locked_out`, `second_factor_added`, `second_factor_removed`, `second_factor_cleared`, `password_changed`, `password_reset_created`, `session_ended`, `device_paired`, `device_revoked`, `device_expired`, `server_admin_granted`, `server_admin_revoked`, `sso_settings_changed`; workspace codes `invitation_created`, `invitation_revoked`, `invitation_accepted`, `member_role_changed`, `member_removed`, `member_left`, `workspace_setting_changed`, `rule_created`, `rule_changed`, `rule_deleted`, `rule_run`, `label_deleted`, `person_deleted`, `visibility_changed`, `person_linked`, `person_unlinked`, `department_member_added`, `department_member_removed`, `emergency_access_switched_on`, `emergency_access_switched_off`, `emergency_access_used`, `export_created`, `export_downloaded`. Codes of parts B and C are in the list now; nothing in part A writes them. (`server_admin_granted`/`revoked` are new in D 11 through D 21.)
- Produces: `ActorKind` (`user`, `device`, `system`, `sso`), `TargetKind` (`user`, `invitation`, `device`, `session`, `rule`, `label`, `person`, `document`, `workspace`, `export`); `ALLOWED_VALUES: dict[AuditCode, frozenset[str]]`, e.g. `member_role_changed: {"from", "to"}`, `member_removed`/`member_left: {"account_deactivated"}`, `invitation_created: {"role", "expires_in_days"}`, `invitation_accepted: {"role", "new_account", "account_reactivated"}`, `invitation_revoked: {"reason"}` (`admin`, `renewed`, `creator_lost_role`), `password_changed: {"via"}`, `password_reset_created: set()`, `session_ended: {"count", "reason"}` (`self`, `admin`, `password_changed`, `reset`, `deactivated`), `sign_in_succeeded: {"via"}`, `device_paired: {"via"}` (`code`, `proof`), `workspace_setting_changed: {"setting", "value"}`, `export_created: {"document_count"}`, and for part B's codes `visibility_changed: {"visibility", "origin", "share", "member"}` (a share added or removed is a `visibility_changed` row with `share` and `member`; there are no share codes), `department_member_added`/`department_member_removed: {"member"}`, `emergency_access_used: {"reason", "document_count"}`; every other code `frozenset()`.
- Produces (`apps/audit/models.py`): `WorkspaceAuditEvent(WorkspaceModel)` and `ServerAuditEvent(BaseModel)`, both with `actor_kind` (CharField 10), `actor_id` (UUIDField null), `actor_name` (CharField 150, blank), `actor_device_id` (UUIDField null), `code` (CharField 50, choices `AuditCode`), `target_kind` (CharField 20, blank), `target_id` (UUIDField null), `target_name` (CharField 150, blank), `client_ip` (GenericIPAddressField null), `values` (JSONField default dict). `WorkspaceAuditEvent` overrides the inherited field: `workspace = models.ForeignKey("workspaces.Workspace", on_delete=models.DO_NOTHING, db_constraint=False, related_name="+")`, so the table has no foreign key (D 11) and `test_rls_coverage.py` still sees it. Index `(workspace, -created_at)` / `(-created_at)`; `ordering = ["-created_at", "-id"]`.
- Produces (`apps/common/net.py`): `valid_client_ip(request: HttpRequest) -> str | None`: `client_ip(request)` when `ipaddress.ip_address()` accepts it (a forged `X-Forwarded-For` entry such as `"unknown"` or `"1.2.3.4:80"` gives None), else None. `client_ip` itself does not change (the throttles key on it).
- Produces (`apps/audit/services.py`):
  - `@dataclass(frozen=True) class AuditActor: kind: str; user_id: UUID | None; name: str; device_id: UUID | None = None; client_ip: str | None = None` with `@classmethod system() -> AuditActor` (kind `system`, name `"Brievie"`) and `@classmethod for_user(user: User, *, client_ip: str | None = None) -> AuditActor`.
  - `actor_from_request(request: HttpRequest) -> AuditActor`: kind `device` with `device_id` when `request.device` is set (bearer), else `user`; `client_ip` from `valid_client_ip`.
  - `@dataclass(frozen=True) class AuditTarget: kind: str; id: UUID | None; name: str = ""`.
  - `record(code: AuditCode, *, actor: AuditActor, target: AuditTarget | None = None, workspace_id: UUID | None = None, values: Mapping[str, str | int | bool] | None = None) -> None`: `workspace_id` set → a `WorkspaceAuditEvent` written inside `workspace_context(workspace_id)` (it enters the context itself, nesting is fine); None → a `ServerAuditEvent`. Raises `ValueError` for a value key outside `ALLOWED_VALUES[code]` or a value that is not `str | int | bool`; strings longer than 200 characters are cut; names pass through `mask_bsns`.
- Migration `0001_initial`: both tables, `enable_rls("audit_workspaceauditevent")`, then one `RunSQL` (depends on the latest `pipeline` migration, because it also closes the AI consent history that DECISIONS 2026-11-17 lists as "Not done"):

```sql
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'brievie_app') THEN
    REVOKE UPDATE, DELETE, TRUNCATE ON audit_workspaceauditevent, audit_serverauditevent, pipeline_aiconsentevent FROM brievie_app;
    GRANT SELECT, INSERT ON audit_workspaceauditevent, audit_serverauditevent, pipeline_aiconsentevent TO brievie_app;
  END IF;
END $$;
```

(The production database grants the app role everything through default privileges, so the REVOKE matters there; the test database has no default privileges, so the GRANT makes the positive half of the test meaningful. `pipeline_aiconsentevent.actor` is `SET_NULL`: no code deletes a user today; a later user deletion must handle it, see Plan decision 22.) The reverse `RunSQL` grants UPDATE and DELETE on `pipeline_aiconsentevent` back.
- Contract fixture: copy `docs/api/openapi.json` as it is on `main` at branch start to `backend/tests/fixtures/openapi_1c.json`; `released_contract()` becomes `released_contracts()` returning both fixtures, and each `test_released_*` test is parametrized over them (`ids=["1b", "1c"]`). The 1c fixture includes `updateLabel` with `LabelUpdateIn`, so `RENAMED_REQUEST_BODIES` needs no entry for it.

- [ ] **Step 1: Write the failing tests**

```python
# test_audit_privileges.py
def test_app_role_exists() -> None:                       # SELECT 1 FROM pg_roles WHERE rolname='brievie_app' -> a row
@pytest.mark.parametrize("table", ["audit_workspaceauditevent", "audit_serverauditevent", "pipeline_aiconsentevent"])
def test_app_role_may_insert_and_read_audit_rows_but_not_change_them(table: str) -> None:
    # has_table_privilege('brievie_app', table, p) for p in SELECT, INSERT -> True
    # for p in UPDATE, DELETE, TRUNCATE -> False
def test_audit_tables_have_no_foreign_keys() -> None:     # pg_constraint contype='f' for both audit tables -> []

# test_audit_record.py
def test_record_writes_a_workspace_row_in_its_own_context(ws_a, ws_b) -> None:
    # called with no context; visible in workspace_context(ws_a.id), not in ws_b's; code, actor, target as given
def test_record_without_workspace_writes_a_server_row() -> None
def test_record_rejects_a_value_key_not_listed_for_the_code(ws_a) -> None:
    # record(AuditCode.LABEL_DELETED, ..., values={"name": "Zorg"}) -> ValueError; no row
def test_record_masks_a_bsn_in_a_name(ws_a) -> None:     # target name "Jan 111222333" -> "Jan [BSN]"
def test_actor_from_request_names_the_device(rf) -> None: # request.device set -> kind "device", device_id, user_id
def test_every_code_has_an_allowed_values_entry() -> None:# set(ALLOWED_VALUES) == set(AuditCode)

# test_common_net.py
@pytest.mark.parametrize(("forwarded", "expected"), [("192.0.2.7", "192.0.2.7"), ("2001:db8::1", "2001:db8::1"),
    ("unknown", None), ("1.2.3.4:80", None), ("", None)])
def test_valid_client_ip_refuses_what_inet_cannot_hold(rf, settings, forwarded, expected) -> None
    # settings.BRIEVIE_PROXY_COUNT = 1; HTTP_X_FORWARDED_FOR = forwarded
```

and the parametrized contract tests (`test_released_schemas_still_exist[1c]` etc.).

- [ ] **Step 2: Run** `cd backend && uv run pytest tests/test_audit_record.py tests/test_audit_privileges.py tests/test_common_net.py tests/test_openapi_contract.py -v` → FAIL (no app `audit`; the 1c fixture is missing).
- [ ] **Step 3: Implement** the app, `valid_client_ip`, the migration (`uv run python manage.py makemigrations audit`, then append the `enable_rls` and the SQL above), the fixture and the parametrization.
- [ ] **Step 4: Run** the same command plus `tests/test_rls_coverage.py` and `tests/test_pipeline_*consent*` → PASS (the consent tests run as the owner and only insert). `make lint typecheck` → clean.
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

- Produces: setting `BRIEVIE_AUDIT_RETENTION_DAYS: int` (env, default 365; `ImproperlyConfigured` below 90). `apps/audit/jobs.py`: `purge_audit_events(timestamp: int) -> None` (`@app.periodic(cron="17 3 * * *")`, `@app.task(name="purge_audit_events", queue=QUEUE)` with `QUEUE` from `apps.pipeline.jobs`): for each workspace id, in its own short transaction, `SELECT brievie_purge_workspace_audit(%s, %s)`; then the server function; logs the two totals only.

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
    # settings.BRIEVIE_AUDIT_RETENTION_DAYS = 120; purge_audit_events(timestamp=0): a 130-day row goes, a 100-day row stays
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
- Produces (`apps/accounts/services.py`): `lock_active_server_admins() -> list[User]` (`User.objects.select_for_update().filter(is_server_admin=True, is_active=True).order_by("id")`; the lock every check of "another server admin" takes, Tasks 14 and 15) and `other_active_server_admin_exists(user: User) -> bool` (calls `lock_active_server_admins()` and answers whether a user other than `user` is in it; must run inside a transaction).

- [ ] **Step 1: Failing tests** (migration tested the way `test_rules_preset_migration.py` does: import the module, call the function with the global `apps`):

```python
def test_migration_marks_the_first_admin_of_the_oldest_workspace() -> None:
    # two workspaces, the older one with two admins (membership created_at apart) -> only the first is server admin
def test_migration_changes_nothing_when_a_server_admin_exists() -> None
def test_migration_on_an_empty_server_does_nothing() -> None
def test_setup_makes_the_first_user_server_admin(client) -> None       # POST /setup -> body user.is_server_admin True
def test_me_reports_is_server_admin(client, ws_a) -> None              # false for a plain admin
def test_other_active_server_admin_exists_ignores_inactive_admins() -> None
def test_server_admins_are_locked_in_id_order() -> None                # captured SQL: ... ORDER BY id ... FOR UPDATE
```

- [ ] **Step 2: Run** `cd backend && uv run pytest tests/test_accounts_server_admin.py -v` → FAIL.
- [ ] **Step 3: Implement**; `make openapi && cd web && pnpm gen:api`.
- [ ] **Step 4: Run** `make test` → PASS (the contract test passes: `UserOut` only gained a defaulted field).
- [ ] **Step 5: Commit** `feat(accounts): the server admin, given to the first admin on existing servers`

### Task 4: Recorded sessions, one place to sign in, and their lifetime

**Files:**
- Create: `backend/apps/accounts/sessions.py`, `backend/apps/accounts/middleware.py`, `backend/apps/accounts/jobs.py`, `backend/apps/accounts/migrations/0005_usersession.py`, `backend/tests/test_accounts_sessions.py`
- Modify: `backend/apps/accounts/models.py`, `backend/apps/accounts/apps.py` (`ready()` connects receivers), `backend/apps/accounts/services.py` (`login`, `setup_server`, `logout`), `backend/apps/accounts/api.py` (`login` throttle), `backend/brievie/settings.py`

**Interfaces:**
- Consumes: `record`, `AuditActor`, `AuditTarget`, `AuditCode` (Task 1), `valid_client_ip` (Task 1).
- Produces: `UserSession(BaseModel)`: `user` (FK `User`, CASCADE, `related_name="web_sessions"`), `session_key` (CharField 40, unique; plain, as Django's own table holds it, D 7.2), `method` (`Method(TextChoices)`: `password`, `sso`), `created_via` (CharField 20, default `"password"`: `password`, `setup`, `invitation`, `reset`), `last_used_at` (DateTimeField), `client_ip` (GenericIPAddressField null), `browser` (CharField 100: a description, never the raw user agent). `created_at` is the start.
- Produces (`apps/accounts/sessions.py`):
  - `record_session(request: HttpRequest, user: User, *, method: str, via: str = "password") -> UserSession`: when `request.session.session_key` is None it calls `request.session.save()` first (a `login()` that flushed leaves no key); then `UserSession.objects.update_or_create(session_key=key, defaults={"user": user, "method": method, "created_via": via, "last_used_at": now, "client_ip": valid_client_ip(request), "browser": describe_user_agent(...)})` and sets the session expiry (see the middleware). `update_or_create`, so a second call for the same key (the receiver, then `start_session`) never hits the unique constraint.
  - `start_session(request: HttpRequest, user: User, *, method: str = UserSession.Method.PASSWORD, via: str) -> None`: **the one function that signs somebody in** (login, setup, accepting an invitation, completing a reset). `old_key = request.session.session_key`; `django.contrib.auth.login(request, user, backend=MODEL_BACKEND)` (two backends are configured; invitation and reset users carry no `user.backend`); `record_session(request, user, method=method, via=via)` (the method is an explicit argument, never a private request attribute); when `old_key` is set and differs from the new key, `UserSession.objects.filter(session_key=old_key).delete()`; then `record(SIGN_IN_SUCCEEDED, values={"via": via})`. `via` is one of `password`, `setup`, `invitation`, `reset`. Part C inserts the second-factor step here.
  - `rekey_session(request: HttpRequest, old_key: str) -> None`: after `update_session_auth_hash` (which cycles the key), `UserSession.objects.filter(session_key=old_key).update(session_key=request.session.session_key)`, so the middleware does not sign the user out.
  - `@dataclass(frozen=True) class SessionPolicy: idle: timedelta; maximum: timedelta` and `session_policy(method: str) -> SessionPolicy` (from settings; part C caps `sso` at 12 hours here).
  - `on_user_logged_in(sender: object, request: HttpRequest, user: User, **kwargs: object) -> None`: the `user_logged_in` receiver; calls `record_session(request, user, method=UserSession.Method.PASSWORD)`. A receiver as well as `start_session`, so `Client.force_login` in the 66 existing tests records a session too; `start_session` then overwrites method and `created_via` on the same row.
  - `end_sessions(user: User, *, except_key: str | None = None, current: HttpRequest | None = None) -> int`: deletes the user's `UserSession` rows and the matching `django.contrib.sessions.models.Session` rows; returns the count. When `current` is given and its session key is among those ended, that one is ended with `current.session.flush()` instead of a row delete (deleting the row under a session the middleware has modified makes `SessionMiddleware` answer `SessionInterrupted` as an HTML 400).
  - `end_current_session(request: HttpRequest) -> None`: deletes the caller's `UserSession` row and calls `request.session.flush()`.
  - `describe_user_agent(user_agent: str) -> str`: pure; "Firefox op Windows", "Safari op iPhone", "Chrome op Android", "Edge op Windows", "Onbekende browser" (Dutch via `gettext`).
- Produces: `UserSessionMiddleware` (after `AuthenticationMiddleware` in `MIDDLEWARE`). For a request whose session holds an authenticated user: no `UserSession` row → `request.session.flush()` and `request.user = AnonymousUser()` (fails closed); `now - created_at > policy.maximum` or `now - last_used_at > policy.idle` → the same plus delete the row; otherwise, when `now - last_used_at >= 1 minute`, update `last_used_at` and `client_ip` and call `request.session.set_expiry(min(now + idle, created_at + maximum))`, so Django's own `expire_date` and cookie enforce both limits. Its writes run in their own `transaction.atomic()` (middleware is outside the request transaction).
- Produces: settings `BRIEVIE_SESSION_IDLE_DAYS: int = 14`, `BRIEVIE_SESSION_MAX_DAYS: int = 90` (env; `ImproperlyConfigured` when idle > max or either < 1), `SESSION_COOKIE_AGE = idle in seconds`. They stay environment settings until part C's server settings page (D 7.2 as changed by D 21).
- Produces (`apps/accounts/jobs.py`): `remove_stale_sessions(timestamp: int) -> None` (`@app.periodic(cron="23 * * * *")`, queue `QUEUE` from `apps.pipeline.jobs`): deletes `UserSession` rows whose Django `Session` row is gone or has `expire_date < now`, and rows past their policy limits together with their `Session` row (D 7.2: a browser that never returns is not listed forever). Logs the count only.
- Produces: migration `0005_usersession` (depends on `("sessions", "0001_initial")`) with a `RunPython` that creates a `UserSession` for every unexpired Django session holding `_auth_user_id` (method `password`, `last_used_at` now, browser ""), decoding with `django.contrib.sessions.backends.db.SessionStore().decode(row.session_data)` (the historical `Session` model has no `get_decoded()`), so nobody is signed out by the upgrade and the middleware can fail closed.
- Produces: `login` gets `throttle=ClientAddressThrottle("login", "30/10m")` (every failure writes a server audit row, so the table must not grow without bound from one address; axes still locks per email and address). `login` failures write `sign_in_failed` (server log; `actor` kind `user` with `user_id` of the account the email belongs to, or None; `target` that account; never the email typed for an unknown account, never the password); an `axes.signals.user_locked_out` receiver writes `sign_in_locked_out` once per lockout. Both are written before the 401/429, which commits the request transaction. `logout` deletes the `UserSession` row before `django_logout`.

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
def test_signing_in_again_as_the_same_user_keeps_one_row(client) -> None:
    # force_login, then POST /auth/login as the same user: one UserSession, with the new key; GET /me 200
def test_signing_in_as_another_user_replaces_the_row(client) -> None:
    # signed in as A, POST /auth/login as B: no row of A's old key; one row for B; GET /me is B
def test_start_session_uses_the_model_backend(rf) -> None:
    # a user object without .backend -> start_session succeeds; session[BACKEND_SESSION_KEY] == MODEL_BACKEND
def test_rekey_session_follows_a_cycled_key(client) -> None:
    # cycle_key() then rekey_session(old) -> the row holds the new key; GET /me 200
def test_ending_the_current_session_flushes_instead_of_deleting(client) -> None:
    # end_sessions(user, current=request) inside a request after the middleware touched the session -> 204, not 400
def test_failed_sign_in_is_logged_without_the_password(client, caplog) -> None:
    # ServerAuditEvent code sign_in_failed, target the account; the password not in caplog.text
def test_failed_sign_in_for_an_unknown_address_names_nobody(client) -> None  # target_id None, target_name ""
def test_lockout_is_logged_once(client) -> None                               # 6 failures -> one sign_in_locked_out
def test_login_is_throttled_per_address(client) -> None                       # 31st POST /auth/login from one address -> 429
def test_successful_sign_in_is_logged_with_via(client) -> None                # values {"via": "password"}
def test_start_session_is_the_only_caller_of_django_login() -> None:
    # scan backend/apps/**/*.py: `login(` from django.contrib.auth is imported only in apps/accounts/sessions.py
@pytest.mark.parametrize(("agent", "text"), [...])
def test_describe_user_agent(agent: str, text: str) -> None
def test_migration_records_existing_sessions() -> None:
    # call the migration function directly with the global apps; a Session row made with SessionStore -> one UserSession
def test_stale_sessions_are_removed() -> None:
    # rows whose Session is gone, expired, or past the maximum -> remove_stale_sessions(timestamp=0) deletes them; a live one stays
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
- Consumes: `UserSession`, `end_sessions`, `end_current_session`, `rekey_session` (Task 4), `record` (Task 1).
- Produces: `AccountNotice(BaseModel)`: `user` (FK CASCADE), `code` (`Code(TextChoices)`: `password_reset_created`, `password_reset_completed`, `sessions_ended`, `device_revoked`, `second_factor_cleared`, and for part B `emergency_access_switched_on`, `emergency_access_switched_off`, `emergency_access_used`, `private_without_readers`; nothing in part A sends the last four), `actor_name` (CharField 150), `values` (JSONField default dict), `seen_at` (null).
- Produces (`apps/accounts/notices.py`): `notify(user: User, code: str, *, actor_name: str, values: Mapping[str, str] | None = None) -> None`; `notice_message(notice: AccountNotice) -> str` (Dutch, date as "3 mei" in `Europe/Amsterdam`): `password_reset_created` "%(name)s heeft op %(date)s een herstellink voor je wachtwoord gemaakt.", `password_reset_completed` "Je wachtwoord is op %(date)s opnieuw ingesteld door %(name)s." (D 6.3), `sessions_ended` "%(name)s heeft op %(date)s je sessies beëindigd.", `device_revoked` "%(name)s heeft op %(date)s je apparaat ‘%(device)s’ ingetrokken.", `emergency_access_switched_on` "%(name)s heeft op %(date)s noodtoegang voor de beheerder aangezet in %(workspace)s.", `emergency_access_switched_off` "%(name)s heeft op %(date)s noodtoegang voor de beheerder uitgezet in %(workspace)s.", `emergency_access_used` "%(name)s heeft op %(date)s met noodtoegang %(count)s privé-brieven van %(person)s geopend. De reden staat bij elke brief.", `private_without_readers` "Er is post die privé had moeten worden, maar niemand zou hem kunnen lezen. Hij is zichtbaar gebleven voor iedereen in %(workspace)s (document %(document_id)s)."; `can_dismiss(notice: AccountNotice, session: UserSession | None) -> bool`: False for `password_reset_completed` when the current session's `created_via` is `reset` (D 21: whoever completed the reset cannot hide it), else True; `mark_seen(request: HttpRequest, notice_ids: Sequence[UUID]) -> None` marks only the caller's notices for which `can_dismiss` holds; the others stay unseen without an error.
- Produces endpoints (all `auth=session_auth`: they are about the web session; a bearer token gets 401):

| Operation | Method, path | In → Out | Errors |
|---|---|---|---|
| `listMySessions` | GET `/me/sessions` | → `list[SessionOut]` | 401 |
| `endMySession` | DELETE `/me/sessions/{session_id}` | → 204 | 404 `session_not_found`, 401, 403 |
| `changePassword` | POST `/me/password` | `PasswordChangeIn{current_password: str, new_password: str}` (max 128) → 204 | 400 `wrong_password`, 422, 429, 401, 403 |
| `listMyNotices` | GET `/me/notices` | → `list[NoticeOut]` (unseen, oldest first) | 401 |
| `markNoticesSeen` | POST `/me/notices/seen` | `NoticesSeenIn{notice_ids: list[UUID]}` (max 50) → 204 | 401, 403, body errors |

  `SessionOut{id: UUID, method: str, started_at: datetime, last_used_at: datetime, client_ip: str = "", browser: str, current: bool}`; `NoticeOut{id: UUID, code: str, message: str, created_at: datetime, can_dismiss: bool = True}`.
- Produces: `end_my_session(request: HttpRequest, *, session_id: UUID) -> None`: another session → `end_sessions`-style delete of both rows; the current one → `end_current_session(request)`; both write `session_ended {"count": 1, "reason": "self"}` (D 11). A 204 in both cases, the next request of the ended browser 401.
- Produces: `change_password(request: HttpRequest, *, current_password: str, new_password: str) -> None` (throttle `CallerThrottle("password", "10/10m")`): checks the current password, `validate_password`, `set_password`, `old_key = request.session.session_key`, `update_session_auth_hash(request, user)`, `rekey_session(request, old_key)`, `end_sessions(user, except_key=request.session.session_key)`, `record(PASSWORD_CHANGED, values={"via": "self"})` and, when sessions ended, `record(SESSION_ENDED, values={"count": n, "reason": "password_changed"})`.

- [ ] **Step 1: Failing tests**

```python
def test_list_my_sessions_marks_the_current_one(client) -> None      # two logins of the same user, two clients
def test_ending_another_session_signs_that_browser_out(client) -> None # other client's GET /me -> 401; both rows gone
def test_ending_my_current_session_answers_204_and_signs_me_out(client) -> None  # 204 (not 400); next GET /me 401
def test_ending_a_session_is_logged(client) -> None                    # session_ended {"count": 1, "reason": "self"}
def test_ending_somebody_elses_session_is_404(client) -> None
def test_my_sessions_refuse_a_bearer_token(client) -> None             # 401 not_authenticated
def test_change_password_ends_the_other_sessions_and_keeps_this_one(client) -> None
    # after 204: this client's GET /me 200 and its UserSession holds the new key; the other client 401
def test_change_password_with_a_wrong_current_password_is_400(client) -> None   # code wrong_password; password unchanged
def test_change_password_runs_the_password_rules(client) -> None       # "kort" -> 422
def test_change_password_is_logged(client) -> None                     # password_changed {"via": "self"}, session_ended {"count": 1, ...}
def test_notices_are_listed_once_and_marked_seen(client) -> None
def test_notice_message_reads_like_the_design() -> None
    # password_reset_completed by "Anne" on 2026-05-03 -> "Je wachtwoord is op 3 mei opnieuw ingesteld door Anne."
def test_marking_another_users_notice_changes_nothing(client) -> None
def test_a_session_made_by_a_reset_cannot_dismiss_the_reset_notice(client) -> None:
    # UserSession.created_via "reset": listMyNotices -> can_dismiss False; markNoticesSeen -> 204, notice still unseen;
    # after a fresh password sign-in the same notice can be dismissed
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
  - `complete_password_reset(request: HttpRequest, *, token: str, password: str) -> User`: row lock on the reset; unknown, used, expired, revoked, the account inactive, **or the creator no longer holding the authority** (`created_by` is None, or `account_authority(created_by, user.id)` raises: the target joined a second workspace, or the creator lost the admin role since) → 400 `reset_link_invalid` "Deze herstellink werkt niet meer. Vraag een nieuwe." (one sentence for all); `validate_password` → 422 (link stays unused); sets the password, marks used, `end_sessions(user, current=request)`, `notify(user, "password_reset_completed", actor_name=created_by_name)`, `record(PASSWORD_CHANGED, values={"via": "reset"})`, then `start_session(request, user, via="reset")` (so the new session has `created_via="reset"` and cannot dismiss that notice, Task 5).
  - `revoke_resets_made_by(user_id: UUID) -> int`: sets `revoked_at` on every unused, unrevoked reset whose `created_by` is that user (Tasks 13, 14, 15 call it when the creator loses the role).
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
    # old session client -> 401; response client -> GET /me 200 (its UserSession created_via "reset"); login with the new password works
@pytest.mark.parametrize("state", ["used", "expired", "revoked", "unknown", "inactive_account"])
def test_every_dead_link_answers_the_same_sentence(client, state) -> None:
    # 400, {"code": "reset_link_invalid", "message": "Deze herstellink werkt niet meer. Vraag een nieuwe."}
def test_completion_rechecks_the_authority(client, ws_a, ws_b) -> None:
    # ws_a admin makes a link for a member; the member then joins ws_b -> completing answers the same 400; password unchanged
    # likewise when the creator was demoted to member in between
def test_a_weak_password_keeps_the_link_usable(client) -> None
def test_reset_writes_notices_and_audit_rows(client) -> None   # both notices; server rows; workspace row for a single-membership target
def test_reset_token_never_logged(client, caplog) -> None      # caplog.set_level(DEBUG); token and its hash not in caplog.text
def test_reset_password_command_sets_the_password(monkeypatch) -> None
def test_revoke_resets_made_by_stops_only_that_creators_links() -> None
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

### Task 7: App version, the session method and the per-code pairing link

**Files:**
- Create: `backend/apps/devices/migrations/0002_device_app_version.py`, `backend/tests/test_devices_app_version.py`, `backend/tests/test_devices_server_link.py`
- Modify: `backend/apps/devices/{models,services,auth,api,schemas}.py`, `docs/api/openapi.json`, `web/src/api/schema.d.ts`

**Interfaces:**
- Consumes: `UserSession` (Task 4), `record` (Task 1).
- Produces: `Device.app_version = CharField(max_length=50, blank=True, default="")`, `Device.app_version_at = DateTimeField(null=True, blank=True)`; `PairingCode.paired_via` and `Device.paired_via` (`CharField(max_length=10, choices=UserSession.Method.choices, default="password")`): the sign-in method of the web session that made the code, copied to the device at pairing. Part C's device rule (D 9.4) reads `Device.paired_via`.
- Produces: header name `APP_VERSION_HEADER = "X-Brievie-App-Version"` in `apps/devices/services.py`; `clean_app_version(raw: str) -> str` (accepts `^[0-9A-Za-z.+()\- ]{1,50}$` after trimming, else ""); `device_for_token(token: str, *, app_version: str = "") -> Device | None` refreshes `app_version` when the cleaned value is non-empty, differs from the stored one, and `app_version_at` is None or at least 24 hours old (D 7.1: at most once a day), in the same `update()` as `last_seen_at` when both are due. `DeviceTokenAuth.authenticate` passes the header.
- Produces: `PairIn.app_version: str = Field(default="", max_length=50)` (`code` and `device_name` unchanged, so a 1c app's body is still valid); `DeviceOut.app_version: str = ""`; `pair_device(*, code: str, device_name: str, app_version: str = "", client_ip: str | None = None) -> tuple[Device, str]`; it copies `paired_via` and writes `device_paired {"via": "code"}` (server log; actor the code's user with the client address; target the device), and `revoke_device` writes `device_revoked`.
- Produces: `create_pairing_code(user: User, *, paired_via: str = "password") -> tuple[PairingCode, str]` and `issue_pairing_code(request: HttpRequest) -> IssuedPairingCode` (reads the method of the caller's `UserSession` by `request.session.session_key`, default `password`). `IssuedPairingCode` gains `server_link: str`.
- Produces: `server_link(pairing_id: UUID) -> str` and `PairingCodeOut.server_link: str = ""`: `brievie://server#` + `urllib.parse.urlencode([("v", "1"), *(("url", u) for u in BRIEVIE_PUBLIC_URLS), ("ca_sha256", pin), ("pairing", str(pairing_id))])`, the `ca_sha256` pair left out without a private CA. `pairing` is the `PairingCode` row's id in lower-case hyphenated form: not secret (it identifies the code for the proof of Task 8, it does not redeem it). No code, no token (D 6.2, D 20). Part D adds `addresses` here.

- [ ] **Step 1: Failing tests**

```python
def test_pairing_stores_the_app_version(client) -> None                 # PairIn app_version "1.4.0 (12)" -> device.app_version
def test_a_1c_pair_body_without_app_version_still_pairs(client) -> None  # {"code", "device_name"} only -> 201; app_version ""
def test_header_refreshes_the_version_at_most_once_a_day(client) -> None
    # header "1.5.0" -> stored; header "1.5.1" an hour later -> unchanged; app_version_at set back 25 h -> "1.5.1"
def test_a_malformed_header_is_ignored(client) -> None                  # "<script>" and 51 chars -> unchanged
def test_list_devices_shows_the_app_version(client) -> None
def test_the_device_remembers_how_its_pairing_session_signed_in(client) -> None
    # session UserSession.method "password" -> PairingCode.paired_via and Device.paired_via "password"
def test_server_link_carries_addresses_pin_and_pairing_id_only(settings, monkeypatch) -> None:
    # URLs ["https://brievie.local", "https://192.168.1.4"], pin "ab"*32, id 0b7e2f4c-5d1a-4e8b-9c3f-6a2d8e1f4b70 ->
    # "brievie://server#v=1&url=https%3A%2F%2Fbrievie.local&url=https%3A%2F%2F192.168.1.4&ca_sha256=abab...&pairing=0b7e2f4c-5d1a-4e8b-9c3f-6a2d8e1f4b70"
def test_server_link_has_no_pairing_code(client) -> None                # the code (with and without dash) not in server_link
def test_each_code_gets_its_own_link(client) -> None                    # two createPairingCode calls -> different pairing ids
def test_pairing_and_revoking_are_logged(client) -> None                # device_paired {"via": "code"}, device_revoked
```

- [ ] **Step 2: Run** both test files → FAIL.
- [ ] **Step 3: Implement**; `make openapi && cd web && pnpm gen:api`.
- [ ] **Step 4: Run** `make test` → PASS.
- [ ] **Step 5: Commit** `feat(devices): the app version, the pairing session's method and a per-code pairing link without secrets`

### Task 8: Pairing from a link with a proof, never the code

D 6.2 as changed by D 20: a phone that follows a link must not send the typed code to whatever server the link named. It proves it knows the code, bound to the certificate or host it actually connected to; a relay with another CA or host cannot produce a valid proof and never learns the code.

**Files:**
- Create: `backend/apps/devices/proof.py`, `backend/apps/devices/migrations/0003_pairing_proof.py`, `backend/tests/test_devices_pairing_proof.py`, `backend/tests/fixtures/pairing_proof_v1.json`
- Modify: `backend/apps/devices/{models,services,api,schemas}.py`, `backend/apps/audit/codes.py` (nothing new: `device_paired {"via": "proof"}` is listed), `docs/api/openapi.json`, `web/src/api/schema.d.ts`

**Interfaces:**
- Produces (`apps/devices/proof.py`), the protocol, written out so the iOS plan implements the same bytes:
  - `PROOF_CONTEXT = b"brievie-pair-v1"`; `PROOF_KDF_ITERATIONS = 600_000`.
  - `derive_proof_key(code: str, pairing_id: UUID) -> bytes`: `hashlib.pbkdf2_hmac("sha256", normalise_code(code).encode("ascii"), PROOF_CONTEXT + str(pairing_id).encode("ascii"), PROOF_KDF_ITERATIONS, 32)`. `normalise_code` is the existing one (upper case, no `-` or space); `str(pairing_id)` is lower-case with hyphens.
  - `pairing_proof(key: bytes, *, nonce: str, trust_binding: str) -> str`: `hmac.new(key, PROOF_CONTEXT + nonce.encode("ascii") + trust_binding.encode("ascii"), hashlib.sha256).hexdigest()` (64 lower-case hex). The nonce has a fixed length (43), so the concatenation is unambiguous.
  - `trust_bindings() -> frozenset[str]`: `pki.ca_certificate_sha256()` (lower-case hex of the CA certificate's DER) when there is a private CA, plus, for every `BRIEVIE_PUBLIC_URLS` entry, `urlsplit(url).hostname` (lower case, no brackets, no port) with a trailing `.` removed. The app's binding is the pin it used for this connection, or, without a pin, the host it connected to; a server accepts any binding in this set (part D lists external addresses with a public certificate beside a private CA).
- Produces models (migration `0003_pairing_proof`):
  - `PairingCode.proof_key_sealed = BinaryField(null=True)`: `crypto.seal(master_key(), derive_proof_key(code, id), aad=b"pairing-proof-key|" + str(id).encode())`, written by `create_pairing_code` (which now sets `id=uuid4()` before the insert). The code itself stays stored only as its hash; the derived key is the only material from which a proof can be checked. A code made before the update has no key: it pairs by QR only.
  - `PairingCode.failed_proofs = PositiveSmallIntegerField(default=0)`; `MAX_FAILED_PROOFS = 5`.
  - `PairingChallenge(BaseModel)`: `nonce` (CharField 64, unique; `secrets.token_urlsafe(32)`, not secret), `expires_at` (created + 5 minutes), `used_at` (null). Server table, no user, no workspace.
- Produces services (`apps/devices/services.py`):
  - `create_pairing_challenge() -> PairingChallenge`: deletes challenges with `expires_at < now` first, then creates one.
  - `pair_device_with_proof(*, pairing_id: UUID, nonce: str, proof: str, device_name: str, app_version: str = "", client_ip: str | None = None) -> tuple[Device, str]`, in this order:
    1. Name trimmed and required (422 as `pair_device`).
    2. Its own `transaction.atomic()` that commits: `select_for_update()` the challenge by `nonce`; unknown, expired or used → after the block, 400 `pairing_challenge_invalid` ("Het koppelen duurde te lang. Probeer het opnieuw."); otherwise `used_at = now` (single use, also when the proof turns out wrong).
    3. Its own `transaction.atomic()`: `select_for_update().select_related("user")` the `PairingCode` with `id=pairing_id`, `used_at` null, `expires_at > now`, `proof_key_sealed` not null, user active. Missing → outcome invalid. Otherwise unseal the key and compare `proof` with `pairing_proof(key, nonce=nonce, trust_binding=b)` for each `b` in `trust_bindings()` with `hmac.compare_digest` (every candidate is compared, no early exit). No match → `failed_proofs += 1`, and at `MAX_FAILED_PROOFS` also `used_at = now` (the code is burned for QR as well); the block commits, then 400 `pairing_code_invalid` with the existing sentence ("Deze koppelcode klopt niet of is verlopen. Maak een nieuwe code aan."). A match → `used_at = now` and the device created exactly as `pair_device` does (a shared private `_create_device(pairing_code, name, app_version, client_ip, via)`), `device_paired {"via": "proof"}`.
- Produces endpoints (both `auth=None`; the QR operation `pairDevice` and `PairIn` are unchanged apart from Task 7's defaulted field):

| Operation | Method, path | Throttle | In → Out | Errors |
|---|---|---|---|---|
| `createPairingChallenge` | POST `/devices/pairing-challenges` (no body) | `ClientAddressThrottle("pairing_challenge", "20/10m")` | → 201 `PairingChallengeOut{nonce: str, expires_at: datetime}` | 429 |
| `pairDeviceWithProof` | POST `/devices/pair-with-proof` | `ClientAddressThrottle("pairing", "10/10m")` (the scope of `pairDevice`, so both share one count per address) | `PairProofIn{pairing_id: UUID, nonce: str (max 64), proof: str (pattern `^[0-9a-f]{64}$`), device_name: str (1–100), app_version: str = "" (max 50)}` → 201 `PairOut` | 400 `pairing_challenge_invalid` / `pairing_code_invalid`, 429, body errors |

- Fixture `backend/tests/fixtures/pairing_proof_v1.json`, shared verbatim with the iOS plan (Task 3 there) and copied into the iOS repo:

```json
{
  "context": "brievie-pair-v1",
  "iterations": 600000,
  "typed_code": "abcd-2345",
  "normalised_code": "ABCD2345",
  "pairing_id": "0b7e2f4c-5d1a-4e8b-9c3f-6a2d8e1f4b70",
  "nonce": "q3Vb8xN2pL5sT9wE1yH4kM7rC0uZ6aJdFgQiXoWnYeK",
  "key": "b0d30d4a0224e576656286cc732bc500d8b93217860fd69ca42cb95cf245105f",
  "cases": [
    {"trust_binding": "abababababababababababababababababababababababababababababababab",
     "proof": "f658f764994838ca7b728313d410619295065549d3ccf22a04746ec95c0f2805"},
    {"trust_binding": "brievie.example.nl",
     "proof": "f6a8154668ac8fcdcdb1d516278aed3ad86bba62f6d993fd590c64bc54e641ad"}
  ]
}
```

  (Computed for this plan with Python's `hashlib.pbkdf2_hmac` and `hmac`; the second proof was checked independently with `openssl dgst -sha256 -mac HMAC`.)

- [ ] **Step 1: Failing tests**

```python
def test_proof_matches_the_shared_vector() -> None:
    # load fixtures/pairing_proof_v1.json: derive_proof_key(typed_code, pairing_id).hex() == key;
    # for each case pairing_proof(key, nonce=..., trust_binding=...) == proof
def test_trust_bindings_are_the_pin_and_the_public_hosts(settings, monkeypatch) -> None:
    # URLs ["https://Brievie.Example.NL.", "https://[2001:db8::1]:8443"], pin "ab"*32 -> {"ab"*32, "brievie.example.nl", "2001:db8::1"}
def test_a_link_pairing_pairs_without_the_code(client, settings) -> None:
    # signed-in user: createPairingCode -> read pairing from server_link; createPairingChallenge -> nonce;
    # pairDeviceWithProof with the proof for binding "brievie.example.nl" -> 201 PairOut; no request body holds the code;
    # Device.paired_via "password"; device_paired {"via": "proof"}
def test_a_proof_bound_to_another_ca_is_refused(client, monkeypatch) -> None:
    # server pin "ab"*32; proof computed with binding "cd"*32 -> 400 pairing_code_invalid; failed_proofs 1;
    # the same code still pairs by pairDevice (QR)
def test_a_proof_bound_to_another_host_is_refused(client, settings) -> None   # binding "attacker.example" -> 400
def test_a_replayed_nonce_is_refused(client) -> None:
    # a successful pairing; the same nonce again with a new code's valid proof -> 400 pairing_challenge_invalid;
    # an expired nonce (expires_at in the past) -> the same; an unknown nonce -> the same
def test_five_wrong_proofs_burn_the_code(client) -> None:
    # five wrong proofs (fresh nonce each) -> 400 each; failed_proofs 5, used_at set;
    # the sixth with the right proof -> 400 pairing_code_invalid; pairDevice with the code -> 400 pairing_code_invalid
def test_failed_proofs_are_counted_although_the_request_fails(client) -> None:
    # the 400 answer does not roll the count back: a new DB read shows failed_proofs 1
def test_a_1c_app_still_pairs_by_qr_with_the_code(client) -> None:
    # pairDevice with {"code": "ABCD-2345", "device_name": "iPhone"} (the 1c body, no app_version) -> 201
def test_the_code_is_never_stored(client) -> None:
    # after createPairingCode: neither the code nor its normalised form occurs in any column of PairingCode;
    # proof_key_sealed does not contain the derived key in plain bytes
def test_a_code_from_before_the_update_has_no_proof_path(client) -> None:
    # proof_key_sealed None -> pairDeviceWithProof 400 pairing_code_invalid; pairDevice still 201
def test_the_challenge_is_rate_limited(client) -> None                 # 21st createPairingChallenge -> 429
def test_proof_pairing_shares_the_pairing_throttle(client) -> None     # 6 pairDevice + 5 pairDeviceWithProof -> the 11th 429
def test_old_challenges_are_removed_when_a_new_one_is_made() -> None
```

- [ ] **Step 2: Run** `cd backend && uv run pytest tests/test_devices_pairing_proof.py -v` → FAIL.
- [ ] **Step 3: Implement** `proof.py`, the migration, the services and the endpoints; `make openapi && cd web && pnpm gen:api`.
- [ ] **Step 4: Run** `make test` → PASS (the contract test passes: two new operations, `PairIn` unchanged).
- [ ] **Step 5: Commit** `feat(devices): pair from a link with a proof bound to the connection, never the code`

### Task 9: Device tokens that stop when unused

**Files:**
- Create: `backend/apps/devices/migrations/0004_tokenexpirystart.py`, `backend/tests/test_devices_expiry.py`
- Modify: `backend/apps/devices/{models,services,auth}.py`

**Interfaces:**
- Produces: `TokenExpiryStart(BaseModel)` with `started_at` (DateTimeField) and the singleton constraint pattern of `SetupToken`; the migration's module-level `record_install_moment(apps: StateApps, schema_editor: object) -> None` creates the row with `timezone.now()`: the moment the update was installed (D 14). There is no extra column on `Device` and no extra write per request (D 7.1).
- Produces: `TOKEN_IDLE_LIMIT = timedelta(days=180)`; pure `token_expired(device: Device, *, baseline: datetime | None, now: datetime) -> bool`: the reference is the later of `device.last_seen_at or device.created_at` and `baseline`; expired when `now - reference >= TOKEN_IDLE_LIMIT`. Part C adds the SSO confirmation rule (D 9.4, reading `Device.paired_via`) to this one function.
- Produces: `device_for_token` loads the device by hash with the baseline in the same query (`Subquery` on `TokenExpiryStart`); revoked, unknown, inactive user → None; expired → in its own `transaction.atomic()`: `revoked = Device.objects.filter(pk=device.pk, revoked_at__isnull=True).update(revoked_at=now)`, and `device_expired` (server log, `AuditActor.system()`, target the device) is written only when `revoked == 1` (two requests with the same expired token at once log it once) → None. The caller raises exactly the existing `ApiError(401, "not_authenticated", _("Je bent niet ingelogd."))`. `list_devices` and `listUserDevices` leave out expired devices.

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
def test_a_second_detection_of_the_same_expiry_writes_nothing() -> None:
    # the expiry step run twice for the same loaded (stale) device object -> one revoked_at write, one audit row
def test_a_used_token_keeps_working(client) -> None         # last_seen 1 day ago -> 200
def test_expired_devices_are_not_listed(client) -> None
def test_migration_records_the_install_moment() -> None:
    # call record_install_moment(apps, None) directly (a transaction=True test elsewhere truncates the seeded row):
    # one TokenExpiryStart, started_at within a second of now
```

- [ ] **Step 2: Run** `cd backend && uv run pytest tests/test_devices_expiry.py tests/test_devices_auth.py -v` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `make test` → PASS.
- [ ] **Step 5: Commit** `feat(devices): a token stops 180 days after its last use and answers like a revoked one`

### Task 10: An admin over a member's account: devices and sessions

**Files:**
- Create: `backend/tests/test_accounts_user_actions_api.py`
- Modify: `backend/apps/accounts/{api,services,schemas}.py`, `backend/apps/devices/services.py`, `backend/apps/workspaces/{services,schemas,api}.py`, `docs/api/openapi.json`, `web/src/api/schema.d.ts`

**Interfaces:**
- Consumes: `account_authority` (Task 6), `end_sessions` (Task 4), `notify` (Task 5), `token_expired` (Task 9).
- Produces endpoints (default auth):

| Operation | Method, path | Out | Errors |
|---|---|---|---|
| `endUserSessions` | DELETE `/users/{user_id}/sessions` | 204 | 403 `server_admin_only`, 404 `user_not_found`, 401, 403 |
| `listUserDevices` | GET `/users/{user_id}/devices` | `list[DeviceOut]` | 403, 404, 401 |
| `revokeUserDevice` | DELETE `/users/{user_id}/devices/{device_id}` | 204 | 403, 404 `user_not_found` / `device_not_found`, 401 |

- Produces services: `end_user_sessions(request, *, target_id) -> None` (`end_sessions(target, current=request)`, so a server admin who ends their own sessions gets a clean 204; notice `sessions_ended`, `session_ended {"count": n, "reason": "admin"}`), `list_user_devices(request, *, target_id) -> list[Device]`, `revoke_user_device(request, *, target_id, device_id) -> None` (notice `device_revoked` with `{"device": name}`, `device_revoked`). Every audit row goes to the server log and, when `authority.workspace_id`, also to that workspace's log.
- Produces: `MemberOut.can_manage_account: bool = False`, computed for the caller by `list_members(workspace: Workspace, *, viewer: User) -> list[MemberOut]` with the same rule as `account_authority` (one query for the members' membership counts).

- [ ] **Step 1: Failing tests**

```python
def test_workspace_admin_ends_a_members_sessions(client) -> None     # member's client -> 401; notice; server + workspace rows
def test_workspace_admin_cannot_touch_a_member_of_two_workspaces(client) -> None:
    # all three operations -> 403 server_admin_only; member's session alive; device not revoked; no notice
def test_server_admin_lists_and_revokes_anyones_device(client) -> None
def test_server_admin_ending_their_own_sessions_gets_204(client) -> None  # not SessionInterrupted; next request 401
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

### Task 11: Invitations for admins

**Files:**
- Create: `backend/apps/workspaces/invitations.py`, `backend/apps/workspaces/access.py`, `backend/apps/workspaces/migrations/0003_invitation.py`, `backend/tests/test_workspaces_invitations_api.py`
- Modify: `backend/apps/workspaces/{models,api,schemas}.py`, `docs/api/openapi.json`, `web/src/api/schema.d.ts`

**Interfaces:**
- Produces: `Invitation(BaseModel)`, a server table like `PairingCode` (not a `WorkspaceModel`, so no RLS and outside `test_rls_coverage.py`): `workspace` (FK CASCADE, `related_name="invitations"`), `role` (CharField 10, `Membership.Role`, default `member`), `person` (FK `people.Person`, SET_NULL, null), `email` (EmailField 254, blank: lower case), `token_hash` (CharField 64, unique), `expires_at`, `created_by` (FK User, SET_NULL, null), `created_by_name` (CharField 150), `accepted_by` (FK User, SET_NULL, null), `accepted_at` (null), `revoked_at` (null), `note` (CharField 100, blank, BSN-masked; shown to admins only).
- Produces (`apps/workspaces/access.py`, part B seams; no-ops in A, each with a docstring naming the part-B work): `person_requires_email(person: Person) -> bool` (returns False; B: True when the person has restricted documents), `on_member_added(workspace_id: UUID, user_id: UUID, *, person_id: UUID | None, created_by_id: UUID | None) -> None` (B: the person now linked to the user becomes a reader, and the grant is recorded with the invitation's creator as actor), `on_member_removed(workspace_id: UUID, user_id: UUID) -> None` (B: reader rows, shares, department memberships, as the system).
- Produces services (`apps/workspaces/invitations.py`): `create_invitation(*, membership: Membership, actor: AuditActor, request: HttpRequest, role: str, expires_in_days: int, email: str, person_id: UUID | None, note: str) -> IssuedInvitation` (`IssuedInvitation(invitation: Invitation, link: str, home_network_only: bool)`); `list_invitations(workspace: Workspace) -> list[Invitation]` (not accepted, not revoked; expired ones included with status `expired`; newest first); `revoke_invitation(*, workspace, invitation_id, actor) -> None`; `renew_invitation(*, membership, actor, request, invitation_id) -> IssuedInvitation` (revokes the old one, creates a new one with the same role, person, email and note and the original validity in days); `revoke_invitations_made_by(*, workspace_id: UUID, user_id: UUID, actor: AuditActor) -> int` (every open invitation of that workspace whose `created_by` is the user gets `revoked_at`; one `invitation_revoked {"reason": "creator_lost_role"}` per row; Tasks 13 and 14 call it).
- Rules: role in `admin|member|viewer`; `expires_in_days` in 1, 7, 30 (D 6.2); the person must belong to the workspace (404 `person_not_found`), not be archived, and have no account link (409 `person_already_linked`, "Deze persoon is al gekoppeld aan een lid."); `person_requires_email(person)` and no email → 422 `email_required`; an email that belongs to a member of this workspace → 409 `already_member`. Audit `invitation_created {"role", "expires_in_days"}`, `invitation_revoked {"reason": "admin" | "renewed"}`; target kind `invitation`, name = the note or the email (never the token).
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
def test_revoke_invitations_made_by_stops_only_that_admins_open_links(ws_a) -> None
def test_invitation_is_not_a_workspace_table() -> None                       # not issubclass(Invitation, WorkspaceModel)
```

- [ ] **Step 2: Run** `cd backend && uv run pytest tests/test_workspaces_invitations_api.py -v` → FAIL.
- [ ] **Step 3: Implement**; `make openapi && cd web && pnpm gen:api`.
- [ ] **Step 4: Run** `make test` → PASS.
- [ ] **Step 5: Commit** `feat(workspaces): invitations by link, with expiry, revoke and renew`

### Task 12: Opening and accepting an invitation

**Files:**
- Create: `backend/tests/test_workspaces_accept_invitation.py`
- Modify: `backend/apps/workspaces/{invitations,api,schemas}.py`, `backend/apps/accounts/services.py`, `docs/api/openapi.json`, `web/src/api/schema.d.ts`

**Interfaces:**
- Consumes: `Invitation` (Task 11), `start_session` (Task 4), `ensure_person_for_member` (1c), `access.on_member_added`, `apps.pipeline.settings_service.status` and `apps.pipeline.schemas.AiStatusOut` (1c/AI consent: the AI line every member sees).
- Produces (`apps/accounts/services.py`): `verify_password(request: HttpRequest, *, email: str, password: str) -> User`: the lockout check of `login` (429 `too_many_attempts`), then the account by normalised email **whether active or not**, `check_password`; a wrong password or an unknown address (with a dummy hash run for equal timing) sends `user_login_failed` so axes counts it, and raises 401 `invalid_credentials`. Plain `login` keeps refusing a deactivated account (Django's `ModelBackend`), so a deactivated account comes back only through an invitation and its own password (D 6.2).
- Produces endpoints (`auth=None`, both `ClientAddressThrottle("invitation", "20/10m")`, sharing one count per address):

| Operation | Method, path | In → Out | Errors |
|---|---|---|---|
| `getInvitation` | POST `/invitations/lookup` | `InvitationTokenIn{token: str (max 100)}` → `InvitationInfoOut{workspace_name: str, invited_by: str, role: str, email: str, person_name: str = "", ai: AiStatusOut}` | 400 `invitation_invalid`, 429, body errors |
| `acceptInvitation` | POST `/invitations/accept` | `AcceptInvitationIn{token: str, account: str (max 10), name: str = "" (max 150), email: str (max 254), password: str (max 128)}` → 200 `AcceptedInvitationOut{user: UserOut, workspace: WorkspaceOut}` | 400 `invitation_invalid`, 401 `invalid_credentials`, 403 `invitation_email_mismatch`, 409 `account_exists` / `already_member` / `invitation_person_taken`, 422, 429, body errors |

  `account` is a validated `str`, not a `Literal`: part C adds `"sso"` without changing a released property's definition (the contract test compares `enum`). `accept_invitation` refuses anything but `new` and `existing` with 422 `validation_error` ("Kies een nieuw account of een bestaand account."). `InvitationInfoOut.person_name` and `ai` are read inside `workspace_context(invitation.workspace_id)` (`Person` and `AISettings` have forced RLS; outside the context they come back empty). `ai` is `AiStatusOut` built as `getAiStatus` builds it for a non-admin (`model` None): what every member sees on the Inbox page, shown before accepting (DECISIONS 2026-11-17: the per-member "read" step waited for invitations).
- Produces: `lookup_invitation(*, token: str) -> InvitationInfo` and `accept_invitation(request: HttpRequest, *, token: str, account: str, name: str, email: str, password: str) -> Membership`, in this order:
  1. Look the invitation up by hash (no lock); unknown, accepted, revoked or expired → 400 `invitation_invalid`, "Deze uitnodiging werkt niet meer. Vraag een nieuwe." for every case. `account` not in `new|existing` → 422.
  2. `existing`: `verify_password` **outside** the transaction (a failure must keep axes' record). `new`: an account with this email exists (active or not) → 409 `account_exists` ("Er is al een account met dit e-mailadres. Kies ‘Ik heb al een account’."); otherwise validate email, name (required) and password with the existing rules.
  3. `invitation.email` set and different from the account's → 403 `invitation_email_mismatch`.
  4. `transaction.atomic()`: `select_for_update()` on the invitation and the same validity check again (the second of two simultaneous accepts stops here); already a member → 409 `already_member` (invitation stays unused); create the user for `new`; reactivate an inactive `existing` account; create the membership (user and membership inserts inside a savepoint: an `IntegrityError` from a concurrent accept with the same email, or the same user twice, becomes 409 `account_exists` resp. `already_member`, never a 500); in `workspace_context`: with `invitation.person`, lock it, a person that got linked meanwhile → 409 `invitation_person_taken`, else `person.user = user` and `record(PERSON_LINKED, target=the person)` (D 11); without, `ensure_person_for_member`; `on_member_added(workspace_id, user.id, person_id=<the person now linked to the user>, created_by_id=invitation.created_by_id)`; mark accepted; `record(INVITATION_ACCEPTED, values={"role", "new_account", "account_reactivated"})`; `start_session(request, user, via="invitation")` (which replaces the row of a session the browser had before).
  Part C adds `account: "sso"` and the SSO hand-off here (D 6.2).

- [ ] **Step 1: Failing tests**

```python
def test_lookup_shows_workspace_inviter_and_role(client, ws_a) -> None
def test_lookup_names_the_person_from_inside_the_workspace_context(client, ws_a) -> None   # person "Papa" -> person_name "Papa"
def test_lookup_shows_the_ai_line_when_ai_is_on(client, ws_a) -> None:
    # AI external with consent (mistral) -> ai.mode "external", ai.supplier "mistral", ai.host set, ai.since set, ai.model None;
    # AI off -> ai.mode "off"
@pytest.mark.parametrize("state", ["accepted", "revoked", "expired", "unknown"])
def test_dead_invitation_answers_one_sentence(client, ws_a, state) -> None:
    # lookup and accept: 400 {"code": "invitation_invalid", "message": "Deze uitnodiging werkt niet meer. Vraag een nieuwe."}
def test_accept_with_a_new_account_signs_in_and_links_the_person(client, ws_a) -> None
    # invitation with person "Papa": Person.user == new user; GET /me 200; role member; sign_in_succeeded via invitation; person_linked row
def test_accept_without_person_creates_one(client, ws_a) -> None
def test_an_unknown_account_kind_is_422(client, ws_a) -> None                    # account "sso" -> 422 validation_error (until part C)
def test_new_account_with_a_taken_address_is_409(client, ws_a) -> None          # account_exists; invitation unused
def test_a_unique_violation_on_accept_answers_409(client, ws_a, monkeypatch) -> None:
    # a user with the same email is created between the check and the insert -> 409 account_exists, not 500; invitation unused
def test_existing_account_joins_a_second_workspace(client, ws_a, ws_b) -> None
def test_accepting_while_signed_in_as_the_same_user_keeps_one_session_row(client, ws_a, ws_b) -> None
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

### Task 13: Changing a member's role

**Files:**
- Create: `backend/apps/workspaces/members.py`, `backend/tests/test_workspaces_member_role_api.py`
- Modify: `backend/apps/workspaces/{api,schemas}.py`, `docs/api/openapi.json`, `web/src/api/schema.d.ts`

**Interfaces:**
- Consumes: `revoke_invitations_made_by` (Task 11), `revoke_resets_made_by` (Task 6).
- Produces (`apps/workspaces/members.py`): `lock_memberships(workspace: Workspace) -> list[Membership]` (`select_for_update()`, ordered by id: the one lock order for every member change, so two admins demoting each other cannot both pass); `change_role(*, workspace: Workspace, user_id: UUID, role: str, actor: AuditActor) -> Membership`: after the lock, the actor's role is read again **from the locked rows**: not `admin` any more → 403 `forbidden` (with three admins, A demoting B and B demoting A at once: whichever runs second finds itself no longer admin); unknown member → 404 `member_not_found`; demoting the only admin → 409 `last_admin` ("Een werkruimte heeft altijd minstens één beheerder. Maak eerst iemand anders beheerder."); same role → no write, no event; else `member_role_changed {"from", "to"}` with the member's person name as target name. A demotion from `admin` also calls `revoke_invitations_made_by(workspace_id, user_id)` and `revoke_resets_made_by(user_id)` (D 21: what an admin made stops when they stop being admin).
- Produces endpoint `updateMemberRole`: PUT `/workspaces/{workspace_id}/members/{user_id}/role`, admin, `MemberRoleIn{role: Literal["admin","member","viewer"]}` → `MemberOut`; errors 404, 409, `SESSION_WRITE_ERRORS`, `BODY_ERRORS`. The member sees the new role at their next request (no session change needed: `workspace_member` reads the membership each time).

- [ ] **Step 1: Failing tests**

```python
def test_admin_changes_a_members_role(client, ws_a) -> None          # member -> viewer; member's next POST to a member endpoint -> 403
def test_last_admin_cannot_demote_themselves(client, ws_a) -> None   # 409 last_admin; role unchanged; no audit row
def test_an_admin_may_step_down_when_another_admin_exists(client, ws_a) -> None
def test_a_demoted_actor_cannot_finish_a_change(ws_a) -> None:
    # admins A, B, C; B demoted by A first; then change_role with actor B (already authorised by the endpoint) -> 403 forbidden; nothing changed
def test_demotion_revokes_open_invitations_and_reset_links(client, ws_a) -> None:
    # admin B made an admin invitation and a reset link; A demotes B -> both revoked; invitation_revoked {"reason": "creator_lost_role"}
def test_members_cannot_change_roles(client, ws_a) -> None           # 403
def test_unknown_member_is_404(client, ws_a) -> None
def test_role_change_is_logged_with_from_and_to(client, ws_a) -> None
def test_member_changes_take_the_memberships_lock_in_id_order(ws_a) -> None   # captured SQL: one SELECT ... ORDER BY id ... FOR UPDATE
```

- [ ] **Step 2: Run** the test file → FAIL.
- [ ] **Step 3: Implement**; `make openapi && cd web && pnpm gen:api`.
- [ ] **Step 4: Run** `make test` → PASS.
- [ ] **Step 5: Commit** `feat(workspaces): change a member's role, never below one admin`

### Task 14: Removing a member and leaving

**Files:**
- Create: `backend/tests/test_workspaces_remove_member.py`
- Modify: `backend/apps/workspaces/{members,api}.py`, `backend/apps/accounts/services.py`, `docs/api/openapi.json`, `web/src/api/schema.d.ts`

**Interfaces:**
- Consumes: `lock_memberships` (Task 13), `end_sessions`, `end_current_session` (Task 4), `lock_active_server_admins`, `other_active_server_admin_exists` (Task 3), `revoke_invitations_made_by` (Task 11), `revoke_resets_made_by` (Task 6), `access.on_member_removed` (Task 11).
- Produces (`apps/accounts/services.py`): `deactivate_account(user: User, *, actor: AuditActor, current: HttpRequest | None = None) -> None`: `is_active = False`, `end_sessions(user, current=current)` (`session_ended {"reason": "deactivated"}`), every unrevoked device `revoked_at = now`, unused pairing codes deleted, unused reset links revoked, `revoke_resets_made_by(user.id)`.
- Produces (`apps/workspaces/members.py`): `remove_member(*, request: HttpRequest, workspace: Workspace, user_id: UUID, actor: AuditActor, left: bool) -> None`, one `transaction.atomic()` (D 6.3):
  1. `lock_memberships`; for a removal (not `left`) the actor's role is re-read from the locked rows (not admin → 403 `forbidden`); unknown → 404 `member_not_found`; the only admin → 409 `last_admin` (also when they remove or leave themselves).
  2. No other membership on the server (the account would be deactivated): `lock_active_server_admins()` (after the membership lock: one order everywhere); the user is a server admin and the actor is not a server admin → 403 `server_admin_only` (D 21: a workspace admin never deactivates a server admin); the user is a server admin without another active one → 409 `last_admin` ("Er is altijd minstens één serverbeheerder nodig. Dit account kan daarom niet weg."). The `User` row lock makes two server admins who each lose their last membership in different workspaces at once leave one.
  3. In `workspace_context`: `Person.objects.filter(workspace=…, user_id=…).update(user=None)` (an `update()`, before the membership goes, because `Person.save` refuses a non-member), `Task.objects.filter(workspace=…, assigned_to_id=…).update(assigned_to=None)`, `on_member_removed(workspace.id, user_id)`; the member's exports are removed by Task 19's hook; `revoke_invitations_made_by(workspace_id, user_id)` and `revoke_resets_made_by(user_id)` when the member was admin.
  4. Delete the membership; with none left, `deactivate_account(user, actor=actor, current=request)` (a member who leaves their last workspace has their own session flushed, not deleted under them).
  5. `record(MEMBER_LEFT if left else MEMBER_REMOVED, values={"account_deactivated": …})`, and `person_unlinked` when a person was unlinked.
  Writes "as the system" (D 6.3) need nothing extra in part A: without part B every row in the workspace is visible to the admin; part B wraps steps 3 and 4 in `system_actor()`.
- Produces endpoints: `removeMember` DELETE `/workspaces/{workspace_id}/members/{user_id}` (admin) → 204; `leaveWorkspace` POST `/workspaces/{workspace_id}/leave` (any role, no body) → 204, then the caller's session has ended when the account was deactivated. Errors 403 `server_admin_only`, 404, 409, `SESSION_WRITE_ERRORS`.

- [ ] **Step 1: Failing tests**

```python
def test_last_admin_cannot_leave_or_remove_themselves(client, ws_a) -> None:  # DELETE own id and POST leave -> 409; nothing changed
def test_removal_unlinks_the_person_and_keeps_it(client, ws_a) -> None
def test_removal_unassigns_the_members_tasks(client, ws_a) -> None            # tasks of ws_a only; a task in ws_b stays assigned
def test_removing_the_last_membership_deactivates_the_account(client, ws_a) -> None:
    # is_active False; their web client -> 401; their device token -> 401 not_authenticated; pairing code unusable
def test_leaving_the_last_workspace_answers_204_and_signs_out(client, ws_a) -> None   # 204, not SessionInterrupted 400; next GET /me 401
def test_member_of_two_workspaces_removed_from_one_keeps_account_and_devices(client, ws_a, ws_b) -> None:
    # device token: GET /workspaces lists only ws_b; GET ws_a's documents -> 404 workspace_not_found
def test_removed_member_cannot_upload_or_confirm(client, ws_a) -> None       # upload with token -> 401; confirm with session -> 401
def test_workspace_admin_cannot_deactivate_a_server_admin(client, ws_a) -> None:
    # a server admin whose only membership is ws_a, removed by a plain ws_a admin -> 403 server_admin_only; membership and account intact
def test_last_server_admin_cannot_be_deactivated(client, ws_a) -> None       # the only server admin leaves their only workspace -> 409 last_admin
@pytest.mark.django_db(transaction=True)
def test_two_server_admins_leaving_at_once_keep_one() -> None:
    # server admins S1 (only in ws_a) and S2 (only in ws_b) leave at the same time (Barrier(2)) -> one 204, one 409 last_admin; one active server admin
def test_removal_revokes_what_the_admin_made(client, ws_a) -> None           # removed admin's open invitations and reset links revoked
def test_member_leaves(client, ws_a) -> None                                 # member_left logged; person unlinked
def test_re_invited_member_gets_their_person_back(client, ws_a) -> None      # invitation with the old person -> linked again
def test_removal_calls_the_part_b_seam(client, ws_a, monkeypatch) -> None    # on_member_removed called once with (ws id, user id)
```

- [ ] **Step 2: Run** the test file → FAIL.
- [ ] **Step 3: Implement**; `make openapi && cd web && pnpm gen:api`.
- [ ] **Step 4: Run** `make test` → PASS.
- [ ] **Step 5: Commit** `feat(workspaces): remove a member or leave, with devices and sessions ending with the last membership`

### Task 15: Appointing another server admin

Part A has no web page for server settings (part C) and no way to appoint a second server admin otherwise (D 21). Workspace creation stays as today: only `/setup` creates a workspace (`setup_server` → `create_workspace`); there is no API or command for a second one, and part A adds none.

**Files:**
- Create: `backend/apps/accounts/management/commands/set_server_admin.py`, `backend/tests/test_accounts_set_server_admin.py`
- Modify: `backend/apps/accounts/services.py`

**Interfaces:**
- Consumes: `lock_active_server_admins` (Task 3), `revoke_resets_made_by` (Task 6), `record` (Task 1).
- Produces (`apps/accounts/services.py`): `set_server_admin(*, email: str, grant: bool, actor: AuditActor) -> User` in one `transaction.atomic()`: the account by normalised email (unknown → `ValueError("unknown_account")`); `grant` and the account inactive → `ValueError("account_inactive")`; already in the asked state → no write, no event; removing: `lock_active_server_admins()`, the account is the only active one → `ValueError("last_server_admin")`; then the flag, `revoke_resets_made_by(user.id)` on removal, and `record(SERVER_ADMIN_GRANTED | SERVER_ADMIN_REVOKED, target=the user)` in the server log.
- Produces: `manage.py set_server_admin --email <address> [--remove]`: calls the service with `AuditActor.system()`; maps the three `ValueError`s to `CommandError` with a Dutch sentence each ("Er is geen account met dit e-mailadres.", "Dit account is uitgeschakeld. Nodig de persoon eerst opnieuw uit.", "Er is altijd minstens één serverbeheerder nodig."); prints one line on success. Documented in `docs/SELFHOST.md` (Task 26).

- [ ] **Step 1: Failing tests**

```python
def test_command_makes_a_member_server_admin() -> None          # call_command("set_server_admin", "--email", ...) -> is_server_admin; server_admin_granted row, actor system
def test_command_removes_the_flag_when_another_admin_exists() -> None   # --remove -> False; server_admin_revoked row; their reset links revoked
def test_the_last_server_admin_cannot_be_removed() -> None      # --remove on the only one -> CommandError; flag unchanged; no row
def test_an_unknown_address_or_inactive_account_is_refused() -> None
def test_granting_twice_writes_one_event() -> None
def test_removal_takes_the_server_admin_lock() -> None          # captured SQL: SELECT ... FROM accounts_user ... FOR UPDATE
```

- [ ] **Step 2: Run** `cd backend && uv run pytest tests/test_accounts_set_server_admin.py -v` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `make test` → PASS.
- [ ] **Step 5: Commit** `feat(accounts): appoint or remove a server admin from the command line`

---

## Slice A5: what is logged, reading the log, the own export

### Task 16: Audit rows from the existing services, and workspace settings

**Files:**
- Create: `backend/apps/workspaces/settings_service.py`, `backend/apps/workspaces/migrations/0004_workspace_allow_member_export.py`, `backend/tests/test_audit_existing_services.py`, `backend/tests/test_workspaces_settings_api.py`
- Modify: `backend/apps/rules/{services,api}.py`, `backend/apps/labels/{services,api}.py`, `backend/apps/people/{services,api}.py`, `backend/apps/pipeline/{settings_service,api}.py`, `backend/apps/pipeline/management/commands/configure_ai.py`, `backend/apps/workspaces/{models,services,api,schemas}.py`, the tests that call these services directly, `docs/api/openapi.json`, `web/src/api/schema.d.ts`

**Interfaces:**
- Produces: a required keyword `actor: AuditActor` on `create_rule`, `update_rule`, `delete_rule`, `run_rule`, `delete_label`, `delete_person`, `link_member` (callers pass `actor_from_request(request)`; tests pass `AuditActor.system()` or a user actor). They write `rule_created`, `rule_changed`, `rule_deleted`, `rule_run`, `label_deleted`, `person_deleted`, `person_linked` / `person_unlinked`. Rule and label rows carry the id only, with an empty target name (D 11 as changed by D 21: never label names; a rule name can hold one). Person rows carry the person's display name.
- Produces: `settings_service.Actor` gains `client_ip: str | None = None`; `configure(...)` writes `workspace_setting_changed {"setting": "ai", "value": mode}` in the same transaction as the `AIConsentEvent` it already writes. The audit actor: with `Actor.user_id` set, `configure` loads the user (`User.objects.only("id", "name").get(id=actor.user_id)`) and passes `AuditActor.for_user(user, client_ip=actor.client_ip)`, because `Actor` holds no name; without, `AuditActor.system()` (the command). The consent table, its events and the rule "updating AI settings needs role admin" stay as they are (DECISIONS 2026-11-17); Task 1 made the consent table insert-only for the app role.
- Produces: `Workspace.allow_member_export = BooleanField(default=True)`; the migration sets it False for `business` workspaces; `create_workspace` sets `kind == "home"`. D 6.3: off by default in Zakelijk.
- Produces endpoints: `getWorkspaceSettings` GET `/workspaces/{workspace_id}/settings` (any member) → `WorkspaceSettingsOut{allow_member_export: bool}`; `updateWorkspaceSettings` PUT same path (admin), `WorkspaceSettingsIn{allow_member_export: bool | None = None}` (None leaves the setting as it is; part B adds its settings the same way, so an older client never wipes a newer setting) → `WorkspaceSettingsOut`, writes `workspace_setting_changed {"setting": "allow_member_export", "value": …}` when it changes.

- [ ] **Step 1: Failing tests**

```python
@pytest.mark.parametrize(("call", "code"), [...])   # one row per service above
def test_service_writes_its_audit_code(ws_a, call, code) -> None
def test_label_and_rule_rows_carry_no_name(ws_a) -> None          # target_name "" and target_id set for label_deleted, rule_*
def test_ai_settings_change_is_logged_beside_the_consent_event(client, ws_a) -> None   # actor_name is the admin's name
def test_configure_ai_command_is_logged_as_the_system(ws_a) -> None
def test_member_export_is_on_for_home_and_off_for_business(ws_a, ws_b) -> None
def test_only_an_admin_changes_workspace_settings(client, ws_a) -> None
```

- [ ] **Step 2: Run** both test files → FAIL.
- [ ] **Step 3: Implement**; update the direct callers in existing tests (`grep -rn "delete_label(\|delete_person(\|create_rule(\|update_rule(\|delete_rule(\|run_rule(\|link_member(" backend/tests`); `make openapi && cd web && pnpm gen:api`.
- [ ] **Step 4: Run** `make test` → PASS.
- [ ] **Step 5: Commit** `feat(audit): rules, labels, persons and settings write the audit log`

### Task 17: Reading the log, with filters and CSV

**Files:**
- Create: `backend/apps/audit/{api,schemas}.py`, `backend/tests/test_audit_api.py`
- Modify: `backend/apps/audit/services.py`, `backend/brievie/api.py`, `docs/api/openapi.json`, `web/src/api/schema.d.ts`

**Interfaces:**
- Produces: `AuditFilter` (dataclass: `user_id: UUID | None`, `code: str`, `since: date | None`, `until: date | None`, `before: datetime | None`, `limit: int`); `list_workspace_events(workspace: Workspace, f: AuditFilter) -> list[WorkspaceAuditEvent]`, `list_server_events(f: AuditFilter) -> list[ServerAuditEvent]`; `user_id` matches `actor_id` or `target_id` (both hold user ids for user actors and user targets; person rows carry the person id as `target_id` and are found through the actor); `since`/`until` are whole days in `Europe/Amsterdam`, both inclusive; `before` pages backwards; `limit` 1–500, default 100. `csv_rows(events) -> Iterator[list[str]]` with the header `Tijdstip, Wie, Gebeurtenis, Code, Om wie of wat, Adres, Details`; every cell that starts with `=`, `+`, `-`, `@`, tab or carriage return gets a leading `'` (spreadsheet formula injection).
- Produces endpoints (query parameters all optional with defaults: `user_id: UUID | None = None`, `code: str = ""`, `since: date | None = None`, `until: date | None = None`, `before: datetime | None = None`, `limit: int = 100`):

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
def test_filters_on_user_code_and_days(client, ws_a) -> None               # user_id matches rows where the user acted or was acted on
def test_before_pages_backwards(client, ws_a) -> None
def test_server_log_is_for_server_admins(client, ws_a) -> None
def test_csv_has_the_header_and_guards_formulas(client, ws_a) -> None      # person named "=HYPERLINK(...)" -> cell "'=HYPERLINK(...)"
def test_csv_is_an_attachment(client, ws_a) -> None
```

- [ ] **Step 2: Run** the test file → FAIL.
- [ ] **Step 3: Implement**; `make openapi && cd web && pnpm gen:api`.
- [ ] **Step 4: Run** `make test` → PASS.
- [ ] **Step 5: Commit** `feat(audit): read the workspace and server logs with filters and a CSV download`

### Task 18: The export building block: sealed streams and `write_export`

**Files:**
- Create: `backend/apps/common/sealed_stream.py`, `backend/apps/documents/export.py`, `backend/tests/test_common_sealed_stream.py`, `backend/tests/test_documents_export.py`

**Interfaces:**
- Produces (`apps/common/sealed_stream.py`), so a large ZIP never exists in plaintext on disk or whole in memory: `class SealedWriter(io.RawIOBase)` with `__init__(self, target: BinaryIO, key: bytes, *, aad: bytes)`: buffers 4 MiB, writes each chunk as a 4-byte big-endian length plus `crypto.seal(key, chunk, aad=aad + b"|" + index + b"|" + (b"1" if last else b"0"))`; `close()` writes the last chunk (possibly empty). `iter_opened(source: BinaryIO, key: bytes, *, aad: bytes) -> Iterator[bytes]`: raises `DecryptionError` on a changed, reordered or missing chunk, and when the stream ends without a last chunk.
- Produces (`apps/documents/export.py`), the building block of Beheer 5 (part E adds the PDF per letter and the workspace export to it):
  - `export_context(workspace_id: UUID, *, reader_id: UUID | None) -> AbstractContextManager[None]`: **every read of an export goes through this one helper.** In part A it returns `workspace_context(workspace_id)` and ignores `reader_id`; part B makes it pass the reader to the RLS context, so private letters the reader may not see are left out. Nothing in `export.py` or `member_export.py` calls `workspace_context` directly (a test scans for it).
  - `@dataclass(frozen=True) class ExportCounts: documents: int; pages: int`.
  - `write_export(*, workspace_id: UUID, reader_id: UUID | None, document_ids: Sequence[UUID], target: BinaryIO, title: str) -> ExportCounts`: a ZIP (`zipfile`, streamed, no seek) with `pagina's/<document id>/<n>.jpg` (each page opened from its sealed file), `brievie-export.json` (`{"version": 1, "documents": [{"id", "status", "sender", "document_type", "letter_date", "summary", "summary_by_ai", "created_at", "persons": [names], "labels": [names], "tasks": [{"type", "description", "deadline", "status"}], "pages": [paths]}]}`) and `LEESMIJ.txt` (Dutch: what is in it; the BSN is masked in the text but visible on the page images; the file is not encrypted once downloaded). It opens **its own short `export_context` per document** (one transaction each, closed before the next page file is read), so it must be called outside any open transaction; it never runs inside the caller's context. A document that is gone by the time it is read is skipped.

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
@pytest.mark.django_db(transaction=True)
def test_write_export_opens_a_short_context_per_document(ws_a) -> None:
    # called with no transaction open; CaptureQueriesContext shows one BEGIN/COMMIT pair per document; no transaction open at return
def test_a_document_deleted_meanwhile_is_skipped(ws_a) -> None
def test_every_export_read_goes_through_export_context() -> None:
    # source scan: "workspace_context(" appears in apps/documents/export.py only inside export_context, and not in member_export.py
```

- [ ] **Step 2: Run** `cd backend && uv run pytest tests/test_common_sealed_stream.py tests/test_documents_export.py -v` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same tests, `make lint typecheck` → PASS / clean.
- [ ] **Step 5: Commit** `feat(documents): sealed streams and the export building block`

### Task 19: The member's own export

**Files:**
- Create: `backend/apps/documents/member_export.py`, `backend/apps/documents/jobs.py`, `backend/apps/documents/migrations/<next number>_memberexport.py` (from `makemigrations documents`), `backend/tests/test_documents_member_export_api.py`
- Modify: `backend/apps/documents/{models,api,schemas}.py`, `backend/apps/workspaces/members.py` (removal deletes the member's exports), `docs/api/openapi.json`, `web/src/api/schema.d.ts`

**Interfaces:**
- Consumes: `SealedWriter`, `iter_opened`, `export_context`, `write_export` (Task 18), `allow_member_export` (Task 16).
- Produces: `MemberExport(WorkspaceModel)` with `enable_rls`: `user` (FK User, PROTECT), `status` (`pending`, `ready`, `failed`), `storage_path` (CharField 300; set at creation to `exports/<workspace id>/<export id>.sealed` under `BRIEVIE_STORAGE_ROOT`, outside the sweep's `<uuid>/<uuid>/<n>.page` layout, so a cleanup knows the file of a job that died), `document_count` (int 0), `size` (BigInteger 0), `expires_at` (null). Validity 24 hours.
- Produces services (`apps/documents/member_export.py`):
  - `member_document_ids(workspace_id: UUID, user_id: UUID) -> list[UUID]`, the part-A scope "every document on the member's name" (D 6.3 as changed by D 21): documents with a `DocumentAddressee` whose person's `user_id` is the member, read through `export_context(workspace_id, reader_id=user_id)`. **Part B** replaces the query with the member's reader rows ("what the member may read").
  - `create_my_export(*, membership: Membership, actor: AuditActor) -> MemberExport` (`allow_member_export` off → 403 `export_not_allowed`, "In deze werkruimte staat exporteren uit. Vraag de beheerder."; a pending one → 409 `export_running`; an older ready one is removed; defers the job; `export_created`).
  - `my_export(membership) -> MemberExport | None`.
  - `open_my_export(*, membership: Membership, actor: AuditActor) -> Iterator[bytes]`: a plain function, **not a generator**. Eagerly, inside the request (its transaction and RLS context still open): the row (ready, own, not expired, else 404 `export_not_found`), the workspace key, the open file handle, `export_downloaded`; then it returns `iter_opened(handle, key, aad=...)`, which the `StreamingHttpResponse` consumes after the view has returned and needs neither the database nor the context.
  - `remove_member_exports(workspace_id: UUID, user_id: UUID) -> None` (rows now, files `transaction.on_commit`).
- Produces jobs (`apps/documents/jobs.py`, queue `QUEUE` from `apps.pipeline.jobs`): `build_member_export(*, workspace_id: str, export_id: str) -> None`: the ids in a short `export_context` transaction; `write_export(..., reader_id=user_id)` into a `SealedWriter` on `storage_path`, outside any open transaction (each document in its own short context, pages read one at a time); then `ready` with `expires_at = now + 24 h`, `document_count`, `size` in a short transaction; any failure → `failed`, the partial file removed, the type of the exception logged only. `remove_expired_exports(timestamp: int) -> None` (`@app.periodic(cron="*/30 * * * *")`): per workspace, files and rows past `expires_at`; `pending` older than 2 hours become `failed` **and their file at `storage_path` is removed when it exists** (a job killed mid-write leaves a partial file).
- Produces endpoints (any role, own export only):

| Operation | Method, path | Out | Errors |
|---|---|---|---|
| `createMyExport` | POST `/workspaces/{workspace_id}/my-export` | 202 `MyExportOut` | 403 `export_not_allowed`, 409 `export_running`, 404, 401, 403 |
| `getMyExport` | GET `/workspaces/{workspace_id}/my-export` | `MyExportOut` | 404 `workspace_not_found`, 401 |
| `downloadMyExport` | GET `/workspaces/{workspace_id}/my-export/file` | `application/zip`, streamed (`StreamingHttpResponse`), `Cache-Control: private, no-store`, `Content-Disposition: attachment; filename="brievie-mijn-post-<yyyy-mm-dd>.zip"` | 404 `export_not_found`, 401 |

  `MyExportOut{allowed: bool, status: str ("none" | "pending" | "ready" | "failed"), id: UUID | None = None, created_at: datetime | None = None, expires_at: datetime | None = None, document_count: int = 0, size: int = 0}` (flat, primitives only).

- [ ] **Step 1: Failing tests**

```python
def test_member_document_ids_are_the_documents_on_their_name(ws_a) -> None   # not the documents of another person
def test_member_exports_and_downloads_their_post(client, ws_a) -> None:
    # POST -> 202 pending; run the job inline; GET -> ready; download -> a valid ZIP with only their letters
def test_a_missing_export_answers_404_before_streaming(client, ws_a) -> None:
    # expired or another member's export -> the response is a JSON 404 export_not_found, not a started stream
def test_the_download_streams_after_the_request_transaction(client, ws_a) -> None:
    # consuming streaming_content after the view returned (no RLS context) still yields the whole ZIP
def test_the_file_on_disk_is_sealed(client, ws_a, settings) -> None   # no b"PK\x03\x04" and no page bytes in the stored file
def test_another_member_cannot_download_it(client, ws_a) -> None      # 404 export_not_found
def test_business_workspace_refuses_until_allowed(client, ws_b) -> None
def test_expired_export_is_removed(ws_a) -> None                      # expires_at in the past -> remove_expired_exports(timestamp=0) deletes file and row
def test_a_stuck_pending_export_loses_its_partial_file(ws_a) -> None  # pending 3 h old with a partial file -> failed, file gone
def test_removing_the_member_removes_their_export(client, ws_a) -> None
def test_export_is_logged(client, ws_a) -> None                       # export_created {"document_count"}, export_downloaded
```

- [ ] **Step 2: Run** `cd backend && uv run pytest tests/test_documents_member_export_api.py -v` → FAIL.
- [ ] **Step 3: Implement**; the migration ends with `enable_rls("documents_memberexport")`; `make openapi && cd web && pnpm gen:api`.
- [ ] **Step 4: Run** `make test` → PASS (`test_rls_coverage.py` covers the new table).
- [ ] **Step 5: Commit** `feat(documents): a member exports their own post as a sealed ZIP for 24 hours`

---

## Slice A6: the web interface

Every web task: add queries to `web/src/api/queries.ts`, routes to `router.tsx`, strings through `t`, then `cd web && pnpm extract && pnpm test && pnpm lint && pnpm typecheck && pnpm build`. Tests use MSW handlers and `renderApp`, query by role, and wait for the visible result. New icons in `icons.tsx` (20 px, 1.5 px stroke, `aria-hidden`): `personAdd`, `key`, `shield`, `history`, `settings`, `laptop`, `qr`.

### Task 20: The page "Leden"

**Files:**
- Create: `web/src/routes/members.tsx`, `web/src/routes/members.test.tsx`, `web/src/components/role-dialog.tsx`
- Modify: `web/src/router.tsx`, `web/src/components/page.tsx` (nav item "Leden", icon `personAdd`, for every role), `web/src/components/icons.tsx`, `web/src/api/queries.ts`, `web/src/styles.css`, `web/src/locales/nl/messages.po`

**Interfaces:**
- Consumes: `listMembers` (with `can_manage_account`), `updateMemberRole`, `removeMember`, `leaveWorkspace`.
- Produces: route `/w/$workspaceId/leden` (`membersRoute`, child of `workspaceRoute`); `membersQuery` exists. A details list: Persona, name, role as a badge ("Beheerder", "Lid", "Meekijker"); for an admin per row "Rol wijzigen" (a dialog with three radios, each with one plain sentence: Beheerder "Kan leden uitnodigen en alles instellen.", Lid "Kan post scannen, controleren en ordenen.", Meekijker "Kan alles lezen, maar niets wijzigen.") and "Verwijderen" (alertdialog: "<naam> kan daarna niet meer bij deze werkruimte. De post blijft hier. Taken van <naam> hebben daarna niemand."); on the caller's own row "Werkruimte verlaten" (alertdialog; Task 25 adds the export offer). At home nothing about roles shows unless somebody opens "Rol wijzigen" (D 6.1). A 409 `last_admin` and a 403 `server_admin_only` show the server's sentence in the dialog.

- [ ] **Step 1: Failing tests**

```tsx
test("lists members with their role")                     // getByRole("row", {name: /Anna.*Beheerder/})
test("an admin changes a role with three plain choices")   // radios "Beheerder", "Lid", "Meekijker"; PUT body {role: "viewer"}
test("a member sees no admin actions")                     // queryByRole("button", {name: /Rol wijzigen/}) null
test("removing asks first and explains what stays")
test("the last admin sees why they cannot leave")          // 409 last_admin -> its message in the dialog
test("removing a server admin shows the server's sentence") // 403 server_admin_only -> its message in the dialog
test("the navigation links Leden")
```

- [ ] **Step 2: Run** `cd web && pnpm test src/routes/members.test.tsx` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full web check → PASS.
- [ ] **Step 5: Commit** `feat(web): the Leden page with roles, removing and leaving`

### Task 21: Inviting with a link and a QR code

**Files:**
- Create: `web/src/components/invite-dialog.tsx`, `web/src/components/invite-dialog.test.tsx`
- Modify: `web/src/routes/members.tsx`, `web/src/routes/members.test.tsx`, `web/src/api/queries.ts`, `web/src/locales/nl/messages.po`

**Interfaces:**
- Consumes: `createInvitation`, `listInvitations`, `revokeInvitation`, `renewInvitation`, `listPersons`.
- Produces: on Leden for admins, the command "Uitnodigen" opens `InviteDialog`: "Rol" (radios as in Task 20, default Lid), "Wie is het?" (optional: persons without an account link, as in `person-picker`; "Iemand nieuw" default), "E-mailadres" (optional, with the description "Alleen dit adres kan de uitnodiging gebruiken."), "Geldig" (select: 1 dag, 7 dagen (default), 30 dagen), "Notitie voor jezelf" (optional). The result view: the link in a read-only field with "Kopieer link", `QRCodeSVG` of the same link with the accessible name "QR-code met de uitnodiging", the expiry, and with `home_network_only` the message bar "Deze link werkt alleen op je thuisnetwerk." The link is shown once; closing the dialog forgets it. Below the members, "Open uitnodigingen" lists `listInvitations` (note or email, role, "Verloopt op …" or badge "Verlopen", actions "Nieuwe link" (opens the result view with the new link) and "Intrekken" (alertdialog)).

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

### Task 22: Accepting an invitation, then the app

**Files:**
- Create: `web/src/routes/invitation.tsx`, `web/src/routes/invitation.test.tsx`
- Modify: `web/src/components/ai-status-line.tsx` (split out `AiStatusSentence`), `web/src/router.tsx`, `web/src/api/queries.ts`, `web/src/locales/nl/messages.po`

**Interfaces:**
- Consumes: `getInvitation` (with `ai`), `acceptInvitation`, `createPairingCode` (`server_link`, `code`, `qr_payload`).
- Produces: `AiStatusSentence({status}: {status: AiStatusOut})` in `ai-status-line.tsx`: the existing sentence logic of `AiStatusLine` moved into a component that takes the status; `AiStatusLine` keeps its signature and renders `AiStatusSentence` with its query's data, so Inbox, Post and AI do not change.
- Produces: route `/uitnodiging` (child of `rootRoute`, no guard: it works signed in or not). The token is read from `useLocation().hash` (the URL fragment), then `history.replaceState(null, "", location.pathname)` removes it from the address bar and history; it goes only into the JSON bodies of `POST /invitations/lookup` and `/invitations/accept`, never into a URL. Without a fragment, or on 400: the server's sentence and nothing else. Otherwise "<invited_by> nodigt je uit voor <workspace_name>" with the role in a plain sentence, then, when `ai.mode` is not `off`, `AiStatusSentence` with the invitation's `ai` above the choice (the invitee reads where the text of letters goes before joining), then a choice "Nieuw account" (Naam, E-mailadres prefilled and read-only when the invitation has one, Wachtwoord with the rule hint of setup) or "Ik heb al een account" (E-mailadres, Wachtwoord). After success the page calls `createPairingCode` and shows: on a touch device (`useMediaQuery("(pointer: coarse)")`) a link "Open de Brievie-app" (`href={server_link}`, which names this code's pairing id) and the eight-character code to type in the app, with its countdown as on `/koppel-app`; otherwise the pairing QR code (`qr_payload`) with "Scan deze code met de Brievie-app". Both show "Naar Brievie" to `/`.

- [ ] **Step 1: Failing tests**

```tsx
test("the token goes in the body, not the url")            // MSW: new URL(request.url).search === "" and no token in the path; body.token === "abc"
test("the token is removed from the address bar")          // after load: window.location.hash === ""
test("a dead invitation shows the server's sentence")      // "Deze uitnodiging werkt niet meer. Vraag een nieuwe."
test("the AI line shows before accepting when AI is on")   // ai {mode: "external", supplier: "mistral", host, since} -> getByText(/AI aan: tekst van brieven gaat naar/) before the "Nieuw account" choice
test("no AI line when AI is off")
test("a new account is made and the app link is offered on a phone")   // matchMedia stub (pointer: coarse) -> link "Open de Brievie-app" with href brievie://server#...pairing=...; code "ABCD-2345"
test("on a computer the pairing QR code is shown")
test("an existing account signs in to accept")             // POST body account "existing"
test("a wrong password shows the server's sentence and keeps the form")
```

- [ ] **Step 2: Run** `cd web && pnpm test src/routes/invitation.test.tsx src/components/ai-status-line.test.tsx` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full web check → PASS (the existing AI status line tests stay green).
- [ ] **Step 5: Commit** `feat(web): accept an invitation, seeing the AI line first, and continue in the app`

### Task 23: My devices, my sessions, my password and the reset page

**Files:**
- Create: `web/src/routes/sessions.tsx`, `web/src/routes/password.tsx`, `web/src/routes/password-reset.tsx`, tests next to each
- Modify: `web/src/routes/pair-app.tsx` (title "Apparaten"), `web/src/components/device-table.tsx` (column "App-versie"; optional `userId` prop, used by Task 24), `web/src/components/page.tsx` (account menu: "Apparaten", "Waar je bent ingelogd", "Wachtwoord wijzigen"), `web/src/router.tsx`, `web/src/api/queries.ts`, `web/src/locales/nl/messages.po`

**Interfaces:**
- Consumes: `listDevices` (`app_version`), `listMySessions`, `endMySession`, `changePassword`, `completePasswordReset`.
- Produces routes: `/account/sessies` ("Waar je bent ingelogd": browser, method, address, started, last used, badge "Deze browser", "Afmelden" per row; ending "Deze browser" goes to the sign-in page); `/account/wachtwoord` (current, new, repeat; success message "Je wachtwoord is gewijzigd. Je andere sessies zijn beëindigd."); `/wachtwoord-herstellen` (root child, no guard; token from the fragment, then `history.replaceState`, into the body of `POST /auth/password-reset`; then to `/`).

- [ ] **Step 1: Failing tests**

```tsx
test("devices show the app version")
test("sessions mark this browser and end another")          // DELETE /me/sessions/{id}; the row disappears
test("ending this browser's session goes to the sign-in page")
test("changing the password reports that other sessions ended")
test("the reset page sends the token in the body")         // as in Task 22
test("the reset page removes the token from the address bar")
```

- [ ] **Step 2: Run** the new test files → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full web check → PASS.
- [ ] **Step 5: Commit** `feat(web): my devices with app versions, my sessions, password change and the reset page`

### Task 24: An admin's view of a member's account, and notices

**Files:**
- Create: `web/src/routes/member-devices.tsx`, `web/src/components/notice-bar.tsx`, tests next to each
- Modify: `web/src/components/page.tsx` (`NoticeBar` in `Shell`), `web/src/routes/members.tsx` (per row with `can_manage_account`: "Apparaten en sessies", "Herstellink maken"), `web/src/router.tsx`, `web/src/api/queries.ts`, `web/src/locales/nl/messages.po`

**Interfaces:**
- Consumes: `listUserDevices`, `revokeUserDevice`, `endUserSessions`, `createPasswordReset`, `listMyNotices`, `markNoticesSeen`; `DeviceTable` with `userId` (Task 23).
- Produces: route `/w/$workspaceId/leden/$userId` (admin, "Apparaten en sessies van <naam>": the device table with revoke, the button "Alle sessies beëindigen" with an alertdialog). "Herstellink maken" on Leden opens a dialog with the link once and the sentence "Geef deze link aan <naam>. Hij werkt 24 uur en één keer." A 403 `server_admin_only` shows the server's sentence. `NoticeBar`: one info message bar per unseen notice with the server's message and, when `can_dismiss`, a button "Gezien" (marks it seen); without it, the bar says nothing more and stays until a later sign-in.

- [ ] **Step 1: Failing tests**

```tsx
test("an admin ends a member's sessions after confirming")
test("an admin revokes a member's device")
test("a reset link is shown once with whom to give it")
test("a notice shows until it is marked seen")             // "Je wachtwoord is op 3 mei opnieuw ingesteld door Anne."
test("a notice that cannot be dismissed has no Gezien button")   // can_dismiss false -> queryByRole("button", {name: "Gezien"}) null
```

- [ ] **Step 2: Run** the new test files → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full web check → PASS.
- [ ] **Step 5: Commit** `feat(web): an admin's view of a member's devices and sessions, reset links, notices`

### Task 25: Instellingen, the log and "Mijn post exporteren"

**Files:**
- Create: `web/src/routes/settings.tsx`, `web/src/routes/audit-log.tsx`, `web/src/routes/server-audit-log.tsx`, `web/src/components/audit-table.tsx`, `web/src/components/export-panel.tsx`, tests next to each
- Modify: `web/src/components/page.tsx` (nav item "Instellingen", icon `settings`, admins only; account menu "Serverlogboek" for `is_server_admin`), `web/src/routes/members.tsx` (the export panel and the leave dialog's export offer), `web/src/router.tsx`, `web/src/api/queries.ts`, `web/src/locales/nl/messages.po`

**Interfaces:**
- Consumes: `getWorkspaceSettings`, `updateWorkspaceSettings`, `listWorkspaceAuditEvents`, `exportWorkspaceAuditEvents`, `listServerAuditEvents`, `exportServerAuditEvents`, `createMyExport`, `getMyExport`, `downloadMyExport`.
- Produces routes: `/w/$workspaceId/instellingen` (admin): switch "Leden mogen hun eigen post exporteren" and a link "Logboek"; `/w/$workspaceId/instellingen/logboek`: filters "Persoon" (members; sends `user_id`), "Gebeurtenis" (the closed list with Dutch labels, through Lingui in the web), "Van" and "Tot" (dates), a details list (Tijdstip, Wie, Gebeurtenis, Om wie of wat, Adres), "Meer laden" (`before` = the last row's time), and a link "Download als CSV" whose `href` is the CSV endpoint with the same query (a GET with the session cookie; no token involved); `/server/logboek` the same for the server log. `ExportPanel` on Leden for the caller: "Mijn post exporteren", then "Wordt gemaakt…" (polls `getMyExport` every 3 s while pending), then "Download (<n> brieven, geldig tot <tijd>)" as a link to `/my-export/file`; hidden with a sentence when `allowed` is false. The leave dialog first offers the export when allowed: "Exporteer eerst je post: na het verlaten kun je niet meer downloaden."

- [ ] **Step 1: Failing tests**

```tsx
test("an admin switches member export")                   // PUT body {allow_member_export: false}
test("the log filters by person and kind and loads more")  // query string carries user_id and code; second page uses before
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

### Task 26: Documentation and the last checks

**Files:**
- Modify: `docs/DECISIONS.md` (one new section at the end), `docs/GLOSSARY.md`, `docs/PRIVACY.md`, `docs/SELFHOST.md` (the `reset_password` and `set_server_admin` commands; the three new environment settings)

**Interfaces:**
- Produces: `## 2026-11-17: Members and devices (phase 1d, part A)` at the end of `DECISIONS.md`, one line each: the audit tables (no foreign keys, the overridden `workspace` field, the REVOKE in the migration and why the test checks `brievie_app` itself; that a restore keeps the privileges because `pg_restore` runs without `--no-privileges`); the AI consent history made insert-only by the same migration (closes "a database rule that forbids UPDATE and DELETE" of 2026-11-17; a later user deletion must handle its `SET_NULL`); the purge functions and the retention setting; `start_session` as the only sign-in, `UserSession` through `record_session` (receiver and `start_session`), key changes (`rekey_session`, the old key's row removed), the current session flushed rather than deleted, the middleware failing closed, the cleanup job and the migration for existing sessions; session lifetime through `BRIEVIE_SESSION_IDLE_DAYS`/`MAX_DAYS` until part C's server settings page; the login throttle; the account-action rule and its two error codes, re-checked when a reset link is completed; what an admin made revoked when they lose the role; a reset-made session cannot dismiss the reset notice; notices composed on the server; invitation and reset tokens (fragment, body, hash, one sentence for every dead link, `POST` for the lookup, `replaceState`); `Invitation` as a server table; the AI line on the invitation page (closes the per-member "read" step deferred to 1d); reactivation only through an invitation and the own password; `last_admin` for workspaces and for the server, with the server-admin row lock; a workspace admin never deactivates a server admin; `set_server_admin` and that workspace creation stays at setup; device expiry (180 days, `TokenExpiryStart`, revoked on detection, same 401); `X-Brievie-App-Version` at most once a day; `paired_via`; the pairing link `brievie://server#v=1&url=…&ca_sha256=…&pairing=…` and pairing by proof (PBKDF2 600,000 + HMAC-SHA256, trust binding, nonce, five failures; the fixture shared with iOS); the accepted race of a member removed during an upload (Plan decision 26); the own export (part-A scope "documents with an addressee linked to the member", `export_context` as the one read path, chunked sealing in `sealed_stream`, `documents.export` as the shared building block, ZIP of JPEGs plus JSON until part E adds PDFs, off in Zakelijk); the 1c contract fixture. In "Open items", strike "Two-factor authentication is not built" only after part C, and replace "Device tokens never expire…" and "Removing a member must unlink their person…" with a pointer to this section.
- Produces GLOSSARY rows: serverbeheerder | server admin; uitnodiging | invitation; herstellink | password reset link; sessie, "Waar je bent ingelogd" | session (`UserSession`); logboek | audit log; melding (aan een account) | notice (`AccountNotice`); intrekken (apparaat) | revoke; verlopen (koppeling) | expired; werkruimte verlaten | leave; lid verwijderen | remove member; mijn post exporteren | member export; koppellink | server link; koppelbewijs | pairing proof; app-versie | app version.
- Produces PRIVACY: the audit log (what it holds, client addresses, one year by default), sessions (address and browser description), the export (unencrypted once downloaded; the BSN visible on page images), the AI line shown to an invitee.

- [ ] **Step 1:** Write the four documents.
- [ ] **Step 2: Run** `make lint typecheck test openapi` and confirm `git diff --exit-code docs/api/openapi.json`; `cd web && pnpm gen:api && git diff --exit-code src/api/schema.d.ts && pnpm extract && pnpm test && pnpm lint && pnpm build` → all pass, nothing changed.
- [ ] **Step 3: Manual check** on a local stack with a throwaway database: invite a second user in one browser profile, accept in a private window (AI line visible when AI is on), open the app link on a phone or the QR on a computer, remove the member and see their session end; `set_server_admin` on the second user and back; record the result in the task report (not verified by tests: a real phone following `brievie://`).
- [ ] **Step 4: Commit** `docs: members and devices (phase 1d, part A)`

---

## Plan decisions

1. `WorkspaceAuditEvent` subclasses `WorkspaceModel` but overrides `workspace` with `db_constraint=False, on_delete=DO_NOTHING`: no foreign key (D 11) while the RLS coverage test and the house rule still apply.
2. The migration both REVOKEs and GRANTs on the audit tables, guarded by the role's existence: the test database has no default privileges, so a test that only checked "UPDATE is false" would pass with a forgotten REVOKE.
3. `start_session` is the only caller of Django's `login` (a test enforces it) and passes the method explicitly to `record_session`; the `user_logged_in` receiver calls the same function with the default, so `force_login` in existing tests keeps working and the row is written with `update_or_create` whichever runs first.
4. A session without a `UserSession` row is signed out (fail closed); a data migration records existing sessions, so the upgrade signs nobody out. Every key change (`login` flushing or cycling, `update_session_auth_hash`) moves or replaces the row in the same request (`start_session`, `rekey_session`), and a session ended under the caller is flushed, never row-deleted (check report 1.1, 1.2).
5. Session lifetime is enforced twice: Django's own `expire_date` and cookie (set by the middleware) and an explicit check of both limits; the limits are environment settings until part C's server settings page (D 21).
6. Account actions use one function, `account_authority`, with 404 for strangers and 403 `server_admin_only` for a member who also lives elsewhere: the reviewer's ruling of D 20 in one testable place. A reset link re-runs it at completion, so a target who joined a second workspace, or a creator who lost the role, ends the link.
7. Notices are composed in Dutch on the server: the web and a later iOS screen show the same sentence without repeating the list. `NoticeOut.can_dismiss` tells the web when "Gezien" would do nothing (a session made by a reset).
8. `Invitation` and `PasswordReset` are server tables found by token hash; the lookup is a `POST` so the token is in a body; every dead link answers one sentence with a 400, like `pairing_code_invalid`.
9. Accepting checks an existing account's password before taking the invitation's row lock, so a failed password keeps axes' record while the lock still decides the race; the inserts run in a savepoint so a unique-constraint race answers 409.
10. Reactivation happens only in `accept_invitation` with the account's own password; plain sign-in still refuses a deactivated account, as Django's backend does.
11. Removal unlinks the person with a queryset `update()` before deleting the membership, because `Person.save` refuses a user who is no longer a member.
12. The install moment for device expiry lives in a one-row table (`TokenExpiryStart`) read in the same query as the device: no column per device, no extra write (D 7.1, D 14). Its test calls the migration function directly, because a `transaction=True` test truncates the seeded row.
13. An expired token is revoked when it is detected, with a conditional `update()`, so the expiry is logged once even for two requests at the same moment; the 401 body is the existing one.
14. The app version is refreshed when it differs and the stored one is at least a day old (`app_version_at`): the "at most once a day" of D 7.1 without a write per request.
15. The pairing link is URL-encoded in the fragment (`v`, repeated `url`, `ca_sha256`, `pairing`): `URLComponents` parses it on iOS, and it carries no code. It is made per code, because the proof needs the code's id; `v` stays `1` because no app has shipped link support yet.
16. **Pairing from a link sends a proof, not the code** (controller ruling, D 6.2 and D 20). The proof key is `PBKDF2-HMAC-SHA256(code, "brievie-pair-v1" ‖ pairing id, 600,000)`, not the bare code the ruling named: the code has only 40 bits (32⁸), so a relay that saw one HMAC keyed with the code could try every code offline within the code's ten minutes; 600,000 iterations make that cost years per GPU, while the phone and the server pay it once per code (about half a second). Salting with the pairing id lets the server keep the derived key (sealed with the master key) instead of the code, which answers how a proof is checked when the code itself is stored only as a hash.
17. The proof path is two new operations beside `pairDevice` (`createPairingChallenge`, `pairDeviceWithProof`), not new optional fields in `PairIn`: the QR body a 1c app sends stays exactly valid, and the proof input has every field required. Both share the `pairing` throttle scope with `pairDevice`.
18. The server accepts a proof for any binding in `trust_bindings()` (its private CA's fingerprint and every public host name): part D can list an address with a public certificate beside a private CA; a link naming another host or CA still cannot produce a binding in the set.
19. Whoever learns the pairing id (another app that claims `brievie://`) can burn the code with five wrong proofs; that costs the user a new code and is accepted. A failed proof also spends its nonce. Failure counts are committed before the 400 is raised.
20. The own export in part A covers documents with an addressee linked to the member; every read goes through `export_context`, so part B changes one function (and the id query) to "what the member may read".
21. The export is sealed in 4 MiB AES-GCM chunks with index and last-chunk flag in the associated data: no plaintext on disk, bounded memory, and truncation is detected. The download does its checks, key and audit row eagerly and returns a plain iterator, because a generator would run after the request transaction and RLS context are gone.
22. The REVOKE also covers `pipeline_aiconsentevent`, which DECISIONS lists as not done. Its `actor` is `SET_NULL`; no code deletes a user today, so nothing breaks, and a later user deletion must keep the history some other way (accepted, written in DECISIONS).
23. `allow_member_export` is a column with a kind-dependent default, set by a data migration: D 6.3's "off in Zakelijk unless allowed" without a settings framework.
24. A `openapi_1c.json` fixture joins `openapi_1b.json`, so the contract test guards what a 1c app was built against (D 14).
25. Rule and label audit rows carry ids only: D 11 forbids label names, and a rule name can contain one.
26. **A member removed while their phone uploads or confirms** (D 15) is an accepted race, not a lock: `workspace_member` does not lock the membership, so a request that passed the check a moment before the removal commits. Its effect equals the same request finishing a moment earlier: the letter belongs to the workspace and stays there ("De post blijft hier"), a confirmation is a workspace change an admin can see in the letter's history; every later request of the member answers 401 or 404 (`test_removed_member_cannot_upload_or_confirm`). A `FOR KEY SHARE` lock on every write would hold a row lock for the length of an upload and is not worth that.
27. `AcceptInvitationIn.account` is a validated `str`, so part C adds `"sso"` without changing a released property's definition (`enum` is part of the wire shape the contract test compares).
28. `audit` filters are on `user_id`, because `actor_id` and `target_id` hold user ids for people who act and are acted on; person rows are reached through the actor (check report 1.12).
29. Two server admins are guarded by a row lock on the active server-admin `User` rows (`lock_active_server_admins`), always taken after the membership lock: the membership lock covers one workspace only (check report 1.10).
30. A second server admin is appointed with `set_server_admin` on the server (D 21); a web page for it belongs with part C's server settings. Workspace creation stays at `/setup`.
31. Check report items not adopted: none. Every item of `plan-1d-a-controle.md` is in a task above (1.1–1.13 in Tasks 4, 5, 9, 12, 13, 14, 17, 18, 19; 2.1–2.6 in Tasks 1, 4, 5, 6, 13, 14, 22, 23; the splits as Tasks 18/19 and 23/24; the missing work in Tasks 4, 5, 12, 19 and decision 26; the seams in Tasks 7, 12, 4, 18, 19; AI consent in Tasks 1, 12, 16, 22).

## Seams left for parts B, C, D

- **B, `apps/workspaces/access.py`:** `person_requires_email(person)` (True when the person has restricted documents: then an invitation for them needs an email), `on_member_added(workspace_id, user_id, *, person_id, created_by_id)` (recompute readers when a person gets an account; record the grant), `on_member_removed(workspace_id, user_id)` (delete reader rows, shares and department memberships as the system). All no-ops in A, each called from exactly one place.
- **B, removal and the export:** `remove_member` steps 3 and 4 get wrapped in `system_actor()`; `export_context` passes `reader_id` to the RLS context and `member_document_ids` reads the member's reader rows; `link_member` gets the `cannot_grant_self` rule. The audit codes `visibility_changed`, `department_member_*` and `emergency_access_*` already exist.
- **C, `apps/accounts/sessions.py`:** `start_session` gains the second-factor step and the required set-up (D 10: "where a session is created"); `session_policy("sso")` caps an SSO session at 12 hours; the session lifetime moves from environment settings to the server settings page (with a page for server admins beside `set_server_admin`); changing to a second factor calls `end_sessions(user, except_key=..., current=request)`.
- **C, accounts:** `clearSecondFactor` (DELETE `/users/{user_id}/second-factor`) uses `account_authority` and `notify(..., "second_factor_cleared")`, both ready; `acceptInvitation` accepts `account: "sso"` (a validated `str` already); `UserSession.Method.SSO` exists.
- **C, devices:** `token_expired` gains the rule for devices paired from an SSO session (D 9.4), reading `Device.paired_via`.
- **D:** `apps/common/links.py` (`public_base_url`, `home_network_only`), `devices.services.server_link` / `qr_payload` and `devices.proof.trust_bindings` gain the external addresses and the `addresses` list (D 19.3).

## What the iOS plan for part A must cover

- Register the `brievie` URL scheme and parse `brievie://server#v=1&url=…&url=…&ca_sha256=…&pairing=…` (ignore unknown keys; https only; a pin of 64 hex characters or none; exactly one `pairing`, a UUID); show the server's host and ask for the eight-character pairing code typed by hand.
- **Never send the typed code from a link.** After the user's tap: verify the server as for a QR code (CA download and pin, health), `createPairingChallenge`, compute the proof of Task 8 (the key from the code and the pairing id, the binding: the link's pin, or without one the lower-case host it connected to), `pairDeviceWithProof`. Test it with `fixtures/pairing_proof_v1.json` copied verbatim. The QR path keeps sending the code to `pairDevice`.
- Send `X-Brievie-App-Version: <CFBundleShortVersionString> (<CFBundleVersion>)` on every request and `app_version` in `pairDevice` and `pairDeviceWithProof`.
- Confirm (open point of D 20) that the 1c app treats `401 not_authenticated` on any request as "this device is revoked" and shows pairing; part A's expired token answers exactly that.
- Confirm what the app does with calendar events of tasks that disappear from the list (a member removed elsewhere, a workspace that disappears): D 20 asks; part B makes it common.
- A member removed from one of several workspaces: `listWorkspaces` no longer has it and its endpoints answer 404 `workspace_not_found`; the app drops it from the picker without signing out.
- Contract sync: new optional fields (`UserOut.is_server_admin`, `DeviceOut.app_version`, `PairingCodeOut.server_link`, `MemberOut.can_manage_account`) and new operations (`createPairingChallenge`, `pairDeviceWithProof` among them); nothing is required of the app beyond the items above. Account notices and the export stay web-only in part A.
- Keep the token in the Keychain as now; nothing in part A changes the bearer token's format.
