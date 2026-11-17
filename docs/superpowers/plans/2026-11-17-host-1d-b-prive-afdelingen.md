# Host 1d part B "Privé en afdelingen" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A letter can be private for the people it is meant for (by hand, by the Zorg label, by a rule), a business workspace has departments whose members read the department's post, an admin can open a person's private post only through logged emergency access, and the database, not the API, keeps everybody else out.

**Architecture:** A second transaction-local setting `brievie.actor` (user id, `system` or empty) joins `brievie.workspace_id`. A derived table `DocumentReader` holds who may read each restricted document; a RESTRICTIVE `FOR SELECT` policy on `documents_document` checks it, and every table with a foreign key to a document gets a RESTRICTIVE `FOR SELECT` policy that asks whether its document is visible. One module, `apps.documents.visibility`, is the only writer of readers, shares and visibility, writes them as the system, and runs in the same transaction as every input change; a nightly job verifies it. Departments are persons of `kind="department"` with `DepartmentMember` rows.

**Tech Stack:** As in 1c and part A (Python 3.13, Django 6.1, Django Ninja, PostgreSQL 18 with forced RLS, Procrastinate, pytest; React 19, TanStack Router/Query, React Aria Components, Lingui, Vitest, MSW). No new dependency.

**Spec:** `docs/superpowers/specs/2026-11-17-fase-1d-leden-rollen-sso-design.md` (cited as "D 8.3"); part B is the row in D 13. Binding: D 5.1, 5.2, 8 (all), 11 (codes below), 14, 15, 20. Also binding: `docs/superpowers/specs/2026-11-17-beheer-backup-export-updates-design.md` 4.3/6.3 (the storage sweep, the manifest counts and the later archive job read as a role or actor that sees private post). Part A plan: `docs/superpowers/plans/2026-11-17-host-1d-a-leden-apparaten.md` (built first; its names are assumed where it was not readable, see "Assumptions about part A"). House style: `docs/superpowers/plans/2026-11-16-host-fase-1c.md`.

## Global Constraints

From `CLAUDE.md` (verbatim where quoted):

- "Every table: UUID `id`, `created_at`, `updated_at`. Every workspace content table: `workspace_id` (subclass `WorkspaceModel`) and RLS enabled **and forced**." New tables end their first migration with `enable_rls(<table>)`.
- "`mypy --strict` and `ruff check` clean. No `Any`, no bare `except`." Datetimes timezone-aware.
- "API only under `/api/v1`, additive changes only. Every endpoint: typed schemas and an explicit camelCase `operation_id`." Error bodies `{"code": "<snake_case>", "message": "<Dutch sentence>"}` via `ApiError`.
- "Thin endpoints: validate, call one function in the app's `services.py`, return." (Visibility endpoints call `documents.services`, which calls `documents.visibility`.)
- "Every mutating service function is wrapped in `transaction.atomic`, so raising inside it rolls back its own writes. An error response (`ApiError`, Ninja `HttpError`, `Http404`) does not roll back the request transaction; only a 500 does."
- "No document content, tokens, passwords or BSNs in logs." The nightly check logs a number only; no person ids or names.
- "No new dependency without a line in `docs/DECISIONS.md`." (None is added.)
- "Only `apps.documents.organise` adds or removes a document's labels and addressees (it writes the history)." Visibility, shares and readers have their own single writer: `apps.documents.visibility`.
- "A label or addressee proposal is a `DocumentSuggestion`; nothing automatic repeats a dismissed one." "The rules engine (`apps.rules.engine`) stays pure: no Django import."
- "Background jobs: Procrastinate, in an app's `jobs.py`; a job takes ids only and enters `workspace_context` itself, in short transactions."
- "Text is BSN-masked (`apps.common.privacy.mask_bsns`) before it is stored, logged or sent." Applies to the emergency reason and history notes.
- "A field added to an existing API schema has a default (never newly required; the contract test compares with `backend/tests/fixtures/openapi_1b.json`)." "Nullable fields in API schemas are primitives only, and nullable inputs have a default; the contract test forbids `anyOf`."
- User-facing text Dutch B1 via `gettext` (backend) and Lingui `t` (web, then `pnpm extract`). After every API change: `make openapi`, `cd web && pnpm gen:api`, commit both generated files with the change.
- Tests first for logic. Conventional commits ending with `Co-Authored-By: Claude Code <noreply@anthropic.com>`. Branch `feat/1d-b-prive` from part A's branch; never push. Never commit an iCloud conflict copy (" 2", " 3" in a name).

From the design, not left to the builder (D 17):

