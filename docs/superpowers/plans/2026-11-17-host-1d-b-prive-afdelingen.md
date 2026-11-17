# Host 1d part B "Privé en afdelingen" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A letter can be private for the people it is meant for (by hand, by the Zorg label, by a rule), a business workspace has departments whose members read the department's post, an admin can open a person's private post only through logged emergency access, and the database, not the API, keeps everybody else out.

**Architecture:** A second transaction-local setting `brievie.actor` (user id, `system` or empty) joins `brievie.workspace_id`. A derived table `DocumentReader` holds who may read each restricted document; three RESTRICTIVE policies on `documents_document` (`FOR SELECT`, `FOR UPDATE`, `FOR DELETE`, the same visibility condition) check it, and every table with a foreign key to a document gets the same three policies asking whether its document is visible. One module, `apps.documents.visibility`, is the only writer of readers, shares and visibility, writes them as the system, and runs in the same transaction as every input change; a nightly job verifies it. Departments are persons of `kind="department"` with `DepartmentMember` rows. One lock order (memberships, then the document, then readers) everywhere.

**Tech Stack:** As in 1c and part A (Python 3.13, Django 6.1, Django Ninja, PostgreSQL 18 with forced RLS, Procrastinate, pytest; React 19, TanStack Router/Query, React Aria Components, Lingui, Vitest, MSW). No new dependency.

**Spec:** `docs/superpowers/specs/2026-11-17-fase-1d-leden-rollen-sso-design.md` (cited as "D 8.3"); part B is the row in D 13. Binding: D 5.1, 5.2, 8 (all), 11, 14, 15, 20, 21. Also binding: `docs/superpowers/specs/2026-11-17-beheer-backup-export-updates-design.md` 4.3/6.3 (the storage sweep, the manifest counts and the later archive job read as a role or actor that sees private post). Part A plan: `docs/superpowers/plans/2026-11-17-host-1d-a-leden-apparaten.md` (26 tasks, built first; its real names are used below, see "Seams with part A"). House style: `docs/superpowers/plans/2026-11-16-host-fase-1c.md`. Check report processed into this version: `plan-1d-b-controle.md` (session notes) plus the controller's rulings a–j; every item is in a task or in "Plan decisions".

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
- "A field added to an existing API schema has a default (never newly required; the contract test compares with `backend/tests/fixtures/openapi_1b.json`)." Part A adds `openapi_1c.json`; both are compared. "Nullable fields in API schemas are primitives only, and nullable inputs have a default; the contract test forbids `anyOf`."
- User-facing text Dutch B1 via `gettext` (backend) and Lingui `t` (web, then `pnpm extract`). After every API change: `make openapi`, `cd web && pnpm gen:api`, commit both generated files with the change.
- Tests first for logic. Conventional commits ending with `Co-Authored-By: Claude Code <noreply@anthropic.com>`. Branch `feat/1d-b-prive` from part A's branch `feat/1d-a-leden`; never push. Never commit an iCloud conflict copy (" 2", " 3" in a name).

From the design, not left to the builder (D 17):

