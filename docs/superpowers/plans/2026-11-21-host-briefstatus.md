# Host "Briefstatus" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every letter has a status derived from its review and its tasks (Nieuw, Openstaand, Wacht op reactie, Afgehandeld) plus the marker "Te laat"; tasks can wait for a reply (shared, dated) or be snoozed (personal); "Brief afhandelen" completes the open tasks after a confirmation the server checks; status, filter and counts show under a person, in the Inbox, on the letter and in "Mijn taken".

**Architecture:** A pure module `apps.tasks.letter_state` turns a letter's tasks into three date-free facts (`task_open_count`, `task_wait_until`, `task_overdue_from`) and those facts plus today into a status and a marker. The facts are cached on `Document` by one writer, `letter_state.sync(document)`, called in the same transaction as every task change under the letter's row lock, and repaired by the nightly `verify_visibility_job`. Lists and counts filter on the three columns through `visibility.visible_documents` (status is never stored). `Task.waiting_until` is shared; `TaskSnooze` rows are per user under a forced RLS policy. History lines go through `documents.organise`.

**Tech Stack:** Python 3.13, Django 6.1, Django Ninja, PostgreSQL 18 (forced RLS), pytest; React 19, TanStack Router/Query, React Aria Components, Lingui, Vitest, MSW. No new dependency.

**Spec:** `docs/superpowers/specs/2026-11-21-briefstatus-design.md`, cited "S 4.2". "Mijn taken": `docs/superpowers/specs/2026-11-21-mijn-taken-design.md` ("MT S 6").

## Global Constraints

From `CLAUDE.md` (verbatim where quoted):

- "Every table: UUID `id`, `created_at`, `updated_at`. Every workspace content table: `workspace_id` (subclass `WorkspaceModel`) and RLS enabled **and forced**." → `TaskSnooze`.
- "`mypy --strict` and `ruff check` clean. No `Any`, no bare `except`." "Money is `Decimal`; datetimes are timezone-aware." Dates here are days (`date`), today is `tasks.services.task_today()` (Europe/Amsterdam).
- "API only under `/api/v1`, additive changes only. Every endpoint: typed schemas and an explicit camelCase `operation_id`." "Every non-2xx body: `{"code": "<snake_case>", "message": "<Dutch sentence>"}` via `apps.common.errors.ApiError`."
- "Thin endpoints: validate, call one function in the app's `services.py`, return." "Every mutating service function is wrapped in `transaction.atomic`."
- "A field added to an existing API schema has a default" and "Nullable fields in API schemas are primitives only, and nullable inputs have a default; the contract test forbids `anyOf`." (`date | None = None` is already used by `TaskItemOut.deadline`.)
- "A member-facing read of documents starts from `apps.documents.visibility.visible_documents`." "A trashed letter is out of every read."
- "Automatic changes have no actor and are marked." → `letter_handled` / `letter_reopened` from `sync`: origin `system`, no actor. "Never record a user for what nobody did."
- "No document content, tokens, passwords or BSNs in logs." No log line names a description, sender, amount or date of a task; the nightly repair logs counts only.
- User-facing text in Dutch (B1) and English: backend `gettext` + `make messages`, web Lingui `t` + `pnpm extract`; English terms from `docs/GLOSSARY.md`. After every API change: `make openapi`, `cd web && pnpm gen:api`, commit both generated files with the change.
- "Tests first for logic. Conventional commits." Each commit ends with `Co-Authored-By: Claude <noreply@anthropic.com>`.

From the environment:

- **Backend tests run in CI only.** Never connect to the PostgreSQL on this Mac, never `make dev`, `make test`, `docker` or `./brievie`. A backend "run" step means: commit, push `feat/letter-status`, read the `backend` job (and `visibility-perf` where named). `make lint`, `make typecheck` and every `pnpm` command run locally.
- **Start point:** branch from `main` after PR #39 (`feat/wie-ziet-wat-4`) is merged; it changes no function this plan touches (checked: its diff stays out of `list_documents`, `inbox_counts`, `list_persons`, `tasks/services`, `review`, `duplicates`), but it edits `documents/visibility.py` and `documents/api.py`, so rebase first. Migration numbers: take the next free number in `documents/migrations` (0020 on 24c0318) and `tasks/migrations` (0006); renumber in the same commit if another branch landed first.

From the spec, not left to the builder:

