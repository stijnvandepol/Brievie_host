# Host "Navigatie en Mijn post" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A menu in the owner's order (Inbox, Mijn post, Mijn taken, …, Instellingen at the bottom) with "Mijn post" (the letters addressed to me) as the start screen; a per-member "Ongelezen" state in lists and counts; a "Blijft liggen" signal for organisations; "Ik doe dit" only on unassigned tasks and hand-over of someone else's task only by that person or an admin; and switching workspaces as a clear move between separate spaces (fixing what made the old switch look dead).

**Architecture:** One new workspace table `documents_documentreadmark` (one row per member and letter, RLS forced plus a restrictive own-row policy) holds the read marks; the unread filter is an anti-join (`~Exists(mark for me)`) on top of `visibility.visible_documents`, so it can only narrow what the member already sees. `Document.addressee_opened_at` (set once by the system) feeds the organisation signal without saying who. A new module `apps.documents.reading` is the only writer of marks; `apps.documents.lingering` decides "Blijft liggen" (pure rule + one service). One menu endpoint `getMenuCounts` replaces two requests per page. Task assignment gets one rule table in `apps.tasks.assignment` and three `can_*` booleans for the clients. The web keys the workspace subtree by its id, gets a switcher at the top of the menu, a route `/w/$id/mijn-post`, and `/` redirects to the last workspace's Mijn post.

**Tech Stack:** Python 3.13, Django 6.1, Django Ninja, PostgreSQL 18 (forced RLS), pytest; React 19, TanStack Router/Query, React Aria Components, Lingui, Vitest, MSW, Biome. iOS: Swift 6, SwiftUI, Observation, swift-openapi-generator, Swift Testing. No new dependency.

**Spec:** `docs/superpowers/specs/2026-11-21-navigatie-en-persoonlijke-inbox-design.md`, cited "S 5.3". Neighbours: "Mijn taken" (`2026-11-21-mijn-taken-design.md`, "MT"), letter status (branch `docs/letter-status-design`, "LS"), Mensen (branch `docs/people-accounts-design`, "PA"), rights (`2026-11-19-zichtbaarheid-postkamer-inrichting-design.md`, "RS").

## Global Constraints

From `CLAUDE.md` (verbatim where quoted):

- "Every table: UUID `id`, `created_at`, `updated_at`. Every workspace content table: `workspace_id` (subclass `WorkspaceModel`) and RLS enabled **and forced**." The read-mark table is a `WorkspaceModel` with `enable_rls` **and** a restrictive policy `read_mark_own` (`user_id = public.brievie_actor_id() OR public.brievie_is_system()`).
- "API only under `/api/v1`, additive changes only. Every endpoint: typed schemas and an explicit camelCase `operation_id`." "A field added to an existing API schema has a default." "Nullable fields in API schemas are primitives only, and nullable inputs have a default; the contract test forbids `anyOf`." (`UUID | None = None` is already used in `apps/audit/schemas.py`.)
- "Every non-2xx body: `{"code": "<snake_case>", "message": "<Dutch sentence>"}` via `apps.common.errors.ApiError`." "Thin endpoints: validate, call one function in the app's `services.py`, return." "Every mutating service function is wrapped in `transaction.atomic`."
- "A member-facing read of documents starts from `apps.documents.visibility.visible_documents`." Every new list or count here does; `tests/test_visibility_seams.py` must keep passing.
- "Brievie never logs, audits or echoes a search term." / "No document content, tokens, passwords or BSNs in logs." No log line names a letter's sender, type or a member's read behaviour.
- "Only `apps.documents.organise` adds or removes a document's labels and addressees." `addressee_opened_at` is recomputed from the existing sync point (`visibility.sync_addressee_ids`), not by a second writer of addressees.
- User-facing text Dutch B1 through `gettext` / Lingui `t`, English next to it (`make messages`, `pnpm extract`, terms from `docs/GLOSSARY.md`). After every API change: `make openapi`, `cd web && pnpm gen:api`, commit both generated files.
- "Tests first for logic. Conventional commits." Every commit ends with `Co-Authored-By: Claude <noreply@anthropic.com>`.

From the environment:

- **Backend tests run in CI only.** Never connect to the PostgreSQL on this Mac, never `make dev`, `make test`, `docker` or `./brievie`. A backend "run" step means: commit, push the branch, read the `backend` job (and `visibility-perf` where named). `make lint`, `make typecheck` and every `pnpm` command run locally.
- **Coordination with `feat/wie-ziet-wat-5`.** That branch edits `web/src/components/page.tsx`, `router.tsx`, `routes/home.tsx`, `routes/inbox.tsx`, `routes/document.tsx`, `components/document-table.tsx`, `routes/persons.tsx`, `routes/members.tsx`, `api/queries.ts` and both catalogues, plus `apps/workspaces/schemas.py` and `apps/documents/services.py` (2 lines). Web tasks marked **[after 5]** start only after it is merged to `main`. Server tasks can start now; rebase before the PR.
- **Coordination with Mensen (PA) and letter status (LS).** Both are designs not yet built. This plan places "Mensen" in the menu as one entry when PA has landed and keeps "Personen" + "Leden" in that slot until then (Task 14). LS's status badges sit next to the unread dot; whichever lands second adds its marker to the shared row component.

From the spec, not left to the builder:

- **Unread** (S 5.2): visible, not `processing`, `created_at >= Membership.unread_from`, no own mark. **Read** (S 5.3): explicit `markDocumentRead` from the letter page / reader, an upload by the member, "Markeer als gelezen", "Alles gelezen" (≤ 200 ids). Never on `GET`. Never history, never audit.
- **Name**: "Ongelezen"/"Unread", never "Nieuw" (that is LS's "not yet checked").
- **Addressee opened** (S 5.5): set once, as the system, at the first mark of a member whose `brievie_my_post_ids` meet `addressee_ids`; not by emergency access; recomputed when the addressees change.
- **Blijft liggen** (S 6): only `kind == business` with `lingering_enabled`; only sorters/admins; only letters in `visible_documents(viewer)`; defaults 2 days in Inbox, 5 days not opened, calendar days in Europe/Amsterdam.
- **Assignment** (S 7.1): table in the spec; new 409 `task_taken`.
- **Switch** (S 4.3): to `/w/<other>/mijn-post`; current one = close only; no counts of other workspaces; subtree remounts.
- The answers in "Besluiten voor Stijn" (spec S 13) can change B1 (label), B2 (department filter), B3 (defaults), B5 (iOS tabs), B6 (Instellingen hub), B7 (notices). Every task names the decision it depends on; build the recommendation unless Stijn answered otherwise.

## Review Focus

1. **No read state of one member reaches another.** RLS own-row policy, tested as `brievie_app` with two users (Task 1); no mark in history, audit, export or logs (Task 2, `test_mark_writes_no_event_or_audit`).
2. **Unread and lingering only narrow `visible_documents`.** No count of hidden letters: a private letter the admin cannot read is in no lingering count (Task 5, `test_lingering_skips_letters_viewer_cannot_read`); seam test passes (Tasks 4–6).
3. **A colleague's task is not taken.** 409 for a member on someone else's task in all three directions; admins pass; flags match the rule (Task 7).
4. **Workspaces do not mix.** Same user, two workspaces: menu counts, lists, unread, lingering, search, my tasks and notices of A hold nothing of B (Task 11); the web remounts and re-queries on a switch (Task 12).
5. **250 ms.** `menu_counts`, Mijn post (all and unread only), Alle post unread only and `lingering_letters` at 100,000 letters with realistic marks (Task 10).
6. **Older clients.** Every new field defaulted; `listDocuments` without the new params unchanged; `getInboxCounts` and `countMyTasks` unchanged and kept (Task 6 contract test).

---

## Build order

| Order | Work | Waits for |
|---|---|---|
| 1 | Server Tasks 1–11 (`feat/navigation-server`) | nothing; rebase on `main` after branch 5 merges (one-line overlaps in `documents/services.py`, `workspaces/schemas.py`) |
| 2 | Web Task 12 (remount + query key + switch test) | may go first as its own small PR; touches `routes/workspace.tsx`, `api/queries.ts` (one key), `routes/invitation.tsx`; if branch 5 is still open, wait to avoid a `queries.ts` conflict |
| 3 | Web Tasks 13–19 (`feat/navigation-web`) | **[after 5]** merge of `feat/wie-ziet-wat-5`, and server Tasks 1–8 on `main` |
| 4 | iOS I1–I8 (in `Brievie_ios`) | server on `main`; the iOS "Mijn taken" plan (`docs/ios-my-tasks-plan`) built first, its screen is re-homed here |

## File structure

| File | Task | Purpose |
|---|---|---|
| `backend/apps/documents/models.py` | 1 | `DocumentReadMark`; `Document.addressee_opened_at` |
| `backend/apps/workspaces/models.py` | 1 | `Membership.unread_from`; `Workspace.lingering_enabled`, `lingering_inbox_days`, `lingering_unopened_days` |
| `backend/apps/accounts/models.py` | 1 | `AccountNotice.workspace` (nullable FK) |
| `backend/apps/documents/migrations/0020_read_marks.py` | 1 | table, RLS, own-row policy, indexes, backfill |
| `backend/apps/workspaces/migrations/0009_unread_and_lingering.py` | 1 | fields with defaults by kind |
| `backend/apps/accounts/migrations/0015_notice_workspace.py` | 1 | field |
| `backend/apps/documents/reading.py` (new) | 2, 3 | the only writer of marks; `unread_q`; `addressee_opened_at` |
| `backend/apps/documents/lingering.py` (new) | 5 | pure rule + `lingering_letters` |
| `backend/apps/documents/services.py`, `api.py`, `schemas.py` | 4, 6 | list params, `unread` fields, read endpoints, `menu_counts` |
| `backend/apps/tasks/assignment.py`, `schemas.py`, `services.py` | 7 | rule table, `can_*` |
| `backend/apps/accounts/notices.py`, `schemas.py` | 8 | notice workspace |
| `backend/apps/documents/visibility.py` | 3, 9 | `sync_addressee_ids` hook, `forget_member` deletes marks |
| `backend/tests/perf/data.py`, `test_visibility_at_scale.py` | 10 | marks in the world, new measurements |
| `web/src/routes/workspace.tsx`, `api/queries.ts`, `routes/invitation.tsx` | 12 | remount, list key |
| `web/src/components/workspace-switcher.tsx` (new), `last-workspace.ts` (new), `routes/home.tsx`, `routes/workspaces.tsx` (new) | 13 | switcher, `/` redirect, `/werkruimtes` |
| `web/src/components/page.tsx` | 13, 14, 18 | menu order, counts, notices |
| `web/src/routes/my-post.tsx` (new), `components/document-table.tsx`, `routes/document.tsx`, `routes/post.tsx` | 15 | Mijn post, unread rows, mark read |
| `web/src/routes/inbox.tsx`, `routes/settings.tsx` | 16 | Blijft liggen |
| `web/src/components/task-actions.tsx`, `task-row.tsx` | 17 | flags |

---

## Slice S: server

### Task 1: Tables, fields and the own-row policy

**Files:**
- Modify: `backend/apps/documents/models.py`, `backend/apps/workspaces/models.py`, `backend/apps/accounts/models.py`, `backend/apps/workspaces/rls.py` (helper `own_rows_policy(table, name)`)
- Create: the three migrations above
- Test: `backend/tests/test_read_marks_rls.py`, `backend/tests/test_rls_coverage.py` (existing, must list the table)

**Interfaces:**
- `class DocumentReadMark(WorkspaceModel)`: `document` FK (CASCADE, `related_name="read_marks"`), `user` FK (CASCADE, `related_name="+"`), `read_at` (aware datetime). `same_workspace_fields = ("document",)`. Unique `(document, user)` (`documents_readmark_unique`); index `(user, document)` (`documents_readmark_user`) for the anti-join.
- `Document.addressee_opened_at: DateTimeField(null=True)`; partial index `documents_document_unopened` on `(workspace_id, updated_at)` `WHERE status='done' AND addressee_ids <> '{}' AND addressee_opened_at IS NULL AND trashed_at IS NULL`.
- `Membership.unread_from: DateTimeField(default=timezone.now)`; migration sets it to the migration's `now()` for every existing row.
- `Workspace.lingering_enabled: bool` (migration: `kind == business`), `lingering_inbox_days: PositiveSmallIntegerField(default=2)`, `lingering_unopened_days: PositiveSmallIntegerField(default=5)`; `create_workspace` sets `lingering_enabled` from the kind (spec B3).
- `AccountNotice.workspace: FK(Workspace, null=True, on_delete=CASCADE)`.
- Backfill: `addressee_opened_at = updated_at` for every existing `done` letter with addressees (no flood of "nog niet geopend" on day one; DECISIONS line in Task 9).
- `rls.own_rows_policy("documents_documentreadmark", "read_mark_own")` -> `RunSQL` creating a **restrictive** policy `USING (user_id = public.brievie_actor_id() OR public.brievie_is_system()) WITH CHECK (same)`.

- [ ] **Step 1: Failing tests**

```python
def test_read_mark_table_has_forced_rls_and_own_row_policy(): ...   # pg_class.relforcerowsecurity, pg_policies
def test_member_sees_only_own_marks_as_brievie_app(): ...            # Anne's mark invisible to Bas, same workspace
def test_member_cannot_insert_mark_for_someone_else(): ...           # WITH CHECK fails
def test_system_sees_all_marks_of_the_workspace_only(): ...
def test_existing_members_get_unread_from_at_migration(): ...
def test_business_workspaces_get_lingering_on_home_off(): ...
```

- [ ] **Step 2:** Push → FAIL. **Step 3:** Implement. **Step 4:** Push → PASS.
- [ ] **Step 5:** Commit `feat(documents): read marks per member, with their own row policy`.

### Task 2: `reading`: marking read and unread

**Files:**
- Create: `backend/apps/documents/reading.py`
- Modify: `backend/apps/documents/intake.py` (`create_document_from_upload` marks the uploader)
- Test: `backend/tests/test_reading.py`

**Interfaces:**
- `mark_read(membership: Membership, document_id: UUID) -> None`: `transaction.atomic`; the letter through `services.get_document` (404 `document_not_found` / `document_in_trash` as today); `INSERT … ON CONFLICT DO NOTHING` as the member; then `_note_addressee_opened(document, membership)` (Task 3).
- `mark_unread(membership, document_id) -> None`: same lookup; deletes the own mark. Does **not** clear `addressee_opened_at` (an opening happened).
- `mark_many_read(membership, document_ids: Sequence[UUID]) -> int`: at most 200 (422 `validation_error` "Kies hoogstens 200 brieven."), only ids in `visible_documents(membership)`; the number newly marked.
- `unread_q(membership) -> Q`: `~Q(status=PROCESSING) & Q(created_at__gte=membership.unread_from) & ~Exists(DocumentReadMark.objects.filter(document=OuterRef("pk"), user_id=membership.user_id))`. Callers apply it only to `visible_documents(membership)`.
- `is_unread(membership, documents: Sequence[Document]) -> set[UUID]`: one query, for summaries.
- Upload: the uploader's mark is written in the same transaction as the document (as the uploader).

- [ ] **Step 1: Failing tests**

```python
def test_mark_read_then_not_unread(): ...
def test_mark_read_twice_is_one_row(): ...
def test_mark_read_on_invisible_letter_is_404_and_writes_nothing(): ...
def test_mark_read_on_trashed_letter_answers_document_in_trash(): ...
def test_mark_unread_removes_only_own_mark(): ...
def test_processing_letter_is_never_unread(): ...
def test_letter_before_unread_from_is_read(): ...
def test_uploader_has_read_own_upload(): ...
def test_mark_many_skips_invisible_ids_and_caps_at_200(): ...
def test_mark_writes_no_event_or_audit(): ...      # DocumentEvent and AuditEntry counts unchanged
def test_get_document_does_not_mark(): ...
```

- [ ] **Step 2:** Push → FAIL. **Step 3:** Implement. **Step 4:** Push → PASS.
- [ ] **Step 5:** Commit `feat(documents): mark a letter read or unread, for yourself only`.

### Task 3: "Opened by an addressee"

**Files:**
- Modify: `backend/apps/documents/reading.py`, `backend/apps/documents/visibility.py` (`sync_addressee_ids` calls `reading.recompute_addressee_opened(document)` as the system), `verify_visibility` (repairs it nightly)
- Test: `backend/tests/test_addressee_opened.py`

**Interfaces:**
- `_note_addressee_opened(document, membership)`: when `addressee_opened_at` is null and `facts.member_facts(membership)` person or department ids meet `document.addressee_ids` **and** the member reads it other than only through emergency access (`visibility.only_through_emergency(document)` false), set it to now as the system (`UPDATE … WHERE addressee_opened_at IS NULL`).
- `recompute_addressee_opened(document) -> None` (system): null when none of the current addressees' members (linked users of the persons, `DepartmentMember` users of the departments) has a mark; else the earliest of their marks' `read_at`.

- [ ] **Step 1: Failing tests**

```python
def test_addressee_opening_sets_opened(): ...
def test_department_member_opening_sets_opened(): ...
def test_scanner_or_sorter_opening_does_not_set_opened(): ...
def test_emergency_access_opening_does_not_set_opened(): ...
def test_new_addressee_without_mark_clears_opened(): ...       # Inbox re-assigns Jan -> Piet
def test_mark_unread_keeps_opened(): ...
def test_opened_is_not_in_any_response(): ...                  # schema dump of DocumentOut / Summary
```

- [ ] **Step 2–4:** Push → FAIL, implement, push → PASS.
- [ ] **Step 5:** Commit `feat(documents): remember that an addressee opened a letter, not who`.

### Task 4: Mijn post and unread in the lists

**Files:**
- Modify: `backend/apps/documents/services.py` (`list_documents`), `api.py`, `schemas.py`
- Test: `backend/tests/test_my_post.py`, `backend/tests/test_visibility_seams.py` (stays green)

**Interfaces:**
- `list_documents(..., addressed_to_me: bool = False, unread_only: bool = False)`: `addressed_to_me` filters `addressee_ids__overlap=list(facts.person_ids)` (own persons only, spec B2; a later `include_department: bool = False` param is the hook for B2's filter button); `unread_only` applies `reading.unread_q`. Both after `visible_documents`.
- `DocumentSummaryOut.unread: bool = False`, `DocumentOut.unread: bool = False` (filled by `reading.is_unread` in `document_summaries_out` / the detail).
- Endpoints: `markDocumentRead` (POST `/workspaces/{id}/documents/{doc}/read`, 204), `markDocumentUnread` (DELETE same, 204), `markDocumentsRead` (POST `/workspaces/{id}/documents/read`, body `MarkReadIn{document_ids: list[UUID] = Field(max_length=200)}`, 200 `MarkReadOut{marked: int}`). All `auth` default (session or device), 404 `ErrorOut`.

- [ ] **Step 1: Failing tests**

```python
def test_addressed_to_me_lists_only_own_person_letters(): ...
def test_addressed_to_me_leaves_out_department_letters(): ...   # spec B2 default
def test_member_without_person_gets_empty_my_post(): ...
def test_unread_only_after_mark_read(): ...
def test_summary_and_detail_carry_unread(): ...
def test_private_letter_of_other_never_in_my_post(): ...         # state E off
def test_list_without_new_params_unchanged(): ...                # same ids and order as before
```

- [ ] **Step 2–4:** Push → FAIL, implement (`make openapi`, `pnpm gen:api`), push → PASS.
- [ ] **Step 5:** Commit `feat(documents): my post and unread letters`.

### Task 5: "Blijft liggen"

**Files:**
- Create: `backend/apps/documents/lingering.py`
- Modify: `backend/apps/documents/api.py`, `schemas.py`; `backend/apps/workspaces/schemas.py`, `services.py` (settings in/out)
- Test: `backend/tests/test_lingering_rule.py` (pure), `backend/tests/test_lingering.py`

**Interfaces:**
- Pure: `lingering.age_days(since: datetime, now: datetime) -> int` (calendar days in Europe/Amsterdam); `lingering.applies(kind: str, enabled: bool, sorts_post: bool, role: str) -> bool`.
- `lingering_letters(membership) -> Lingering` (frozen: `too_long_in_inbox: list[Document]`, `not_opened: list[Document]`, `inbox_days`, `unopened_days`): empty unless `applies`; both from `visible_documents(membership)`: review/unassigned with `created_at < cutoff`; `done`, `addressee_ids <> '{}'`, `addressee_opened_at IS NULL`, `updated_at < cutoff` (the partial index of Task 1); 100 rows each, oldest first.
- `count_lingering(membership) -> int` (both groups, for the menu).
- `listLingeringLetters` (GET `/workspaces/{id}/inbox/lingering`): `LingeringOut{too_long_in_inbox: list[LingeringLetterOut], not_opened: list[LingeringLetterOut], inbox_days: int, unopened_days: int}`; `LingeringLetterOut` = summary fields + `days: int`.
- Settings: `WorkspaceSettingsOut/In.lingering_enabled: bool = False`, `lingering_inbox_days: int = 2` (1–30), `lingering_unopened_days: int = 5` (1–60); only business shows them (web), home rejects `lingering_enabled=true` with 422 `validation_error` "Dit kan alleen in een organisatie.".

- [ ] **Step 1: Failing tests**

```python
def test_age_days_counts_amsterdam_calendar_days(): ...
def test_home_workspace_has_no_lingering(): ...
def test_plain_member_gets_empty_result(): ...
def test_sorter_and_admin_get_both_groups(): ...
def test_lingering_skips_letters_viewer_cannot_read(): ...     # private letter of Femke, admin without admins_read_all
def test_opened_by_addressee_leaves_not_opened(): ...
def test_thresholds_from_settings(): ...
def test_home_cannot_switch_lingering_on(): ...
```

- [ ] **Step 2–4:** Push → FAIL, implement, push → PASS.
- [ ] **Step 5:** Commit `feat(documents): letters that lie too long, for the sorters of an organisation`.

### Task 6: `getMenuCounts`

**Files:**
- Modify: `backend/apps/documents/services.py` (`menu_counts`), `api.py`, `schemas.py`
- Test: `backend/tests/test_menu_counts.py`, `backend/tests/test_openapi_contract.py` (existing)

**Interfaces:**
- `menu_counts(membership) -> MenuCounts`: `inbox` = `waiting_letters(membership)` (two partial-index counts, as today); `my_post_unread` = `visible_documents` ∩ own persons ∩ `unread_q` count; `my_tasks`, `my_tasks_overdue` from `tasks.services.count_my_tasks`; `lingering` = `lingering.count_lingering` (0 unless applies); `shows_lingering`; `has_person`.
- `MenuCountsOut{inbox: int = 0, my_post_unread: int = 0, my_tasks: int = 0, my_tasks_overdue: int = 0, lingering: int = 0, shows_lingering: bool = False, has_person: bool = True}`; GET `/workspaces/{id}/menu/counts`, `operation_id="getMenuCounts"`.
- `getInboxCounts` and `countMyTasks` stay as they are.

- [ ] **Step 1: Failing tests**

```python
def test_menu_counts_equal_the_lists(): ...    # each number == len of the matching list call
def test_menu_counts_hide_nothing_and_count_nothing_hidden(): ...
def test_contract_additive(): ...              # fixtures openapi_1b/1c still a subset; no anyOf
```

- [ ] **Step 2–4:** Push → FAIL, implement, push → PASS.
- [ ] **Step 5:** Commit `feat(documents): one call for the menu's counts`.

### Task 7: Picking up and handing on a task

**Files:**
- Modify: `backend/apps/tasks/assignment.py`, `schemas.py`, `services.py` (item out), `api.py` (409 in `response`)
- Test: `backend/tests/test_task_assignment_rule.py`, existing `backend/tests/test_task_assignment.py`

**Interfaces:**
- `assignment.allowed(*, caller_id: UUID, caller_is_admin: bool, current: UUID | None, wanted: UUID | None) -> bool` (pure; the table of spec S 7.1).
- `assign(...)`: after `_visible(..., lock=True)`, when `not allowed(...)`: `ApiError(409, "task_taken", _("Iemand anders doet deze taak al. Vraag die persoon of een beheerder om hem door te geven."))`. Same assignee again: unchanged no-op.
- `flags(membership, task) -> TaskFlags(can_take, can_hand_over, can_release)`; `TaskOut` and the my-task item get `can_take: bool = False`, `can_hand_over: bool = False`, `can_release: bool = False`. Viewers: all false. Letterless tasks: all false.

- [ ] **Step 1: Failing tests**

```python
@pytest.mark.parametrize(...)  # every row of S 7.1, member and admin
def test_allowed_table(caller_is_admin, current, wanted, expected): ...
def test_member_cannot_take_colleagues_task(): ...          # 409 task_taken, nothing written, no event
def test_member_cannot_release_colleagues_task(): ...
def test_member_cannot_give_colleagues_task_to_third(): ...
def test_admin_reassigns_colleagues_task(): ...
def test_member_takes_unassigned_task_on_readable_letter(): ...
def test_flags_match_allowed_for_every_case(): ...
def test_viewer_flags_all_false(): ...
```

- [ ] **Step 2–4:** Push → FAIL, implement, push → PASS.
- [ ] **Step 5:** Commit `fix(tasks): a task someone else has is handed on only by them or an admin`.

### Task 8: Notices per workspace

**Files:**
- Modify: `backend/apps/accounts/notices.py` (`notify(..., workspace: Workspace | None = None)`; every workspace-bound code passes it, including branch 5's sight notices), `schemas.py`
- Test: `backend/tests/test_notices.py`

**Interfaces:** `NoticeOut.workspace_id: UUID | None = None`. A source test asserts that every code in `{EMERGENCY_ACCESS_SWITCHED_ON, EMERGENCY_ACCESS_SWITCHED_OFF, PRIVATE_WITHOUT_READERS, PRIVATE_WITHOUT_READERS_UNNAMED, <branch 5 sight codes>}` is created with a workspace. Depends on spec B7.

- [ ] **Step 1–4:** Failing tests (`test_workspace_notice_carries_its_workspace`, `test_account_notice_has_none`, `test_codes_about_a_workspace_pass_it`), implement, push → PASS.
- [ ] **Step 5:** Commit `feat(accounts): a notice about a workspace says which`.

### Task 9: Leaving, purging and the documents of record

**Files:**
- Modify: `backend/apps/documents/visibility.py` (`forget_member` deletes the member's marks), `docs/PRIVACY.md`, `docs/GLOSSARY.md`, `docs/DECISIONS.md`
- Test: `backend/tests/test_reading.py` (add)

- [ ] **Step 1:** Tests `test_leaving_member_marks_go`, `test_purge_removes_marks` (cascade under `purge`), push → FAIL; implement; push → PASS.
- [ ] **Step 2:** PRIVACY: the sentence of spec S 6.3. GLOSSARY: "Ongelezen / Unread" (per member) next to LS's "Nieuw / New" (per letter); "Mijn post / My post"; "Blijft liggen / Lingering". DECISIONS: rulings N1–N10 of the spec, the backfill of `addressee_opened_at`, `unread_from`.
- [ ] **Step 3:** Commit `docs: unread, my post and lingering letters`.

### Task 10: Measured at 100,000 letters

**Files:**
- Modify: `backend/tests/perf/data.py` (`Shape.read_mark_percent = 70`: 70 % of letters have a mark from each member linked to an addressee, 20 % also from a sorter; 3 % of `done` letters with addressees keep `addressee_opened_at` null; `unread_from` 90 days back), `backend/tests/perf/test_data_plan.py`, `backend/tests/perf/test_visibility_at_scale.py` (`"menu_counts"`, `"list_my_post"`, `"list_my_post_unread"`, `"list_all_unread"`, `"lingering_letters"` for the four actors in E on/off), `docs/DECISIONS.md` (the table)

- [ ] **Step 1:** Plan tests for the new shape (pure, local); push; read the `visibility-perf` job.
- [ ] **Step 2: Decide.** Every median ≤ 250 ms → record the table. Over → first restrict the anti-join to the member's own persons' letters through the GIN index before `~Exists` (already the case for Mijn post) and add `documents_readmark_user` as covering `(user_id, document_id)`; measure again; still over → `menu_counts` falls back to the client calling `getInboxCounts` and `countMyTasks` and the unread count alone, then stop and report the numbers to the owner.
- [ ] **Step 3:** Commit `test(perf): menu counts, my post and lingering at 100,000 letters`.

### Task 11: One user, two workspaces

**Files:**
- Test: `backend/tests/test_workspace_isolation.py`

- [ ] **Step 1:** World: Anne admin of "Thuis" and member of "Bedrijf"; one letter on her person in each, one task each, one lingering letter in Bedrijf, a notice for each workspace. Assert for each workspace: `getMenuCounts`, `listDocuments` (all, `addressed_to_me`, `unread_only`), `markDocumentRead` with the other workspace's letter id (404), `listLingeringLetters`, `searchDocuments`, `listMyTasks`, `countMyTasks`, `assignTask` with the other workspace's task id (404), notices' `workspace_id`. Verified by reading (spec 2.1); this test keeps it so.
- [ ] **Step 2:** Push → PASS (or FAIL → fix in the task that owns the path).
- [ ] **Step 3:** Commit `test(workspaces): one member of two workspaces sees each on its own`.

## Slice W: web

### Task 12: A switch remounts and re-asks (the old dead switch)

Can start before branch 5 merges if it stays this small; otherwise **[after 5]**.

**Files:**
- Modify: `web/src/routes/workspace.tsx` (component `WorkspaceOutlet`: `<Outlet key={workspace.id} />` from `workspaceRoute.useRouteContext()`), `web/src/api/queries.ts` (`workspacesQuery.queryKey = ["workspace-list"]`), `web/src/routes/invitation.tsx`, every `invalidateQueries({ queryKey: workspacesQuery.queryKey })` caller
- Test: `web/src/routes/workspace-switch.test.tsx` (new)

- [ ] **Step 1: Failing tests**

```ts
it("re-asks the server for the other workspace after a switch", ...)   // render /w/A/inbox, navigate to /w/B/inbox; MSW saw inbox/counts and documents for B; A's rows gone
it("forgets per-page state of the other workspace", ...)               // the dismissed-duplicates state of A is not in B
it("refreshing the workspace list does not refetch every workspace query", ...)
```

- [ ] **Step 2:** `cd web && pnpm test` → FAIL. **Step 3:** Implement. **Step 4:** `pnpm test`, `pnpm lint`, `pnpm typecheck` → PASS.
- [ ] **Step 5:** Commit `fix(web): switching workspaces draws the new workspace afresh`.

### Task 13: The workspace switcher and the start screen [after 5]

**Files:**
- Create: `web/src/components/workspace-switcher.tsx`, `web/src/last-workspace.ts`, `web/src/routes/workspaces.tsx` (`/werkruimtes`, the old home table)
- Modify: `web/src/routes/home.tsx` (`/` only redirects), `web/src/router.tsx`, `web/src/components/page.tsx` (switcher at the top of `navpane`; brand link to `/w/$id/mijn-post`; remove "Werkruimtes" item; page title with workspace when several), `web/src/guards.ts` (after login: same redirect)
- Test: `web/src/components/workspace-switcher.test.tsx`, `web/src/routes/home.test.tsx` (rewrite)

**Interfaces:**
- `last-workspace.ts`: `rememberWorkspace(id: string): void`, `lastWorkspace(): string | null` (localStorage `brievie.last-workspace`, try/catch).
- `WorkspaceSwitcher({ current }: { current: NavWorkspace })`: one workspace → a non-interactive block (`role` none, name + kind); several → React Aria `MenuTrigger` + `Menu` (`aria-label` "Werkruimte kiezen"), items name/kind/role, current with `isSelected`; action on another id → `navigate({ to: "/w/$workspaceId/mijn-post" })`; on the current id → close only; last item "Alle werkruimtes" → `/werkruimtes`.

- [ ] **Step 1: Failing tests**

```ts
it("with one workspace shows its name and no button", ...)          // the dead-click bug of daa62fc can't come back
it("lists the workspaces without counts and marks the current", ...)
it("goes to the other workspace's Mijn post", ...)
it("choosing the current workspace only closes the menu", ...)
it("/ goes to the last workspace used, else the first", ...)
it("/ goes to /werkruimtes without workspaces", ...)
it("the brand link opens Mijn post of this workspace", ...)
it("the page title names the workspace when there are several", ...)
```

- [ ] **Step 2–4:** FAIL → implement (`pnpm extract`, English) → PASS.
- [ ] **Step 5:** Commit `feat(web): a workspace switcher on top of the menu, and Mijn post as the start`.

### Task 14: The menu in the owner's order [after 5]

**Files:**
- Modify: `web/src/components/page.tsx` (`WorkspaceNav`), `web/src/api/queries.ts` (`menuCountsQuery(workspaceId)`, `refetchOnWindowFocus`, 60 s interval, invalidated by `refreshAfterTask` and the read mutations), `web/src/routes/settings.tsx` (Instellingen hub, spec 4.2 / B6)
- Test: `web/src/components/shell.test.tsx`

**Interfaces:** order and visibility exactly as spec 4.1; Mensen slot: one item `/w/$id/mensen` when the route exists (PA merged), else "Personen" and "Leden"; Afdelingen/Groepen (branch 5) right after it; Instellingen last, after a divider, for everyone, with sections by role; `/w/$id/ai` and `/koppel-app` reached from Instellingen.

- [ ] **Step 1: Failing tests**

```ts
it("lists the entries in the owner's order", ...)                 // names in order, by role (viewer, member, admin)
it("Inbox counts waiting letters and says lingering ones in words", ...)
it("Mijn post counts unread letters", ...)
it("Mijn taken warns in words when a task is late", ...)
it("asks one menu-counts call per page", ...)
it("Instellingen is last and open to members", ...)
```

- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5:** Commit `feat(web): the menu in the order Inbox, Mijn post, Mijn taken, … Instellingen`.

### Task 15: Mijn post and unread rows [after 5]

**Files:**
- Create: `web/src/routes/my-post.tsx` (`/w/$workspaceId/mijn-post`)
- Modify: `web/src/components/document-table.tsx` (unread dot, bold, "Ongelezen" in the row name, optional "Markeer als gelezen"), `web/src/routes/post.tsx` (filter "Alleen ongelezen"), `web/src/routes/document.tsx` (`markDocumentRead` once when the first page or the text has rendered; "Markeer als ongelezen"), `web/src/api/queries.ts` (`documentsQuery` filters `addressed_to_me`, `unread_only`)
- Test: `web/src/routes/my-post.test.tsx`, `web/src/routes/document.test.tsx`

- [ ] **Step 1: Failing tests**

```ts
it("Mijn post lists my letters, unread first marked", ...)
it("Alleen ongelezen filters and Alles gelezen marks the rows on screen", ...)
it("a member without a person is told why the list is empty", ...)
it("opening a letter marks it read once, not on prefetch", ...)
it("Markeer als ongelezen brings the dot back and the count up", ...)
it("the Inbox rows carry no unread mark", ...)
```

- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5:** Commit `feat(web): Mijn post, and which letters you have not read`.

### Task 16: "Blijft liggen" in the Inbox [after 5]

**Files:**
- Modify: `web/src/routes/inbox.tsx` (section above the list when `shows_lingering`), `web/src/routes/settings.tsx` (business only: switch and two day fields)
- Test: `web/src/routes/inbox-lingering.test.tsx`, `web/src/routes/settings.test.tsx`

- [ ] **Step 1: Failing tests**

```ts
it("sorters see the two groups with ages", ...)
it("plain members and home workspaces see nothing of it", ...)
it("the section collapses to one line when empty", ...)
it("an admin changes the thresholds in an organisation only", ...)
```

- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5:** Commit `feat(web): letters that lie too long, above the Inbox`.

### Task 17: Task actions follow the server's flags

After server Task 7; `task-actions.tsx` is not touched by branch 5, `tasks.tsx` only in one line.

**Files:**
- Modify: `web/src/components/task-actions.tsx`, `task-row.tsx`, `api/tasks.ts` (409 `task_taken` sentence)
- Test: `web/src/components/task-dialogs.test.tsx`, `web/src/routes/tasks.test.tsx`

- [ ] **Step 1: Failing tests**

```ts
it("shows Ik doe dit only on an unassigned task", ...)
it("shows Doet: Anne without buttons on a colleague's task", ...)
it("an admin sees Vrijgeven and Geef aan… on a colleague's task", ...)
it("a 409 task_taken shows the server's sentence and refreshes", ...)
```

- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5:** Commit `fix(web): only pick up a task nobody has`.

### Task 18: Notices in their own workspace [after 5]

**Files:** Modify `web/src/components/notice-bar.tsx` (`NoticeBar({ workspaceId })`), `web/src/components/page.tsx`. Test `web/src/components/notice-bar.test.tsx`.

- [ ] **Step 1–4:** Tests `shows a workspace notice only in its workspace`, `shows account notices everywhere`, `shows all on /werkruimtes`; FAIL → implement → PASS.
- [ ] **Step 5:** Commit `feat(web): a notice about a workspace shows there`.

### Task 19: Texts and catalogues

- [ ] **Step 1:** `pnpm extract`; fill every new English `msgstr` with the GLOSSARY terms (Mijn post / My post, Ongelezen / Unread, Markeer als gelezen / Mark as read, Markeer als ongelezen / Mark as unread, Alles gelezen / Mark all read, Blijft liggen / Lying too long, "ligt er # dagen" / "waiting # days", Werkruimte kiezen / Choose workspace, Alle werkruimtes / All workspaces, Vrijgeven / Release); `make messages` for the backend sentences (`task_taken`, lingering validation, 200-cap).
- [ ] **Step 2:** `pnpm test` (catalogue test) and `make lint` → PASS.
- [ ] **Step 3:** Commit `chore(i18n): English for the menu, my post and unread`.

---

## iOS (separate, in `Brievie_ios`)

Start after the server tasks are on `main` and after the iOS "Mijn taken" plan (`docs/ios-my-tasks-plan`, `2026-11-21-ios-mijn-taken.md`) is built: its `MyTasksModel`/`MyTasksView` are reused as the Taken tab. **That plan's premise changes:** it adds Mijn taken to the title menu and keeps Inbox as the start screen ("Q1"); decision 1 makes Mijn post the start and replaces the title menu by tabs. Build it as planned (the screen is the same), then this section moves it. One amendment to apply already in its Task 5: `canTakeOn` must be false when the task is assigned to someone else, and `canHandOver`/`canRelease` false on a colleague's task unless the member is an admin; read `can_take`, `can_hand_over`, `can_release` when the server sends them.

Global constraints: those of `Brievie_ios/CLAUDE.md` (strings only via `Localizable.xcstrings` with `en`; views dumb; VoiceOver and largest Dynamic Type; a field the server added later is optional with a default; Swift Testing; `make generate` when `project.yml` changes; never push without being asked).

- **I1. Sync the API.** `make sync-api HOST=../Brievie_host`; add `getMenuCounts`, `markDocumentRead`, `markDocumentUnread`, `markDocumentsRead`, `listLingeringLetters` to the generator config; decode `unread`, `can_*`, `MenuCountsOut` with defaults; 404 on an older server → feature hidden.
- **I2. `BrievieUI` style tokens (spec 10).** `Brand.swift`: `BrievieColor.surface`, `card`, `warning`, `unread`; `BrievieRadius`; `NumberText`; components `CountCard`, `RoundActionButton`, `WorkspacePill`, each with a `#Preview` in light, dark and AX5 and a snapshot-free test of its accessibility label.
- **I3. Workspace pill and sheet.** `FeatureWorkspace`: `WorkspacePickerSheet` (rows initial/name/kind/role, current checked); `WorkspacesModel.select` as today; choosing the current closes; one workspace → no button. Settings keeps server and unpair; its workspace list goes (the calendar switch per workspace moves to the sheet's row detail or stays in Settings under "Agenda").
- **I4. Tabs.** `App/RootView.swift` + new `MainTabView` (`FeatureWorkspace`): Inbox, Mijn post, Taken, Meer in that order; initial and after a switch: Mijn post; `.id(InboxIdentity)` stays so a switch rebuilds every tab; iPad (`horizontalSizeClass == .regular`): `NavigationSplitView` sidebar in the web's order. The scope title menu goes; a person's post is a "Voor wie" filter in Alle post. Test: `MainTabModelTests` (start tab, reset on switch, order).
- **I5. Mijn post screen.** `FeatureDocuments`: `DocumentsModel.Scope.myPost` (`addressed_to_me=true`), count cards (unread, tasks, lingering for sorters), round actions Scannen / Zoeken / Alles gelezen, unread dot and bold rows, swipe "Gelezen"/"Ongelezen"; the reader and the confirmed-letter screen call `markDocumentRead` once on appear.
- **I6. Inbox tab.** Existing Inbox list with round actions Scannen / Naam kiezen; for sorters in an organisation a "Blijft liggen" card that opens the two groups.
- **I7. Meer.** Alle post (search there), Labels, Prullenbak, Instellingen; no Mensen (PA 11).
- **I8. Texts and VoiceOver pass.** New keys in both catalogues (terms as Task 19); VoiceOver labels for cards ("4 ongelezen brieven in Mijn post"), pill ("Werkruimte: Bedrijf, kies een andere"), round buttons; largest Dynamic Type wraps cards to one column.

## Not in this plan (and where each hooks in)

- Department letters in Mijn post (spec B2's filter button): `list_documents(include_department=…)`, Task 4's hook.
- Working days for "Blijft liggen": `lingering.age_days` (pure) is the one place.
- Push or e-mail for lingering post: would read `lingering_letters` as the system per sorter; not designed.
- Letter status badges (LS) and Mensen (PA): their own plans; the menu slot and the row component are ready for them.

## Self-review

- Spec coverage: S 4.1–4.4 → Tasks 12–14; S 4.5 and 10 → I2–I8; S 5 → Tasks 1–4, 15; S 6 → Tasks 1, 5, 16; S 7 → Tasks 7, 17, iOS note; S 8 → Tasks 8, 11, 12, 18; S 9 → Tasks 4–8; S 2.1 bug → Tasks 12, 13 (tests that make the dead click impossible).
- Every new list and count starts at `visible_documents` and is measured (Task 10).
- Every web task that touches a file of `feat/wie-ziet-wat-5` is marked [after 5].