- **Readers (D 8.1).** A restricted document is readable by: every user linked to a person it is on the name of; every member of a department it is on the name of; every user it was shared with (`DocumentShare`), which by default includes whoever scanned it and whoever made it private (also by confirming). Only current members count. Anybody who can read it can remove a share, their own included.
- **Who may make private:** a member who can read it. **Who may make public again:** a reader who is an addressee (linked to the person, or a member of the department); when no addressee has an account, any reader. Audit event and history line.
- **Nobody gives themselves access.** Linking a person to an account and a department membership are grants, made by an admin, never for the admin's own account when that person or department has private post (409 `cannot_grant_self`); a linked person is unlinked only by removing the member or by that member. Each grant is an audit event and a history line on every private letter it opens.
- **Automatic actions do not widen the readers.** Pipeline and rules (automatic or run by hand) never add an addressee to a private letter; they leave a suggestion that only its readers see. Labels can still be added.
- **Zorg is private by default:** workspace setting, on for Thuis, off for Zakelijk. The label with system key `zorg` arriving (by hand, by accepting a suggestion, by a rule) makes the document restricted; a proposal of it does not.
- **Rule action "privé maken":** a new action type in format version 1 (no version bump), only in rules with `mode="auto"`; a 1c client editing such a rule sends it back unchanged (contract test).
- **Tasks** of a restricted document are restricted with it.
- **Departments (D 8.2):** `Person.kind` `person` | `department`; `DepartmentMember(department, user)`; everything for persons works for departments; Thuis hides the kind.
- **RLS (D 8.3):** `brievie.actor` = user id in a request, `system` for code that must see everything, empty otherwise. `workspace_member` sets the user (session and device token). `workspace_context` takes the actor without a default and restores it on exit. `system_actor()` is used only in `documents.visibility`, removing a member, the named deletes (after the authorisation check), the repeated-upload lookup, and (later) the archive job; a test lists its callers. Helper functions `brievie_actor_id()` (null for `system` or empty) and `brievie_is_system()`. Document: the existing workspace policy unchanged plus a restrictive read-only visibility policy. Every table with a foreign key to a document: the same restrictive read policy as a correlated `EXISTS` on the document ("has no document, or" for a task). `DocumentReader`: workspace matches and the row is the actor's own, or the actor is `system`. An empty actor sees no restricted document. Order when making private: history line and other child rows, readers as the system (including the actor's own share), then the visibility. `WorkspaceModel.save` no longer reads "not visible" as "other workspace". "Is it in use" counts as the system; deletes of person, label and rule run as the system after the authorisation check. `visibility.recompute(document)` is the only writer of `addressee`, `department`, `share` rows, as the system, in the same transaction as every input change; nightly verification logs how many rows differed.
- **Emergency access (D 8.5):** workspace setting "Noodtoegang voor de beheerder", off for Thuis, on for Zakelijk, changed only by a server admin, an audit event and a notice to every member. With it on an admin opens the private post of one person or department with a reason of at least ten characters: `emergency` reader rows for one hour, written as the system, an audit event per use, a history line on each opened letter ("<beheerder> opende deze brief met noodtoegang: <reden>"). With it off there is no code path.
- **Removing a member (D 6.3):** the user's reader rows, shares and department memberships in the workspace go, written as the system; private post of a removed member stays locked.
- **Contract (D 8.4, 14):** `DocumentSummaryOut.visibility` (default `workspace`), `reader_names` (default empty); `setDocumentVisibility` (`visibility`, `shared_user_ids`); `ConfirmIn.visibility` default "leave as it is". A 1c app never receives a letter the user may not read.
- **Audit codes part B writes (D 11, the closed list):** `document_visibility_changed`, `document_share_added`, `document_share_removed`, `person_linked`, `person_unlinked`, `department_member_added`, `department_member_removed`, `emergency_access_enabled`, `emergency_access_disabled`, `emergency_access_used`, `workspace_setting_changed` (Zorg setting), `person_deleted`, `label_deleted`, `rule_deleted` (the last four reuse part A's codes when A defines them). A private document is named by its id only; values never hold a sender, summary, text or label name.

Final list of allowed system callers (Task 2's test grows to exactly this): `apps/pipeline/steps.py`, `apps/pipeline/jobs.py`, `apps/rules/jobs.py`, `apps/documents/jobs.py`, `apps/documents/visibility.py`, `apps/documents/intake.py`, `apps/people/services.py`, `apps/labels/services.py`, `apps/rules/services.py`, `apps/common/models.py`, and part A's member-removal module (assumed `apps/workspaces/members.py`).

## PostgreSQL facts this plan relies on (verified against the PG 18 docs, "Policies Applied by Command Type"; pinned by Task 3's tests)

- A RESTRICTIVE policy is ANDed with the permissive ones; `FOR SELECT` restricts reading only. `INSERT` (without `RETURNING`) checks only the INSERT `WITH CHECK` (the workspace policy). Django inserts rows with a UUID primary key **without** `RETURNING` unless a field has `db_default`; therefore **no field added in part B uses `db_default`** (a `RETURNING` would apply the SELECT policy to the new row).
- `UPDATE … WHERE id = …` and `DELETE … WHERE …` need read access: rows the actor cannot read are not touched (0 rows, no error). For `UPDATE` the SELECT policies are also checked against the **new** row; a new row the actor cannot read raises SQLSTATE 42501 ("new row violates row-level security policy"), which Django raises as `ProgrammingError` (a `DatabaseError`).
- Django `save(update_fields=…)` on an unreadable row raises `DatabaseError("Save with update_fields did not affect any rows.")`; `save()` without `update_fields` falls back to `INSERT` and fails with `IntegrityError` on the primary key (which also reveals the row exists). Rule: any document save that can follow a visibility change passes `update_fields`. `refresh_from_db()` on an unreadable row raises `DoesNotExist`; `select_for_update()` does not return it (callers answer 404).
- Unique constraints and foreign-key checks ignore RLS. Django's delete collector does not: it collects and `SET_NULL`s only readable children, and the deferred FK check then fails at commit (`IntegrityError`). Fix chosen: the delete runs inside `system_actor()` after the authorisation check (D 8.3), not `DB_CASCADE` (that needs FK rewrites on six tables and still leaves Django's `SET_NULL` emulation on `DocumentEvent`).
- Referential cascades run as the table owner with `SECURITY_NOFORCE_RLS`, so they bypass RLS; not used here.
- `now()` is the transaction start: an emergency row that expires mid-request still counts until the next request.
- No circle: document → reader (reader's policy reads no other table); child → document → reader. `documents_documentreader` itself does **not** get the child policy (that would be the circle).
- Helper calls are written `(SELECT public.brievie_actor_id())` so PostgreSQL evaluates them once per statement (an InitPlan), not per row.

## Review Focus

1. A user removes their own last access through an organising call (takes themselves off as addressee of a private letter): the request must not end in a half-written document or a 500. → Task 5 (`test_removing_own_last_access_is_409_would_lose_access`).
2. An admin with emergency access tries to keep access (adds their own person as addressee, adds a share, makes the letter public): refused, read-only. → Task 15 (`test_emergency_reader_cannot_organise_share_or_publish`).
3. The app retries an upload (same `client_id`) of a letter that has meanwhile become private for others: answered 409 `document_conflict`, never 500, never the document. → Task 6 (`test_repeated_upload_of_a_letter_now_private_for_others_is_409`).
4. A rule with "privé maken" is switched to "voorstellen" in the web or by an API client: 422 with a plain sentence; the stored rule is unchanged. → Task 14 (`test_make_private_in_a_propose_rule_is_422`, web `test("Privé maken disappears when the rule only proposes")`).
5. A member is removed while their request is making a letter private: no share for a non-member, no 42501, the letter keeps a valid reader set. → Task 13 (`test_make_private_by_an_actor_without_membership_is_404_and_writes_nothing`).

---

## File Structure

```
backend/apps/
  workspaces/
    rls.py                    # + ACTOR_SETTING, actor_functions(), restrict_to_visible_documents(), restrict_reader_rows()
    context.py                # + Actor, SYSTEM, NOBODY, workspace_context(actor=), system_actor(), current_actor()
    deps.py                   # workspace_member sets the user as actor
    models.py                 # Workspace.care_is_private, Workspace.emergency_access
    services.py  api.py  schemas.py      # defaults per kind; updatePrivacySettings, setEmergencyAccess
    migrations/0003_actor_functions.py  0004_privacy_settings.py
  common/models.py            # WorkspaceModel.save: hidden != other workspace
  documents/
    models.py                 # Document.visibility, DocumentShare, DocumentReader, DocumentEvent.note + kinds
    visibility.py             # NEW: the only writer of visibility, shares, readers; reader names; grants; emergency
    jobs.py                   # NEW: nightly verify_readers
    organise.py               # hooks: addressee changes -> recompute; zorg label -> make_private
    services.py  review.py  intake.py  api.py  schemas.py
    migrations/0008_visibility.py
  people/
    models.py                 # Person.kind, DepartmentMember
    services.py  api.py  schemas.py      # departments, grants, deletes as system
    migrations/0003_departments.py
  labels/services.py          # delete as system
  rules/
    engine.py  definition.py  facts.py  executor.py  services.py
    migrations/0004_rulerun_visibility.py
  tasks/
    services.py
    migrations/0002_task_visibility.py
  <part A>                    # members.py (removal seam), invitations, export job, audit codes, notices
backend/tests/
  privacy_support.py          # private_letter(), as_user() helpers
  test_workspaces_actor.py  test_actor_callers.py  test_privacy_rls.py  test_rls_coverage.py
  test_privacy_performance.py  test_documents_visibility.py  test_privacy_deletes.py
  test_rules_private.py  test_people_departments.py  test_people_grants.py  test_privacy_members.py
  test_workspaces_privacy_api.py  test_documents_visibility_api.py  test_documents_confirm_private.py
  test_privacy_emergency.py  test_privacy_leaks.py
web/src/
  components/private-lock.tsx  share-dialog.tsx  emergency-dialog.tsx  privacy-settings.tsx
  components/icons.tsx (+lock)  document-table.tsx  document-history.tsx  rule-sentence.ts
  routes/document.tsx  post.tsx  inbox.tsx  persons.tsx  rule-editor.tsx  <A's settings route>
docs/ DECISIONS.md  GLOSSARY.md  PRIVACY.md  api/openapi.json
```

---

## Slice B1: the actor and the policies (nothing private yet)

### Task 1: The actor helper functions in PostgreSQL

**Files:**
- Modify: `backend/apps/workspaces/rls.py`
- Create: `backend/apps/workspaces/migrations/0003_actor_functions.py`
- Test: `backend/tests/test_workspaces_actor.py`

**Interfaces:**
- Produces: `rls.ACTOR_SETTING = "brievie.actor"`; `rls.actor_functions() -> migrations.RunSQL` creating (reverse: `DROP FUNCTION`) exactly:

```sql
CREATE FUNCTION public.brievie_actor_id() RETURNS uuid
LANGUAGE sql STABLE PARALLEL SAFE SET search_path = ''
AS $$
  SELECT CASE
    WHEN pg_catalog.current_setting('brievie.actor', true)
         ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    THEN pg_catalog.current_setting('brievie.actor', true)::uuid
  END
$$;

CREATE FUNCTION public.brievie_is_system() RETURNS boolean
LANGUAGE sql STABLE PARALLEL SAFE SET search_path = ''
AS $$
  SELECT COALESCE(pg_catalog.current_setting('brievie.actor', true), '') = 'system'
$$;
```

  `CASE` makes the cast happen only for a well-formed id (an `OR` has no evaluation order). Both are `SECURITY INVOKER`, owned by the migrating role (`brievie_owner`). `EXECUTE` stays granted to `PUBLIC` (PostgreSQL's default), so `brievie_app` may call them.

- [ ] **Step 1: Failing tests** (raw `set_config('brievie.actor', …, true)` inside `transaction.atomic()`):

```python
def test_actor_id_is_the_uuid_of_a_user_actor(): ...          # == UUID(...)
@pytest.mark.parametrize("value", ["system", "", "SYSTEM", "not-a-uuid", "0000000-0000-0000-0000-000000000000"])
def test_actor_id_is_null_for_anything_else(value): ...       # is None, and no error
def test_actor_id_is_null_without_the_setting(): ...
def test_is_system_only_for_the_exact_word(): ...             # "system" True; "System", "", uuid False
def test_helpers_pin_search_path_and_are_stable(): ...       # pg_proc.proconfig == ['search_path=""'], provolatile == 's'
def test_helpers_are_owned_by_the_schema_owner(): ...        # pg_get_userbyid(proowner) == current_user
def test_app_role_may_execute_the_helpers(): ...             # has_function_privilege('brievie_app', 'public.brievie_actor_id()', 'EXECUTE')
```

- [ ] **Step 2:** `make test` → the new tests FAIL (function does not exist).
- [ ] **Step 3:** Implement `actor_functions()` and migration `0003` (depends on `workspaces.0002` and part A's last `workspaces` migration).
- [ ] **Step 4:** `make test && make lint && make typecheck` → PASS.
- [ ] **Step 5:** Commit `feat(workspaces): actor helper functions for row-level security`.

### Task 2: Every workspace context names its actor

**Files:**
- Modify: `backend/apps/workspaces/context.py`, `deps.py`, `services.py`; `backend/apps/pipeline/steps.py`, `pipeline/jobs.py`, `pipeline/management/commands/configure_ai.py`, `rules/jobs.py`; part A's call sites (see Assumptions); every test that calls `workspace_context` (294 calls in 43 files at the time of writing)
- Create: `backend/tests/test_actor_callers.py`
- Modify: `docs/DECISIONS.md` (one line: the actor setting, `NOBODY`, the callers test)
- Test: `backend/tests/test_workspaces_actor.py`, `test_workspaces_rls.py`

**Interfaces:**
- Produces in `apps.workspaces.context`:
  - `SYSTEM: Final = "system"`, `NOBODY: Final = ""`, `Actor = UUID | Literal["system", ""]`.
  - `workspace_context(workspace_id: UUID, *, actor: Actor) -> Iterator[None]`: sets both settings, restores both on a normal exit (same savepoint rule as today). Any other actor value raises `ValueError`.
  - `enter_workspace_for_transaction(workspace_id: UUID, *, actor: UUID) -> None` (a request is never the system).
  - `system_actor() -> Iterator[None]` (context manager): in a savepoint, sets `brievie.actor` to `system` and restores the previous actor; `RuntimeError` when no workspace is set.
  - `current_actor() -> UUID | Literal["system"] | None` (None for empty).
- `workspace_member` calls `enter_workspace_for_transaction(workspace_id, actor=user.id)` with `user = current_user(request)`; that covers the session and the device-token path (`DeviceTokenAuth` sets `request.user`).
- Call sites and their actor:

| Call site | Actor |
|---|---|
| `pipeline/steps.py` `run_pipeline` (two contexts), `mark_failed` | `SYSTEM` |
| `pipeline/jobs.py` `requeue_abandoned_documents` | `SYSTEM` |
| `rules/jobs.py` `apply_rule_batch` (manual run) | `SYSTEM` |
| `pipeline/management/commands/configure_ai.py` (two) | `NOBODY` (touches AI settings only) |
| `workspaces/services.py` `create_workspace` | `admin.id` |
| `workspaces/deps.py` `workspace_member` | the request's user |
| part A: accept invitation, own-export job, removal | the accepting user / the exporting user / the request's user |
| tests | `SYSTEM` (mechanical rewrite; behaviour unchanged, nothing is private yet) |

- `tests/test_actor_callers.py`: scans `backend/apps/**/*.py` for `system_actor(` and `actor=SYSTEM`; asserts the set of files equals `ALLOWED_SYSTEM_CALLERS` (now: `apps/pipeline/steps.py`, `apps/pipeline/jobs.py`, `apps/rules/jobs.py`; later tasks add their file in the same commit, ending at the list in Global Constraints). Also asserts that `set_config(` with `brievie.actor` appears only in `context.py`.

- [ ] **Step 1: Failing tests:**

```python
def test_workspace_context_requires_an_actor(): ...            # workspace_context(ws.id) -> TypeError (type: ignore[call-arg])
def test_workspace_context_refuses_an_unknown_actor(): ...     # actor=str(uuid) -> ValueError
def test_context_sets_and_restores_the_actor(): ...            # nested user inside SYSTEM; after: "" again
def test_actor_restored_after_an_exception(): ...
def test_system_actor_sets_and_restores(): ...                 # current_actor(): user -> "system" -> user
def test_system_actor_needs_a_workspace(): ...                 # RuntimeError
def test_workspace_member_runs_as_the_session_user(rf): ...    # current_actor() == user.id
def test_workspace_member_runs_as_the_device_user(rf): ...     # request via DeviceTokenAuth.authenticate
def test_system_callers_are_the_known_list(): ...
def test_actor_setting_is_written_only_by_context(): ...
```

- [ ] **Step 2:** `make test` → FAIL.
- [ ] **Step 3:** Implement `context.py`, `deps.py`, the call sites in the table. Rewrite the tests with a one-off script (not committed): regex `workspace_context\(([^()]*(?:\([^()]*\))?)\)` → `workspace_context(\1, actor=SYSTEM)` plus the import; `make typecheck` finds the rest.
- [ ] **Step 4:** `make test && make lint && make typecheck` → PASS.
- [ ] **Step 5:** Commit `feat(workspaces): every workspace context names its actor`.

### Task 3: Visibility, shares, readers and the policies

All documents stay `workspace`; the policies change nothing a user sees until something becomes `restricted`.

**Files:**
- Modify: `backend/apps/workspaces/rls.py`, `backend/apps/common/models.py`, `backend/apps/documents/models.py`
- Create: `backend/apps/documents/migrations/0008_visibility.py`, `backend/apps/rules/migrations/0004_rulerun_visibility.py`, `backend/apps/tasks/migrations/0002_task_visibility.py`, `backend/tests/privacy_support.py`, `backend/tests/test_privacy_rls.py`
- Modify: `backend/tests/test_rls_coverage.py`, `test_actor_callers.py` (+ `apps/common/models.py`), `docs/DECISIONS.md` (the two-layer policy, why reader rows have no child policy, no `db_default`)

**Interfaces:**
- `Document.visibility = CharField(max_length=20, choices=Visibility.choices, default="workspace")`, `Document.Visibility`: `WORKSPACE = "workspace"`, `RESTRICTED = "restricted"`. No `db_default`.
- `DocumentShare(WorkspaceModel)`: `document` FK `Document` CASCADE `related_name="shares"`; `user` FK `AUTH_USER_MODEL` CASCADE `related_name="+"`; `created_by` FK user null `SET_NULL` `related_name="+"`. Unique `documents_documentshare_unique` (`document`, `user`). `same_workspace_fields = ("document",)`.
- `DocumentReader(WorkspaceModel)`: `document` FK CASCADE `related_name="readers"`; `user` FK CASCADE `related_name="+"`; `reason = CharField(max_length=20, choices=Reason.choices)` with `ADDRESSEE="addressee"`, `DEPARTMENT="department"`, `SHARE="share"`, `EMERGENCY="emergency"`; `expires_at = DateTimeField(null=True, blank=True)`. Unique `documents_documentreader_unique` (`document`, `user`, `reason`); index `documents_reader_user_document` (`user`, `document`). `same_workspace_fields = ("document",)`.
- `DocumentEvent.note = TextField(blank=True, default="")` (BSN-masked); new `Kind` values `MADE_PRIVATE="made_private"`, `MADE_PUBLIC="made_public"`, `SHARE_ADDED="share_added"`, `SHARE_REMOVED="share_removed"`, `ACCESS_GRANTED="access_granted"`, `EMERGENCY_OPENED="emergency_opened"`.
- In `rls.py`: `VISIBILITY_POLICY = "document_visibility"`, `READER_POLICY = "reader_scope"`;
  - `document_visibility_policy() -> RunSQL`:

```sql
CREATE POLICY document_visibility ON "documents_document" AS RESTRICTIVE FOR SELECT
USING (
  visibility = 'workspace'
  OR (SELECT public.brievie_is_system())
  OR EXISTS (
    SELECT 1 FROM "documents_documentreader" r
    WHERE r.document_id = "documents_document".id
      AND r.user_id = (SELECT public.brievie_actor_id())
      AND (r.expires_at IS NULL OR r.expires_at > pg_catalog.now())
  )
);
```

  - `restrict_to_visible_documents(table: str, *, nullable: bool = False) -> RunSQL` (name checked as in `enable_rls`):

```sql
CREATE POLICY document_visibility ON "<table>" AS RESTRICTIVE FOR SELECT
USING (<"document_id IS NULL OR " when nullable>EXISTS (
  SELECT 1 FROM "documents_document" d WHERE d.id = "<table>".document_id));
```

  - `restrict_reader_rows() -> RunSQL`:

```sql
CREATE POLICY reader_scope ON "documents_documentreader" AS RESTRICTIVE FOR ALL
USING ((SELECT public.brievie_is_system()) OR user_id = (SELECT public.brievie_actor_id()))
WITH CHECK ((SELECT public.brievie_is_system()));
```

  Each with its `DROP POLICY` reverse.
- Migration `documents/0008`: add `visibility`, `note`; create `DocumentShare`, `DocumentReader`; `enable_rls` on both; `restrict_reader_rows()`; `document_visibility_policy()`; `restrict_to_visible_documents()` on `documents_documentpage`, `documents_extraction`, `documents_documentaddressee`, `documents_documentlabel`, `documents_documentevent`, `documents_documentsuggestion`, `documents_documentshare`. `rules/0004`: `rules_rulerun`. `tasks/0002`: `tasks_task` with `nullable=True`.
- `WorkspaceModel.save`: when the related row raises `ObjectDoesNotExist`, read its `workspace_id` again inside `system_actor()` (the workspace policy still binds, so another workspace's row stays invisible and raises `ValueError` as before).
- `tests/privacy_support.py`: `restrict_raw(document, readers: Sequence[User]) -> None` (as the system: reader rows with reason `share`, then `update(visibility="restricted")`); `as_actor(workspace, actor)` = `workspace_context(workspace.id, actor=actor)`. Task 5 adds `private_letter(...)` on top of the real service.

- [ ] **Step 1: Failing tests** in `test_privacy_rls.py` (one workspace, users Anne and Bob, a letter restricted to Bob with one row in every child table):

```python
def test_restricted_letter_is_hidden_from_a_non_reader(): ...         # as Anne: Document count excludes it
def test_restricted_letter_is_visible_to_its_reader_and_to_system(): ...
def test_empty_actor_sees_public_letters_but_no_restricted_one(): ...  # actor=NOBODY: the "job that forgets"
def test_expired_reader_row_hides_the_letter(): ...
@pytest.mark.parametrize("model", [DocumentPage, Extraction, DocumentAddressee, DocumentLabel,
                                   DocumentEvent, DocumentSuggestion, DocumentShare, RuleRun, Task])
def test_child_rows_follow_their_document(model): ...                  # Anne 0, Bob 1, SYSTEM 1
def test_task_without_document_stays_visible(): ...
def test_user_sees_only_own_reader_rows(): ...                        # Bob 1 row; Anne 0; SYSTEM all
def test_user_cannot_write_reader_rows(): ...                         # as Bob: create -> DatabaseError
def test_update_of_a_hidden_letter_touches_nothing(): ...             # filter(id=).update(sender="x") == 0
def test_save_with_update_fields_of_a_hidden_letter_raises(): ...     # DatabaseError "did not affect any rows"
def test_refresh_of_a_hidden_letter_raises_does_not_exist(): ...
def test_update_that_hides_the_row_from_the_actor_is_42501(): ...     # as Anne: update(visibility="restricted") on a public letter -> ProgrammingError, __cause__.sqlstate == "42501"
def test_making_private_after_writing_own_reader_row_succeeds(): ...  # SYSTEM writes Anne's row first; then the update as Anne == 1
def test_child_insert_on_a_hidden_letter_passes_the_workspace_check(): ...  # DocumentEvent(document_id=hidden).save() as Anne: no ValueError
def test_child_pointing_into_another_workspace_still_raises(): ...
def test_delete_of_hidden_children_deletes_nothing(): ...
def test_fk_check_ignores_rls_so_a_user_delete_fails_at_commit(): ... # as Anne, label.delete() with a hidden DocumentLabel, SET CONSTRAINTS ALL IMMEDIATE -> IntegrityError
def test_no_policy_recursion(): ...                                   # SELECT count(*) on every table above as Anne: no error
```

  In `test_rls_coverage.py` (replace the "exactly one policy" assertion):

```python
def test_every_workspace_table_has_exactly_one_permissive_policy(): ...   # workspace_isolation, cmd '*'
def test_other_policies_are_known_restrictive_ones(): ...                 # name in {document_visibility (cmd 'r'), reader_scope (cmd '*')}, polpermissive False
def test_every_table_with_a_document_foreign_key_has_the_visibility_policy(): ...
    # tables from pg_constraint (contype='f', confrelid='documents_document'::regclass) minus {'documents_documentreader'}
    # == tables from Django meta (FK to Document) minus the same; each has document_visibility, restrictive, cmd 'r',
    # qual mentions documents_document
def test_document_policy_reads_the_reader_table_and_the_helpers(): ...
def test_reader_table_has_reader_scope_and_no_child_policy(): ...
```

- [ ] **Step 2:** `make test` → FAIL.
- [ ] **Step 3:** Implement models, `rls.py` operations, migrations, `WorkspaceModel.save`.
- [ ] **Step 4:** `make test && make lint && make typecheck && make openapi` (no API change expected: `git diff docs/api` empty) → PASS.
- [ ] **Step 5:** Commit `feat(documents): visibility, readers and the second row-level-security layer`.

### Task 4: Measure the check at 100,000 documents

D 18: measured before the features are built.

**Files:**
- Create: `backend/tests/test_privacy_performance.py`
- Modify: `backend/pyproject.toml` (`markers = ["slow: runs only with make perf"]`, addopts `-m "not slow"`), `Makefile` (`perf:` → `$(BACKEND) uv run pytest -m slow`), `docs/DECISIONS.md` (the measured numbers and the machine)

**Interfaces:**
- Data, inserted as `SYSTEM` with `INSERT … SELECT generate_series` (all `NOT NULL` columns explicit, `visibility` included): one workspace, 100,000 documents (10 % restricted), 50 persons, 100,000 addressee links, 100,000 label links, 20,000 tasks, 300,000 events; the measuring user is a reader (`share`) of 1,000 restricted letters; `ANALYZE` afterwards.
- Measured as that user, median of 5 runs after one warm-up: `services.list_documents(ws)` (200 rows with prefetches), `services.inbox_counts(ws)`, `people.services.list_persons(ws)` (the `document_count` annotation walks every addressee link), `tasks.services.list_tasks(ws, status="all")`, `services.list_documents(ws, person_id=…)`.
- **Pass:** each median ≤ 250 ms on the development machine, and `EXPLAIN (FORMAT JSON)` of the inbox count has no `Seq Scan` on `documents_documentreader`.
- **Fail → stop:** report the numbers to the owner; do not continue to Task 5. The fallback (D 18) is the reader check per child table: a copied `document_visibility` column on each child table, kept by `visibility` in the same transaction, and the child policy `document_visibility = 'workspace' OR (SELECT public.brievie_is_system()) OR EXISTS (SELECT 1 FROM documents_documentreader r WHERE r.document_id = <table>.document_id AND r.user_id = (SELECT public.brievie_actor_id()) AND (r.expires_at IS NULL OR r.expires_at > now()))`, which drops the join to the document. That becomes its own task with the owner's yes.

- [ ] **Step 1:** Write `test_visibility_check_at_100k` with the thresholds above (it fails only if the policies are slow).
- [ ] **Step 2:** `make perf` → record the five medians.
- [ ] **Step 3:** If it passes, add the DECISIONS line with the numbers; if not, stop (see above).
- [ ] **Step 4:** `make test` (the slow test is skipped there) → PASS.
- [ ] **Step 5:** Commit `test(documents): measure the visibility check at 100,000 documents`.

---

## Slice B2: one writer, and every write the user cannot see

### Task 5: `documents.visibility`: readers, private, public, the nightly check

**Files:**
- Create: `backend/apps/documents/visibility.py`, `backend/apps/documents/jobs.py`, `backend/tests/test_documents_visibility.py`
- Modify: `backend/apps/documents/organise.py` (addressee hooks), `backend/tests/privacy_support.py` (`private_letter`), `test_actor_callers.py` (+ `visibility.py`, `jobs.py`)

**Interfaces** (`apps.documents.visibility`; every write inside `system_actor()`; every public function in `transaction.atomic`):
- `recompute(document: Document) -> int`: makes the non-emergency reader rows exactly the desired set and returns how many rows were inserted plus deleted. Public document: delete every reader row. Restricted: `{(u, "addressee")}` for persons on the document with `kind="person"` and a linked user; `{(u, "department")}` for members of departments on the document; `{(u, "share")}` for its shares; each `u` only when `Membership(workspace, u)` exists. Afterwards, when `current_actor()` is a user who could read the document before the call and cannot now, raise `ApiError(409, "would_lose_access", "Dan kun je deze brief zelf niet meer lezen.")` (the caller's `atomic` rolls back). `setDocumentVisibility` passes `allow_losing_access=True` (signature `recompute(document, *, allow_losing_access: bool = False)`).
- `recompute_where(*, workspace_id: UUID, person_id: UUID | None = None, user_id: UUID | None = None) -> int`: `recompute` for every restricted document of that person (addressee) or user (reader, share, linked person, department member).
- `make_private(document: Document, *, actor: User | None, origin: str, rule: Rule | None = None, share_user_ids: Collection[UUID] = ()) -> bool`: False when already restricted. Order (D 8.3): (1) when `actor` is a user, lock `Membership(workspace, actor)` with `select_for_share()`; missing → `ApiError(404, "workspace_not_found", …)`; (2) history line `made_private` (actor, origin, rule); (3) as system: shares for `scanned_by` (when a member), the request's `current_actor()` user (when a user) and `share_user_ids`; reader rows computed as restricted; (4) **as the actor**: `Document.objects.filter(id=…).update(visibility="restricted")` (the database proves the actor can still read it); set the attribute; (5) audit `document_visibility_changed` {visibility: restricted}.
- `make_public(document: Document, *, actor: User) -> bool`: `can_make_public` or `ApiError(403, "cannot_make_public", "Alleen iemand op wiens naam deze brief staat, kan hem weer voor iedereen zichtbaar maken.")`; history `made_public`; update to `workspace` as the actor; as system delete shares and readers; audit.
- `set_shares(document: Document, *, user_ids: Collection[UUID], actor: User) -> None`: restricted only; every id a current member (`ApiError(404, "member_not_found", …)`); adds/removes `DocumentShare` rows (history `share_added`/`share_removed` with `person_name` = the user's name; audit per change), then `recompute(document, allow_losing_access=True)`.
- `can_make_public(document: Document, user: User) -> bool`: the user has a non-emergency reader row (as system: rows of the document) with reason `addressee` or `department`, or the document has no such rows at all and the user has a non-emergency reader row.
- `can_read(document_id: UUID) -> bool` (as the current actor, no system).
- `reader_names(documents: Sequence[Document]) -> dict[UUID, list[str]]`: first filters the ids as the current actor to readable restricted ones, then reads `DocumentReader` (non-emergency, not expired) as system; names of distinct users sorted case-insensitively.
- `shared_user_ids(document: Document) -> list[UUID]`.
- `has_private_post(person: Person) -> bool` (as system).
- `forget_member(workspace_id: UUID, user_id: UUID) -> None` (as system: reader rows, shares, department memberships of the user).
- `record_grant(person: Person, *, grantee: User, actor: User | None) -> int`: as system, an `access_granted` history line (`person`, `person_name`, `note` = grantee's name, `actor`) on every restricted document of the person; returns the count.
- `verify_readers(workspace_id: UUID) -> int`: as system, deletes expired emergency rows and returns the sum of `recompute` over restricted documents.
- `organise.add_addressee`, `organise.set_addressees` (once at the end), `organise.undo_event` (addressee branch) call `visibility.recompute(document)`.
- `apps.documents.jobs.verify_readers_job` (`@app.periodic(cron="40 3 * * *")`, queue `pipeline`): per workspace `workspace_context(ws, actor=SYSTEM)`, logs `"Reader check: %d rows differed"` (warning when > 0, info otherwise).
- `privacy_support.private_letter(workspace, *, scanned_by: User, addressees: Sequence[Person] = (), shared_with: Sequence[User] = (), labels=(), with_task: bool = True) -> Document`: a confirmed letter with a page row, an extraction, a suggestion, an event and a task, then `make_private(actor=None, origin="user")` as system.

- [ ] **Step 1: Failing tests** (Thuis workspace: Anne admin, Bob member, Carla member; persons linked):

```python
def test_make_private_gives_scanner_and_actor_a_share(): ...            # readers {Bob(share), Anne(share)}; history made_private
def test_make_private_writes_history_before_the_flip(): ...              # event visible to Anne afterwards; flip as Anne passed
def test_addressee_with_account_becomes_reader(): ...
def test_readers_after_addressee_added_and_removed(): ...
def test_readers_after_share_removed(): ...
def test_readers_after_person_linked_to_another_account(): ...           # recompute_where(person_id=)
def test_readers_after_visibility_switched_twice(): ...                  # private, public, private: only scanner+actor, old shares gone
def test_only_current_members_count(): ...                               # Membership deleted -> recompute drops the row
def test_make_public_by_addressee(): ...
def test_make_public_by_non_addressee_when_addressee_has_account_is_403(): ...
def test_any_reader_may_make_public_when_no_addressee_has_account(): ...
def test_removing_own_last_access_is_409_would_lose_access(): ...        # set_addressees without self, no share -> 409, nothing changed
def test_set_shares_may_remove_own_share(): ...                          # can_read() False afterwards
def test_reader_names_only_for_readable_letters(): ...                   # Anne asks for Bob-only letter id: {}
def test_verify_readers_counts_and_repairs_a_missing_row(): ...          # delete a row as system, job returns 1, row back
def test_verify_readers_removes_expired_emergency_rows(): ...
def test_verify_job_logs_a_number_only(caplog): ...                      # message has digits, no uuid, no name
```

- [ ] **Step 2:** `make test` → FAIL.
- [ ] **Step 3:** Implement. Audit calls use part A's `record(...)` (see Assumptions).
- [ ] **Step 4:** `make test && make lint && make typecheck` → PASS.
- [ ] **Step 5:** Commit `feat(documents): visibility service, the only writer of readers`.

### Task 6: Writes the user cannot see: deletes, "in use", repeated upload

**Files:**
- Modify: `backend/apps/people/services.py` (`_in_use`, `delete_person`), `backend/apps/labels/services.py` (`delete_label`), `backend/apps/rules/services.py` (`delete_rule`), `backend/apps/documents/intake.py`, `test_actor_callers.py` (+ the four files)
- Test: `backend/tests/test_privacy_deletes.py`; one case added to `test_people_api.py`, `test_labels_api.py`, `test_rules_api.py`, `test_documents_upload_api.py`

**Interfaces:**
- `delete_person`: rules lock and `_get_for_update` as the user (the authorisation), then `with system_actor():` `_in_use` (linked user, any addressee link, or a department with members) and `person.delete()`. Message unchanged (409 `person_in_use`), so it never says what the use is.
- `delete_label`: the rule checks as the user; `label.delete()` and the preset rule deletes inside `system_actor()`.
- `delete_rule`: `_get_for_update` as the user; `.delete()` inside `system_actor()`.
- Audit `person_deleted`, `label_deleted`, `rule_deleted` when part A did not wire them.
- `intake._find_existing` and the `IntegrityError` winner lookup and `_same_upload`'s page hashes run inside `system_actor()`; then, as the user, `visibility.can_read(existing.id)`; not readable → the same 409 `document_conflict` sentence as a different upload.

- [ ] **Step 1: Failing tests** (each with a letter private for Bob that Anne cannot read):

```python
def test_delete_label_used_on_someone_elses_private_letter_commits(): ...   # SET CONSTRAINTS ALL IMMEDIATE passes; the hidden DocumentLabel is gone; its event keeps label_name
def test_delete_rule_that_ran_on_a_private_letter_commits(): ...           # RuleRun gone, event.rule NULL, rule_name kept
def test_delete_person_on_a_private_letter_is_409_in_use(): ...
def test_delete_unused_person_still_works(): ...
def test_repeated_upload_of_own_letter_still_returns_it(): ...
def test_repeated_upload_of_a_letter_now_private_for_others_is_409(): ...  # body has no sender, no id
```

- [ ] **Step 2:** `make test` → FAIL (IntegrityError at commit, 500, or 200 with the letter).
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** `make test && make lint && make typecheck` → PASS.
- [ ] **Step 5:** Commit `fix: deletes and repeated uploads see private letters as the system`.

### Task 7: Automatic actions never widen the readers

**Files:**
- Modify: `backend/apps/rules/executor.py`
- Test: `backend/tests/test_rules_private.py`

**Interfaces:**
- `executor.execute`: an `auto` `add_addressee` on a document whose in-memory `visibility` is `restricted` (it is updated by `make_private` during the same run) is recorded through `_propose` instead (origin `rule`, outcome `proposed`). `add_label` is unchanged. The pipeline's name matching and AI picks only propose already; no change there.

- [ ] **Step 1: Failing tests:**

```python
def test_auto_addressee_rule_on_private_letter_leaves_a_suggestion(): ...   # trigger processed: no DocumentAddressee, one open suggestion, readers unchanged
def test_manual_run_does_not_add_addressee_to_private_letter(): ...          # apply_rule_batch as SYSTEM
def test_label_rule_still_labels_a_private_letter(): ...
def test_that_suggestion_is_visible_only_to_readers(): ...                   # Anne 0 rows, Bob 1
def test_reader_accepting_it_makes_the_person_a_reader(): ...                # accept_suggestion as Bob -> recompute
```

- [ ] **Step 2:** `make test` → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** `make test && make lint && make typecheck` → PASS.
- [ ] **Step 5:** Commit `feat(rules): automatic actions leave a suggestion on a private letter`.

### Task 8: Departments

**Files:**
- Modify: `backend/apps/people/models.py`, `services.py`, `api.py`, `schemas.py`, `backend/apps/documents/visibility.py` (department readers), `docs/GLOSSARY.md` (afdeling = department, afdelingslid = department member)
- Create: `backend/apps/people/migrations/0003_departments.py`, `backend/tests/test_people_departments.py`

**Interfaces:**
- `Person.kind = CharField(max_length=20, choices=Kind.choices, default="person")` (`PERSON`, `DEPARTMENT`); no `db_default`. `Person.save` raises `ValueError` for a department with a `user`.
- `DepartmentMember(WorkspaceModel)`: `department` FK `Person` CASCADE `related_name="department_members"`; `user` FK user CASCADE `related_name="+"`; unique `people_departmentmember_unique` (`department`, `user`); `same_workspace_fields = ("department",)`; `save` raises `ValueError` when the department is not `kind="department"` or the user is not a member. Migration ends with `enable_rls("people_departmentmember")`.
- `people.services.add_department_member(*, workspace, person_id, user_id, actor: User) -> Person` and `remove_department_member(*, workspace, person_id, user_id, actor: User) -> Person`: 404 `person_not_found`, 404 `member_not_found`, 409 `not_a_department` ("Dit is geen afdeling."); add: `user_id == actor.id` and `visibility.has_private_post(person)` → 409 `cannot_grant_self` ("Je kunt jezelf geen toegang geven tot privé-post. Gebruik een uitnodiging of noodtoegang."); then `recompute_where(person_id=)`, `record_grant` (add only), audit `department_member_added` / `department_member_removed`. Adding an existing member answers 200 unchanged.
- `link_member` on a department: 409 `is_a_department` ("Een afdeling koppel je niet aan één account. Voeg leden toe."). `ensure_person_for_member` and `list_members` only look at `kind="person"`. `_in_use` counts department members.
- Schemas (additive): `PersonOut.kind: str = "person"`, `PersonOut.member_user_ids: list[UUID] = Field(default_factory=list)`; `PersonIn.kind: str = "person"` read on create only (`updatePerson` ignores it, so a 1c client editing a department keeps it a department); any other value → 422.
- Endpoints (admin): `addDepartmentMember` `POST /workspaces/{workspace_id}/persons/{person_id}/members` body `DepartmentMemberIn {user_id: UUID}` → 200 `PersonOut`; `removeDepartmentMember` `DELETE /workspaces/{workspace_id}/persons/{person_id}/members/{user_id}` → 200 `PersonOut`. Errors documented as above plus `SESSION_WRITE_ERRORS` / `BODY_ERRORS`.

- [ ] **Step 1: Failing tests:**

```python
def test_create_department_and_add_members(): ...                          # kind department, member_user_ids
def test_department_cannot_be_linked_to_an_account(): ...                  # 409 is_a_department
def test_private_letter_for_department_is_read_by_its_members(): ...
def test_readers_after_department_member_added_and_removed(): ...
def test_admin_cannot_add_self_to_department_with_private_post(): ...      # 409 cannot_grant_self
def test_admin_may_add_self_to_department_without_private_post(): ...
def test_grant_writes_audit_and_history_on_each_private_letter(): ...      # access_granted visible to Bob, not to Anne
def test_name_matching_and_rules_work_for_a_department(): ...              # variant "Afd. Finance" proposes the department
def test_update_person_without_kind_keeps_a_department(): ...
def test_delete_department_with_members_is_in_use(): ...
```

- [ ] **Step 2:** `make test` → FAIL.
- [ ] **Step 3:** Implement; `make openapi`, `cd web && pnpm gen:api`.
- [ ] **Step 4:** `make test && make lint && make typecheck && (cd web && pnpm build)` → PASS.
- [ ] **Step 5:** Commit `feat(people): departments as persons with members`.

### Task 9: Linking a person is a grant

**Files:**
- Modify: `backend/apps/people/services.py` (`link_member`), `backend/apps/people/api.py` (`linkPersonToMember` min role becomes viewer; the service checks)
- Test: `backend/tests/test_people_grants.py`; adjust the 1c cases in `test_people_api.py` that unlink another member's link (they now expect 409)

**Interfaces:**
- `link_member(*, workspace, person_id, user_id: UUID | None, actor: User, actor_role: str) -> Person`:
  - linking (`user_id` set) needs `actor_role == "admin"` (else 403 `forbidden`); `user_id == actor.id` and `has_private_post(person)` → 409 `cannot_grant_self`;
  - when the person is linked to another user U (unlink or relink): only U may (`actor.id == U`), else 409 `cannot_unlink` ("Alleen %(name)s kan deze koppeling losmaken.");
  - then `recompute_where(person_id=)`; audit `person_linked` / `person_unlinked` (target person, values: the account's name); `record_grant` on link.

- [ ] **Step 1: Failing tests:**

```python
def test_admin_cannot_link_self_to_person_with_private_post(): ...   # 409 cannot_grant_self, no reader row
def test_admin_links_another_member_and_they_become_reader(): ...    # history access_granted on each private letter
def test_admin_cannot_unlink_another_member(): ...                   # 409 cannot_unlink
def test_member_unlinks_self_and_loses_the_letters(): ...            # as Bob (viewer role suffices)
def test_relinking_to_another_account_moves_the_readers(): ...       # by the linked user
def test_viewer_cannot_link(): ...                                   # 403 forbidden
def test_link_and_unlink_are_audited(): ...
```

- [ ] **Step 2:** `make test` → FAIL.
- [ ] **Step 3:** Implement; `make openapi` (the description changes only) and `pnpm gen:api`.
- [ ] **Step 4:** `make test && make lint && make typecheck` → PASS.
- [ ] **Step 5:** Commit `feat(people): linking a person is an access grant`.

### Task 10: Removing a member, invitations and the own export (part A's seams)

**Files:**
- Modify: part A's member removal (assumed `backend/apps/workspaces/members.py`: `remove_member`, `leave_workspace`), invitation service (`create_invitation`, `accept_invitation`), own-export job; `test_actor_callers.py` (+ the removal module)
- Test: `backend/tests/test_privacy_members.py`

**Interfaces:**
- Removal and leaving: after the membership is deleted and the person unlinked (part A), `with system_actor():` `visibility.forget_member(workspace.id, user.id)`, part A's "assigned tasks become unassigned" update, and `recompute_where(person_id=<their person>)`. Nothing else changes in A's flow.
- `accept_invitation` with a `person`: after the link, `recompute_where(person_id=)` and `record_grant(person, grantee=user, actor=invitation.created_by)`; audit `person_linked`.
- `create_invitation` with a `person` for which `has_private_post(person)` and no `email`: 422 `validation_error` ("Vul een e-mailadres in: %(name)s heeft privé-post.") (D 6.2).
- The own-export job enters `workspace_context(ws, actor=<the user's id>)` (already from Task 2); this task adds its private-letter test.

- [ ] **Step 1: Failing tests:**

```python
def test_removed_member_loses_reader_rows_shares_and_departments(): ...
def test_private_post_of_removed_member_stays_locked(): ...          # Anne (admin) still cannot read it
def test_task_on_hidden_private_letter_is_unassigned_on_removal(): ...
def test_member_of_two_workspaces_keeps_readers_in_the_other(): ...
def test_reinvited_with_this_is_person_reads_again(): ...
def test_invitation_for_person_with_private_post_needs_email(): ...
def test_own_export_contains_only_readable_letters(): ...           # Anne's export lacks Bob's private letter
```

- [ ] **Step 2:** `make test` → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** `make test && make lint && make typecheck` → PASS.
- [ ] **Step 5:** Commit `feat(workspaces): removing a member takes their access to private post`.

### Task 11: Workspace settings: Zorg and the emergency-access switch

**Files:**
- Modify: `backend/apps/workspaces/models.py`, `services.py`, `api.py`, `schemas.py`
- Create: `backend/apps/workspaces/migrations/0004_privacy_settings.py`, `backend/tests/test_workspaces_privacy_api.py`

**Interfaces:**
- `Workspace.care_is_private = BooleanField(default=False)`, `Workspace.emergency_access = BooleanField(default=False)`. Migration: add fields, then a data step: `home` → `care_is_private=True, emergency_access=False`; `business` → `False, True`. `create_workspace` sets the same per kind.
- `WorkspaceOut.care_is_private: bool = False`, `WorkspaceOut.emergency_access: bool = False`.
- `updatePrivacySettings` `PUT /workspaces/{workspace_id}/privacy`, admin, body `PrivacySettingsIn {care_is_private: bool}` → `WorkspaceOut`; audit `workspace_setting_changed` {setting: "care_is_private", value}. Changes no existing letter.
- `setEmergencyAccess` `PUT /workspaces/{workspace_id}/emergency-access`, session only, body `EmergencyAccessIn {enabled: bool}` → `EmergencyAccessOut {enabled: bool}`; only `user.is_server_admin` (403 `forbidden`), membership not required; unknown workspace 404 `workspace_not_found`; enters `workspace_context(ws, actor=user.id)`; audit `emergency_access_enabled` / `emergency_access_disabled`; a notice to every member through part A (`code="emergency_access_changed"`, params `{"enabled": "1"|"0", "by": <name>}`); setting it to its current value writes nothing.

- [ ] **Step 1: Failing tests:**

```python
def test_defaults_per_kind_for_new_and_existing_workspaces(): ...   # migration test via MigrationExecutor
def test_admin_switches_care_is_private(): ...
def test_member_cannot_switch_care_is_private(): ...               # 403
def test_only_a_server_admin_switches_emergency_access(): ...      # workspace admin 403
def test_switching_emergency_access_notifies_every_member(): ...
def test_switching_emergency_access_is_audited(): ...
```

- [ ] **Step 2:** `make test` → FAIL.
- [ ] **Step 3:** Implement; `make openapi`, `pnpm gen:api`.
- [ ] **Step 4:** `make test && make lint && make typecheck && (cd web && pnpm build)` → PASS.
- [ ] **Step 5:** Commit `feat(workspaces): Zorg and emergency-access settings`.

---

## Slice B3: making letters private

### Task 12: Private by hand and by the Zorg label

From this commit on, letters become private in normal use; every earlier task closed a write path that would otherwise break or leak.

**Files:**
- Modify: `backend/apps/documents/organise.py` (Zorg hook), `services.py`, `api.py`, `schemas.py`, `backend/apps/rules/services.py` (`test_rule` summaries), `docs/PRIVACY.md` (what private does not cover: the AI service, page files under the master key, back-ups, the audit log names ids), `docs/GLOSSARY.md` (privé = restricted, lezer = reader, delen = share)
- Test: `backend/tests/test_documents_visibility_api.py`

**Interfaces:**
- `organise.add_label`: after the link, when `label.system_key == "zorg"` and the workspace's `care_is_private` and the document is public: `visibility.make_private(document, actor=actor, origin=origin, rule=rule)`. Removing the label changes nothing.
- `documents.services.document_summaries_out(documents: Sequence[Document]) -> list[DocumentSummaryOut]` (one `reader_names` call); `listDocuments`, `createDocument` and `testRule` use it; `document_out` fills the new fields.
- Schemas (additive): `DocumentSummaryOut.visibility: str = "workspace"`, `DocumentSummaryOut.reader_names: list[str] = Field(default_factory=list)`; `DocumentOut.shared_user_ids: list[UUID] = Field(default_factory=list)`, `DocumentOut.can_make_public: bool = False`; `DocumentEventOut.note: str = ""`.
- `services.set_document_visibility(*, workspace, user, document_id, visibility: str, shared_user_ids: list[UUID]) -> DocumentVisibilityOut`: `_lock_for_organising`; `restricted` → `make_private(share_user_ids=…)` when public, else `set_shares(user_ids=…)`; `workspace` → `make_public` (non-empty `shared_user_ids` → 422); other values → 422.
- Endpoint `setDocumentVisibility` `PUT /workspaces/{workspace_id}/documents/{document_id}/visibility`, member role, body `DocumentVisibilityIn {visibility: str, shared_user_ids: list[UUID] = Field(default=[], max_length=50)}` → 200 `DocumentVisibilityOut {visibility: str, reader_names: list[str], shared_user_ids: list[UUID], can_read: bool}`; errors 404 `document_not_found`, 404 `member_not_found`, 403 `cannot_make_public`, 409 `document_processing`, 422.

- [ ] **Step 1: Failing tests:**

```python
def test_zorg_label_by_hand_makes_the_letter_private_for_actor_and_scanner(): ...
def test_zorg_label_by_accepting_a_suggestion_makes_it_private(): ...
def test_zorg_suggestion_alone_does_not(): ...
def test_zorg_label_in_a_business_workspace_stays_public(): ...      # care_is_private False
def test_set_visibility_restricted_then_lists_show_lock_fields(): ...  # listDocuments item visibility "restricted", reader_names ["Anne", "Bob"]
def test_set_visibility_shares_with_a_member(): ...
def test_set_visibility_share_with_non_member_is_404(): ...
def test_removing_own_share_answers_can_read_false(): ...
def test_make_public_by_non_addressee_is_403_and_nothing_changes(): ...
def test_viewer_cannot_change_visibility(): ...                      # 403 forbidden
def test_non_reader_gets_404_for_the_letter(): ...                   # same body as an unknown id
def test_1c_shaped_client_reads_a_private_letter_without_new_fields(): ...  # response validates against the openapi_1b DocumentSummaryOut
```

- [ ] **Step 2:** `make test` → FAIL.
- [ ] **Step 3:** Implement; `make openapi`, `pnpm gen:api`.
- [ ] **Step 4:** `make test && make lint && make typecheck && (cd web && pnpm build)` → PASS.
- [ ] **Step 5:** Commit `feat(documents): private letters by hand and by the Zorg label`.

### Task 13: Confirm with visibility

**Files:**
- Modify: `backend/apps/documents/review.py`, `schemas.py`
- Test: `backend/tests/test_documents_confirm_private.py`

**Interfaces:**
- `ConfirmIn.visibility: str = ""`: `""` leaves it as it is (a Zorg label in the confirmed labels still makes it private), `"restricted"`, `"workspace"`; anything else 422.
- Order inside `confirm_document` (all in its `atomic`): validate → write the confirmed fields with `save(update_fields=[…])` → `_set_organisation` (labels first: a Zorg label makes the letter private with the confirmer's share; then addressees → `recompute`) → `record_confirmed` → tasks → `restricted`: `make_private(actor=user, origin="user")`; `workspace` on a restricted letter: `make_public(actor=user)` → rules (`_rules_guarded`, as the user). `_is_repeat` ignores `visibility`.
- `make_private`'s membership lock (Task 5) is what serialises a concurrent removal.

- [ ] **Step 1: Failing tests:**

```python
def test_confirm_with_zorg_label_stores_everything_and_is_private(): ...   # fields, tasks, confirmed event, readers {confirmer, scanner}
def test_confirm_with_visibility_restricted(): ...
def test_confirm_workspace_on_zorg_letter_by_addressee_is_public(): ...
def test_confirm_workspace_by_non_addressee_is_403_and_stores_nothing(): ...  # document still needs_review, no tasks
def test_confirm_without_visibility_field_behaves_as_1c(): ...
def test_repeated_confirm_of_a_private_letter_is_200(): ...
def test_make_private_by_an_actor_without_membership_is_404_and_writes_nothing(): ...
def test_rules_at_confirm_run_as_the_user(): ...                             # an auto addressee rule after Zorg leaves a suggestion
```

- [ ] **Step 2:** `make test` → FAIL.
- [ ] **Step 3:** Implement; `make openapi`, `pnpm gen:api`.
- [ ] **Step 4:** `make test && make lint && make typecheck` → PASS.
- [ ] **Step 5:** Commit `feat(documents): confirm can make a letter private or public`.

### Task 14: The rule action "privé maken" (engine, API, web editor)

**Files:**
- Modify: `backend/apps/rules/engine.py`, `definition.py`, `facts.py`, `executor.py`, `services.py`, `schemas.py` (comment only), `web/src/routes/rule-editor.tsx`, `web/src/components/rule-sentence.ts`, `docs/DECISIONS.md` (new action type in version 1; a 1c server skips such a rule whole)
- Test: `backend/tests/test_rules_engine.py`, `test_rules_definition.py`, `test_rules_private.py`, `test_openapi_contract.py`; `web/src/routes/rule-editor.test.tsx`, `web/src/components/rule-sentence.test.ts`

**Interfaces:**
- Engine: `ACTIONS = frozenset({"add_label", "add_addressee", "make_private"})`; `Action.target_id: UUID | None` (None only for `make_private`); `PlannedAction.target_id: UUID | None`; `DocumentFacts.restricted: bool = False`. `make_private` is planned only when the rule is `auto` and matches the settled state and the document is not restricted (and not planned already in this run); never as `propose`. Once planned, later `add_addressee` actions in the same run are planned `propose`.
- Definition: `VERSION` stays 1. `validate(..., mode: str)` gains `mode`; `make_private` with a label or person id → 422; in a `propose` rule → 422 `validation_error` ("Privé maken kan alleen in een regel die automatisch werkt."). `to_json` writes `{"type": "make_private", "label_id": null, "person_id": null}`; `_parse_action` reads it. `rules.services._fill` passes the mode.
- Facts: `facts_for` / `facts_for_many` fill `restricted`.
- Executor: `make_private` → `visibility.make_private(document, actor=None, origin="rule", rule=rule)` (the request's user gets a share through `current_actor()`; in the pipeline and a manual run nobody but the scanner); outcome `applied`.
- Web: action option "Privé maken" (no target picker) shown only while "Automatisch uitvoeren" is on; turning it off with such an action shows an inline error and disables "Opslaan"; actions of a type the editor does not know are kept and sent back unchanged; sentence "maak de brief privé".

- [ ] **Step 1: Failing tests:**

```python
def test_make_private_is_planned_for_an_auto_rule_only(): ...           # engine
def test_make_private_not_planned_on_a_restricted_letter(): ...
def test_addressee_after_make_private_in_the_same_run_is_proposed(): ...
def test_make_private_in_a_propose_rule_is_422(): ...
def test_make_private_with_a_target_is_422(): ...
def test_definition_keeps_version_1(): ...
def test_auto_rule_makes_letter_private_in_the_pipeline(): ...          # readers {scanner}; RuleRun applied; history origin rule
def test_manual_run_makes_private_and_the_admin_loses_sight(): ...      # rule list applied_count as admin excludes it
def test_rule_with_make_private_round_trips_through_a_1c_client(): ...  # contract: GET rule, PUT the 1c RuleIn fields back, definition unchanged
def test_rule_action_schema_type_is_still_a_plain_string(): ...
```

```tsx
test("Privé maken is offered only for an automatic rule")
test("Privé maken disappears when the rule only proposes")   // inline error, Opslaan disabled
test("an unknown action type is sent back unchanged")
test("the sentence says maak de brief privé")
```

- [ ] **Step 2:** `make test` and `cd web && pnpm test` → FAIL.
- [ ] **Step 3:** Implement; `make openapi`, `pnpm gen:api`, `pnpm extract`.
- [ ] **Step 4:** `make test && make lint && make typecheck && (cd web && pnpm test && pnpm lint && pnpm build)` → PASS.
- [ ] **Step 5:** Commit `feat(rules): the rule action privé maken`.

### Task 15: Emergency access

**Files:**
- Modify: `backend/apps/documents/visibility.py` (`open_emergency`, `require_regular_reader`), `backend/apps/documents/services.py` (`_lock_for_organising`, `set_document_visibility`), `review.py`, `backend/apps/tasks/services.py`, `backend/apps/people/api.py`, `schemas.py`, `docs/GLOSSARY.md` (noodtoegang = emergency access)
- Test: `backend/tests/test_privacy_emergency.py`

**Interfaces:**
- `visibility.open_emergency(*, workspace: Workspace, person: Person, actor: User, reason: str) -> EmergencyResult` (`EmergencyResult(opened_count: int, expires_at: datetime)`): `workspace.emergency_access` False → `ApiError(409, "emergency_access_off", "Noodtoegang staat uit in deze werkruimte.")` before anything else; reason `mask_bsns(reason).strip()` of 10–500 characters else 422; as system: for every restricted document with the person (or department) as addressee, an `emergency` reader row for the actor with `expires_at = now() + 1 hour` (update when it exists), an `emergency_opened` history line (`actor`, `note` = reason); audit `emergency_access_used` {reason, document_count} on the person.
- `visibility.require_regular_reader(document: Document, user: User) -> None`: on a restricted document, the user needs a non-emergency, unexpired reader row; else `ApiError(403, "emergency_read_only", "Met noodtoegang kun je deze brief alleen lezen.")`. Called by `_lock_for_organising` (labels, addressees, suggestions, undo), `set_document_visibility`, `confirm_document`, and the task complete/reopen services.
- Endpoint `openEmergencyAccess` `POST /workspaces/{workspace_id}/persons/{person_id}/emergency-access`, admin, body `EmergencyAccessUseIn {reason: str = Field(min_length=10, max_length=500)}` → 200 `EmergencyAccessUseOut {opened_count: int, expires_at: datetime}`; errors 404 `person_not_found`, 409 `emergency_access_off`, 422.

- [ ] **Step 1: Failing tests:**

```python
def test_off_means_no_path(): ...                                    # 409 emergency_access_off, no rows, no audit
def test_reason_of_nine_characters_is_422(): ...
def test_on_opens_the_persons_private_letters_for_one_hour(): ...    # Anne reads them; expires_at ~ now+1h
def test_each_opened_letter_shows_it_in_its_history_to_readers(): ...  # Bob sees emergency_opened with the reason
def test_use_is_audited_with_reason_and_count(): ...
def test_department_post_opens_too(): ...
def test_expired_access_hides_the_letter_again(): ...                # expires_at moved to the past: 404 document_not_found
def test_emergency_reader_cannot_organise_share_or_publish(): ...    # labels, addressees, accept, visibility, confirm, task complete: 403 emergency_read_only
def test_emergency_rows_survive_recompute_and_go_with_make_public(): ...
def test_reader_names_do_not_list_the_emergency_reader(): ...
```

- [ ] **Step 2:** `make test` → FAIL.
- [ ] **Step 3:** Implement; `make openapi`, `pnpm gen:api`.
- [ ] **Step 4:** `make test && make lint && make typecheck` → PASS.
- [ ] **Step 5:** Commit `feat(documents): emergency access to a person's private post`.

### Task 16: The leak suite (D 3 anti-goals, D 15)

**Files:**
- Create: `backend/tests/test_privacy_leaks.py`, `backend/tests/test_privacy_backup.py`

**Interfaces:** none new; the tests use the API as Anne (admin) against a letter private for Bob (addressee Bob, scanned by Bob, labelled, one task, one open suggestion, one rule run, one event), and a public letter as control.

- [ ] **Step 1: Write the tests** (they should pass; any failure is a leak to fix in the owning task's code before committing):

```python
def test_post_list_omits_it(): ...                 # listDocuments
def test_inbox_counts_omit_it(): ...               # getInboxCounts equals the counts without it
def test_person_filter_omits_it(): ...             # listDocuments?person_id=<Bob's person>
def test_persons_document_count_omits_it(): ...    # listPersons as Anne
def test_rule_test_omits_it(): ...                 # testRule with a phrase from its text: total and matched exclude it
def test_manual_rule_run_shows_nothing_of_it(): ...  # runRule; Anne's listRules counts exclude it; Bob sees the event
def test_its_task_is_not_listed_or_completable(): ...  # listTasks (the calendar's source), completeTask -> 404
def test_its_history_and_pages_are_404(): ...      # listDocumentEvents, getDocumentPage, undoDocumentEvent
def test_its_suggestion_cannot_be_accepted(): ...  # acceptSuggestion -> 404
def test_audit_log_names_it_by_id_only(): ...      # every audit row: no sender, summary, text or label name in values
def test_export_omits_it(): ...                    # part A's own export as Anne
def test_job_that_forgets_the_actor_sees_nothing_of_it(): ...  # workspace_context(actor=NOBODY): count excludes it, children 0
```

  In `test_privacy_backup.py` (read-only use of `apps.backup`; other agents own that code):

```python
def test_storage_sweep_keeps_a_private_letters_pages(backup_conninfo, tmp_path): ...  # find_orphans lists none of its files
def test_manifest_counts_private_letters(backup_conninfo): ...                         # documents and pages per workspace include it
```

- [ ] **Step 2:** `make test` → PASS (or fix and rerun).
- [ ] **Step 3:** Commit `test: private post leaks nowhere`.

---

## Slice B4: the web

Fluent 2 building blocks from `web/src/components/ui.tsx` (`MessageBar`, `Badge`, `Persona(s)`, `EmptyState`) and React Aria Components dialogs as in `persons.tsx`; Dutch B1, je-form; tests query by role; `pnpm extract` after every text change.

### Task 17: The lock in lists and on the letter

**Files:**
- Create: `web/src/components/private-lock.tsx`, `private-lock.test.tsx`
- Modify: `web/src/components/icons.tsx` (`lock`), `document-table.tsx`, `document-history.tsx`, `web/src/routes/document.tsx`, `post.tsx`, `inbox.tsx`, `automations.tsx` (rule test results), `web/src/test-utils.tsx` (fixtures with `visibility`), tests next to each

**Interfaces:**
- `<PrivateLock readerNames={string[]} variant="row" | "page" />`: `row` is a `Badge` "Privé" with the reader sentence as its accessible description; `page` is a `MessageBar intent="info"` "Privé voor Anne en Bob" ("Privé voor Anne, Bob en Carla"; "Privé" alone when the list is empty).
- History sentences for the new kinds: "<naam> maakte deze brief privé", "… maakte deze brief weer zichtbaar voor iedereen", "… deelde deze brief met <naam>", "… haalde <naam> weg bij delen", "<naam> kan deze brief nu lezen (gekoppeld aan <persoon>)", "<beheerder> opende deze brief met noodtoegang: <reden>".
- A server without `visibility` (1c): no lock (the fields are optional).

- [ ] **Step 1: Failing tests:**

```tsx
test("a private letter in the post list shows Privé")             // within(row).getByText("Privé")
test("the letter page says who may read it")                       // getByText("Privé voor Anne en Bob")
test("a public letter has no lock")
test("history tells who opened it with emergency access and why")
test("a letter without visibility from an old server shows no lock")
```

- [ ] **Step 2:** `cd web && pnpm test` → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** `pnpm extract && pnpm test && pnpm lint && pnpm build` → PASS.
- [ ] **Step 5:** Commit `feat(web): the lock on private letters`.

### Task 18: Privé maken, delen, weer zichtbaar maken

**Files:**
- Create: `web/src/components/share-dialog.tsx`, `share-dialog.test.tsx`
- Modify: `web/src/routes/document.tsx`, `web/src/api/queries.ts` (`setDocumentVisibility` mutation; invalidates the document, the lists and the inbox counts), `document.test.tsx`

**Interfaces:**
- On the letter (role member or admin): public → button "Privé maken" opens the dialog "Privé maken" with checkboxes "Delen met" from `listMembers` ("Jij" checked and disabled; the scanner pre-checked); restricted → "Delen" (the same dialog on `shared_user_ids`; unchecking yourself shows "Daarna kun je deze brief zelf niet meer openen.") and, when `can_make_public`, "Voor iedereen zichtbaar maken" with a confirm step. `can_read: false` in the answer → to the post list with `MessageBar` "Je kunt deze brief niet meer openen.". 403 `cannot_make_public` and 403 `emergency_read_only` show the server's sentence. Viewers see no buttons.

- [ ] **Step 1: Failing tests:**

```tsx
test("Privé maken sends restricted with the chosen members")
test("Delen shows the current shares and saves the new list")
test("removing yourself warns and leads back to the post")
test("Voor iedereen zichtbaar maken appears only when allowed")
test("a viewer sees no privacy buttons")
test("the server's refusal is shown")
```

- [ ] **Step 2:** `pnpm test` → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** `pnpm extract && pnpm test && pnpm lint && pnpm build` → PASS.
- [ ] **Step 5:** Commit `feat(web): make a letter private and share it`.

### Task 19: Departments, emergency access and the privacy settings; final check

**Files:**
- Create: `web/src/components/emergency-dialog.tsx`, `privacy-settings.tsx`, tests next to each
- Modify: `web/src/routes/persons.tsx`, `persons.test.tsx`, part A's workspace settings route (assumed `web/src/routes/settings.tsx`; when it does not exist, a route `/w/$workspaceId/privacy` "Privacy" in the sidebar for admins), `web/src/api/queries.ts`

**Interfaces:**
- Persons page in a Zakelijk workspace: two sections "Personen" and "Afdelingen"; "Afdeling toevoegen" (`createPerson` with `kind: "department"`); per department "Leden" dialog with member checkboxes (`addDepartmentMember` / `removeDepartmentMember`); 409 `cannot_grant_self` shows its sentence. In Thuis no kind anywhere (departments, if any, are listed as persons).
- Per person or department row, for an admin when `emergency_access`: menu item "Noodtoegang" → dialog "Noodtoegang tot de privé-post van <naam>" with "Reden" (`TextField`, at least 10 characters, the count shown, "Openen" disabled below it), the explanation "Dit komt in het logboek en de lezers zien het bij de brief." and the result "Je kunt 3 brieven een uur lang openen." (0: "Er is geen privé-post van <naam>.").
- `<PrivacySettings />`: switch "Zorg-post is privé" (admins; `updatePrivacySettings`); "Noodtoegang voor de beheerder": a switch for a server admin (`setEmergencyAccess`), a plain sentence of the state for others.

- [ ] **Step 1: Failing tests:**

```tsx
test("a business workspace lists departments with their members")
test("a home workspace shows no departments section")
test("adding yourself to a department with private post shows the refusal")
test("emergency access needs a reason of ten characters")
test("emergency access reports how many letters opened")
test("no Noodtoegang item when the setting is off")
test("an admin switches Zorg-post is privé")
test("only a server admin can switch emergency access")
```

- [ ] **Step 2:** `pnpm test` → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** `pnpm extract && pnpm test && pnpm lint && pnpm build`, then from the root `make test && make lint && make typecheck && make perf` → all PASS.
- [ ] **Step 5: Manual check** on the test server: Thuis, two members; Anne scans a letter with the Zorg label for Bob's person; Bob sees the lock with "Privé voor Anne en Bob"; Carla does not see the letter, its task or the inbox count; the browser console shows no CSP violation. Record in the task report.
- [ ] **Step 6:** Commit `feat(web): departments, emergency access and privacy settings`.

---

## Plan decisions

1. `actor` is keyword-only and required on `workspace_context`, with a third value `NOBODY` (empty): code that never reads documents (`configure_ai`) gets least privilege instead of `SYSTEM`, and the list of system callers stays short.
2. Tests are rewritten mechanically to `actor=SYSTEM`: their behaviour is unchanged because nothing is private before Task 12, and the new privacy tests use real user actors.
3. Helper calls are wrapped as `(SELECT public.brievie_actor_id())` and the functions guard the cast with `CASE` (not `OR`) and pin `SET search_path = ''`: evaluated once per statement, no cast error, no search-path hijack.
4. `documents_documentreader` gets its own `reader_scope` policy (own rows or system; writes only as system) and is exempt from the child policy, which would create the recursion D 5.1 rejects.
5. No `db_default` on anything added: Django then inserts without `RETURNING`, so inserts never meet the SELECT policy.
6. The visibility flip in `make_private` runs as the actor, not the system: the database itself proves the actor kept access, so a missing share fails loudly (42501) instead of producing a request that cannot read its own letter.
7. The actor's own share comes from `current_actor()`, not a parameter: rules at confirm pass `actor=None` to `organise`, and the confirming user must still get a share (D 20 ruling 5).
8. `recompute` refuses to remove the caller's own last access (409 `would_lose_access`) except in `setDocumentVisibility`, which answers `can_read: false`; every other endpoint returns the document it changed.
9. Making a letter public deletes its shares: switched private again it starts from scanner and actor, which is what "visibility switched twice" tests.
10. Emergency access is read-only (`emergency_read_only`), because otherwise an admin with an hour of access could add their own person or a share and keep the letter for good.
11. `make_private` locks the actor's `Membership` row `FOR SHARE`: a concurrent removal waits, so no share or reader is written for somebody who is no longer a member.
12. Deletes use `system_actor()` after authorisation (D 8.3), not `DB_CASCADE`: one code path, no FK rewrites, and `SET_NULL` on history rows keeps working.
13. A repeated upload of a letter the uploader can no longer read answers the existing `document_conflict` sentence: it reveals nothing beyond the client's own id.
14. `WorkspaceModel.save` re-reads a hidden related row's workspace inside `system_actor()`; the workspace policy still binds, so the cross-workspace guard keeps its meaning. This adds `apps/common/models.py` to the system callers, beyond the design's list.
15. `has_private_post` is not exposed per person: the emergency dialog reports a count after the fact, so an ordinary member cannot see whose post is private.
16. Linking needs an admin, unlinking needs the linked member (viewer role suffices), exactly as D 8.1; 1c tests that let an admin unlink somebody else change to expect 409.
17. Department membership changes stay admin-only in both directions; D 8.1 restricts only who may be granted, not who may revoke.
18. The performance gate stops the plan before the feature tasks; the fallback (copied visibility on child rows) needs the owner's yes because it changes the policy shape.

## Assumptions about part A

The part A plan was not present when this plan was written. Assumed, from the design:

- Audit: `apps.audit` (or similar) with `WorkspaceAuditEvent`, a closed `AuditCode` list B extends, and `record(workspace_id: UUID, *, code: str, actor: User | None, target_kind: str, target_id: UUID | None, target_name: str = "", values: Mapping[str, str] = {}) -> None` usable inside a workspace context.
- `User.is_server_admin: bool`, and the web's current-user schema exposes it.
- Member removal in one service (assumed `apps/workspaces/members.py`: `remove_member`, `leave_workspace`) with a place after the membership delete where B adds `forget_member`, the task unassignment as system and `recompute_where`.
- Invitations: `create_invitation(..., person_id, email)` and `accept_invitation(...)` that links the person; B adds the private-post email rule and the grant hooks.
- Notices: `notify(user: User, *, code: str, params: Mapping[str, str]) -> None` shown at the next visit.
- Own export: a Procrastinate job that enters `workspace_context` for the exporting user.
- Part A's codes `person_deleted`, `label_deleted`, `rule_deleted`, `workspace_setting_changed` exist; if not, B adds them (Tasks 6 and 11).
- Part A adds no table with a foreign key to `Document`; if it does, Task 3's coverage test fails until it gets the policy.
- A workspace settings page in the web where B mounts `<PrivacySettings />`.
- Part A's last migration numbers; B's migrations depend on them and are renumbered at build time.

## What the iOS plan for part B must cover

- Read `visibility` and `reader_names` (optional): a lock with "Privé voor …" in lists and on the letter; nothing when the server does not send them (1c server).
- The review: a "Privé" switch sending `ConfirmIn.visibility` only when the user changed it; pre-set on when the chosen labels contain the `zorg` system key and `WorkspaceOut.care_is_private` is true.
- On a letter: "Privé maken" / "Delen met" / "Voor iedereen zichtbaar maken" through `setDocumentVisibility` with members from `listMembers`; handle `can_read: false`, 403 `cannot_make_public`, 403 `emergency_read_only`, 409 `would_lose_access`.
- History: the new event kinds and `note`; an unknown kind is skipped, not a crash.
- Departments appear in the person picker (`PersonOut.kind`), labelled as departments in a Zakelijk workspace.
- Calendar: tasks that disappear from `listTasks` (a letter became private) must be removed from the phone's calendar; check what the 1c app does today (D 20 open point).
- A letter that answers 404 after it was open (made private, emergency expired) closes with a plain sentence.
- The 1c app against this server: verify it ignores unknown event kinds and the new fields, and does not edit rules.

## Risks I could not close from reading the code

- The cost of the correlated `EXISTS` on child tables at scale is unknown until Task 4 runs; `list_persons`' `document_count` over every addressee link is the most likely to fail the threshold.
- Non-leakproof operators (a future `ILIKE` search, `~`) are evaluated above the RLS barrier and lose index use; search in a later phase must be measured with the policies on.
- Django 6.1's exact `save()` fallback and `RETURNING` behaviour are pinned by tests in Task 3, not read from Django's source here; if a later Django adds `RETURNING` for UUID inserts, Task 3's tests fail first.
- Part A's real names and seams (see the list above).
- The 1c iOS app's behaviour with vanishing tasks and unknown history kinds.
- Restore of a back-up must recreate the helper functions with their owner and `search_path`; the back-up plan's restore test should assert the policies and functions exist after restore (that code belongs to other agents).
- The `FOR SHARE` lock on `Membership` in `make_private` adds a lock to confirm; under heavy concurrent confirms by one user this has not been measured.