- **Status (S 4.1, 4.2):** `new` = `status != done`; `handled` = done ∧ `task_open_count = 0`; `waiting` = done ∧ count > 0 ∧ `task_wait_until > today`; `open` = done ∧ count > 0 ∧ (`task_wait_until` null ∨ ≤ today); `overdue` = done ∧ `task_overdue_from <= today`.
- **Facts:** `task_open_count` = open tasks; `task_wait_until` = min of `waiting_until` over open tasks when every open task has one, else null; `task_overdue_from` = min over open tasks with a deadline of `max(deadline + 1 day, waiting_until)` (`waiting_until` ignored when null), null when none.
- **Dates (S 5):** `waiting_until` and snooze `until` must be after today and at most today + 365; snooze `until` at most the task's deadline (when it has one; B2 default). Same value again: no write, no line.
- **Lines (S 4.3, LS8):** `letter_handled` / `letter_reopened` only when the handled-ness of a reviewed, not trashed letter changes; origin `system` without actor, except `handleDocument` (origin `user`, actor). No line when a date passes.
- **Copy:** spec section 12 verbatim.

## Review Focus

1. **No status or count of a letter the member may not read.** Every list and count starts at `visible_documents`; snooze rows are own-only under forced RLS. → Task 11 (`test_status_counts_hide_unreadable_letters`, `test_snooze_rows_are_own_only`).
2. **The cache cannot drift.** One writer, every task writer calls it, the nightly repair finds nothing after any sequence of operations. → Task 3 (`test_only_letter_state_writes_task_facts`, `test_every_task_writer_syncs`), Task 3 (`test_random_operations_leave_nothing_to_repair`).
3. **Nothing automatic happens at a date.** No job, no write, no line when a waiting date or deadline passes. → Task 1 (pure), Task 6 (`test_no_line_when_waiting_ends`).
4. **The confirmation of "Brief afhandelen" is real.** Mismatched ids → 409, unconfirmed payment → 409, nothing completed. → Task 7.
5. **250 ms.** The new list filters and counts at 100,000 letters, four actors, E on/off. → Task 4 (go/no-go before any API).
6. **Older clients.** Every new field defaulted, no `anyOf`; `listMyTasks` without `include_waiting` never returns an unknown `group`. → Tasks 5, 10, 12.

---

## File structure

```
backend/apps/
  tasks/
    letter_state.py   # NEW: pure facts + status (TaskFacts, LetterFacts, facts_of, status_of)
    letter_sync.py    # NEW: the one writer sync(document, *, actor=None) and verify_all()
    snooze.py         # NEW: snooze(), unsnooze(), snoozed_until()
    waiting.py        # NEW: set_waiting()
    models.py         # Task.waiting_until; TaskSnooze
    services.py       # lock order; complete/reopen/set_deadline call sync; my_tasks/count_my_tasks
    schemas.py        # TaskItemOut + waiting_until, wait_over, snoozed_until; MyTaskCountsOut + waiting, snoozed; WaitingIn, SnoozeIn
    api.py            # + setTaskWaiting, snoozeTask; listMyTasks include_waiting
    migrations/00NN_waiting_and_snooze.py
  documents/
    models.py         # Document task_* columns + index; DocumentEvent.on_date + 4 kinds
    handle.py         # NEW: handle_document()
    services.py       # letter_status filter, status_counts(), summary fields
    schemas.py        # DocumentSummaryOut + 4 fields; LetterStatusCountsOut; HandleDocumentIn; TaskOut + 3; DocumentEventOut.on_date
    api.py            # listDocuments params; countLetterStatuses; handleDocument
    organise.py       # record_letter_handled, record_letter_reopened, record_task_waiting
    review.py         # sync at confirm step 2
    duplicates.py     # sync both letters after _merge_tasks
    jobs.py           # verify_visibility_job also runs letter_sync.verify_all
    migrations/00NN_letter_status.py
  workspaces/rls.py   # own_rows_of_visible_tasks policy helper
backend/tests/
  test_letter_state.py  test_letter_sync.py  test_letter_status_api.py  test_handle_document.py
  test_task_waiting.py  test_task_snooze.py  test_my_tasks_waiting.py  test_leak_letter_status.py
  perf/test_visibility_at_scale.py (+5 calls)  perf/data.py (waiting tasks, snoozes, done letters)
web/src/
  components/letter-status.tsx (+ test)  components/handle-dialog.tsx  components/waiting-dialog.tsx
  components/snooze-dialog.tsx  components/document-table.tsx  components/task-row.tsx  task-list.tsx
  routes/post.tsx (+ test)  routes/inbox.tsx  routes/document.tsx  routes/tasks.tsx  routes/persons.tsx
  api/queries.ts  locales/{nl,en}/messages.po
docs/ DECISIONS.md  GLOSSARY.md  api/openapi.json   CLAUDE.md (rules in short)
```

---

## Slice S: server

### Task 1: The status rule, pure

**Files:**
- Create: `backend/apps/tasks/letter_state.py`, `backend/tests/test_letter_state.py`

**Interfaces:**
- `@dataclass(frozen=True) class TaskFacts: open: bool; deadline: date | None; waiting_until: date | None`
- `@dataclass(frozen=True) class LetterFacts: open_count: int; wait_until: date | None; overdue_from: date | None`
- `class LetterStatus(StrEnum): NEW = "new"; OPEN = "open"; WAITING = "waiting"; HANDLED = "handled"`
- `facts_of(tasks: Iterable[TaskFacts]) -> LetterFacts` (S 4.2 definitions).
- `status_of(reviewed: bool, facts: LetterFacts, today: date) -> tuple[LetterStatus, bool]` (status, overdue).
- No Django import (a source test checks it, like `apps.rules.engine`).