- **Readers (D 8.1).** A restricted document is readable by: every user linked to a person it is on the name of; every member of a department it is on the name of; every user it was shared with (`DocumentShare`), which by default includes whoever scanned it and whoever made it private (also by confirming). Only current members count. Anybody who can read it can remove a share, their own included.
- **Who may make private:** a member who can read it. **Who may make public again:** a reader who is an addressee (linked to the person, or a member of the department); when no addressee has an account, any reader. Audit event and history line.
- **No private letter without a reader (D 8.1, ruling d).** When an automatic action (a pipeline rule, a manual rule run, a Zorg label added by a rule outside a request) would make a letter private and no current member would be a reader (no addressee with an account, no department member, the scanner absent or no longer a member), the letter stays `workspace`; no "privé maken" suggestion is invented; the history gets a `private_skipped` line; every admin of the workspace gets the notice `private_without_readers` with the document id (once per document).
- **Nobody gives themselves access.** Linking a person to an account and a department membership are grants, made by an admin, never for the admin's own account when that person or department has private post (409 `cannot_grant_self`); a linked person is unlinked only by removing the member or by that member. Each grant is an audit event and a history line on every private letter it opens.
- **Losing one's own access by accident (ruling c).** `409 would_lose_access` is raised only in the organising paths (`organise.set_addressees`, `organise.undo_event`'s addressee branch, and so `confirm_document`), where a user removes the addressee that gives them access. Unlinking oneself from a person and removing oneself from a department are deliberate, are allowed, and recompute readers as the system without that check. `setDocumentVisibility` removing one's own share answers `can_read: false`.
- **Automatic actions do not widen the readers.** Pipeline and rules (automatic or run by hand) never add an addressee to a private letter; they leave a suggestion that only its readers see. Labels can still be added.
- **Zorg is private by default:** workspace setting, on for Thuis, off for Zakelijk. The label with system key `zorg` arriving (by hand, by accepting a suggestion, by a rule) makes the document restricted; a proposal of it does not.
- **Rule action "privé maken":** a new action type in format version 1 (no version bump), only in rules with `mode="auto"`; a 1c client editing such a rule sends it back unchanged (contract test).
- **Tasks** of a restricted document are restricted with it.
- **Departments (D 8.2):** `Person.kind` `person` | `department`; `DepartmentMember(department, user)`; everything for persons works for departments; Thuis hides the kind.
- **RLS (D 8.3, as changed by ruling a).** `brievie.actor` = user id in a request, `system` for code that must see everything, empty otherwise. `workspace_member` sets the user (session and device token). `workspace_context` takes the actor without a default and restores it on exit. Helper functions `brievie_actor_id()` (null for `system` or empty) and `brievie_is_system()`. Document: the existing workspace policy unchanged plus three restrictive policies with one visibility condition, `FOR SELECT`, `FOR UPDATE` and `FOR DELETE`, the last two **without** `WITH CHECK` (so PostgreSQL uses the `USING` condition for the new row of an `UPDATE` too). Every table with a foreign key to a document: the same three restrictive policies as a correlated `EXISTS` on the document ("has no document, or" for a task). `DocumentReader`: workspace matches and the row is the actor's own, or the actor is `system`; writes only as system. An empty actor sees and changes no restricted document. Order when making private: history line and other child rows, readers as the system (including the actor's own share), then the visibility flip **as the current actor**. `WorkspaceModel.save` no longer reads "not visible" as "other workspace". "Is it in use" counts as the system; deletes of person, label and rule run as the system after the authorisation check. `visibility.recompute(document)` is the only writer of `addressee`, `department`, `share` reader rows, as the system, in the same transaction as every input change; nightly verification logs how many rows differed.
- **`system_actor()` callers** (a test lists them; Task 2's list grows to exactly this): `apps/pipeline/steps.py`, `apps/pipeline/jobs.py`, `apps/rules/jobs.py`, `apps/documents/jobs.py`, `apps/documents/visibility.py`, `apps/documents/intake.py`, `apps/documents/export.py` (`reader_id=None`: the server-wide export of Beheer 5), `apps/people/services.py`, `apps/labels/services.py`, `apps/rules/services.py`, `apps/common/models.py`, `apps/workspaces/members.py`, `apps/workspaces/access.py`.
- **One lock order (ruling b).** Every service that locks rows takes them in this order and no other: (1) memberships: a request-path service that will lock a document or write reader rows first takes the actor's own `Membership` row `FOR SHARE` (`apps.workspaces.locks.lock_own_membership`); part A's member changes take all memberships of the workspace `FOR UPDATE` in id order (`lock_memberships`); (2) then the document, and a person or department, always `select_for_update(no_key=True)` (`FOR NO KEY UPDATE`), never plain `FOR UPDATE`; (3) then reader and share rows (inserts and deletes, no explicit lock). `recompute` takes no row lock; its inserts' foreign-key checks take only `FOR KEY SHARE` on the document, user and workspace rows, which conflicts with none of the locks above (`FOR KEY SHARE` conflicts only with `FOR UPDATE`). System paths without a membership (pipeline, manual rule run, nightly check) skip step 1 and never lock a membership.
- **Emergency access (D 8.5):** workspace setting "Noodtoegang voor de beheerder", off for Thuis, on for Zakelijk, changed only by a server admin, an audit event and a notice to every member. With it on an admin opens the private post of one person or department with a reason of 10 to 200 characters: `emergency` reader rows for one hour, written as the system, an audit event per use, a history line on each opened letter ("<beheerder> opende deze brief met noodtoegang: <reden>"), a notice to every user who reads that post by account link or department. Emergency access is read-only, `retryDocumentProcessing` included. With it off there is no code path.
- **Account actions versus private post (ruling e, provisional pending the owner, D 8.5).** A reset link, ending sessions, listing or revoking devices for a member who reads any private letter (a non-emergency `DocumentReader` row) only by a server admin: `account_authority` answers a workspace admin 403 `server_admin_only` even when the member has a single membership. The member always gets the notice (part A). An invitation naming a person with private post requires `email` (part A's `email_required`) and is accepted only by an account with that address (part A's `invitation_email_mismatch`).
- **Answers that reveal private post (ruling f).** `person_in_use` (409, a person whose visible `document_count` is 0), `cannot_grant_self` (409), `email_required` (422) and the account-action `server_admin_only` above reveal that private post exists for somebody. They are given only to workspace admins, which is accepted (D 8.3, `docs/PRIVACY.md`). `deletePerson` therefore becomes admin-only: a plain member gets 403 `forbidden` for every person, whatever it is used for.
- **Removing a member (D 6.3):** the user's reader rows, shares and department memberships in the workspace go, written as the system; private post of a removed member stays locked.
- **Contract (D 8.4, 14):** `DocumentSummaryOut.visibility` (default `workspace`), `reader_names` (default empty); `setDocumentVisibility` (`visibility`, `shared_user_ids`); `ConfirmIn.visibility` default "leave as it is". A 1c app never receives a letter the user may not read.
- **Audit codes part B writes** (part A's `AuditCode`, unchanged names): `visibility_changed` (values `visibility`, `origin`, `share`, `member`; a share added or removed is a `visibility_changed` row with `share` = `added`|`removed` and `member` = the user's name), `department_member_added`, `department_member_removed` (values `member`), `emergency_access_switched_on`, `emergency_access_switched_off`, `emergency_access_used` (values `reason`, `document_count`), `workspace_setting_changed` (`setting` `care_is_private`). Part A already writes `person_linked`, `person_unlinked`, `person_deleted`, `label_deleted`, `rule_deleted`, `member_removed`, `member_left`; part B adds no second row for them. A private document is named by its id only (`AuditTarget(kind="document", id=…, name="")`); values never hold a sender, summary, text or label name. Call shape: `record(AuditCode.X, actor=<AuditActor>, target=<AuditTarget>, workspace_id=<UUID>, values={…})`.
- **Notices part B sends** (part A's `notify(user, code, *, actor_name, values)` and its closed `AccountNotice.Code`): `emergency_access_switched_on`, `emergency_access_switched_off`, `emergency_access_used`, `private_without_readers`.

## PostgreSQL facts this plan relies on (verified against the PG 18 docs, "Policies Applied by Command Type" and `CREATE POLICY`; pinned by Task 3's tests)

- A RESTRICTIVE policy is ANDed with the permissive ones; the permissive `workspace_isolation` (`FOR ALL`) is the permissive policy for every command, so the restrictive ones only narrow. `INSERT` (without `RETURNING`) checks only the INSERT `WITH CHECK` (the workspace policy). Django inserts rows with a UUID primary key **without** `RETURNING` unless a field has `db_default`; therefore **no field added in part B uses `db_default`** (a `RETURNING` would apply the SELECT policy to the new row).
- `UPDATE … WHERE <column>` and `DELETE … WHERE <column>` need read access, so the SELECT policies apply to the existing row. **Without a WHERE that reads a column** (`Model.objects.update(...)` without a filter, a fast `.all().delete()`, a `SET` that reads no column) no SELECT policy applies; that is why part B adds the restrictive `FOR UPDATE` and `FOR DELETE` policies (ruling a): they filter the existing rows of every `UPDATE`/`DELETE`, with or without WHERE.
- A policy for `UPDATE` without `WITH CHECK` uses its `USING` expression for the new row as well. So the `FOR UPDATE` visibility policy checks the new row: switching a letter to `restricted` as an actor who already has a reader row passes (existing row `workspace`, new row readable through the reader row); as an actor without one it fails with SQLSTATE 42501 ("new row violates row-level security policy"), an error, not a skipped row, raised by Django as `ProgrammingError` (a `DatabaseError`). That is the rule "whoever makes it private stays a reader". When the UPDATE's WHERE reads columns, the SELECT policies are also checked against the new row, with the same outcome.
- `SELECT … FOR UPDATE / FOR NO KEY UPDATE / FOR SHARE` applies the SELECT and the UPDATE policies' `USING` to the existing row: a hidden row is not returned (callers answer 404).
- Django `save(update_fields=…)` on an unreadable row raises `DatabaseError("Save with update_fields did not affect any rows.")`; `save()` without `update_fields` falls back to `INSERT` and fails with `IntegrityError` on the primary key. Rule: any document save that can follow a visibility change passes `update_fields`. `refresh_from_db()` on an unreadable row raises `DoesNotExist`.
- Unique constraints and foreign-key checks ignore RLS. Django's delete collector does not: it collects and `SET_NULL`s only readable children, and the deferred FK check then fails at commit (`IntegrityError`). Fix chosen: the delete runs inside `system_actor()` after the authorisation check (D 8.3), not `DB_CASCADE`.
- Referential cascades run as the table owner with `SECURITY_NOFORCE_RLS`, so they bypass RLS; not used here.
- Row locks: `FOR KEY SHARE` (taken by a foreign-key check on the referenced row) conflicts only with `FOR UPDATE`; `FOR NO KEY UPDATE` (taken by an `UPDATE` of non-key columns and by `select_for_update(no_key=True)`) conflicts with `FOR SHARE`, `FOR NO KEY UPDATE` and `FOR UPDATE`; `FOR SHARE` conflicts with `FOR NO KEY UPDATE` and `FOR UPDATE`. Django has no `FOR SHARE` API; `lock_own_membership` uses raw SQL.
- `now()` is the transaction start: an emergency row that expires mid-request still counts until the next request.
- No circle: document → reader (reader's policy reads no other table); child → document → reader. `documents_documentreader` itself does **not** get the child policies (that would be the circle).
- Helper calls are written `(SELECT public.brievie_actor_id())` so PostgreSQL evaluates them once per statement (an InitPlan), not per row.

## Review Focus

1. A user removes their own last access through an organising call (takes themselves off as addressee of a private letter): 409, never a half-written document or a 500; unlinking oneself is allowed. → Task 7 (`test_removing_own_last_access_by_organising_is_409_would_lose_access`), Task 12 (`test_member_unlinks_self_and_loses_the_letters`).
2. An admin with emergency access tries to keep access (adds their own person as addressee, adds a share, makes the letter public) or re-sends it to the AI: refused, read-only. → Task 20 (`test_emergency_reader_cannot_organise_share_publish_or_retry`).
3. An `UPDATE` or `DELETE` without a column in its WHERE touches a hidden row: it must not. → Task 3 (`test_update_without_where_leaves_hidden_letters_alone`, `test_delete_without_where_leaves_hidden_children_alone`).
4. Confirm-with-Zorg and removing a member at the same moment: no deadlock (40P01), no share for a non-member. → Task 18 (`test_confirm_with_zorg_and_removing_a_member_at_once_never_deadlock`).
5. Every operation with a `{document_id}` answers someone else's private letter exactly as an unknown id. → Task 16 (`test_every_document_operation_answers_a_hidden_letter_like_an_unknown_id`).
6. A workspace admin takes over a member who reads private post (reset link, sessions, devices): 403 `server_admin_only`. → Task 14 (`test_workspace_admin_cannot_reset_a_reader_of_private_post`).
7. A rule with "privé maken" is switched to "voorstellen": 422; the stored rule unchanged. → Task 19.

---

## File Structure

```
backend/apps/
  workspaces/
    rls.py                    # + ACTOR_SETTING, actor_functions(), document_visibility_policy(),
                              #   restrict_to_visible_documents(), restrict_reader_rows()
    context.py                # + Actor, SYSTEM, NOBODY, workspace_context(actor=), system_actor(), current_actor()
    locks.py                  # NEW: lock_own_membership (the first step of the lock order)
    deps.py                   # workspace_member sets the user as actor
    models.py                 # Workspace.care_is_private, Workspace.emergency_access
    access.py                 # part A's seams, filled in: person_requires_email, on_member_added,
                              #   on_member_removed; + reads_private_post, private_post_readers
    members.py                # remove_member steps 3-4 inside system_actor()
    settings_service.py  services.py  api.py  schemas.py    # settings extended; setEmergencyAccess
    migrations/0005_actor_functions.py  0006_privacy_settings.py
  accounts/authority.py       # account_authority: reader of private post -> server admin only
  common/models.py            # WorkspaceModel.save: hidden != other workspace
  documents/
    models.py                 # Document.visibility, DocumentShare, DocumentReader, DocumentEvent.note + kinds
    visibility.py             # NEW: the only writer of visibility, shares, readers; names; grants; emergency
    jobs.py                   # (part A's file) + verify_readers_job
    export.py  member_export.py   # (part A's files) actor for export_context; member_document_ids
    organise.py               # hooks: addressee changes -> recompute + keep_own_access; zorg -> make_private
    services.py  review.py  intake.py  api.py  schemas.py
    migrations/0009_visibility.py
  people/
    models.py                 # Person.kind, DepartmentMember
    services.py  api.py  schemas.py      # departments, grants, deletes and archiving as system
    migrations/0003_departments.py
  labels/services.py          # delete as system
  pipeline/steps.py           # document lock no_key
  rules/
    engine.py  definition.py  facts.py  executor.py  services.py  jobs.py
    migrations/0004_rulerun_visibility.py
  tasks/
    services.py
    migrations/0002_task_visibility.py
  backup/verify.py            # verify_restore checks the helpers and the visibility policies (Task 21)
backend/tests/
  privacy_support.py          # restrict_raw(), as_actor(), private_letter()
  test_workspaces_actor.py  test_actor_callers.py  test_privacy_rls.py  test_rls_coverage.py
  test_privacy_performance.py  test_privacy_locks.py  test_documents_readers.py
  test_documents_visibility.py  test_documents_grants.py  test_privacy_deletes.py
  test_rules_private.py  test_people_departments.py  test_people_grants.py  test_privacy_members.py
  test_privacy_account_actions.py  test_workspaces_privacy_settings.py  test_privacy_leaks.py
  test_privacy_backup.py  test_documents_visibility_api.py  test_documents_confirm_private.py
  test_privacy_concurrency.py  test_privacy_emergency.py  test_backup_verify.py (+ cases)
web/src/
  components/private-lock.tsx  share-dialog.tsx  emergency-dialog.tsx  privacy-settings.tsx
  components/icons.tsx (+lock)  document-table.tsx  document-history.tsx  rule-sentence.ts
  routes/document.tsx  post.tsx  inbox.tsx  persons.tsx  rule-editor.tsx  settings.tsx (part A's)
docs/ DECISIONS.md  GLOSSARY.md  PRIVACY.md  api/openapi.json
```

Migration numbers follow part A's: A creates `workspaces/0003_invitation`, `workspaces/0004_workspace_allow_member_export`, `documents/0008_memberexport`, `accounts/0003`–`0007`; it adds none in `people`, `rules`, `tasks`. If A's numbers differ at build time, B's are renumbered to follow them and the dependencies below are updated with them.

---

## Slice B1: the actor and the policies (nothing private yet)

### Task 1: The actor helper functions in PostgreSQL

**Files:**
- Modify: `backend/apps/workspaces/rls.py`
- Create: `backend/apps/workspaces/migrations/0005_actor_functions.py`
- Test: `backend/tests/test_workspaces_actor.py`

**Interfaces:**
- Produces: `rls.ACTOR_SETTING = "brievie.actor"`; `rls.actor_functions() -> migrations.RunSQL` creating (reverse: `DROP FUNCTION public.brievie_actor_id(); DROP FUNCTION public.brievie_is_system();`) exactly:

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
- Migration `workspaces/0005_actor_functions` depends on `("workspaces", "0004_workspace_allow_member_export")` (part A's last).

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
- [ ] **Step 3:** Implement `actor_functions()` and migration `0005`.
- [ ] **Step 4:** `make test && make lint && make typecheck` → PASS.
- [ ] **Step 5:** Commit `feat(workspaces): actor helper functions for row-level security`.

### Task 2: Every workspace context names its actor

**Files:**
- Modify: `backend/apps/workspaces/context.py`, `deps.py`, `services.py`; `backend/apps/pipeline/steps.py`, `pipeline/jobs.py`, `pipeline/management/commands/configure_ai.py`, `rules/jobs.py`; part A's call sites: `backend/apps/audit/services.py`, `backend/apps/documents/export.py`, `backend/apps/documents/jobs.py`, `backend/apps/workspaces/invitations.py`, `backend/apps/workspaces/members.py`; every test that calls `workspace_context` (294 calls in 43 files before part A, plus part A's tests)
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
| A: `audit/services.py` `record` (it enters the context itself) | `NOBODY` (audit tables have no document policy) |
| A: `documents/export.py` `export_context(workspace_id, *, reader_id)` | `reader_id`, or `SYSTEM` when `reader_id` is None (the server-wide export of Beheer 5 must contain private post) |
| A: `documents/jobs.py` `remove_expired_exports` | `NOBODY` (export rows only) |
| A: `documents/jobs.py` `build_member_export` | through `export_context(..., reader_id=user_id)` |
| A: `workspaces/invitations.py` `lookup_invitation` | `NOBODY` (person name and AI status only) |
| A: `workspaces/invitations.py` `accept_invitation` | the accepting user's id |
| A: `workspaces/members.py` `remove_member` step 3 | `actor.user_id` (the request's user; Task 13 wraps steps 3–4 in `system_actor()`) |
| tests | `SYSTEM` (mechanical rewrite; behaviour unchanged, nothing is private yet) |

- `tests/test_actor_callers.py`: scans `backend/apps/**/*.py` for `system_actor(` and `actor=SYSTEM`; asserts the set of files equals `ALLOWED_SYSTEM_CALLERS` (now: `apps/pipeline/steps.py`, `apps/pipeline/jobs.py`, `apps/rules/jobs.py`, `apps/documents/export.py`; later tasks add their file in the same commit, ending at the list in Global Constraints). Also asserts that `set_config(` with `brievie.actor` appears only in `context.py`.

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
def test_record_inside_a_user_context_restores_the_user(): ... # record(...) inside as_actor(ws, bob): current_actor() == bob.id afterwards
def test_export_context_runs_as_the_reader_or_the_system(): ...  # reader_id=bob.id -> bob.id; None -> "system"
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
- Create: `backend/apps/documents/migrations/0009_visibility.py`, `backend/apps/rules/migrations/0004_rulerun_visibility.py`, `backend/apps/tasks/migrations/0002_task_visibility.py`, `backend/tests/privacy_support.py`, `backend/tests/test_privacy_rls.py`
- Modify: `backend/tests/test_rls_coverage.py`, `test_actor_callers.py` (+ `apps/common/models.py`), `docs/DECISIONS.md` (the two-layer policy; read, update and delete restricted alike and why `WITH CHECK` is left out; why reader rows have no child policy; no `db_default`)

**Interfaces:**
- `Document.visibility = CharField(max_length=20, choices=Visibility.choices, default="workspace")`, `Document.Visibility`: `WORKSPACE = "workspace"`, `RESTRICTED = "restricted"`. No `db_default`.
- `DocumentShare(WorkspaceModel)`: `document` FK `Document` CASCADE `related_name="shares"`; `user` FK `AUTH_USER_MODEL` CASCADE `related_name="+"`; `created_by` FK user null `SET_NULL` `related_name="+"`. Unique `documents_documentshare_unique` (`document`, `user`). `same_workspace_fields = ("document",)`.
- `DocumentReader(WorkspaceModel)`: `document` FK CASCADE `related_name="readers"`; `user` FK CASCADE `related_name="+"`; `reason = CharField(max_length=20, choices=Reason.choices)` with `ADDRESSEE="addressee"`, `DEPARTMENT="department"`, `SHARE="share"`, `EMERGENCY="emergency"`; `expires_at = DateTimeField(null=True, blank=True)`. Unique `documents_documentreader_unique` (`document`, `user`, `reason`); index `documents_reader_user_document` (`user`, `document`). `same_workspace_fields = ("document",)`.
- `DocumentEvent.note = TextField(blank=True, default="")` (BSN-masked); new `Kind` values `MADE_PRIVATE="made_private"`, `MADE_PUBLIC="made_public"`, `SHARE_ADDED="share_added"`, `SHARE_REMOVED="share_removed"`, `ACCESS_GRANTED="access_granted"`, `EMERGENCY_OPENED="emergency_opened"`, `PRIVATE_SKIPPED="private_skipped"`.
- In `rls.py`: `VISIBILITY_POLICIES: Final = ("document_visibility", "document_visibility_update", "document_visibility_delete")`, `READER_POLICY: Final = "reader_scope"`;
  - `document_visibility_policy() -> RunSQL` (reverse: the three `DROP POLICY … ON "documents_document"`):

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

CREATE POLICY document_visibility_update ON "documents_document" AS RESTRICTIVE FOR UPDATE
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

CREATE POLICY document_visibility_delete ON "documents_document" AS RESTRICTIVE FOR DELETE
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

  No `WITH CHECK` on the update policy, deliberately: PostgreSQL then checks the new row of an `UPDATE` with the same condition, which is the "whoever makes it private stays a reader" rule (see the PostgreSQL facts).
  - `restrict_to_visible_documents(table: str, *, nullable: bool = False) -> RunSQL` (name checked as in `enable_rls`; reverse: the three `DROP POLICY … ON "<table>"`):

```sql
CREATE POLICY document_visibility ON "<table>" AS RESTRICTIVE FOR SELECT
USING (<"document_id IS NULL OR " when nullable>EXISTS (
  SELECT 1 FROM "documents_document" d WHERE d.id = "<table>".document_id));

CREATE POLICY document_visibility_update ON "<table>" AS RESTRICTIVE FOR UPDATE
USING (<"document_id IS NULL OR " when nullable>EXISTS (
  SELECT 1 FROM "documents_document" d WHERE d.id = "<table>".document_id));

CREATE POLICY document_visibility_delete ON "<table>" AS RESTRICTIVE FOR DELETE
USING (<"document_id IS NULL OR " when nullable>EXISTS (
  SELECT 1 FROM "documents_document" d WHERE d.id = "<table>".document_id));
```

  - `restrict_reader_rows() -> RunSQL`:

```sql
CREATE POLICY reader_scope ON "documents_documentreader" AS RESTRICTIVE FOR ALL
USING ((SELECT public.brievie_is_system()) OR user_id = (SELECT public.brievie_actor_id()))
WITH CHECK ((SELECT public.brievie_is_system()));
```

- Migration `documents/0009_visibility` depends on `("documents", "0008_memberexport")` **and** `("workspaces", "0005_actor_functions")` (the policies call the helpers; without this dependency `CREATE POLICY` fails on a fresh database). Operations: add `visibility`, `note`; create `DocumentShare`, `DocumentReader`; `enable_rls` on both; `restrict_reader_rows()`; `document_visibility_policy()`; `restrict_to_visible_documents()` on `documents_documentpage`, `documents_extraction`, `documents_documentaddressee`, `documents_documentlabel`, `documents_documentevent`, `documents_documentsuggestion`, `documents_documentshare`. `rules/0004_rulerun_visibility` (depends on `documents/0009`): `rules_rulerun`. `tasks/0002_task_visibility` (depends on `documents/0009`): `tasks_task` with `nullable=True`.
- `WorkspaceModel.save`: when the related row raises `ObjectDoesNotExist`, read its `workspace_id` again inside `system_actor()` (the workspace policy still binds, so another workspace's row stays invisible and raises `ValueError` as before).
- `tests/privacy_support.py`: `restrict_raw(document: Document, readers: Sequence[User]) -> None` (as the system: reader rows with reason `share`, then `update(visibility="restricted")`); `as_actor(workspace: Workspace, actor: Actor) -> AbstractContextManager[None]` = `workspace_context(workspace.id, actor=actor)`. Task 7 adds `private_letter(...)` on top of the real service.

- [ ] **Step 1: Failing tests** in `test_privacy_rls.py` (one workspace, users Anne and Bob, a letter restricted to Bob with one row in every child table, and a public letter):

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
def test_update_without_where_leaves_hidden_letters_alone(): ...      # raw "UPDATE documents_document SET sender = 'x'" as Anne:
                                                                      #   rowcount == 1 (the public letter); as SYSTEM the hidden sender unchanged
def test_queryset_update_without_filter_leaves_hidden_tasks_alone(): ...  # as Anne Task.objects.update(description="x"): hidden task unchanged
def test_delete_without_where_leaves_hidden_children_alone(): ...     # raw "DELETE FROM documents_documentlabel" as Anne: the hidden DocumentLabel remains
def test_delete_of_hidden_children_deletes_nothing(): ...             # filter(document_id=hidden).delete() as Anne == (0, {})
def test_select_for_update_does_not_return_a_hidden_letter(): ...     # select_for_update(no_key=True).filter(id=hidden).first() is None
def test_save_with_update_fields_of_a_hidden_letter_raises(): ...     # DatabaseError "did not affect any rows"
def test_refresh_of_a_hidden_letter_raises_does_not_exist(): ...
def test_update_that_hides_the_row_from_the_actor_is_42501(): ...     # as Anne: update(visibility="restricted") on the public letter
                                                                      #   -> ProgrammingError, __cause__.sqlstate == "42501"
def test_flip_without_where_by_a_non_reader_is_42501(): ...           # raw "UPDATE documents_document SET visibility = 'restricted'" as Anne -> 42501
def test_making_private_after_writing_own_reader_row_succeeds(): ...  # SYSTEM writes Anne's row first; then the update as Anne == 1
def test_making_public_by_a_reader_succeeds(): ...                    # as Bob update(visibility="workspace") on his letter == 1
def test_child_update_to_point_at_a_hidden_letter_is_42501(): ...     # as Anne: her DocumentLabel.update(document_id=hidden) -> 42501
def test_child_insert_on_a_hidden_letter_passes_the_workspace_check(): ...  # DocumentEvent(document_id=hidden).save() as Anne: no ValueError
def test_child_pointing_into_another_workspace_still_raises(): ...
def test_fk_check_ignores_rls_so_a_user_delete_fails_at_commit(): ... # as Anne, label.delete() with a hidden DocumentLabel,
                                                                      #   SET CONSTRAINTS ALL IMMEDIATE -> IntegrityError
def test_no_policy_recursion(): ...                                   # SELECT, UPDATE ... WHERE false, DELETE ... WHERE false on every table above as Anne: no error
```

  In `test_rls_coverage.py` (replace the "exactly one policy" assertion):

```python
def test_every_workspace_table_has_exactly_one_permissive_policy(): ...   # workspace_isolation, cmd '*'
def test_other_policies_are_known_restrictive_ones(): ...
    # name -> cmd: {document_visibility: 'r', document_visibility_update: 'w', document_visibility_delete: 'd', reader_scope: '*'};
    # polpermissive False; polwithcheck IS NULL for the three visibility policies
def test_every_table_with_a_document_foreign_key_has_the_visibility_policies(): ...
    # tables from pg_constraint (contype='f', confrelid='documents_document'::regclass) minus {'documents_documentreader'}
    # == tables from Django meta (FK to Document) minus the same; each has all three VISIBILITY_POLICIES,
    # restrictive, qual mentions documents_document
def test_document_policies_read_the_reader_table_and_the_helpers(): ...    # all three on documents_document
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
- Measured as that user, median of 5 runs after one warm-up: `services.list_documents(ws)` (200 rows with prefetches), `services.inbox_counts(ws)`, `people.services.list_persons(ws)` (the `document_count` annotation walks every addressee link), `labels.services.list_labels(ws)`, `tasks.services.list_tasks(ws, status="all")`, `services.list_documents(ws, person_id=…)`.
- **Pass:** each median ≤ 250 ms on the development machine, and `EXPLAIN (FORMAT JSON)` of the inbox count has no `Seq Scan` on `documents_documentreader`.
- **Fail → stop:** report the numbers to the owner; do not continue to Task 5. The fallback (D 18) is the reader check per child table: a copied `document_visibility` column on each child table, kept by `visibility` in the same transaction, and the child policies `document_visibility = 'workspace' OR (SELECT public.brievie_is_system()) OR EXISTS (SELECT 1 FROM documents_documentreader r WHERE r.document_id = <table>.document_id AND r.user_id = (SELECT public.brievie_actor_id()) AND (r.expires_at IS NULL OR r.expires_at > now()))`, which drops the join to the document. That becomes its own task with the owner's yes.

- [ ] **Step 1:** Write `test_visibility_check_at_100k` with the thresholds above (it fails only if the policies are slow).
- [ ] **Step 2:** `make perf` → record the six medians.
- [ ] **Step 3:** If it passes, add the DECISIONS line with the numbers; if not, stop (see above).
- [ ] **Step 4:** `make test` (the slow test is skipped there) → PASS.
- [ ] **Step 5:** Commit `test(documents): measure the visibility check at 100,000 documents`.

---

## Slice B2: one writer, one lock order, and every write the user cannot see

### Task 5: Readers: `recompute`, the hooks and the nightly check

**Files:**
- Create: `backend/apps/documents/visibility.py`, `backend/tests/test_documents_readers.py`
- Modify: `backend/apps/documents/jobs.py` (part A's file: + `verify_readers_job`), `backend/apps/documents/organise.py` (addressee hooks), `test_actor_callers.py` (+ `apps/documents/visibility.py`, `apps/documents/jobs.py`)

**Interfaces** (`apps.documents.visibility`; every write inside `system_actor()`; every public function in `transaction.atomic`):
- `desired_readers(document: Document, *, extra_user_ids: Collection[UUID] = ()) -> set[tuple[UUID, str]]` (as system): for a restricted document, or one about to become restricted, `{(u, "addressee")}` for persons on the document with `kind="person"` and a linked user; `{(u, "department")}` for members of departments on the document (empty until Task 11 adds `DepartmentMember`); `{(u, "share")}` for its shares and for `extra_user_ids`; each `u` only when `Membership(workspace, u)` exists.
- `recompute(document: Document) -> int`: makes the non-emergency reader rows exactly `desired_readers(document)` (public document: none) and returns how many rows were inserted plus deleted. **Never raises for the caller's own access** (ruling c; see `keep_own_access`, Task 7). Takes no row lock.
- `recompute_where(*, workspace_id: UUID, person_id: UUID | None = None, user_id: UUID | None = None) -> int`: `recompute` for every restricted document of that person (addressee) or user (reader, share, linked person, department member).
- `can_read(document_id: UUID) -> bool` (as the current actor, no system).
- `reader_names(documents: Sequence[Document]) -> dict[UUID, list[str]]`: first filters the ids as the current actor to readable restricted ones, then reads `DocumentReader` (non-emergency, not expired) as system; names of distinct users sorted case-insensitively.
- `verify_readers(workspace_id: UUID) -> int`: as system, deletes expired emergency rows and `DocumentShare` rows of users who are no longer members, and returns the sum of `recompute` over restricted documents.
- `organise.add_addressee`, `organise.set_addressees` (once at the end), `organise.undo_event` (addressee branch) call `visibility.recompute(document)`.
- `apps.documents.jobs.verify_readers_job(timestamp: int) -> None` (`@app.periodic(cron="40 3 * * *")`, `@app.task(name="verify_readers", queue=QUEUE)`): per workspace `workspace_context(ws, actor=SYSTEM)`, in a short transaction each; logs `"Reader check: %d rows differed"` (warning when > 0, info otherwise).

- [ ] **Step 1: Failing tests** (Thuis workspace: Anne admin, Bob member, Carla member; persons linked; letters made restricted with `restrict_raw`):

```python
def test_addressee_with_account_becomes_reader(): ...
def test_readers_after_addressee_added_and_removed(): ...
def test_readers_after_share_removed(): ...
def test_readers_after_person_linked_to_another_account(): ...           # recompute_where(person_id=)
def test_only_current_members_count(): ...                               # Membership deleted -> recompute drops the row
def test_recompute_keeps_emergency_rows(): ...
def test_recompute_of_a_public_letter_removes_all_rows(): ...
def test_recompute_does_not_raise_when_the_actor_loses_access(): ...     # as Bob: his share removed by hand, recompute -> returns 1, no error
def test_reader_names_only_for_readable_letters(): ...                   # Anne asks for Bob-only letter id: {}
def test_verify_readers_counts_and_repairs_a_missing_row(): ...          # delete a row as system, job returns 1, row back
def test_verify_readers_removes_expired_emergency_rows(): ...
def test_verify_readers_removes_shares_of_former_members(): ...
def test_verify_job_logs_a_number_only(caplog): ...                      # message has digits, no uuid, no name
```

- [ ] **Step 2:** `make test` → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** `make test && make lint && make typecheck` → PASS.
- [ ] **Step 5:** Commit `feat(documents): readers derived in one place, with a nightly check`.

### Task 6: One lock order

**Files:**
- Create: `backend/apps/workspaces/locks.py`, `backend/tests/test_privacy_locks.py`
- Modify: `backend/apps/documents/services.py` (`_lock_for_organising`, `retry_processing`), `backend/apps/documents/review.py` (`confirm_document`), `backend/apps/pipeline/steps.py` (`_lock_processing`), `backend/apps/rules/jobs.py` (`apply_rule_batch`), `backend/apps/people/services.py` (`_get_for_update`), `docs/DECISIONS.md` (the lock order and why `no_key`)

**Interfaces:**
- `apps.workspaces.locks.lock_own_membership(workspace_id: UUID, user_id: UUID) -> None`: raw SQL `SELECT id FROM workspaces_membership WHERE workspace_id = %s AND user_id = %s FOR SHARE`; no row → `ApiError(404, "workspace_not_found", …)` with the sentence `workspace_member` already uses. A leaf module (imports `django.db`, `apps.common.errors` only).
- Called first, before any document lock, by: `documents.services._lock_for_organising` (labels, addressees, suggestions, undo, and later visibility), `documents.services.retry_processing`, `documents.review.confirm_document`; Tasks 11, 12 and 20 add their services.
- Every `select_for_update()` on `Document` or `Person` passes `no_key=True`: `documents/services.py` (two), `documents/review.py`, `pipeline/steps.py`, `rules/jobs.py`, `people/services.py` `_get_for_update`. `Workspace.objects.select_for_update()` in `rules.services.create_rule` and `labels.presets.apply_presets` stays `FOR UPDATE`: those transactions take no other row lock afterwards, so they cannot be part of a cycle (written in DECISIONS).

- [ ] **Step 1: Failing tests:**

```python
def test_document_and_person_row_locks_are_no_key(): ...
    # source scan of backend/apps: every "select_for_update(" in a statement on Document or Person has "no_key=True"
def test_request_paths_take_the_membership_before_the_document(client): ...
    # CaptureQueriesContext around setDocumentLabels, setDocumentAddressees, confirmDocument, retryDocumentProcessing:
    # the "FOR SHARE" on workspaces_membership comes before the first "FOR NO KEY UPDATE" on documents_document
def test_a_removed_member_gets_404_from_an_organising_call(): ...   # membership deleted between workspace_member and the lock -> 404 workspace_not_found
@pytest.mark.django_db(transaction=True)
def test_a_foreign_key_check_does_not_wait_for_a_document_lock(): ...
    # connection 1 holds a document FOR NO KEY UPDATE; connection 2 inserts a DocumentEvent for it with lock_timeout 1s -> succeeds
```

- [ ] **Step 2:** `make test` → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** `make test && make lint && make typecheck` → PASS.
- [ ] **Step 5:** Commit `refactor: one lock order, memberships before documents, documents without key locks`.

### Task 7: Private, public and shares

**Files:**
- Create: `backend/tests/test_documents_visibility.py`
- Modify: `backend/apps/documents/visibility.py`, `backend/apps/documents/organise.py` (`keep_own_access` around addressee removals), `backend/tests/privacy_support.py` (`private_letter`)

**Interfaces** (`apps.documents.visibility`):
- `MakePrivateResult = Literal["made_private", "already_private", "nobody_could_read"]`.
- `make_private(document: Document, *, actor: AuditActor, origin: str, rule: Rule | None = None, share_user_ids: Collection[UUID] = ()) -> MakePrivateResult`. The caller holds the document lock (and, in a request, the membership lock first, Task 6). `actor.user_id` is the history actor (None for the system or a rule). Order (D 8.3):
  1. Already restricted → `"already_private"`.
  2. As system: `extra = {scanned_by when a member} ∪ {current_actor() when it is a user} ∪ share_user_ids`; every id in `share_user_ids` must be a current member (`ApiError(404, "member_not_found", "We kunnen dit lid niet vinden.")`). `desired_readers(document, extra_user_ids=extra)` empty → **nothing is made private** (ruling d): a `private_skipped` history line (origin, rule, `note` "Deze brief had privé moeten worden, maar niemand zou hem dan kunnen lezen. Hij blijft zichtbaar voor iedereen."), and, when the document had no `private_skipped` line before, `notify(admin, "private_without_readers", actor_name="Brievie", values={"document_id": str(document.id), "workspace": workspace.name})` for every admin membership; return `"nobody_could_read"`. In a request this cannot happen (the current user is in `extra`).
  3. History line `made_private` (actor, origin, rule).
  4. As system: `DocumentShare` rows for `extra`, then `recompute`-equivalent inserts of the reader rows computed as restricted.
  5. **As the current actor** (no `system_actor()`): `Document.objects.filter(id=document.id).update(visibility="restricted")`; the database proves the actor can still read it (42501 otherwise, see the PostgreSQL facts); set the attribute.
  6. `record(AuditCode.VISIBILITY_CHANGED, actor=actor, target=AuditTarget(kind="document", id=document.id), workspace_id=document.workspace_id, values={"visibility": "restricted", "origin": origin})`. Return `"made_private"`.
- `make_public(document: Document, *, actor: AuditActor, user: User) -> bool`: `can_make_public(document, user)` or `ApiError(403, "cannot_make_public", "Alleen iemand op wiens naam deze brief staat, kan hem weer voor iedereen zichtbaar maken.")`; history `made_public`; update to `workspace` as the current actor; as system delete shares and non-emergency readers (emergency rows too: a public letter needs none); audit `visibility_changed {"visibility": "workspace", "origin": "user"}`. False when already public.
- `set_shares(document: Document, *, user_ids: Collection[UUID], actor: AuditActor) -> None`: restricted only; every id a current member (404 `member_not_found`); adds/removes `DocumentShare` rows (history `share_added`/`share_removed` with `person_name` = the user's name; per change `visibility_changed {"visibility": "restricted", "share": "added" | "removed", "member": <name>}`), then `recompute(document)`. Removing one's own share is allowed (the endpoint answers `can_read: false`).
- `can_make_public(document: Document, user: User) -> bool`: the user has a non-emergency reader row with reason `addressee` or `department`, or the document has no such rows at all and the user has a non-emergency reader row (as system: rows of the document).
- `shared_user_ids(document: Document) -> list[UUID]`.
- `keep_own_access(document: Document) -> AbstractContextManager[None]`: when `current_actor()` is a user who can read the document on entry and cannot on exit, raises `ApiError(409, "would_lose_access", "Dan kun je deze brief zelf niet meer lezen.")` (the caller's `atomic` rolls back). A no-op for the system and the empty actor. Used **only** by `organise.set_addressees` and `organise.undo_event` (addressee branch), wrapped around the change and its `recompute` (ruling c).
- `privacy_support.private_letter(workspace, *, scanned_by: User, addressees: Sequence[Person] = (), shared_with: Sequence[User] = (), labels=(), with_task: bool = True) -> Document`: a confirmed letter with a page row, an extraction, a suggestion, an event and a task, then `make_private(actor=AuditActor.system(), origin="user", share_user_ids=…)` as system.

- [ ] **Step 1: Failing tests** (Thuis: Anne admin, Bob member, Carla member):

```python
def test_make_private_gives_scanner_and_actor_a_share(): ...            # readers {Bob(share), Anne(share)}; history made_private
def test_make_private_writes_history_before_the_flip(): ...              # event visible to Anne afterwards; flip as Anne passed
def test_make_private_audits_visibility_changed_with_the_id_only(): ...  # target_name "", values {"visibility": "restricted", "origin": "user"}
def test_readers_after_visibility_switched_twice(): ...                  # private, public, private: only scanner+actor, old shares gone
def test_make_public_by_addressee(): ...
def test_make_public_by_non_addressee_when_addressee_has_account_is_403(): ...
def test_any_reader_may_make_public_when_no_addressee_has_account(): ...
def test_removing_own_last_access_by_organising_is_409_would_lose_access(): ...  # set_addressees without self, no share -> 409, nothing changed
def test_undo_that_removes_own_access_is_409(): ...
def test_set_shares_may_remove_own_share(): ...                          # can_read() False afterwards, no error
def test_share_change_is_audited_as_visibility_changed(): ...           # values {"visibility": "restricted", "share": "removed", "member": "Carla"}
def test_automatic_make_private_without_any_reader_keeps_the_letter_public(): ...
    # as SYSTEM, scanned_by a former member, no addressee with account -> "nobody_could_read"; visibility "workspace";
    # history private_skipped; no DocumentShare; Anne (admin) has notice private_without_readers with values.document_id
def test_scanner_absent_and_no_addressee_also_keeps_it_public(): ...     # scanned_by None
def test_the_admin_notice_is_sent_once_per_document(): ...              # second attempt: second history line, no second notice
```

  (Department members as readers for this rule are tested in Task 11, which adds them.)

- [ ] **Step 2:** `make test` → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** `make test && make lint && make typecheck` → PASS.
- [ ] **Step 5:** Commit `feat(documents): make a letter private or public, and share it`.

### Task 8: Grants, forgetting a member, and private-post queries

**Files:**
- Create: `backend/tests/test_documents_grants.py`
- Modify: `backend/apps/documents/visibility.py`, `backend/apps/workspaces/access.py` (part A's seams, filled in), `test_actor_callers.py` (+ `apps/workspaces/access.py`)

**Interfaces:**
- `visibility.has_private_post(person: Person) -> bool` (as system): a restricted document has this person (or department) as addressee.
- `visibility.reads_private_post(user_id: UUID) -> bool` (as system, in the current workspace): a non-emergency, unexpired `DocumentReader` row for the user exists.
- `visibility.private_post_readers() -> set[UUID]` (as system, in the current workspace): the users with such a row (one query).
- `visibility.forget_member(workspace_id: UUID, user_id: UUID) -> None` (as system: the user's reader rows, shares and department memberships in the workspace).
- `visibility.record_grant(person: Person, *, grantee: User, actor_user_id: UUID | None) -> int`: as system, an `access_granted` history line (`person`, `person_name`, `note` = grantee's name, `actor` = `actor_user_id`) on every restricted document of the person; returns the count.
- Part A's seams in `apps/workspaces/access.py`, filled in (signatures as part A defines them, `on_member_added` with the arguments part A now passes):
  - `person_requires_email(person: Person) -> bool`: `with system_actor(): return visibility.has_private_post(person)`.
  - `on_member_added(workspace_id: UUID, user_id: UUID, *, person_id: UUID | None, created_by_id: UUID | None) -> None`: with a `person_id`, as system `recompute_where(workspace_id=…, person_id=person_id)` and `record_grant(person, grantee=user, actor_user_id=created_by_id)`.
  - `on_member_removed(workspace_id: UUID, user_id: UUID) -> None`: `forget_member(workspace_id, user_id)`. No `recompute` is needed: part A's step 3 unlinked the person before the seam, and `forget_member` removed the user's shares and department memberships, so the desired reader set of every letter is exactly the old one without this user, which is what deleting their rows leaves.
  - New: `reads_private_post(workspace_id: UUID, user_id: UUID) -> bool` and `private_post_readers(workspace_id: UUID) -> set[UUID]`: each enters `workspace_context(workspace_id, actor=SYSTEM)` itself (callable outside any context, from `accounts.authority`), then calls the `visibility` function.

- [ ] **Step 1: Failing tests:**

```python
def test_has_private_post_counts_only_restricted_letters(): ...
def test_reads_private_post_ignores_emergency_and_expired_rows(): ...
def test_private_post_readers_is_one_query(django_assert_num_queries): ...
def test_forget_member_removes_rows_shares_and_department_memberships_in_that_workspace_only(): ...
def test_record_grant_writes_a_history_line_on_each_private_letter(): ...   # visible to Bob, not to Anne
def test_person_requires_email_when_the_person_has_private_post(): ...
def test_on_member_added_makes_the_linked_person_a_reader_and_records_the_grant(): ...  # access_granted actor = created_by
def test_on_member_removed_forgets_the_member(): ...
def test_seams_work_outside_any_workspace_context(): ...                    # reads_private_post called with no context -> True/False, no error
```

- [ ] **Step 2:** `make test` → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** `make test && make lint && make typecheck` → PASS.
- [ ] **Step 5:** Commit `feat(documents): grants, forgetting a member, and the private-post seams`.

### Task 9: Writes the user cannot see: deletes, "in use", archiving, repeated upload

**Files:**
- Modify: `backend/apps/people/services.py` (`_in_use`, `delete_person`, `update_person`), `backend/apps/people/api.py` (`deletePerson` min role admin), `backend/apps/labels/services.py` (`delete_label`), `backend/apps/rules/services.py` (`delete_rule`), `backend/apps/documents/intake.py`, `test_actor_callers.py` (+ `apps/people/services.py`, `apps/labels/services.py`, `apps/rules/services.py`, `apps/documents/intake.py`)
- Test: `backend/tests/test_privacy_deletes.py`; one case added to `test_people_api.py`, `test_labels_api.py`, `test_rules_api.py`, `test_documents_upload_api.py`; adjust the 1c case in `test_people_api.py` where a member deletes a person (now 403)

**Interfaces:**
- `delete_person(*, workspace, person_id, actor: AuditActor) -> None` (part A's signature, unchanged): rules lock and `_get_for_update` as the user (the authorisation), then `with system_actor():` `_in_use` (linked user, any addressee link, or a department with members) and `person.delete()`. Message unchanged (409 `person_in_use`), so it never says what the use is. Part A writes `person_deleted`; B adds nothing.
- `deletePerson` endpoint: `workspace_member(request, workspace_id, min_role=Membership.Role.ADMIN)` (ruling f): a plain member gets 403 `forbidden` for every person, so `person_in_use` on a person whose visible `document_count` is 0 is told only to admins. The OpenAPI description says so; the response codes are unchanged.
- `update_person` archiving: the delete of open suggestions runs inside `system_actor()` (suggestions on private letters the caller cannot see go too).
- `delete_label`: the rule checks as the user; `label.delete()` and the preset rule deletes inside `system_actor()`.
- `delete_rule`: `_get_for_update` as the user; `.delete()` inside `system_actor()`.
- `intake._find_existing`, the `IntegrityError` winner lookup and `_same_upload`'s page hashes run inside `system_actor()`; then, as the user, `visibility.can_read(existing.id)`; not readable → the same 409 `document_conflict` sentence as a different upload.

- [ ] **Step 1: Failing tests** (each with a letter private for Bob that Anne cannot read):

```python
def test_delete_label_used_on_someone_elses_private_letter_commits(): ...   # SET CONSTRAINTS ALL IMMEDIATE passes; the hidden DocumentLabel is gone; its event keeps label_name
def test_delete_rule_that_ran_on_a_private_letter_commits(): ...           # RuleRun gone, event.rule NULL, rule_name kept
def test_delete_person_on_a_private_letter_is_409_in_use_for_an_admin(): ...
def test_member_cannot_delete_any_person(client): ...                      # unused person and person with hidden use: both 403 forbidden, same body
def test_delete_unused_person_still_works_for_an_admin(): ...
def test_archiving_a_person_closes_suggestions_on_hidden_letters(): ...    # as Anne: open suggestion on Bob's private letter for that person -> gone (checked as SYSTEM)
def test_repeated_upload_of_own_letter_still_returns_it(): ...
def test_repeated_upload_of_a_letter_now_private_for_others_is_409(): ...  # body has no sender, no id
```

- [ ] **Step 2:** `make test` → FAIL (IntegrityError at commit, 500, or 200 with the letter).
- [ ] **Step 3:** Implement; `make openapi` (description only), `pnpm gen:api`.
- [ ] **Step 4:** `make test && make lint && make typecheck` → PASS.
- [ ] **Step 5:** Commit `fix: deletes, archiving and repeated uploads see private letters as the system`.

### Task 10: Automatic actions never widen the readers

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

### Task 11: Departments

**Files:**
- Modify: `backend/apps/people/models.py`, `services.py`, `api.py`, `schemas.py`, `backend/apps/documents/visibility.py` (department readers in `desired_readers`), `docs/GLOSSARY.md` (afdeling = department, afdelingslid = department member)
- Create: `backend/apps/people/migrations/0003_departments.py`, `backend/tests/test_people_departments.py`

**Interfaces:**
- `Person.kind = CharField(max_length=20, choices=Kind.choices, default="person")` (`PERSON`, `DEPARTMENT`); no `db_default`. `Person.save` raises `ValueError` for a department with a `user`.
- `DepartmentMember(WorkspaceModel)`: `department` FK `Person` CASCADE `related_name="department_members"`; `user` FK user CASCADE `related_name="+"`; unique `people_departmentmember_unique` (`department`, `user`); `same_workspace_fields = ("department",)`; `save` raises `ValueError` when the department is not `kind="department"` or the user is not a member. Migration (depends on `("people", "0002_persons_for_members")`) ends with `enable_rls("people_departmentmember")`.
- `people.services.add_department_member(*, workspace: Workspace, person_id: UUID, user_id: UUID, actor: AuditActor) -> Person` and `remove_department_member(*, workspace: Workspace, person_id: UUID, user_id: UUID, actor: AuditActor) -> Person`. Lock order: `lock_own_membership(workspace.id, actor.user_id)`, then the department `select_for_update(no_key=True)`, then readers. 404 `person_not_found`, 404 `member_not_found`, 409 `not_a_department` ("Dit is geen afdeling."); add: `user_id == actor.user_id` and `visibility.has_private_post(person)` → 409 `cannot_grant_self` ("Je kunt jezelf geen toegang geven tot privé-post. Gebruik een uitnodiging of noodtoegang."); then, as system, `recompute_where(person_id=)` (removing oneself is allowed and needs no `keep_own_access`, ruling c), `record_grant` (add only), audit `department_member_added` / `department_member_removed` (target the department, values `{"member": <user's name>}`). Adding an existing member answers 200 unchanged.
- `link_member` on a department: 409 `is_a_department` ("Een afdeling koppel je niet aan één account. Voeg leden toe."). `ensure_person_for_member` and `list_members` only look at `kind="person"`. `_in_use` counts department members.
- Schemas (additive): `PersonOut.kind: str = "person"`, `PersonOut.member_user_ids: list[UUID] = Field(default_factory=list)`; `PersonIn.kind: str = "person"` read on create only (`updatePerson` ignores it, so a 1c client editing a department keeps it a department); any other value → 422.
- Endpoints (admin): `addDepartmentMember` `POST /workspaces/{workspace_id}/persons/{person_id}/members` body `DepartmentMemberIn {user_id: UUID}` → 200 `PersonOut`; `removeDepartmentMember` `DELETE /workspaces/{workspace_id}/persons/{person_id}/members/{user_id}` → 200 `PersonOut`. Errors as above plus `SESSION_WRITE_ERRORS` / `BODY_ERRORS`. `cannot_grant_self` is admin-only by construction (ruling f).

- [ ] **Step 1: Failing tests:**

```python
def test_create_department_and_add_members(): ...                          # kind department, member_user_ids
def test_department_cannot_be_linked_to_an_account(): ...                  # 409 is_a_department
def test_private_letter_for_department_is_read_by_its_members(): ...
def test_readers_after_department_member_added_and_removed(): ...
def test_admin_cannot_add_self_to_department_with_private_post(): ...      # 409 cannot_grant_self
def test_admin_may_add_self_to_department_without_private_post(): ...
def test_admin_may_remove_self_from_a_department_and_loses_its_letters(): ... # 200; no would_lose_access
def test_grant_writes_audit_and_history_on_each_private_letter(): ...      # access_granted visible to Bob, not to Anne; audit values {"member": "Bob"}
def test_a_department_member_counts_as_a_reader_for_automatic_make_private(): ...  # manual run, scanner gone, department with a member -> made_private
def test_name_matching_and_rules_work_for_a_department(): ...              # variant "Afd. Finance" proposes the department
def test_update_person_without_kind_keeps_a_department(): ...
def test_delete_department_with_members_is_in_use(): ...
```

- [ ] **Step 2:** `make test` → FAIL.
- [ ] **Step 3:** Implement; `make openapi`, `cd web && pnpm gen:api`.
- [ ] **Step 4:** `make test && make lint && make typecheck && (cd web && pnpm build)` → PASS.
- [ ] **Step 5:** Commit `feat(people): departments as persons with members`.

### Task 12: Linking a person is a grant

**Files:**
- Modify: `backend/apps/people/services.py` (`link_member`), `backend/apps/people/api.py` (`linkPersonToMember` min role becomes viewer; the service checks)
- Test: `backend/tests/test_people_grants.py`; adjust the 1c cases in `test_people_api.py` that unlink another member's link (they now expect 409)

**Interfaces:**
- `link_member(*, workspace: Workspace, person_id: UUID, user_id: UUID | None, actor: AuditActor, actor_role: str) -> Person` (part A's `actor: AuditActor` plus `actor_role`). Lock order: `lock_own_membership(workspace.id, actor.user_id)`, then the person `select_for_update(no_key=True)`.
  - linking (`user_id` set) needs `actor_role == "admin"` (else 403 `forbidden`); `user_id == actor.user_id` and `has_private_post(person)` → 409 `cannot_grant_self` (admin-only by construction, ruling f);
  - when the person is linked to another user U (unlink or relink): only U may (`actor.user_id == U`), else 409 `cannot_unlink` ("Alleen %(name)s kan deze koppeling losmaken.");
  - then, as system, `recompute_where(person_id=)`, **without** `keep_own_access`: unlinking oneself is deliberate and allowed (ruling c); `record_grant` on link. Part A's `person_linked` / `person_unlinked` rows stay the only audit rows.

- [ ] **Step 1: Failing tests:**

```python
def test_admin_cannot_link_self_to_person_with_private_post(): ...   # 409 cannot_grant_self, no reader row
def test_admin_links_another_member_and_they_become_reader(): ...    # history access_granted on each private letter
def test_admin_cannot_unlink_another_member(): ...                   # 409 cannot_unlink
def test_member_unlinks_self_and_loses_the_letters(): ...            # as Bob (viewer role suffices): 200, Bob no reader, no 409
def test_relinking_to_another_account_moves_the_readers(): ...       # by the linked user
def test_viewer_cannot_link(): ...                                   # 403 forbidden
def test_link_writes_one_audit_row(): ...                            # exactly one person_linked row (part A's)
```

- [ ] **Step 2:** `make test` → FAIL.
- [ ] **Step 3:** Implement; `make openapi` (the description changes only) and `pnpm gen:api`.
- [ ] **Step 4:** `make test && make lint && make typecheck` → PASS.
- [ ] **Step 5:** Commit `feat(people): linking a person is an access grant`.

### Task 13: Part A's seams: removing a member, invitations, the own export

**Files:**
- Modify: `backend/apps/workspaces/members.py` (`remove_member` steps 3–4 inside `system_actor()`), `backend/apps/documents/member_export.py` (`member_document_ids`), `test_actor_callers.py` (+ `apps/workspaces/members.py`)
- Test: `backend/tests/test_privacy_members.py`

**Interfaces:**
- `remove_member(*, request, workspace, user_id, actor: AuditActor, left: bool) -> None` (part A, unchanged signature): steps 3 (unlink the person, unassign tasks, `access.on_member_removed`, revoke what the admin made) and 4 (delete the membership, deactivate) run inside `with system_actor():`, so tasks on private letters the admin cannot see are unassigned too, and `on_member_removed` (Task 8) removes reader rows, shares and department memberships. Lock order: step 1's `lock_memberships` (all, `FOR UPDATE`) comes first, as Global Constraints require.
- `accept_invitation` (part A) calls `access.on_member_added(workspace_id, user_id, person_id=…, created_by_id=invitation.created_by_id)` (part A edit, see "Seams with part A"); Task 8 filled it in. Part A writes `person_linked`; B adds no row.
- `create_invitation` (part A) calls `access.person_requires_email(person)`; Task 8 filled it in, so an invitation for a person with private post without `email` answers part A's 422 `email_required`, and part A's `invitation_email_mismatch` makes only that address accept it.
- `member_export.member_document_ids(workspace_id: UUID, user_id: UUID) -> list[UUID]`: part A's query (documents with an addressee linked to the member) **plus** restricted documents with a non-emergency, unexpired reader row for the member, read through `export_context(workspace_id, reader_id=user_id)` (so every id is one the member can read; RLS proves it).

- [ ] **Step 1: Failing tests:**

```python
def test_removed_member_loses_reader_rows_shares_and_departments(): ...
def test_private_post_of_removed_member_stays_locked(): ...          # Anne (admin) still cannot read it
def test_task_on_hidden_private_letter_is_unassigned_on_removal(): ...
def test_member_of_two_workspaces_keeps_readers_in_the_other(): ...
def test_reinvited_with_this_is_person_reads_again(): ...            # access_granted line, actor the inviting admin
def test_invitation_for_person_with_private_post_needs_email(): ...  # 422 email_required
def test_invitation_for_person_with_private_post_accepts_only_that_address(): ...  # 403 invitation_email_mismatch
def test_own_export_contains_letters_on_their_name_and_their_private_letters(): ...
def test_own_export_omits_letters_the_member_cannot_read(): ...      # Anne's export lacks Bob's private letter
```

- [ ] **Step 2:** `make test` → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** `make test && make lint && make typecheck` → PASS.
- [ ] **Step 5:** Commit `feat(workspaces): removing a member takes their access to private post`.

### Task 14: Account actions on a reader of private post belong to the server admin

Ruling e (provisional, D 8.5): the owner has been asked; this is the default until they answer.

**Files:**
- Modify: `backend/apps/accounts/authority.py` (`account_authority`), `backend/apps/workspaces/services.py` (`list_members`: `can_manage_account`)
- Test: `backend/tests/test_privacy_account_actions.py`

**Interfaces:**
- `account_authority(actor: User, target_id: UUID) -> AccountAuthority` (part A's signature): after part A's checks, when the actor is not a server admin and `access.reads_private_post(<the target's only workspace>, target_id)` → `ApiError(403, "server_admin_only", "Dit kan alleen de serverbeheerder, omdat dit lid privé-post kan lezen.")`. Applies to everything that uses it: `createPasswordReset`, `endUserSessions`, `listUserDevices`, `revokeUserDevice`, and the re-check when a reset link is completed (part A), so a link made before the member became a reader stops working.
- `list_members(workspace: Workspace, *, viewer: User) -> list[MemberOut]`: `can_manage_account` also False for members in `access.private_post_readers(workspace.id)` when the viewer is not a server admin (one extra query).
- The answer reveals to a workspace admin that the member reads private post: accepted (ruling f), written in `docs/PRIVACY.md` (Task 17).

- [ ] **Step 1: Failing tests:**

```python
def test_workspace_admin_cannot_reset_a_reader_of_private_post(client): ...   # single membership, reader -> 403 server_admin_only; no PasswordReset, no notice
def test_workspace_admin_cannot_end_sessions_or_revoke_devices_of_a_reader(client): ...
def test_server_admin_may_act_and_the_member_gets_the_notice(client): ...
def test_workspace_admin_may_still_reset_a_member_without_private_post(client): ...
def test_reset_link_stops_when_the_member_became_a_reader(client): ...        # completing -> 400 reset_link_invalid
def test_emergency_rows_do_not_make_a_reader(client): ...                     # an admin's emergency row on Bob's letters does not lock the admin's own account
def test_list_members_hides_account_actions_for_readers(client): ...          # can_manage_account False for Bob
```

- [ ] **Step 2:** `make test` → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** `make test && make lint && make typecheck` → PASS.
- [ ] **Step 5:** Commit `feat(accounts): account actions on a reader of private post need the server admin`.

### Task 15: Workspace settings: Zorg and the emergency-access switch

**Files:**
- Modify: `backend/apps/workspaces/models.py`, `settings_service.py` (part A's), `services.py`, `api.py`, `schemas.py`
- Create: `backend/apps/workspaces/migrations/0006_privacy_settings.py`, `backend/tests/test_workspaces_privacy_settings.py`

**Interfaces:**
- `Workspace.care_is_private = BooleanField(default=False)`, `Workspace.emergency_access = BooleanField(default=False)`. Migration `0006_privacy_settings` (depends on `("workspaces", "0005_actor_functions")`): add fields, then a data step: `home` → `care_is_private=True, emergency_access=False`; `business` → `False, True`. `create_workspace` sets the same per kind.
- Part A's settings, extended (no new settings endpoint): `WorkspaceSettingsOut.care_is_private: bool = False`, `WorkspaceSettingsOut.emergency_access: bool = False`; `WorkspaceSettingsIn.care_is_private: bool | None = None` (None leaves it unchanged; part A's `allow_member_export` is `bool | None = None` as well, so a part A client that sends only `allow_member_export` never wipes a part B setting). `updateWorkspaceSettings` (admin) writes `workspace_setting_changed {"setting": "care_is_private", "value": …}` when it changes; it changes no existing letter. `emergency_access` is not in `WorkspaceSettingsIn`.
- `setEmergencyAccess` `PUT /workspaces/{workspace_id}/emergency-access`, `auth=session_auth`, body `EmergencyAccessIn {enabled: bool}` → `EmergencyAccessOut {enabled: bool}`; only `user.is_server_admin` (403 `forbidden`), membership not required; unknown workspace 404 `workspace_not_found`; enters `workspace_context(ws, actor=user.id)`; `select_for_update(no_key=True)` on the workspace; audit `emergency_access_switched_on` / `emergency_access_switched_off` (target the workspace); `notify(member, "emergency_access_switched_on" | "emergency_access_switched_off", actor_name=user.name, values={"workspace": ws.name})` for every member; setting it to its current value writes nothing.

- [ ] **Step 1: Failing tests:**

```python
def test_defaults_per_kind_for_new_and_existing_workspaces(): ...   # migration function called directly, as part A's migration tests do
def test_admin_switches_care_is_private(): ...
def test_member_cannot_switch_care_is_private(): ...               # 403
def test_a_part_a_body_keeps_care_is_private(): ...                # PUT {"allow_member_export": false} -> care_is_private unchanged
def test_an_empty_body_changes_nothing(): ...                      # PUT {} -> 200, no audit row
def test_only_a_server_admin_switches_emergency_access(): ...      # workspace admin 403
def test_switching_emergency_access_notifies_every_member(): ...   # AccountNotice code emergency_access_switched_on per member
def test_switching_emergency_access_is_audited(): ...
```

- [ ] **Step 2:** `make test` → FAIL.
- [ ] **Step 3:** Implement; `make openapi`, `pnpm gen:api`.
- [ ] **Step 4:** `make test && make lint && make typecheck && (cd web && pnpm build)` → PASS.
- [ ] **Step 5:** Commit `feat(workspaces): Zorg and emergency-access settings`.

### Task 16: The leak suite (D 3 anti-goals, D 15), before any letter becomes private in normal use

Written now, against letters made private by `private_letter` (Task 7), so that Task 17 onwards must keep it green. Every failure is a leak to fix in the owning task's code before committing.

**Files:**
- Create: `backend/tests/test_privacy_leaks.py`, `backend/tests/test_privacy_backup.py`

**Interfaces:** none new. The tests use the API as Anne (admin) and as Carla (member) against a letter private for Bob (addressee Bob, scanned by Bob, labelled, one task, one open suggestion, one rule run, one event, one page), and a public letter as control.
- `DOCUMENT_OPERATION_CASES: dict[str, Callable[[Client, UUID, UUID], HttpResponse]]` (operation id → a call with a minimal valid body for workspace id and document id); `document_operations_from_schema() -> set[str]`: every operation in `api.get_openapi_schema()` whose path contains `{document_id}`.

- [ ] **Step 1: Write the tests:**

```python
def test_every_document_operation_has_a_case(): ...
    # document_operations_from_schema() == set(DOCUMENT_OPERATION_CASES); today: getDocument, getDocumentPage,
    # retryDocumentProcessing, confirmDocument, setDocumentAddressees, setDocumentLabels, acceptSuggestion,
    # dismissSuggestion, listDocumentEvents, undoDocumentEvent (Task 17 adds setDocumentVisibility)
@pytest.mark.parametrize("operation", sorted(DOCUMENT_OPERATION_CASES))
@pytest.mark.parametrize("who", ["admin", "member"])
def test_every_document_operation_answers_a_hidden_letter_like_an_unknown_id(operation, who): ...
    # status and JSON body for the hidden letter == for uuid4(); nothing changed (checked as SYSTEM)
def test_post_list_omits_it(): ...                 # listDocuments
def test_inbox_counts_omit_it(): ...               # getInboxCounts equals the counts without it
def test_person_filter_omits_it(): ...             # listDocuments?person_id=<Bob's person>
def test_persons_document_count_omits_it(): ...    # listPersons as Anne
def test_labels_document_count_omits_it(): ...     # listLabels as Anne: its label's document_count excludes it
def test_rule_test_omits_it(): ...                 # testRule with a phrase from its text: total and matched exclude it
def test_manual_rule_run_shows_nothing_of_it(): ...  # runRule; Anne's listRules counts exclude it; Bob sees the event
def test_its_task_is_not_listed_or_completable(): ...  # listTasks (the calendar's source), completeTask/reopenTask -> 404 like an unknown task id
def test_its_suggestion_cannot_be_accepted_or_dismissed(): ...
def test_audit_log_names_it_by_id_only(): ...      # every audit row: no sender, summary, text or label name in values or target_name
def test_export_omits_it(): ...                    # part A's own export as Anne (built inline)
def test_job_that_forgets_the_actor_sees_nothing_of_it(): ...  # workspace_context(actor=NOBODY): count excludes it, children 0
def test_member_cannot_learn_of_it_by_deleting_the_person(): ...  # Carla deletePerson on Bob's person -> 403 forbidden, same as an unused person
```

  In `test_privacy_backup.py` (read-only use of `apps.backup`; the back-up role reads committed rows only):

```python
@pytest.mark.django_db(transaction=True)
def test_storage_sweep_keeps_a_private_letters_pages(backup_conninfo, tmp_path): ...  # find_orphans lists none of its files
@pytest.mark.django_db(transaction=True)
def test_manifest_counts_private_letters(backup_conninfo): ...                         # documents and pages per workspace include it
```

- [ ] **Step 2:** `make test` → PASS (or fix in the owning code and rerun).
- [ ] **Step 3:** Commit `test: private post leaks nowhere`.

---

## Slice B3: making letters private

### Task 17: Private by hand and by the Zorg label

From this commit on, letters become private in normal use; every earlier task closed a write path that would otherwise break or leak, and Task 16's suite guards it.

**Files:**
- Modify: `backend/apps/documents/organise.py` (Zorg hook), `services.py`, `api.py`, `schemas.py`, `backend/apps/rules/services.py` (`test_rule` summaries), `backend/tests/test_privacy_leaks.py` (case for `setDocumentVisibility`), `docs/PRIVACY.md`, `docs/GLOSSARY.md` (privé = restricted, lezer = reader, delen = share)
- Test: `backend/tests/test_documents_visibility_api.py`

**Interfaces:**
- `organise.add_label`: after the link, when `label.system_key == "zorg"` and the workspace's `care_is_private` and the document is public: `visibility.make_private(document, actor=<the audit actor passed in, or AuditActor.system() for a rule>, origin=origin, rule=rule)`. A `"nobody_could_read"` result keeps the label and leaves the letter public (ruling d). Removing the label changes nothing.
- `documents.services.document_summaries_out(documents: Sequence[Document]) -> list[DocumentSummaryOut]` (one `reader_names` call); `listDocuments`, `createDocument` and `testRule` use it; `document_out` fills the new fields.
- Schemas (additive): `DocumentSummaryOut.visibility: str = "workspace"`, `DocumentSummaryOut.reader_names: list[str] = Field(default_factory=list)`; `DocumentOut.shared_user_ids: list[UUID] = Field(default_factory=list)`, `DocumentOut.can_make_public: bool = False`; `DocumentEventOut.note: str = ""`.
- `services.set_document_visibility(*, workspace: Workspace, user: User, actor: AuditActor, document_id: UUID, visibility: str, shared_user_ids: list[UUID]) -> DocumentVisibilityOut`: `_lock_for_organising` (membership `FOR SHARE`, then the document `no_key`); `restricted` → `make_private(share_user_ids=…)` when public, else `set_shares(user_ids=…)`; `workspace` → `make_public` (non-empty `shared_user_ids` → 422); other values → 422.
- Endpoint `setDocumentVisibility` `PUT /workspaces/{workspace_id}/documents/{document_id}/visibility`, member role, body `DocumentVisibilityIn {visibility: str, shared_user_ids: list[UUID] = Field(default=[], max_length=50)}` → 200 `DocumentVisibilityOut {visibility: str, reader_names: list[str], shared_user_ids: list[UUID], can_read: bool}`; errors 404 `document_not_found`, 404 `member_not_found`, 403 `cannot_make_public`, 409 `document_processing`, 422.
- `docs/PRIVACY.md`, section "Privé-post" (ruling e iv, ruling f): what private protects against (other members of the workspace; reading unnoticed: every access by an admin, a grant, an emergency opening, a reset link or a session ended by an admin leaves a history line or a notice the member sees) and what it does not (whoever administers the server machine: database, master key, back-ups; the AI service, which reads a letter while processing it, before it can be private; page files sealed per workspace, not per reader). That a workspace admin can learn **that** somebody has private post (not what) from four answers: `person_in_use`, `cannot_grant_self`, `email_required`, and `server_admin_only` for a reader of private post; members cannot (a member cannot delete persons). That an invitation for a person with private post needs an email address, which the server does not verify: an admin who controls that address could accept it, and the grant then shows as a history line on every private letter of that person. The audit log names private letters by id only.

- [ ] **Step 1: Failing tests:**

```python
def test_zorg_label_by_hand_makes_the_letter_private_for_actor_and_scanner(): ...
def test_zorg_label_by_accepting_a_suggestion_makes_it_private(): ...
def test_zorg_suggestion_alone_does_not(): ...
def test_zorg_label_in_a_business_workspace_stays_public(): ...      # care_is_private False
def test_zorg_label_by_a_pipeline_rule_without_any_reader_stays_public(): ...  # scanner removed meanwhile: private_skipped, admin notice
def test_set_visibility_restricted_then_lists_show_lock_fields(): ...  # listDocuments item visibility "restricted", reader_names ["Anne", "Bob"]
def test_set_visibility_shares_with_a_member(): ...
def test_set_visibility_share_with_non_member_is_404(): ...
def test_removing_own_share_answers_can_read_false(): ...
def test_make_public_by_non_addressee_is_403_and_nothing_changes(): ...
def test_viewer_cannot_change_visibility(): ...                      # 403 forbidden
def test_non_reader_gets_404_for_the_letter(): ...                   # same body as an unknown id
def test_1c_shaped_client_reads_a_private_letter_without_new_fields(): ...  # response validates against the openapi_1b and openapi_1c DocumentSummaryOut
```

- [ ] **Step 2:** `make test` → FAIL.
- [ ] **Step 3:** Implement; add the `setDocumentVisibility` case to `DOCUMENT_OPERATION_CASES`; `make openapi`, `pnpm gen:api`.
- [ ] **Step 4:** `make test && make lint && make typecheck && (cd web && pnpm build)` → PASS (the leak suite included).
- [ ] **Step 5:** Commit `feat(documents): private letters by hand and by the Zorg label`.

### Task 18: Confirm with visibility

**Files:**
- Modify: `backend/apps/documents/review.py`, `schemas.py`
- Test: `backend/tests/test_documents_confirm_private.py`, `backend/tests/test_privacy_concurrency.py`

**Interfaces:**
- `ConfirmIn.visibility: str = ""`: `""` leaves it as it is (a Zorg label in the confirmed labels still makes it private), `"restricted"`, `"workspace"`; anything else 422.
- Order inside `confirm_document` (all in its `atomic`): `lock_own_membership` → the document `select_for_update(no_key=True)` → validate → write the confirmed fields with `save(update_fields=[…])` → `_set_organisation` (labels first: a Zorg label makes the letter private with the confirmer's share; then addressees inside `keep_own_access` → `recompute`) → `record_confirmed` → tasks → `restricted`: `make_private(actor=actor_from_request(request), origin="user")`; `workspace` on a restricted letter: `make_public` → rules (`_rules_guarded`, as the user). `_is_repeat` ignores `visibility`.
- The membership `FOR SHARE` taken first is what serialises a concurrent removal (part A's `lock_memberships` takes `FOR UPDATE`), in the one lock order.

- [ ] **Step 1: Failing tests:**

```python
def test_confirm_with_zorg_label_stores_everything_and_is_private(): ...   # fields, tasks, confirmed event, readers {confirmer, scanner}
def test_confirm_with_visibility_restricted(): ...
def test_confirm_workspace_on_zorg_letter_by_addressee_is_public(): ...
def test_confirm_workspace_by_non_addressee_is_403_and_stores_nothing(): ...  # document still needs_review, no tasks
def test_confirm_removing_own_addressee_on_a_private_letter_is_409(): ...   # would_lose_access, nothing stored
def test_confirm_without_visibility_field_behaves_as_1c(): ...
def test_repeated_confirm_of_a_private_letter_is_200(): ...
def test_rules_at_confirm_run_as_the_user(): ...                             # an auto addressee rule after Zorg leaves a suggestion
```

  `test_privacy_concurrency.py` (two database connections, threads, `threading.Barrier`/`Event`; each thread closes its connection; rows cleaned up at the end):

```python
@pytest.mark.django_db(transaction=True)
def test_confirm_with_zorg_and_removing_a_member_at_once_never_deadlock(): ...
    # Thuis: Anne admin, Bob member, Carla member; a needs_review letter scanned by Carla, Zorg label chosen.
    # Thread 1: confirmDocument as Bob; a monkeypatched pause in visibility.make_private after the document lock
    # (an Event) holds it while thread 2 runs removeMember(Carla) as Anne. 20 rounds.
    # Neither thread raises a DatabaseError whose __cause__.sqlstate is "40P01"; thread 2 waits (on the membership lock) and then succeeds;
    # after each round: Carla no member, no DocumentShare and no DocumentReader row for Carla, Bob a reader, the letter restricted.
@pytest.mark.django_db(transaction=True)
def test_removal_first_then_confirm_writes_no_share_for_the_removed_member(): ...
    # a pause in access.on_member_removed holds the memberships lock; confirmDocument as Bob waits at lock_own_membership,
    # then completes: readers {Bob}; no row for Carla; no 40P01
@pytest.mark.django_db(transaction=True)
def test_two_confirms_by_one_user_do_not_block_each_other_on_the_membership(): ...  # FOR SHARE + FOR SHARE: both complete within 2 s
```

- [ ] **Step 2:** `make test` → FAIL.
- [ ] **Step 3:** Implement; `make openapi`, `pnpm gen:api`.
- [ ] **Step 4:** `make test && make lint && make typecheck` → PASS.
- [ ] **Step 5:** Commit `feat(documents): confirm can make a letter private or public`.

### Task 19: The rule action "privé maken" (engine, API, web editor)

**Files:**
- Modify: `backend/apps/rules/engine.py`, `definition.py`, `facts.py`, `executor.py`, `services.py`, `schemas.py` (comment only), `web/src/routes/rule-editor.tsx`, `web/src/components/rule-sentence.ts`, `docs/DECISIONS.md` (new action type in version 1; a 1c server skips such a rule whole)
- Test: `backend/tests/test_rules_engine.py`, `test_rules_definition.py`, `test_rules_private.py`, `test_openapi_contract.py`; `web/src/routes/rule-editor.test.tsx`, `web/src/components/rule-sentence.test.ts`

**Interfaces:**
- Engine: `ACTIONS = frozenset({"add_label", "add_addressee", "make_private"})`; `Action.target_id: UUID | None` (None only for `make_private`); `PlannedAction.target_id: UUID | None`; `DocumentFacts.restricted: bool = False`. `make_private` is planned only when the rule is `auto` and matches the settled state and the document is not restricted (and not planned already in this run); never as `propose`. Once planned, later `add_addressee` actions in the same run are planned `propose`.
- Definition: `VERSION` stays 1. `validate(..., mode: str)` gains `mode`; `make_private` with a label or person id → 422; in a `propose` rule → 422 `validation_error` ("Privé maken kan alleen in een regel die automatisch werkt."). `to_json` writes `{"type": "make_private", "label_id": null, "person_id": null}`; `_parse_action` reads it. `rules.services._fill` passes the mode.
- Facts: `facts_for` / `facts_for_many` fill `restricted`.
- Executor: `make_private` → `visibility.make_private(document, actor=AuditActor.system(), origin="rule", rule=rule)` (the request's user gets a share through `current_actor()`; in the pipeline and a manual run the scanner, addressees with an account and department members). Result `"made_private"` → `RuleRun` outcome `applied`; `"nobody_could_read"` → no `RuleRun` row for that action (nothing changed), the history line and the admin notice of Task 7 instead (ruling d).
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
def test_manual_run_on_a_letter_nobody_could_read_leaves_it_public(): ... # scanner removed, no addressee: no RuleRun, private_skipped, one admin notice
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

### Task 20: Emergency access

**Files:**
- Modify: `backend/apps/documents/visibility.py` (`open_emergency`, `require_regular_reader`), `backend/apps/documents/services.py` (`_lock_for_organising`, `retry_processing`, `set_document_visibility`), `review.py`, `backend/apps/tasks/services.py`, `backend/apps/people/api.py`, `schemas.py`, `backend/tests/test_privacy_leaks.py` (the emergency case), `docs/GLOSSARY.md` (noodtoegang = emergency access)
- Test: `backend/tests/test_privacy_emergency.py`

**Interfaces:**
- `visibility.open_emergency(*, workspace: Workspace, person: Person, actor: AuditActor, reason: str) -> EmergencyResult` (`@dataclass(frozen=True) class EmergencyResult: opened_count: int; expires_at: datetime`): `workspace.emergency_access` False → `ApiError(409, "emergency_access_off", "Noodtoegang staat uit in deze werkruimte.")` before anything else; reason `mask_bsns(reason).strip()` of 10–200 characters else 422; lock order: `lock_own_membership(workspace.id, actor.user_id)`, then readers (no document lock: no document row changes); as system: for every restricted document with the person (or department) as addressee, an `emergency` reader row for the actor with `expires_at = now() + 1 hour` (update when it exists), an `emergency_opened` history line (`actor`, `note` = reason); audit `emergency_access_used {"reason": reason, "document_count": n}` on the person (target kind `person`); `notify(u, "emergency_access_used", actor_name=<admin's name>, values={"person": person.display_name, "count": str(n)})` for every user linked to the person or member of the department, when `n > 0`.
- `visibility.require_regular_reader(document: Document, user: User) -> None`: on a restricted document, the user needs a non-emergency, unexpired reader row; else `ApiError(403, "emergency_read_only", "Met noodtoegang kun je deze brief alleen lezen.")`. Called by `_lock_for_organising` (labels, addressees, suggestions accept and dismiss, undo), `retry_processing` (so a failed private letter is never sent to the AI again by an emergency reader), `set_document_visibility`, `confirm_document`, and the task complete/reopen services.
- Endpoint `openEmergencyAccess` `POST /workspaces/{workspace_id}/persons/{person_id}/emergency-access`, admin, body `EmergencyAccessUseIn {reason: str = Field(min_length=10, max_length=200)}` → 200 `EmergencyAccessUseOut {opened_count: int, expires_at: datetime}`; errors 404 `person_not_found`, 409 `emergency_access_off`, 422. (200 characters: part A's audit cuts strings at 200, so the log keeps the whole reason.)

- [ ] **Step 1: Failing tests:**

```python
def test_off_means_no_path(): ...                                    # 409 emergency_access_off, no rows, no audit, no notice
def test_reason_of_nine_characters_is_422(): ...
def test_reason_of_two_hundred_and_one_characters_is_422(): ...
def test_on_opens_the_persons_private_letters_for_one_hour(): ...    # Anne reads them; expires_at ~ now+1h
def test_each_opened_letter_shows_it_in_its_history_to_readers(): ...  # Bob sees emergency_opened with the reason
def test_the_person_concerned_gets_a_notice(): ...                   # Bob: emergency_access_used, values person and count
def test_use_is_audited_with_reason_and_count(): ...
def test_department_post_opens_too(): ...
def test_expired_access_hides_the_letter_again(): ...                # expires_at moved to the past: 404 document_not_found
def test_emergency_reader_cannot_organise_share_publish_or_retry(): ...  # labels, addressees, accept, dismiss, undo, visibility, confirm,
                                                                         # retryDocumentProcessing (failed letter), task complete: 403 emergency_read_only
def test_emergency_rows_survive_recompute_and_go_with_make_public(): ...
def test_reader_names_do_not_list_the_emergency_reader(): ...
```

  In `test_privacy_leaks.py`: `test_emergency_reader_cannot_resend_a_failed_private_letter` (retryDocumentProcessing → 403, `status` still `failed`, no job deferred).

- [ ] **Step 2:** `make test` → FAIL.
- [ ] **Step 3:** Implement; `make openapi`, `pnpm gen:api`.
- [ ] **Step 4:** `make test && make lint && make typecheck` → PASS.
- [ ] **Step 5:** Commit `feat(documents): emergency access to a person's private post`.

### Task 21: The back-up check knows the visibility policies

For the back-up code owners (ruling i; see "Seams with part A and the back-up code"): a restore without the helper functions or the policies must not pass `verify_restore`, because the counts alone would agree.

**Files:**
- Modify: `backend/apps/backup/verify.py` (`_read`, `verify_restore`)
- Test: `backend/tests/test_backup_verify.py` (new cases)

**Interfaces:**
- `_Restored` gains `privacy_expected: bool`, `helpers_present: int`, `tables_missing_policies: list[str]`, read in the same read-only transaction:
  - `privacy_expected`: `SELECT EXISTS (SELECT 1 FROM django_migrations WHERE app = 'workspaces' AND name = '0005_actor_functions')` (a back-up from before part B is not checked for them);
  - `helpers_present`: `SELECT count(*) FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'public' AND p.proname IN ('brievie_actor_id', 'brievie_is_system')`;
  - `tables_missing_policies`: for `documents_document` and every table with a foreign key to it except `documents_documentreader` (`SELECT DISTINCT conrelid::regclass::text FROM pg_catalog.pg_constraint WHERE contype = 'f' AND confrelid = 'documents_document'::regclass`), the tables whose `SELECT count(*) FROM pg_catalog.pg_policy WHERE polrelid = <t>::regclass AND polname IN ('document_visibility', 'document_visibility_update', 'document_visibility_delete') AND NOT polpermissive` is not 3; plus `documents_documentreader` when it has no `reader_scope` policy.
- `verify_restore`: when `privacy_expected`, `helpers_present != 2` adds the problem "De database mist de functies voor privé-post."; each missing table adds "De database mist de privacyregels voor tabel %(table)s." (a table name, no person or document data).

- [ ] **Step 1: Failing tests:**

```python
def test_restore_with_the_policies_passes(restored_db): ...
def test_restore_without_a_helper_function_fails(restored_db): ...     # DROP FUNCTION public.brievie_is_system() CASCADE in the restored db -> problem listed
def test_restore_without_a_visibility_policy_fails(restored_db): ...   # DROP POLICY document_visibility_delete ON documents_documentlabel -> problem names the table
def test_a_back_up_from_before_part_b_is_not_checked_for_them(restored_db): ...  # delete the django_migrations row and the functions -> no privacy problem
```

  (`restored_db`: the fixture the existing verify tests use for a second database.)

- [ ] **Step 2:** `make test` → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** `make test && make lint && make typecheck` → PASS.
- [ ] **Step 5:** Commit `feat(backup): verify a restore keeps the private-post policies`.

---

## Slice B4: the web

Fluent 2 building blocks from `web/src/components/ui.tsx` (`MessageBar`, `Badge`, `Persona(s)`, `EmptyState`) and React Aria Components dialogs as in `persons.tsx`; Dutch B1, je-form; tests query by role; `pnpm extract` after every text change.

### Task 22: The lock in lists and on the letter

**Files:**
- Create: `web/src/components/private-lock.tsx`, `private-lock.test.tsx`
- Modify: `web/src/components/icons.tsx` (`lock`), `document-table.tsx`, `document-history.tsx`, `web/src/routes/document.tsx`, `post.tsx`, `inbox.tsx`, `automations.tsx` (rule test results), `web/src/test-utils.tsx` (fixtures with `visibility`), tests next to each

**Interfaces:**
- `<PrivateLock readerNames={string[]} variant="row" | "page" />`: `row` is a `Badge` "Privé" with the reader sentence as its accessible description; `page` is a `MessageBar intent="info"` "Privé voor Anne en Bob" ("Privé voor Anne, Bob en Carla"; "Privé" alone when the list is empty).
- History sentences for the new kinds: "<naam> maakte deze brief privé", "… maakte deze brief weer zichtbaar voor iedereen", "… deelde deze brief met <naam>", "… haalde <naam> weg bij delen", "<naam> kan deze brief nu lezen (gekoppeld aan <persoon>)", "<beheerder> opende deze brief met noodtoegang: <reden>", and for `private_skipped` the event's `note`.
- A server without `visibility` (1c): no lock (the fields are optional).

- [ ] **Step 1: Failing tests:**

```tsx
test("a private letter in the post list shows Privé")             // within(row).getByText("Privé")
test("the letter page says who may read it")                       // getByText("Privé voor Anne en Bob")
test("a public letter has no lock")
test("history tells who opened it with emergency access and why")
test("history explains a letter that could not be made private")
test("a letter without visibility from an old server shows no lock")
```

- [ ] **Step 2:** `cd web && pnpm test` → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** `pnpm extract && pnpm test && pnpm lint && pnpm build` → PASS.
- [ ] **Step 5:** Commit `feat(web): the lock on private letters`.

### Task 23: Privé maken, delen, weer zichtbaar maken

**Files:**
- Create: `web/src/components/share-dialog.tsx`, `share-dialog.test.tsx`
- Modify: `web/src/routes/document.tsx`, `web/src/api/queries.ts` (`setDocumentVisibility` mutation; invalidates the document, the lists and the inbox counts), `document.test.tsx`

**Interfaces:**
- On the letter (role member or admin): public → button "Privé maken" opens the dialog "Privé maken" with checkboxes "Delen met" from `listMembers` ("Jij" checked and disabled; the scanner pre-checked); restricted → "Delen" (the same dialog on `shared_user_ids`; unchecking yourself shows "Daarna kun je deze brief zelf niet meer openen.") and, when `can_make_public`, "Voor iedereen zichtbaar maken" with a confirm step. `can_read: false` in the answer → to the post list with `MessageBar` "Je kunt deze brief niet meer openen.". 403 `cannot_make_public`, 403 `emergency_read_only` and 409 `would_lose_access` (from the addressee picker) show the server's sentence. Viewers see no buttons.

- [ ] **Step 1: Failing tests:**

```tsx
test("Privé maken sends restricted with the chosen members")
test("Delen shows the current shares and saves the new list")
test("removing yourself warns and leads back to the post")
test("Voor iedereen zichtbaar maken appears only when allowed")
test("a viewer sees no privacy buttons")
test("the server's refusal is shown")
test("removing your own addressee shows would_lose_access")
```

- [ ] **Step 2:** `pnpm test` → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** `pnpm extract && pnpm test && pnpm lint && pnpm build` → PASS.
- [ ] **Step 5:** Commit `feat(web): make a letter private and share it`.

### Task 24: Departments, emergency access and the privacy settings; final check

**Files:**
- Create: `web/src/components/emergency-dialog.tsx`, `privacy-settings.tsx`, tests next to each
- Modify: `web/src/routes/persons.tsx`, `persons.test.tsx`, `web/src/routes/settings.tsx` (part A's "Instellingen"), `settings.test.tsx`, `web/src/api/queries.ts`

**Interfaces:**
- Persons page in a Zakelijk workspace: two sections "Personen" and "Afdelingen"; "Afdeling toevoegen" (`createPerson` with `kind: "department"`); per department "Leden" dialog with member checkboxes (`addDepartmentMember` / `removeDepartmentMember`); 409 `cannot_grant_self` shows its sentence. In Thuis no kind anywhere (departments, if any, are listed as persons). "Verwijderen" on a person only for admins (Task 9); members keep "Archiveren".
- Per person or department row, for an admin when `emergency_access`: menu item "Noodtoegang" → dialog "Noodtoegang tot de privé-post van <naam>" with "Reden" (`TextField`, 10 to 200 characters, the count shown, "Openen" disabled outside it), the explanation "Dit komt in het logboek, de lezers zien het bij de brief en krijgen een melding." and the result "Je kunt 3 brieven een uur lang openen." (0: "Er is geen privé-post van <naam>.").
- `<PrivacySettings />` on part A's settings page: switch "Zorg-post is privé" (admins; `updateWorkspaceSettings` with `{care_is_private}` only); "Noodtoegang voor de beheerder": a switch for a server admin (`setEmergencyAccess`), a plain sentence of the state for others.

- [ ] **Step 1: Failing tests:**

```tsx
test("a business workspace lists departments with their members")
test("a home workspace shows no departments section")
test("adding yourself to a department with private post shows the refusal")
test("a member sees no Verwijderen for a person")
test("emergency access needs a reason of ten to two hundred characters")
test("emergency access reports how many letters opened")
test("no Noodtoegang item when the setting is off")
test("an admin switches Zorg-post is privé")              // PUT body {care_is_private: false} only
test("only a server admin can switch emergency access")
```

- [ ] **Step 2:** `pnpm test` → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** `pnpm extract && pnpm test && pnpm lint && pnpm build`, then from the root `make test && make lint && make typecheck && make perf` → all PASS.
- [ ] **Step 5: Manual check** on the test server: Thuis, two members; Anne scans a letter with the Zorg label for Bob's person; Bob sees the lock with "Privé voor Anne en Bob"; Carla does not see the letter, its task or the inbox count; the browser console shows no CSP violation. Record in the task report.
- [ ] **Step 6:** Commit `feat(web): departments, emergency access and privacy settings`.

---

## Plan decisions

1. `actor` is keyword-only and required on `workspace_context`, with a third value `NOBODY` (empty): code that never reads documents (`configure_ai`, `record`, `lookup_invitation`, `remove_expired_exports`) gets least privilege instead of `SYSTEM`, and the list of system callers stays short.
2. Tests are rewritten mechanically to `actor=SYSTEM`: their behaviour is unchanged because nothing is private before Task 17, and the new privacy tests use real user actors.
3. Helper calls are wrapped as `(SELECT public.brievie_actor_id())` and the functions guard the cast with `CASE` (not `OR`) and pin `SET search_path = ''`: evaluated once per statement, no cast error, no search-path hijack.
4. `documents_documentreader` gets its own `reader_scope` policy (own rows or system; writes only as system) and is exempt from the child policies, which would create the recursion D 5.1 rejects.
5. No `db_default` on anything added: Django then inserts without `RETURNING`, so inserts never meet the SELECT policy.
6. The visibility flip in `make_private` runs as the current actor, not the system: the database itself proves the actor kept access, so a missing share fails loudly (42501) instead of producing a request that cannot read its own letter.
7. The actor's own share comes from `current_actor()`, not a parameter: rules at confirm pass the system audit actor, and the confirming user must still get a share (D 20 ruling 5).
8. (Changed, ruling c.) `recompute` never raises. `409 would_lose_access` comes from `keep_own_access`, used only by `organise.set_addressees` and `organise.undo_event`; unlinking oneself and leaving a department are deliberate and allowed; `setDocumentVisibility` answers `can_read: false`. This removes the contradiction of the check report's blocking point 1.
9. Making a letter public deletes its shares: switched private again it starts from scanner and actor, which is what "visibility switched twice" tests.
10. Emergency access is read-only (`emergency_read_only`), retry included, because otherwise an admin with an hour of access could add their own person or a share and keep the letter for good, or send a failed letter to the AI again.
11. (Changed, ruling b.) One lock order: own membership `FOR SHARE` (raw SQL, Django has no API for it), then document and person `FOR NO KEY UPDATE`, then reader rows. `FOR NO KEY UPDATE` instead of `FOR UPDATE` is what lets `recompute`'s foreign-key checks (`FOR KEY SHARE`) pass a locked document without waiting, so no cycle forms with part A's `lock_memberships`. Two confirms by one user share the membership lock.
12. Deletes use `system_actor()` after authorisation (D 8.3), not `DB_CASCADE`: one code path, no FK rewrites, and `SET_NULL` on history rows keeps working.
13. A repeated upload of a letter the uploader can no longer read answers the existing `document_conflict` sentence: it reveals nothing beyond the client's own id.
14. `WorkspaceModel.save` re-reads a hidden related row's workspace inside `system_actor()`; the workspace policy still binds, so the cross-workspace guard keeps its meaning. This adds `apps/common/models.py` to the system callers, beyond the design's list.
15. `has_private_post` is not exposed per person: the emergency dialog reports a count after the fact, so an ordinary member cannot see whose post is private.
16. Linking needs an admin, unlinking needs the linked member (viewer role suffices), exactly as D 8.1; 1c tests that let an admin unlink somebody else change to expect 409.
17. Department membership changes stay admin-only in both directions; D 8.1 restricts only who may be granted, not who may revoke.
18. The performance gate stops the plan before the feature tasks; the fallback (copied visibility on child rows) needs the owner's yes because it changes the policy shape.
19. (New, ruling a.) Restrictive `FOR UPDATE` and `FOR DELETE` policies with the read condition and no `WITH CHECK`, on the document and every child table. Without them an `UPDATE`/`DELETE` whose WHERE reads no column touches hidden rows. Leaving out `WITH CHECK` is deliberate: the `USING` then checks the new row too, which is the rule that whoever makes a letter private stays a reader; making private still works because the reader row is written first.
20. (New, ruling d.) An automatic action never creates a private letter without a reader: the letter stays public, the history says why, admins get one notice per document; no suggestion kind "privé maken" is invented. A make-private that changed nothing writes no `RuleRun` (the model records changes).
21. (New, ruling e, provisional.) `account_authority` sends every account action on a reader of private post to the server admin. It reuses `server_admin_only` with its own sentence, so clients need no new code.
22. (New, ruling f.) `deletePerson` becomes admin-only. Every answer that reveals private post (`person_in_use`, `cannot_grant_self`, `email_required`, the account-action `server_admin_only`) is then given only to admins, which is accepted and written in PRIVACY.md. A member still archives persons. This is a behaviour change for a 1c member client (a 403 with the standard sentence it already handles).
23. (New, ruling g.) The leak suite comes before Task 17, the first task that makes letters private in normal use; its list of document operations comes from the OpenAPI schema, so a new operation without a case fails the suite.
24. (New, ruling j.) A share added or removed is a `visibility_changed` audit row with values `share` and `member`, not new codes: D 11 lists "visibility changed", and part A's closed list stays as it is apart from `ALLOWED_VALUES`.
25. (New.) Emergency reasons are 10 to 200 characters: part A's `record` cuts strings at 200, and the log must keep the whole reason.
26. (New.) The own export in part B is the letters on the member's name plus the private letters they read (reader rows), read as the member: "every letter the member may read" in D 6.3 means their own post, not every public letter of the workspace (part A's seam says "reads the member's reader rows").
27. (New.) Workspace settings stay on part A's `get/updateWorkspaceSettings` (no `PUT /privacy`); every input is `bool | None = None`, so an older client never wipes a newer setting. The emergency switch keeps its own operation because only a server admin may use it, without membership.
28. (New.) A system path that makes a letter private (pipeline, manual run) reads memberships without locking them (locking would break the order: it already holds the document). A share it writes for a member removed at that moment is removed by `verify_readers` (it deletes shares of non-members); until then it opens nothing, because the removed user cannot enter the workspace.
29. Check report items not adopted: the report's alternative "a test that refuses WHERE-less writes in `apps/`" (ruling a chose the policies, which also cover raw SQL); "lock the membership before the document **or** drop the membership lock": the lock is kept and moved first (ruling b), because without it a concurrent removal leaves a share for a non-member. Every other item is in a task: 1 (blocking) Tasks 7, 11, 12; 2 (blocking) "Seams with part A"; 3 (blocking) Task 14 and PRIVACY in Task 17; section 1 Tasks 3, 6, 18; section 2 Tasks 9, 16, 20, 7; section 3 Tasks 2, 5–8 (the split), 16, and `transaction=True` in Tasks 6, 16, 18; section 4 below; section 5 Task 21.

## Seams with part A and the back-up code (checked against the part A plan of 26 tasks)

Part A's real names, used above:

- Audit: `apps.audit.services.record(code: AuditCode, *, actor: AuditActor, target: AuditTarget | None = None, workspace_id: UUID | None = None, values: Mapping[str, str | int | bool] | None = None) -> None`; it enters `workspace_context` itself (Task 2 gives it `actor=NOBODY`). `AuditActor.system()`, `AuditActor.for_user(user, *, client_ip=None)`, `actor_from_request(request)`; `AuditTarget(kind, id, name="")`. Codes used: `visibility_changed`, `department_member_added`, `department_member_removed`, `emergency_access_switched_on`, `emergency_access_switched_off`, `emergency_access_used`, `workspace_setting_changed`. Part A writes `person_linked`/`person_unlinked` (accept, `link_member`), `person_deleted`, `label_deleted`, `rule_deleted`.
- **A edit (made in the part A plan, Task 1):** `ALLOWED_VALUES[visibility_changed] = {"visibility", "origin", "share", "member"}`, `department_member_added` / `department_member_removed`: `{"member"}`, `emergency_access_used: {"reason", "document_count"}`.
- Notices: `apps.accounts.notices.notify(user: User, code: str, *, actor_name: str, values: Mapping[str, str] | None = None) -> None`, `notice_message`. **A edit (Task 5):** `AccountNotice.Code` includes `emergency_access_switched_on`, `emergency_access_switched_off`, `emergency_access_used`, `private_without_readers`, with their sentences; part B adds no choices migration.
- Members: `apps.workspaces.members.lock_memberships(workspace)`, `remove_member(*, request, workspace, user_id, actor, left)`; the seam `access.on_member_removed(workspace_id, user_id)` is called in step 3, before the membership is deleted; B wraps steps 3–4 in `system_actor()` (Task 13).
- Invitations: `create_invitation(... person_id ...)` calls `access.person_requires_email(person)` → 422 `email_required`; `accept_invitation` calls **(A edit, Task 12)** `access.on_member_added(workspace_id, user_id, *, person_id: UUID | None, created_by_id: UUID | None)` with the person now linked to the user and the invitation's creator; email-bound invitations answer 403 `invitation_email_mismatch` to another address.
- Account actions: `apps.accounts.authority.account_authority(actor, target_id) -> AccountAuthority`, used by resets, sessions and devices, and re-checked when a reset completes (Task 14 extends it). `User.is_server_admin` exists.
- Settings: `getWorkspaceSettings` / `updateWorkspaceSettings` with `WorkspaceSettingsOut{allow_member_export}`; **A edit (Task 16):** `WorkspaceSettingsIn.allow_member_export: bool | None = None` (None leaves it). Web: `web/src/routes/settings.tsx` (`/w/$workspaceId/instellingen`).
- Export: `apps.documents.export.export_context(workspace_id, *, reader_id)` (Task 2 gives it the actor), `member_export.member_document_ids` (Task 13), `apps/documents/jobs.py` exists (Task 5 modifies it).
- Migrations: B's follow A's (`workspaces/0005`, `0006`; `documents/0009`), see "File Structure".
- `MemberExport` and the audit tables have no foreign key to `Document`, so Task 3's coverage test needs no policy for them.
- `lock_active_server_admins()` takes `FOR UPDATE` on `User` rows, which blocks a reader insert's `FOR KEY SHARE` on that user for the length of a member removal or `set_server_admin`; those transactions take no document lock afterwards in another workspace, so no cycle forms. Not changed.
- **For the back-up code owners (`apps/backup`):** `sweep`, `manifest` and `verify` read with `row_security = off` as a role with `BYPASSRLS`, so the policies do not change their counts (Task 16 pins it). A restore as the superuser keeps the helper functions (owner and `search_path`) and the policies. `verify_restore` gains, in Task 21, a check that `brievie_actor_id` and `brievie_is_system` exist and that every table with a document foreign key has its three visibility policies, so a restore without them cannot pass. Part B adds no page paths.

## What the iOS plan for part B must cover

- Read `visibility` and `reader_names` (optional): a lock with "Privé voor …" in lists and on the letter; nothing when the server does not send them (1c server).
- The review: a "Privé" switch sending `ConfirmIn.visibility` only when the user changed it; pre-set on when the chosen labels contain the `zorg` system key and `getWorkspaceSettings` reports `care_is_private` true.
- On a letter: "Privé maken" / "Delen met" / "Voor iedereen zichtbaar maken" through `setDocumentVisibility` with members from `listMembers`; handle `can_read: false`, 403 `cannot_make_public`, 403 `emergency_read_only`, and 409 `would_lose_access` from `setDocumentAddressees`, `undoDocumentEvent` and `confirmDocument`.
- History: the new event kinds and `note` (including `private_skipped`); an unknown kind is skipped, not a crash.
- Departments appear in the person picker (`PersonOut.kind`), labelled as departments in a Zakelijk workspace.
- `deletePerson` answers a member 403 `forbidden`: hide "Verwijderen" for non-admins.
- Calendar: tasks that disappear from `listTasks` (a letter became private) must be removed from the phone's calendar; check what the 1c app does today (D 20 open point).
- A letter that answers 404 after it was open (made private, emergency expired) closes with a plain sentence.
- The 1c app against this server: verify it ignores unknown event kinds and the new fields, does not edit rules, and shows the 403 sentence when a member deletes a person.

## Risks I could not close from reading the code

- The cost of the correlated `EXISTS` on child tables at scale is unknown until Task 4 runs; `list_persons`' and `list_labels`' `document_count` over every link are the most likely to fail the threshold.
- Non-leakproof operators (a future `ILIKE` search, `~`) are evaluated above the RLS barrier and lose index use; search in a later phase must be measured with the policies on.
- Django 6.1's exact `save()` fallback and `RETURNING` behaviour are pinned by tests in Task 3, not read from Django's source here; if a later Django adds `RETURNING` for UUID inserts, Task 3's tests fail first.
- The concurrency tests of Task 18 make a deadlock unlikely to go unnoticed but cannot prove its absence; the lock order and the `no_key` scan (Task 6) are the structural guard.
- An invitation for a person with private post is bound to an email address the server does not verify (ruling e iii relies on part A's address check); the grant's history line is what makes misuse visible. Pending the owner's answer to ruling e.
- The 1c iOS app's behaviour with vanishing tasks, unknown history kinds and a 403 on deleting a person.