- [ ] **Step 1: Failing tests:**

```python
def test_not_reviewed_is_new_whatever_the_tasks(): ...
def test_reviewed_without_tasks_is_handled(): ...
def test_all_done_is_handled(): ...
def test_one_active_open_task_is_open(): ...
def test_every_open_task_waiting_after_today_is_waiting(): ...
def test_one_waiting_one_active_is_open(): ...
def test_waiting_date_today_is_open_again(): ...                 # wait_until == today -> open
def test_overdue_needs_deadline_before_today(): ...               # deadline == today -> not overdue
def test_waiting_task_is_not_overdue_until_its_date(): ...        # deadline 1 nov, waiting 15 nov: overdue from 15 nov
def test_overdue_from_is_deadline_plus_one_without_waiting(): ...
def test_undated_tasks_never_overdue(): ...
def test_facts_agree_with_direct_rule(): ...                      # 500 seeded random task lists and days: status_of(facts_of(ts)) == the rule applied to ts directly
def test_module_imports_no_django(): ...
```

- [ ] **Step 2:** `make lint typecheck`; push → FAIL. **Step 3:** Implement. **Step 4:** Push → PASS.
- [ ] **Step 5:** Commit `feat(tasks): a letter's status from its tasks, as a pure rule`.

### Task 2: Columns, index, event kinds, waiting and snooze tables

**Files:**
- Modify: `backend/apps/documents/models.py`, `backend/apps/tasks/models.py`, `backend/apps/workspaces/rls.py`
- Create: `backend/apps/documents/migrations/00NN_letter_status.py`, `backend/apps/tasks/migrations/00NN_waiting_and_snooze.py`, `backend/tests/test_letter_status_fields.py`
- Modify: `docs/DECISIONS.md` (LS1–LS8), `docs/GLOSSARY.md` (spec 12)

**Interfaces:**
- `Document.task_open_count = PositiveIntegerField(default=0)`, `task_wait_until = DateField(null=True, blank=True)`, `task_overdue_from = DateField(null=True, blank=True)`; comment: "Derived from the tasks; written only by `apps.tasks.letter_sync`".
- `Index(fields=["workspace", "-created_at"], condition=Q(status="done", task_open_count__gt=0), name="documents_document_open_tasks")`.
- `DocumentEvent.on_date = DateField(null=True, blank=True)`; `Kind.TASK_WAITING = "task_waiting"`, `TASK_WAITING_ENDED = "task_waiting_ended"`, `LETTER_HANDLED = "letter_handled"`, `LETTER_REOPENED = "letter_reopened"`.
- `Task.waiting_until = DateField(null=True, blank=True)`.
- `class TaskSnooze(WorkspaceModel)`: `same_workspace_fields = ("task",)`; `task = FK(Task, CASCADE, related_name="snoozes")`, `user = FK(user, CASCADE, related_name="+")`, `until = DateField()`; `UniqueConstraint(["task", "user"], name="tasks_tasksnooze_unique")`.
- `rls.own_rows_of_visible_tasks(table: str) -> RunSQL`: restrictive policy `(<system actor> OR user_id = <actor>) AND EXISTS (SELECT 1 FROM tasks_task t WHERE t.id = task_id)` (the task's own policy applies inside), using the existing actor functions of `rls.actor_functions`; plus `enable_rls` (enabled and forced).
- Migration backfill: as `SYSTEM` per workspace (pattern of `documents/0014`), one `UPDATE … FROM (SELECT document_id, count(*) FILTER (WHERE status='open'), …)`; the same SQL constant `letter_sync.RECOMPUTE` is used later (copy it into the migration, migrations must not import moving code). Reverse: drop columns.

- [ ] **Step 1: Failing tests:**

```python
def test_document_task_facts_default_empty(): ...
def test_open_tasks_index_exists(): ...                  # pg_indexes: documents_document_open_tasks WHERE status='done' AND task_open_count > 0
def test_new_event_kinds_and_on_date(): ...
def test_task_waiting_until_defaults_null(): ...
def test_tasksnooze_rls_enabled_and_forced(): ...        # pg_class relrowsecurity and relforcerowsecurity
def test_tasksnooze_unique_per_task_and_user(): ...
def test_backfill_matches_the_pure_rule(): ...           # migrate to before, seed tasks, migrate, compare with facts_of
```

- [ ] **Step 2:** Push → FAIL. **Step 3:** Implement, `makemigrations`, hand-write the RLS and backfill parts; reversible. **Step 4:** Push → PASS.
- [ ] **Step 5:** Commit `feat(documents): task facts on the letter, waiting and snooze tables`.

### Task 3: The one writer, the lock order and the nightly repair

**Files:**
- Create: `backend/apps/tasks/letter_sync.py`, `backend/tests/test_letter_sync.py`
- Modify: `backend/apps/tasks/services.py` (`_get_for_update`, `complete_task`, `reopen_task`, `set_deadline`), `backend/apps/documents/review.py` (`_store_confirmation` step 2), `backend/apps/documents/services.py` (`reopen_document`), `backend/apps/documents/duplicates.py` (`_merge_tasks` caller), `backend/apps/documents/jobs.py` (`verify_visibility_job`), `backend/apps/documents/organise.py`, `backend/tests/test_actor_callers.py` (add `apps/tasks/letter_sync.py`)

**Interfaces:**
- `sync(document: Document, *, actor: User | None = None) -> None`: as `system_actor()`, read the letter's tasks (`status`, `deadline`, `waiting_until`), `facts_of`, compare with the stored columns, write them (`update_fields`) when different. When the document is `done`, not trashed and `(old.open_count == 0) != (new.open_count == 0)`: `organise.record_letter_handled(document, actor=actor)` or `organise.record_letter_reopened(document)`; with `actor` the line has origin `user`, without it origin `system` and no actor. Caller holds the letter's row lock.
- `verify_all() -> int`: per workspace as the system, one `UPDATE documents_document d SET … FROM (aggregate) WHERE … IS DISTINCT FROM …` (`RECOMPUTE`); returns the number of letters fixed; logged as a count. No history lines (a repair is not an event).
- Lock order: `tasks.services._get_for_update` first takes `Document.objects.select_for_update(no_key=True)` on the task's letter (as the member, `LIVE`), then the task `FOR UPDATE OF self`. Letterless tasks: task only.
- Callers of `sync` (exactly): `complete_task`, `reopen_task`, `set_deadline`, `waiting.set_waiting`, `handle.handle_document` (once, with actor), `review._store_confirmation` (step 2, as the system, after the status write), `services.reopen_document`, `duplicates._merge_tasks` (both letters, after the loop). `purge` needs none (the letter goes).

- [ ] **Step 1: Failing tests:**

```python
def test_sync_writes_facts_after_complete(): ...
def test_last_task_completed_writes_system_handled_line(): ...   # origin system, actor None
def test_reopen_on_handled_letter_writes_system_reopened_line(): ...
def test_confirm_without_tasks_is_handled_with_line(): ...
def test_confirm_with_tasks_writes_no_status_line(): ...
def test_no_line_for_trashed_or_unreviewed_letter(): ...
def test_merge_moving_open_task_reopens_kept_letter(): ...
def test_reopen_document_clears_ai_tasks_facts(): ...
def test_only_letter_sync_writes_task_facts(): ...               # source scan: task_open_count/task_wait_until/task_overdue_from assigned only in letter_sync.py and the migration
def test_every_task_writer_syncs(): ...                          # source scan of the caller list above
def test_task_change_locks_letter_before_task(): ...             # captured SQL order: documents_document FOR NO KEY UPDATE before tasks_task FOR UPDATE
def test_random_operations_leave_nothing_to_repair(): ...        # 300 seeded ops (confirm, complete, reopen, date, wait, merge, reopen_document) -> verify_all() == 0
def test_verify_all_repairs_a_tampered_row(): ...                # set task_open_count wrong as owner/system -> 1 fixed, value right
```

- [ ] **Step 2:** Push → FAIL. **Step 3:** Implement. **Step 4:** Push → PASS.
- [ ] **Step 5:** Commit `feat(tasks): one writer keeps the letter's task facts, repaired nightly`.

### Task 4: Measure first (go/no-go)

**Files:**
- Modify: `backend/tests/perf/data.py` (per 100,000 letters: 80 % done; tasks as now plus 5 % of open tasks waiting, a third of them with a past date; 2 % of open tasks snoozed by their assignee), `backend/tests/perf/test_visibility_at_scale.py`
- Add to `FILTERED` and `_list`: `list_documents_open` (`letter_status="open"`), `list_documents_own_person_waiting`, `letter_status_counts` (no person), `letter_status_counts_own_person`; `list_my_tasks` and `count_my_tasks` are already there and now include the waiting/snooze filters once Task 10 lands (re-run then).
- Temporary: until Task 5 exists, measure the queries through a test helper that builds the same `Q` from `letter_state` (move the helper into `documents.services` in Task 5; the perf names stay).

- [ ] **Step 1:** Push; run the `visibility-perf` workflow on the branch (workflow_dispatch).
- [ ] **Step 2: Verdict.** Every new call median <= 250 ms for the four actors, E on and off → go. If `letter_status_counts*` misses only through `handled`: drop `handled` from the counts (LS2; `LetterStatusCountsOut.handled` stays in the schema, always 0, and the web shows no number) and re-measure. Anything else missing: stop and report the EXPLAIN artifact to the owner before building on.
- [ ] **Step 3:** Commit `test(perf): measure letter status lists and counts at 100,000 letters`.

### Task 5: Status on lists, the filter and the counts

**Files:**
- Modify: `backend/apps/documents/services.py`, `schemas.py`, `api.py`
- Create: `backend/tests/test_letter_status_api.py`

**Interfaces:**
- `services.letter_status_q(status: str, today: date) -> Q` (S 4.2 expressions; 422 `validation_error` "Deze status kennen we niet." for another value); `overdue_q(today) -> Q`.
- `list_documents(..., letter_status: str = "", overdue: bool = False)`.
- `status_counts(membership: Membership, *, person_id: UUID | None) -> LetterStatusCounts` (dataclass of five ints): one aggregate with `Count(filter=…)` over `visible_documents(membership)` (narrowed by `addressee_ids__contains=[person_id]` when given).
- `_summary_out`: `letter_status`, `overdue`, `open_task_count`, `waiting_until` (only when `waiting`) via `letter_state.status_of` with `task_today()` computed once per request.
- `DocumentSummaryOut` + `letter_status: str = ""`, `overdue: bool = False`, `open_task_count: int = 0`, `waiting_until: date | None = None`. `LetterStatusCountsOut(new, open, waiting, handled, overdue: int)`.
- API: `listDocuments` params `letter_status: str = ""`, `overdue: bool = False` (description updated); `countLetterStatuses` GET `/workspaces/{workspace_id}/documents/status-counts`, query `person_id: UUID | None = None`, `response={200: LetterStatusCountsOut, 404, **AUTH_ERRORS}`.

- [ ] **Step 1: Failing tests:**

```python
def test_summary_carries_status_and_overdue(client): ...
def test_filter_each_status(client): ...                     # one letter per status; each filter returns exactly it
def test_overdue_filter(client): ...
def test_unknown_status_is_422(client): ...
def test_counts_for_person_and_for_all(client): ...
def test_counts_unknown_person_are_zero(client): ...
def test_waiting_until_only_when_waiting(client): ...
def test_today_is_amsterdam(client, freezer): ...            # 23:30 UTC on the 30th counts as the 1st
def test_trashed_letters_not_counted(client): ...
def test_device_token_can_list_and_count(client): ...
```

- [ ] **Step 2:** Push → FAIL. **Step 3:** Implement; replace the Task 4 helper. **Step 4:** Push → PASS.
- [ ] **Step 5:** `make openapi && cd web && pnpm gen:api`; commit `feat(documents): letter status on lists, a status filter and counts`.

### Task 6: History lines and texts

**Files:**
- Modify: `backend/apps/documents/organise.py`, `backend/apps/documents/schemas.py` (`DocumentEventOut.on_date: date | None = None`), `backend/apps/documents/services.py` (`list_document_events` maps `on_date`)
- Test: `backend/tests/test_letter_sync.py` (extend), `backend/tests/test_task_waiting.py` (Task 8 uses these)

**Interfaces:**
- `organise.record_letter_handled(document, *, actor: User | None)`, `record_letter_reopened(document)`, `record_task_waiting(document, *, actor: User, until: date | None)` (kind `task_waiting` with `on_date`, or `task_waiting_ended` when None). None of them undoable (`_UNDOABLE` unchanged).

- [ ] **Step 1: Failing tests:**

```python
def test_event_out_carries_on_date(client): ...
def test_new_kinds_not_undoable(client): ...                 # undoDocumentEvent -> existing not_undoable error
def test_no_line_when_waiting_ends(freezer): ...             # set wait, move clock past it, list events: no new line, no write
```

- [ ] **Step 2–4:** Push → FAIL; implement; push → PASS.
- [ ] **Step 5:** Commit `feat(documents): history lines for a letter that is handled or open again`.

### Task 7: "Brief afhandelen"

**Files:**
- Create: `backend/apps/documents/handle.py`, `backend/tests/test_handle_document.py`
- Modify: `backend/apps/documents/schemas.py` (`HandleDocumentIn`), `api.py` (`handleDocument`), `backend/apps/tasks/services.py` (an internal `_complete_locked(task, user, origin)` shared with `complete_task`, so `handle` completes without a `sync` per task)

**Interfaces:**
- `handle_document(*, membership: Membership, user: User, document_id: UUID, task_ids: list[UUID], payments_confirmed: bool) -> Document` in `transaction.atomic`: letter via `visible_documents(...).select_for_update(no_key=True)` (404 `document_not_found`), `require_editable`; 409 `document_not_reviewed`; open task ids (all open, waiting included) locked `FOR UPDATE`; set differs → 409 `tasks_changed`; a `pay` task and not `payments_confirmed` → 409 `payment_not_confirmed`; `_complete_locked` each (lines `task_completed`, actor; `waiting_until` cleared; snooze rows of the task deleted); `letter_sync.sync(document, actor=user)`. Already handled and `task_ids == []`: return unchanged.
- `HandleDocumentIn(InputSchema)`: `task_ids: list[UUID] = Field(default_factory=list, max_length=200)`, `payments_confirmed: bool = False`.
- `handleDocument` POST `/workspaces/{workspace_id}/documents/{document_id}/handle`, `min_role=MEMBER`, `response={200: DocumentOut, 404, 409, **SESSION_WRITE_ERRORS, **BODY_ERRORS}`, answers `services.document_out_for(document)`.

- [ ] **Step 1: Failing tests:**

```python
def test_handle_completes_exactly_the_shown_tasks(client): ...
def test_handle_writes_user_line_and_no_system_line(client): ...
def test_tasks_changed_when_a_task_was_added_or_ticked(client): ...  # nothing completed
def test_payment_needs_confirmation(client): ...
def test_waiting_tasks_are_handled_too(client): ...
def test_not_reviewed_is_409(client): ...
def test_already_handled_empty_ids_is_noop(client): ...
def test_viewer_is_403_and_emergency_only_is_refused(client): ...
def test_hidden_letter_is_404(client): ...
def test_device_token_can_handle(client): ...
```

- [ ] **Step 2–4:** Push → FAIL; implement; push → PASS.
- [ ] **Step 5:** `make openapi && cd web && pnpm gen:api`; commit `feat(documents): handle a letter after confirming the tasks it closes`.

### Task 8: "Wacht op reactie"

**Files:**
- Create: `backend/apps/tasks/waiting.py`, `backend/tests/test_task_waiting.py`
- Modify: `backend/apps/tasks/schemas.py` (`WaitingIn`, `TaskItemOut` + `waiting_until`, `wait_over`), `backend/apps/documents/schemas.py` (`TaskOut` + `waiting_until: date | None = None`, `wait_over: bool = False`, `snoozed_until: date | None = None`), `api.py`, `services.py` (`complete_task` clears `waiting_until`)

**Interfaces:**
- `set_waiting(*, workspace: Workspace, user: User, task_id: UUID, until: date | None) -> Task` in `transaction.atomic`, through `_get_for_update` (lock order, `require_editable`); done → 409 `task_done` "Deze taak is al afgerond."; `until` not in (today, today + 365] → 422 `validation_error` "Kies een datum na vandaag, binnen een jaar."; same value → return; else write, `organise.record_task_waiting`, `letter_sync.sync`.
- `WaitingIn(InputSchema)`: `until: date | None = None`. `setTaskWaiting` POST `{_TASK}/waiting`, `min_role=MEMBER`.

- [ ] **Step 1: Failing tests:**

```python
def test_wait_moves_letter_to_waiting_and_writes_line(client): ...
def test_wait_on_one_of_two_tasks_keeps_letter_open(client): ...
def test_stop_waiting_writes_ended_line(client): ...
def test_wait_date_must_be_after_today_within_a_year(client): ...
def test_wait_may_pass_the_deadline(client): ...                 # B2 default
def test_same_date_again_writes_nothing(client): ...
def test_completing_clears_waiting(client): ...
def test_wait_over_flag_when_date_arrives(client, freezer): ...
def test_viewer_cannot_set_waiting(client): ...
```

- [ ] **Step 2–4:** Push → FAIL; implement; push → PASS.
- [ ] **Step 5:** `make openapi && cd web && pnpm gen:api`; commit `feat(tasks): wait for a reply until a date`.

### Task 9: "Uitgesteld tot", per person

**Files:**
- Create: `backend/apps/tasks/snooze.py`, `backend/tests/test_task_snooze.py`
- Modify: `backend/apps/tasks/schemas.py` (`SnoozeIn`, `TaskItemOut.snoozed_until`), `api.py`

**Interfaces:**
- `snooze(*, membership: Membership, task_id: UUID, until: date | None) -> Task` in `transaction.atomic`: the task as the member (404 `task_not_found` like `assignment._visible`), open (409 `task_done`); `until` None → delete own row; else (today, today + 365] (422) and `<= task.deadline` when dated (422 `snooze_after_deadline` "Je kunt niet uitstellen tot na de datum van de taak."); upsert own row. No history, no audit, no `sync`.
- `snoozed_until(tasks: Sequence[Task], user_id: UUID, today: date) -> dict[UUID, date]`: one query for a page.
- `snoozeTask` POST `{_TASK}/snooze`, `workspace_member(request, workspace_id)` (any role), body `SnoozeIn(until: date | None = None)`.

- [ ] **Step 1: Failing tests:**

```python
def test_snooze_hides_for_me_only(client): ...                   # Anne snoozes; Piet's my-tasks unchanged
def test_snooze_does_not_change_letter_status_or_history(client): ...
def test_snooze_not_after_deadline(client): ...
def test_overdue_task_cannot_be_snoozed(client): ...             # deadline < today -> no valid date -> 422
def test_unsnooze(client): ...
def test_viewer_may_snooze(client): ...
def test_completion_deletes_snoozes(client): ...
```

- [ ] **Step 2–4:** Push → FAIL; implement; push → PASS.
- [ ] **Step 5:** `make openapi && cd web && pnpm gen:api`; commit `feat(tasks): snooze a task in your own list`.

### Task 10: "Mijn taken" leaves waiting and snoozed tasks out

**Files:**
- Modify: `backend/apps/tasks/services.py` (`my_tasks`, `count_my_tasks`), `backend/apps/tasks/mine.py` (`Group.WAITING`, `Group.SNOOZED`), `schemas.py`, `api.py`
- Create: `backend/tests/test_my_tasks_waiting.py`

**Interfaces:**
- `ACTIVE(today) = Q(waiting_until__isnull=True) | Q(waiting_until__lte=today)`; `MINE_SNOOZED(user, today) = Exists(TaskSnooze.objects.filter(task=OuterRef("pk"), user_id=user, until__gt=today))`.
- `my_tasks(..., include_waiting: bool = False)`: open status: rows `ACTIVE & ~MINE_SNOOZED` in the date groups; with `include_waiting` also the waiting rows (group `waiting`, by `waiting_until`) and the snoozed rows (group `snoozed`, by `until`), each capped inside `MY_LIST_MAX`. Done status unchanged.
- `count_my_tasks`: every existing number over `ACTIVE & ~MINE_SNOOZED`; `waiting` = part mine & ~ACTIVE; `snoozed` = part mine & ACTIVE & MINE_SNOOZED.
- `listMyTasks` + `include_waiting: bool = False`; description says older callers never get the new groups.

- [ ] **Step 1: Failing tests:**

```python
def test_waiting_task_out_of_groups_and_counts(): ...
def test_waiting_over_task_back_in_its_group_with_flag(freezer): ...
def test_snoozed_task_out_for_me_only(): ...
def test_include_waiting_adds_both_groups(): ...
def test_without_include_waiting_no_new_group_values(): ...
def test_overdue_count_excludes_waiting(): ...
def test_counts_match_list_lengths(): ...                    # mine == len(active list) etc.
```

- [ ] **Step 2–4:** Push → FAIL; implement; push → PASS; re-run `visibility-perf` (Task 4 calls `list_my_tasks`, `count_my_tasks`).
- [ ] **Step 5:** `make openapi && cd web && pnpm gen:api`; commit `feat(tasks): waiting and snoozed tasks leave Mijn taken's groups and counts`.

### Task 11: Leak tests

**Files:**
- Create: `backend/tests/test_leak_letter_status.py` (in the `office()` world of `visibility_support`, states E on/off, D on/off)

- [ ] **Step 1: Tests (expected to pass; any failure is a bug to fix here):**

```python
def test_status_counts_hide_unreadable_letters(): ...      # Piet's private overdue letter: not in Anne's counts or lists, also not as handled
def test_status_filter_never_widens(): ...                 # every filter result ⊆ unfiltered list, per actor and state
def test_snooze_rows_are_own_only(): ...                   # Anne cannot read, update or delete Piet's snooze row (as brievie_app)
def test_handle_and_wait_on_hidden_letter_are_404(): ...
def test_letter_lines_follow_letter_sight(): ...           # letter_handled lines only through listDocumentEvents of a visible letter
def test_seam_test_knows_status_counts(): ...              # tests/test_visibility_seams.py covers status_counts
```

- [ ] **Step 2:** Push → PASS (fix and re-push on failure).
- [ ] **Step 3:** Commit `test(documents): letter status leaks nothing of post you may not read`.

### Task 12: Contract, texts, docs

**Files:**
- Modify: `backend/locale/en/LC_MESSAGES/django.po` (`make messages`, fill English from spec 12, run again for `.mo`), `docs/DECISIONS.md`, `docs/GLOSSARY.md`, `CLAUDE.md` (rules in short: "Only `apps.tasks.letter_sync` writes `Document.task_open_count`, `task_wait_until`, `task_overdue_from`; every task writer calls `sync` under the letter's row lock (letter first, then task)." and "A letter's status is derived (`apps.tasks.letter_state`), never stored; nothing happens when a date passes."), `docs/api/openapi.json`, `web/src/api/schema.d.ts`
- Test: existing contract test against `openapi_1b.json` / `openapi_1c.json`

- [ ] **Step 1:** Push → contract test PASS (no field removed, all new ones defaulted, no `anyOf`); i18n test PASS (no empty `msgstr`).
- [ ] **Step 2:** Commit `docs: letter status rules, glossary and English texts`.

---

## Slice W: web

### Task 13: Status badge and the person view in "Alle post"

**Files:**
- Create: `web/src/components/letter-status.tsx` (+ `letter-status.test.tsx`)
- Modify: `web/src/components/document-table.tsx` (column "Status"), `web/src/routes/post.tsx` (+ test), `web/src/api/queries.ts` (`documentsQuery` params, `letterStatusCountsQuery`), `web/src/routes/persons.tsx` (name links to `/post?persoon=`)

**Interfaces:**
- `<LetterStatusBadge status overdue waitingUntil />`: badges of spec 6.1, "Te laat" in words.
- `post.tsx` search: `status?: "nieuw" | "openstaand" | "wacht" | "afgehandeld"`, `telaat?: true` mapped to `letter_status`/`overdue`; heading "Post van {name}" when `persoon`; count line from `countLetterStatuses` (Lingui `plural`); `ToggleButtonGroup` with counts; "Alleen te laat" checkbox; empty "Geen brieven met deze status.".

- [ ] **Step 1: Failing tests (Vitest, by role):** badge texts per status; overdue in words; filter changes the URL and the query; count line skips zeros; person heading; persons page link.
- [ ] **Step 2:** `cd web && pnpm test` → FAIL. **Step 3:** Implement; `pnpm extract`, fill English. **Step 4:** `pnpm test && pnpm lint && pnpm typecheck` → PASS.
- [ ] **Step 5:** Commit `feat(web): letter status and a person's post by status`.

### Task 14: The letter: status, "Brief afhandelen", waiting

**Files:**
- Create: `web/src/components/handle-dialog.tsx`, `web/src/components/waiting-dialog.tsx`
- Modify: `web/src/routes/document.tsx` (+ test), `web/src/components/task-list.tsx`, `web/src/components/task-actions.tsx`

**Interfaces:**
- Handle dialog lists `document.tasks` open ones (description, date, amount), the payment checkbox when any `type === "pay"`, sends `{task_ids, payments_confirmed}`; on 409 `tasks_changed` refetches the letter and shows the server's message in the dialog.
- Waiting dialog: React Aria `DatePicker` (min tomorrow, max +365), hint of spec 12; "Niet meer wachten" when set.

- [ ] **Step 1: Failing tests:** button only for member+ and status open/waiting; dialog names the tasks; "Afhandelen" disabled until the payment box is ticked; 409 path; waiting dialog bounds; badge on the task.
- [ ] **Step 2–4:** as Task 13.
- [ ] **Step 5:** Commit `feat(web): handle a letter and wait for a reply`.

### Task 15: "Mijn taken": waiting and snoozed groups, snooze dialog

**Files:**
- Create: `web/src/components/snooze-dialog.tsx`
- Modify: `web/src/routes/tasks.tsx` (+ test), `web/src/components/task-row.tsx`, `web/src/api/queries.ts` (`include_waiting=true`)

- [ ] **Step 1: Failing tests:** collapsed groups "Wacht op reactie (n)" / "Uitgesteld (n)" (a `Disclosure`), markers "Wachten voorbij sinds …", "Uitgesteld tot …"; snooze dialog max = deadline; nav count unchanged by waiting tasks (uses `mine`).
- [ ] **Step 2–4:** as Task 13.
- [ ] **Step 5:** Commit `feat(web): waiting and snoozed tasks in Mijn taken`.

### Task 16: Inbox

**Files:**
- Modify: `web/src/routes/inbox.tsx` (+ `inbox.test.tsx`)

- [ ] **Step 1: Failing test:** an `unassigned` row shows status and "Te laat"; `review` and `new` rows show none.
- [ ] **Step 2–4:** as Task 13.
- [ ] **Step 5:** Commit `feat(web): status on reviewed letters in the Inbox`.

---

## iOS (in `Brievie_ios`, after the server API is merged)

Short list; the iOS plan details them.

- **I1. API sync:** regenerate from `docs/api/openapi.json`; add `countLetterStatuses`, `handleDocument`, `setTaskWaiting`, `snoozeTask`; decode the new defaulted fields; tests that an older server's JSON (no fields) still decodes.
- **I2. Person scope:** `LetterStatusBadge` per row ("Te laat" in words and VoiceOver), segmented status filter, count line (spec 6.1); hide all of it when the server sends no `letter_status`.
- **I3. Letter:** status under the title; "Brief afhandelen" sheet with the tasks, payment toggle, 409 `tasks_changed` reloads the sheet.
- **I4. Task row:** "Wacht op reactie…" (member+) and "Uitstellen…" (date picker capped at the deadline); collapsed groups in Mijn taken with `include_waiting: true`.
- **I5. Reminders:** `ReminderPlanner` keeps reading the default `listMyTasks` (waiting and snoozed are out) and adds one reminder at 09:00 on `waiting_until` of a waiting task ("Wachten voorbij", body: task type only unless "toon afzender"); `CalendarSync` unchanged.
