# Host "Mijn taken" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every member gets "Mijn taken": the open tasks of the post that is theirs, derived live from confirmed letters (no stored list), grouped by date with the action, days left, amount, sender, for whom and why, with tick/undo, "Afgerond", "Datum invullen", assign-to-self and hand-over; in an organisation a second part "Op te pakken"; the phone's reminders and calendar follow it.

**Architecture:** A pure module `apps.tasks.mine` gives each visible task a reason (`assigned`, `addressee`, `department`, `unassigned`, `other`) and a date group from the member's facts (linked person ids, department ids, sorting flag, covered ids) read once per request through the existing SQL functions of `visibility_sql`. `apps.tasks.services.my_tasks`/`count_my_tasks` turn the same facts into one ORM filter (`addressee_ids && …` on the GIN index) that runs as the member, under the unchanged task policy, so every row and count obeys the rule of sight. Assignment checks `documents.visibility.viewers()` and writes as the system; a helper releases assignees who lose sight, called from every writer that narrows sight. Task changes are letter history (`DocumentEvent`).

**Tech Stack:** Python 3.13, Django 6.1, Django Ninja, PostgreSQL 18 (forced RLS), pytest; React 19, TanStack Router/Query, React Aria Components, Lingui, Vitest, MSW. No new dependency.

**Spec:** `docs/superpowers/specs/2026-11-21-mijn-taken-design.md`, cited "S 5.1". Rights spec and plan: `docs/superpowers/specs/2026-11-19-zichtbaarheid-postkamer-inrichting-design.md` ("RS 4.9.3"), `docs/superpowers/plans/2026-11-20-host-wie-ziet-wat.md` ("WZW T15"); trash: DECISIONS "Trash for letters".

## Global Constraints

From `CLAUDE.md` (verbatim where quoted):

- "Every table: UUID `id`, `created_at`, `updated_at`. Every workspace content table: `workspace_id` (subclass `WorkspaceModel`) and RLS enabled **and forced**." No new table in this plan.
- "`mypy --strict` and `ruff check` clean. No `Any`, no bare `except`." "Money is `Decimal`; datetimes are timezone-aware."
- "API only under `/api/v1`, additive changes only. Every endpoint: typed schemas and an explicit camelCase `operation_id`."
- "Every non-2xx body: `{"code": "<snake_case>", "message": "<Dutch sentence>"}` via `apps.common.errors.ApiError`."
- "Thin endpoints: validate, call one function in the app's `services.py`, return." "Every mutating service function is wrapped in `transaction.atomic`."
- "No document content, tokens, passwords or BSNs in logs." No log line in this plan names a description, sender, amount or quote.
- "A field added to an existing API schema has a default (never newly required; the contract test compares with `backend/tests/fixtures/openapi_1b.json` and `openapi_1c.json`)." "Nullable fields in API schemas are primitives only, and nullable inputs have a default; the contract test forbids `anyOf`." Hence `amount: str = ""`, not `Decimal | None`.
- "Text is BSN-masked (`apps.common.privacy.mask_bsns`) before it is stored." (`term_quote`.)
- "`apps.pipeline.autopilot.decide_confirmation` and `mentions_term` stay pure: no database. `mentions_term` blocks rather than guesses (linear-time regexes)." `term_phrase` joins them, same rules.
- "Only `apps.documents.organise` adds or removes a document's labels and addressees (it writes the history)." Task history events are written through `organise.record_task_event` (extended), not directly.
- "Automatic changes have no actor and are marked." A release by the system: origin `system`, no actor.
- User-facing text Dutch B1 through `gettext` / Lingui `t`; `pnpm extract` after every text change. After every API change: `make openapi`, `cd web && pnpm gen:api`, commit both generated files with the change.
- Tests first for logic. Conventional commits, each ending with the trailer `Co-Authored-By: Claude <noreply@anthropic.com>`.

From the environment (as WZW):

- **Backend tests run in CI only.** Never connect to the PostgreSQL on this Mac (test server `brievie`), never `make dev`. A backend "run" step means: commit, push the branch `feat/my-tasks` (draft PR, after the owner's yes), read the `backend` job (and `visibility-perf` where named). `make lint`, `make typecheck` and every `pnpm` command run locally.
- **Start point.** Branch from `main` after `feat/trash` and WZW merge point M2 (Task 23) are merged; see "Build order". Migration numbers below assume `documents/0015_trash` and `tasks/0004_task_visibility` are the latest; renumber in the same commit if not.

From the spec, not left to the builder:

- **Reasons (S 5.1)** and **parts per kind (S 5.2)**: Thuis `mine` = {assigned, addressee, department, unassigned}; Organisatie `mine` = {assigned, addressee}, `to_pick_up` = {department, unassigned}; `all` = every visible task. Split by `Workspace.kind == business`.
- **Groups (S 6):** `overdue` (deadline < today), `within_week` (today ≤ deadline ≤ today + 6), `later`, `no_date`; today in Europe/Amsterdam.
- **Caps (S 11):** open parts 500 rows (`truncated`), done: completed in the last 30 days, 200 rows.
- **Assignee rule (S 5.3):** only a member who can read the letter, never a viewer (422 `assignee_cannot_read`); letterless tasks are not assignable (422 `task_without_letter`); lost sight releases the assignment as the system.
- **Copy** in the spec is used verbatim; sentences this plan adds are marked "(plan)".

## Review Focus

1. **No task text or count reaches someone who may not read its letter.** Every read runs as the member under the task policy; the new filters only narrow. → Task 5 (`test_hidden_letters_tasks_absent_from_items_and_counts`), Task 11 (the leak tests in state E off).
2. **The list is derived.** No table or cache holds "my list"; trash, restore, hand-over, link changes and confirm show up on the next read. → Task 5 (`test_list_follows_hand_over_without_writes`), Task 11 (`test_trash_and_restore_move_tasks`).
3. **Assignment never outlives sight.** 422 on a non-reader or viewer; every writer that narrows sight calls `release_unseeing_assignees`; a source test names them. → Tasks 7 and 9.
4. **Older clients.** `listTasks` keeps its meaning and shape; every new field is defaulted; no `anyOf`; an old app that confirms without `amount` stores none. → Task 6 (`test_contract_additive`), Task 3.
5. **250 ms.** `list_my_tasks` and `count_my_tasks` at 100,000 letters and 20,000 tasks for the four actors in the E on/off states. → Task 10.

---

## Build order with the remaining "Wie ziet wat" work

| Order | Work | Why here |
|---|---|---|
| 1 | merge `feat/trash` | its task filters (`document__trashed_at__isnull`) are the base of every list here |
| 2 | WZW Tasks 12–23 (leak suite, M2), incl. T15 (tasks follow letters) and T16 (calendar feed) | they settle `tasks/services.py` and prove the task policy this plan relies on |
| 3 | **this plan** (`feat/my-tasks`) | needs only M1 functions + M2 tests; all workspaces still E on, so nothing changes for anyone's sight |
| 4 | WZW Tasks 24–47 | each task there that narrows sight calls `tasks.assignment.release_unseeing_assignees` (listed in Task 9 below and enforced by its source test); T44's "Te doen" wording keeps this plan's description |

If the owner wants "Mijn taken" sooner, it can start right after step 1 in parallel with step 2; Task 11 then waits for WZW T23.

---

## File structure

```
backend/apps/
  tasks/
    models.py        # + amount, term_quote, completed_by, assigned_by; Meta.indexes tasks_task_open
    mine.py          # NEW, pure: Reason, MemberFacts, reason_for, group_for, days_left, PARTS
    facts.py         # NEW: member_facts(membership) -> MemberFacts (SQL functions, once per request)
    services.py      # + my_tasks, count_my_tasks, set_deadline; complete/reopen record completed_by
    assignment.py    # NEW: assign, assignees, release_unseeing_assignees
    schemas.py       # TaskItemOut + fields; MyTasksOut, MyTaskCountsOut, AssignIn, DeadlineIn, TaskAssigneeOut
    api.py           # + listMyTasks, countMyTasks, assignTask, listTaskAssignees, setTaskDeadline
    migrations/0005_my_tasks.py
  documents/
    models.py        # DocumentEvent.Kind + task_assigned, task_unassigned, task_deadline_set
    migrations/0016_task_event_kinds.py
    organise.py      # record_task_event(..., origin=, person_name=)
    review.py        # amount, term_quote at confirm; repeat key
    schemas.py       # TaskIn.amount, TaskOut + amount, assigned_to_name
    visibility.py    # addressee_scope exit and verify_visibility call release_unseeing_assignees
  pipeline/autopilot.py   # term_phrase (pure); pay task amount
  workspaces/members.py, access.py   # release on demotion to viewer and on removal
backend/tests/
  test_tasks_mine.py  test_tasks_term_phrase.py  test_tasks_confirm_fields.py
  test_my_tasks_service.py  test_my_tasks_api.py  test_task_assignment.py
  test_task_release_callers.py  test_leak_my_tasks.py
  perf/test_visibility_at_scale.py (+2 lists)  perf/data.py (assignees, amounts)
web/src/
  routes/tasks.tsx (+ test)  components/task-row.tsx  task-time.ts (+ test)
  components/deadline-dialog.tsx  assign-dialog.tsx  task-list.tsx  page.tsx  icons.tsx
  routes/inbox.tsx  api/queries.ts
docs/ DECISIONS.md  GLOSSARY.md  PRIVACY.md  api/openapi.json
```

---

## Slice S: server

### Task 1: Fields, event kinds and the open-task index

**Files:**
- Modify: `backend/apps/tasks/models.py`, `backend/apps/documents/models.py`
- Create: `backend/apps/tasks/migrations/0005_my_tasks.py`, `backend/apps/documents/migrations/0016_task_event_kinds.py`, `backend/tests/test_tasks_fields.py`
- Modify: `docs/DECISIONS.md` (MT3, MT8, MT14), `docs/GLOSSARY.md` (Mijn taken → my tasks; Op te pakken → to pick up; Datum nodig → needs a date; Afgerond → done; Ik doe dit → assign to self; Geef aan → hand over)

**Interfaces:**
- `Task.amount = DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)` with `CheckConstraint(amount__gte=0)` named `tasks_task_amount_not_negative`.
- `Task.term_quote = CharField(max_length=200, blank=True, default="")`.
- `Task.completed_by`, `Task.assigned_by` = `ForeignKey(user, null=True, blank=True, on_delete=SET_NULL, related_name="+")`.
- `Meta.indexes`: `Index(fields=["workspace", "deadline"], condition=Q(status="open"), name="tasks_task_open")`.
- `DocumentEvent.Kind.TASK_ASSIGNED = "task_assigned"`, `TASK_UNASSIGNED = "task_unassigned"`, `TASK_DEADLINE_SET = "task_deadline_set"`.

- [ ] **Step 1: Failing tests:**

```python
def test_task_amount_is_decimal_and_not_negative(): ...      # Decimal("12.50") round-trips; -1 -> IntegrityError
def test_task_new_fields_default_empty(): ...                # amount None, term_quote "", completed_by/assigned_by None
def test_open_task_index_exists(): ...                       # pg_indexes has tasks_task_open with WHERE status = 'open'
def test_task_event_kinds_exist(): ...
```

- [ ] **Step 2:** Push → FAIL. **Step 3:** Implement, `makemigrations`, reversible. **Step 4:** Push → PASS.
- [ ] **Step 5:** Commit `feat(tasks): amount, term quote, who completed and who assigned`.

### Task 2: `term_phrase`, pure and linear

**Files:**
- Modify: `backend/apps/pipeline/autopilot.py`
- Test: `backend/tests/test_tasks_term_phrase.py`

**Interfaces:**
- `term_phrase(text: str) -> str`: the first span the `mentions_term` patterns match, widened to the surrounding words up to 200 characters on a word boundary, whitespace collapsed, `mask_bsns` applied; `""` when `mentions_term(text)` is False. No database; same compiled patterns (no second list of words).

- [ ] **Step 1: Failing tests:**

```python
@pytest.mark.parametrize("text, expected", [
    ("U kunt binnen zes weken na dagtekening bezwaar maken.", "binnen zes weken na dagtekening bezwaar maken"),
    ("Betaal uiterlijk 14 dagen na ontvangst.", "uiterlijk 14 dagen na ontvangst"),
    ("Geen termijn hier.", ""),
])
def test_term_phrase(text, expected): ...
def test_term_phrase_is_at_most_200_characters(): ...
def test_term_phrase_masks_a_bsn(): ...                   # "binnen 6 weken, BSN 111222333" -> "[BSN]"
def test_term_phrase_agrees_with_mentions_term(): ...     # over the 20 trial letters: bool(term_phrase) == mentions_term
def test_term_phrase_linear_time(): ...                   # 1 MB adversarial text < 0.5 s
```

- [ ] **Step 2:** Run `make lint typecheck`; push → FAIL. **Step 3:** Implement. **Step 4:** Push → PASS.
- [ ] **Step 5:** Commit `feat(pipeline): the words of a term without a date, for a task that needs one`.

### Task 3: Confirm stores the amount and the term; the autopilot fills a payment's amount

**Files:**
- Modify: `backend/apps/documents/schemas.py` (`TaskIn.amount: str = ""`; `TaskOut` + `amount: str = ""`, `term_quote: str = ""`, `assigned_to_name: str = ""`), `backend/apps/documents/review.py`, `backend/apps/pipeline/autopilot.py` (`_task`)
- Test: `backend/tests/test_tasks_confirm_fields.py`

**Interfaces:**
- `review._parse_amount(value: str) -> Decimal | None`: `""` → None; otherwise `Decimal`, 0 ≤ x < 10^10, at most 2 decimals, else 422 `validation_error` "Het bedrag klopt niet. Schrijf het als 1234,56." (plan); accepts "1234.56" and "1234,56".
- `_StoredTask` + `amount: Decimal | None`, `term_quote: str`; `term_quote = term_phrase(document.ocr_text)` when the task has no deadline, else `""`.
- Repeat key: `_key_of` stays (type, description, deadline, source); additionally a task whose deadline was set by `setTaskDeadline` (source `manual`, `term_quote != ""`) also matches the undated key (type, description, None, manual), so a reconfirm after "Datum invullen" makes no second task.
- Autopilot `_task(...)` for `Task.Type.PAY`: `amount` from the group-0 `invoice_amount_incl` extraction when its confidence > 0 (the validation sets 0 when the quote does not hold it), as `f"{value:.2f}"`; else `""`.

- [ ] **Step 1: Failing tests:**

```python
def test_confirm_stores_amount(client): ...                       # TaskIn.amount "1234,56" -> Decimal("1234.56")
def test_confirm_without_amount_stores_none(client): ...          # an old client's body
def test_confirm_rejects_a_bad_amount(client): ...                # "12,345" / "-1" / "abc" -> 422 validation_error
def test_undated_task_gets_the_term_phrase(client): ...           # letter text "binnen zes weken" -> term_quote
def test_dated_task_gets_no_term_phrase(client): ...
def test_repeat_confirm_with_amount_matches(client): ...
def test_reconfirm_after_dating_makes_no_second_task(client): ... # confirm undated, setTaskDeadline, reopen, confirm same body
def test_autopilot_pay_task_takes_a_quoted_amount(): ...
def test_autopilot_pay_task_skips_an_unquoted_amount(): ...       # confidence 0 -> ""
```

- [ ] **Step 2:** Push → FAIL. **Step 3:** Implement (the repeat-key test needs Task 8's service; mark it `xfail(strict=True)` with reason "Task 8" and remove the mark there). **Step 4:** Push → PASS.
- [ ] **Step 5:** `make openapi && cd web && pnpm gen:api`; commit `feat(documents): a task keeps its amount and the term it came from`.

### Task 4: Reasons and groups, pure

**Files:**
- Create: `backend/apps/tasks/mine.py`
- Test: `backend/tests/test_tasks_mine.py`

**Interfaces:**

```python
class Reason(StrEnum): ASSIGNED, ADDRESSEE, DEPARTMENT, UNASSIGNED, OTHER   # values "assigned", ...
class Group(StrEnum): OVERDUE, WITHIN_WEEK, LATER, NO_DATE

@dataclass(frozen=True)
class MemberFacts:
    user_id: UUID
    person_ids: frozenset[UUID]        # persons linked to the member (kind person)
    department_ids: frozenset[UUID]    # empty while departments are off
    sorts_post: bool
    covered_ids: frozenset[UUID]       # empty unless sorts_post
    split: bool                        # Workspace.kind == business

@dataclass(frozen=True)
class TaskFacts:
    assigned_to_id: UUID | None
    created_by_id: UUID | None
    has_letter: bool
    addressee_ids: frozenset[UUID]

def reason_for(task: TaskFacts, member: MemberFacts) -> Reason: ...   # S 5.1 incl. letterless
PARTS: Final[dict[tuple[bool, str], frozenset[Reason]]]               # (split, part) -> reasons; S 5.2
def group_for(deadline: date | None, today: date) -> Group: ...
def days_left(deadline: date | None, today: date) -> int | None: ...
```

- [ ] **Step 1: Failing tests:** one parametrized case per row of S 5.1 (assigned to me / to someone else; my person; two persons; my department with D on; department while off → `other`; uncovered and I sort; uncovered and I do not sort; letterless with maker, with assignee, neither); `PARTS` for both kinds and the three parts; `group_for` at today − 1, today, today + 6, today + 7, None; `days_left` sign.
- [ ] **Step 2:** `uv run pytest backend/tests/test_tasks_mine.py` is pure but the suite needs the DB fixture setup; push → FAIL. **Step 3:** Implement. **Step 4:** Push → PASS.
- [ ] **Step 5:** Commit `feat(tasks): whose task it is, as a pure rule`.

### Task 5: `my_tasks` and `count_my_tasks`

**Files:**
- Create: `backend/apps/tasks/facts.py`
- Modify: `backend/apps/tasks/services.py`
- Test: `backend/tests/test_my_tasks_service.py`

**Interfaces:**
- `facts.member_facts(membership: Membership) -> MemberFacts`: one `SELECT public.brievie_my_post_ids(%s), public.brievie_actor_sorts_post(%s), CASE WHEN public.brievie_actor_sorts_post(%s) THEN public.brievie_covered_post_ids() END` as the member; persons vs departments split by one `Person.objects.filter(id__in=…, kind=…)` lookup; `split` from `membership.workspace.kind`. Runs as the member (`current_actor() == membership.user_id`, else `RuntimeError`, as `visible_documents`).
- `services.my_tasks(membership: Membership, *, part: str = "mine", status: str = "open") -> MyTaskList` (frozen dataclass: `items: list[MyTask]`, `today`, `has_person`, `truncated`, `waiting_letters`, `split`); `MyTask` pairs the `Task` with `reason`, `group`, `days_left`, `for_names`.
  - Query: `Task.objects.filter(workspace=…, status=…)` + `Q(document__isnull=True) | Q(document__trashed_at__isnull=True)` + the part's filter built from `MemberFacts` (`assigned_to=me`; `assigned_to__isnull=True, document__addressee_ids__overlap=list(person_ids)`; same for departments; `document__addressee_ids__overlap=covered` negated for unassigned, plus `document__isnull=False`), `select_related("document", "assigned_to", "assigned_by", "completed_by")`. Order: open by `deadline` nulls last, `created_at`, `id`; within "no date", `term_quote != ""` first; done by `-completed_at`, only `completed_at >= now − 30 days`.
  - `for_names`: display names of the letter's persons and departments, one `Person` query for the page (names are a directory).
  - `waiting_letters`: `inbox_counts(membership)["review"] + ["unassigned"]` only when `part == "mine"` and `status == "open"`.
  - 422 `validation_error` for another part or status ("Kies mine, to_pick_up of all." / existing status sentence); `to_pick_up` on a Thuis workspace returns no items (not an error).
- `services.count_my_tasks(membership) -> MyTaskCounts` (`mine`, `overdue`, `within_week`, `needs_date`, `to_pick_up`): one `aggregate` over the open query with `Count(filter=…)` per number.

- [ ] **Step 1: Failing tests** (world: WZW `visibility_support.office()` plus a Thuis world `household()` added here: admins Anne and Bas, member Piet, person Jan without account):

```python
def test_home_mine_has_own_assigned_and_unnamed_for_admins(): ...
def test_home_child_sees_only_own_post_tasks(): ...           # Piet: his letter yes, unnamed letter no
def test_home_letter_for_person_without_account_goes_to_admins(): ...
def test_business_mine_and_to_pick_up_are_split(): ...        # Femke: own letter in mine, Finance letter in to_pick_up
def test_department_tasks_leave_to_pick_up_when_departments_off(): ...
def test_letter_for_two_persons_listed_for_both_and_done_for_both(): ...
def test_all_part_lists_every_visible_task_with_reason_other(): ...
def test_trashed_letters_tasks_absent(): ...
def test_hidden_letters_tasks_absent_from_items_and_counts(): ...   # state E off: Hans gets none of Finance's
def test_list_follows_hand_over_without_writes(): ...      # change addressees; no Task row written; next read moves it
def test_groups_and_days_left_in_amsterdam(freezer): ...    # 23:30 UTC on the 30th is the 1st in Amsterdam
def test_needs_date_rows_first_in_no_date(): ...
def test_done_shows_last_30_days_newest_first(): ...
def test_caps_and_truncated(): ...                           # 501 open -> 500, truncated True
def test_counts_match_the_list(): ...                        # every count equals len of the matching filtered items
def test_member_without_person_has_person_false(): ...
def test_member_facts_refuses_another_actor(): ...
```

- [ ] **Step 2:** Push → FAIL. **Step 3:** Implement. **Step 4:** Push → PASS.
- [ ] **Step 5:** Commit `feat(tasks): my tasks and their counts, derived from the post`.

### Task 6: Schemas and the two read endpoints

**Files:**
- Modify: `backend/apps/tasks/schemas.py`, `backend/apps/tasks/api.py`, `backend/tests/test_openapi_contract.py` (if it lists operations), `docs/api/openapi.json`, `web/src/api/schema.d.ts`
- Test: `backend/tests/test_my_tasks_api.py`

**Interfaces:**
- `TaskItemOut` + every field of S 11 with its default; resolvers read the `MyTask` wrapper or a bare `Task` (for `listTasks`: `reason=""`, `group`/`days_left` computed, `for_names=[]`), `amount` as `f"{amount:.2f}"` or `""`, `can_edit` = role ≥ member (the policy already made the task visible).
- `MyTasksOut`, `MyTaskCountsOut` as S 11.
- `listMyTasks`: `GET /workspaces/{workspace_id}/my-tasks`, `part: str = "mine"`, `status: str = "open"`, response `{200: MyTasksOut, 404, 422, **AUTH_ERRORS}`. `countMyTasks`: `GET …/my-tasks/counts`. Both default auth (session or device), `workspace_member(request, workspace_id)`.

- [ ] **Step 1: Failing tests:**

```python
def test_list_my_tasks_shape(client): ...
def test_count_my_tasks_shape(client): ...
def test_device_token_lists_and_counts(device_client): ...
def test_list_tasks_unchanged_in_meaning(client): ...     # still every visible open task, same order, new fields defaulted
def test_contract_additive(): ...                         # vs openapi_1c.json: no field removed, every new one has a default, no anyOf
def test_bad_part_is_422(client): ...
```

- [ ] **Step 2:** Push → FAIL. **Step 3:** Implement; `make openapi`, `pnpm gen:api`. **Step 4:** Push → PASS.
- [ ] **Step 5:** Commit `feat(tasks): listMyTasks and countMyTasks`.

### Task 7: Assign, release and the list of possible assignees

**Files:**
- Create: `backend/apps/tasks/assignment.py`
- Modify: `backend/apps/tasks/api.py`, `schemas.py`, `backend/apps/documents/organise.py` (`record_task_event(document, kind, actor, *, origin=USER, person_name="")`), `backend/tests/test_actor_callers.py` (+ `apps/tasks/assignment.py`)
- Test: `backend/tests/test_task_assignment.py`

**Interfaces:**
- `assignment.assignees(membership, task_id) -> list[TaskAssigneeOut-like]`: the task loaded as the member (404 when hidden or trashed); `visibility.viewers([document_id])[document_id]` minus members with role viewer; names from `User` display name; sorted by name; `is_me`.
- `assignment.assign(*, membership, user, task_id, assignee_id: UUID | None) -> Task` in `transaction.atomic`: lock the task as the member (`select_for_update(of=("self",))`, 404); letterless → 422 `task_without_letter` "Deze taak hoort niet bij een brief en kan niet worden gegeven." (plan); `assignee_id` not in `assignees` → 422 `assignee_cannot_read` "Deze persoon mag de brief niet lezen. Kies iemand anders." (plan); unchanged → no write, no event; else, inside `system_actor()`: `assigned_to`, `assigned_by = user` (None on release), `save(update_fields=…)`; event `task_assigned` (person_name = assignee name) or `task_unassigned`, actor `user`, origin `user`.
- `assignment.release_unseeing_assignees(*, workspace_id: UUID, document_ids: Collection[UUID] | None = None) -> int`: as the system, open tasks with an assignee (of those letters, or all of the workspace when None); one `viewers()` call; each assignee no longer in the set (or now a viewer) → `assigned_to=None`, `assigned_by=None`, event `task_unassigned` with origin `system`, no actor; returns how many.
- Endpoints `assignTask` (body `AssignIn {user_id: UUID | None = None}`), `listTaskAssignees`; both `min_role=MEMBER`, default auth.

- [ ] **Step 1: Failing tests:**

```python
def test_assign_to_self(client): ...
def test_hand_over_to_a_reader(client): ...
def test_assign_to_non_reader_is_422(client): ...        # office(), E off: Femke's task to Hans
def test_assign_to_viewer_is_422(client): ...
def test_assign_to_non_member_is_422(client): ...
def test_viewer_cannot_assign(client): ...               # role error of workspace_member
def test_hidden_task_is_404_like_unknown(client): ...
def test_letterless_task_is_422(client): ...
def test_release_writes_task_unassigned(client): ...
def test_repeat_assign_writes_no_second_event(client): ...
def test_assignees_lists_only_readers_without_viewers(client): ...
def test_release_unseeing_assignees_after_hand_over(): ...   # origin system, no actor
def test_assignee_who_lost_sight_sees_nothing(client): ...   # no item, no count, 404 on complete
```

- [ ] **Step 2:** Push → FAIL. **Step 3:** Implement. **Step 4:** Push → PASS. `make openapi`, `pnpm gen:api`.
- [ ] **Step 5:** Commit `feat(tasks): take a task, hand it over, release it`.

### Task 8: Who completed it, and "Datum invullen"

**Files:**
- Modify: `backend/apps/tasks/services.py`, `api.py`, `schemas.py`
- Test: `backend/tests/test_tasks_api.py` (extend)

**Interfaces:**
- `complete_task` sets `completed_by = user`; `reopen_task` clears it (the existing events stay).
- `set_deadline(*, membership, user, task_id, deadline: date | None) -> Task`: lock as the member (404); unchanged → no write; else `deadline`, `deadline_source = manual`, `deadline_quote = ""` (keeps `term_quote`), event `task_deadline_set`; a date before the letter date → 422 `validation_error` "Die datum ligt voor de datum van de brief." (plan). Endpoint `setTaskDeadline` (body `DeadlineIn {deadline: date | None = None}`), `min_role=MEMBER`.

- [ ] **Step 1: Failing tests:** `test_complete_records_who`, `test_reopen_clears_who`, `test_set_deadline_moves_task_out_of_no_date`, `test_set_deadline_keeps_term_quote`, `test_set_deadline_before_letter_date_is_422`, `test_set_deadline_on_hidden_task_is_404`; remove Task 3's `xfail` on `test_reconfirm_after_dating_makes_no_second_task`.
- [ ] **Step 2:** Push → FAIL. **Step 3:** Implement. **Step 4:** Push → PASS; `make openapi`, `pnpm gen:api`.
- [ ] **Step 5:** Commit `feat(tasks): who ticked it off, and a date for a term`.

### Task 9: Every writer that narrows sight releases assignees

**Files:**
- Modify: `backend/apps/documents/visibility.py` (`addressee_scope` exit after `recompute`, and `verify_visibility` as the nightly net), `backend/apps/workspaces/access.py` (`on_member_removed`), `backend/apps/workspaces/members.py` (`change_role` to viewer)
- Test: `backend/tests/test_task_release_callers.py`; `docs/DECISIONS.md` (MT6 and the caller list)

**Interfaces:**
- `RELEASE_CALLERS: Final` in the test: the modules that must call `release_unseeing_assignees` (`documents/visibility.py`, `workspaces/access.py`, `workspaces/members.py`); a comment lists the WZW tasks that add theirs: T24 (private, shares), T27 (unlink, member removal), T37 (switches), T38 (sorters, department members).
- A source scan: every function in `apps/` that calls `visibility.recompute` or `recompute_where`, or writes `Membership.role`, is in a module of `RELEASE_CALLERS`.

- [ ] **Step 1: Failing tests:** `test_hand_over_releases_assignee`, `test_member_removal_releases`, `test_demotion_to_viewer_releases`, `test_nightly_check_releases_and_counts`, `test_every_narrowing_writer_releases` (source scan).
- [ ] **Step 2:** Push → FAIL. **Step 3:** Implement. **Step 4:** Push → PASS.
- [ ] **Step 5:** Commit `feat(tasks): an assignment ends when sight of the letter does`.

### Task 10: Measured at 100,000 letters

**Files:**
- Modify: `backend/tests/perf/data.py` (10 % of tasks assigned, 30 % of pay tasks with an amount, 5 % undated with a term quote), `backend/tests/perf/test_visibility_at_scale.py` (`"list_my_tasks"`, `"list_my_tasks_to_pick_up"`, `"count_my_tasks"` in `_list`), `docs/DECISIONS.md` (the table)

- [ ] **Step 1:** Add the lists; push; read the `visibility-perf` job.
- [ ] **Step 2: Decide.** Every median ≤ 250 ms (WZW P1) → record the table. Over → first restrict the task query through `document__in=visible_documents(membership).values("id")` (index hint, as `member_filter`), measure again; still over → stop and report to the owner with the numbers (no redesign without his choice).
- [ ] **Step 3:** Commit `test(perf): my tasks at 100,000 letters`.

### Task 11: The leak tests for the new paths (state E off)

**Files:**
- Test: `backend/tests/test_leak_my_tasks.py`

- [ ] **Step 1: Tests** (WZW `office()`, E off, D on):

```python
def test_no_item_or_count_of_a_hidden_letter_for_any_member(client): ...   # every member x every part x both statuses
def test_assignees_never_list_a_non_reader(client): ...
def test_for_names_only_on_visible_letters(client): ...
def test_waiting_letters_counts_only_visible(client): ...
def test_trash_and_restore_move_tasks(client): ...                         # needs feat/trash
def test_private_letter_task_only_for_readers(client): ...                 # skip(reason="WZW T24") until private letters exist
def test_no_description_in_logs(caplog, client): ...
```

- [ ] **Step 2:** Push → PASS, or fix the service.
- [ ] **Step 3:** Commit `test: my tasks follow the rule of sight`.

---

## Slice W: web

### Task 12: Queries, navigation and the "Te doen" wording

**Files:**
- Modify: `web/src/api/queries.ts` (`myTasksQuery(workspaceId, part, status)`, `myTaskCountsQuery`), `web/src/components/page.tsx` (`WorkspaceNav`), `web/src/components/icons.tsx` (`checklist`), `web/src/routes/inbox.tsx` (description), tests next to each

**Interfaces:**
- Nav: "Mijn taken" first, `/w/$workspaceId/taken`, `count = counts.mine`, aria `plural(mine, …)` plus ", waarvan # te laat" when `overdue > 0`; warning style when overdue.
- Invalidate `["workspaces", id, "my-tasks"]` together with `documents` after confirm, tick, assign, deadline, trash, restore.
- "Te doen" description: S 4 verbatim.

- [ ] **Step 1: Failing tests:** `test("nav shows Mijn taken first with its count")`, `test("the count's name says how many are late")`, `test("Te doen says where tasks are")`.
- [ ] **Steps 2–4:** `pnpm test` FAIL → implement → PASS; `pnpm lint typecheck extract`.
- [ ] **Step 5:** Commit `feat(web): Mijn taken in the navigation`.

### Task 13: Dates, days and amounts, pure

**Files:**
- Create: `web/src/task-time.ts`, `web/src/task-time.test.ts`

**Interfaces:** `whenText(daysLeft: number | null, deadline: string | null): string` ("Vandaag", "Morgen", "Nog 5 dagen (1 dec)", "1 dag te laat (20 nov)", "3 dagen te laat (18 nov)", "" without a date); `groupTitle(group)`: "Te laat", "Binnen 7 dagen", "Later", "Zonder datum"; `amountText(amount: string): string` (`Intl.NumberFormat("nl-NL", {style: "currency", currency: "EUR"})`, "" for ""); `forText(reason, forNames, assignedToName)`.

- [ ] **Steps 1–4:** tests per case (both plural forms, 0, ±1) → implement → PASS.
- [ ] **Step 5:** Commit `feat(web): words for a task's date and amount`.

### Task 14: The page "Mijn taken"

**Files:**
- Create: `web/src/routes/tasks.tsx`, `web/src/routes/tasks.test.tsx`, `web/src/components/task-row.tsx`
- Modify: the router tree (`routes/workspace.tsx` children), `web/src/styles.css` (task rows)

**Interfaces:**
- Route `/w/$workspaceId/taken`; `Tabs` "Open" / "Afgerond"; `ToggleButtonGroup` "Mijn taken" / "Alle taken"; when `split`: section "Op te pakken" (query `part=to_pick_up`) under the groups, rows with "Ik doe dit".
- `TaskRow` (S 6 order): checkbox `aria-label={t\`Klaar: ${description}\`}`; when; amount; "Van … · Voor …"; why line; "Datum nodig" badge and button; link "Brief bekijken". Ticking keeps the row with "Ongedaan maken" until refetch-on-leave; a polite live region announces "Afgevinkt: …".
- The `waiting_letters` line with a link to Te doen; the empty states and the no-person sentence verbatim from S 9 and S 13; a viewer gets disabled checkboxes and the viewer sentence.

- [ ] **Step 1: Failing tests:**

```tsx
test("groups in order Te laat, Binnen 7 dagen, Later, Zonder datum with counts")
test("a row shows action, days left, amount, sender, for whom and the quote")
test("Datum nodig rows come first under Zonder datum")
test("ticking keeps the row with Ongedaan maken and announces it")
test("Afgerond lists who ticked off and Toch niet klaar reopens")
test("Alle taken shows tasks of others with Voor Piet")
test("an organisation shows Op te pakken with Ik doe dit")
test("empty, no-person and viewer states say what to do")
test("the waiting-letters line links to Te doen")
```

- [ ] **Steps 2–4**; `pnpm extract`.
- [ ] **Step 5:** Commit `feat(web): the page Mijn taken`.

### Task 15: "Datum invullen", "Geef aan…" and tasks on a letter

**Files:**
- Create: `web/src/components/deadline-dialog.tsx`, `web/src/components/assign-dialog.tsx` (+ tests)
- Modify: `web/src/components/task-list.tsx` (amount, "Voor …"/"Aan Anne gegeven", the actions), `web/src/routes/document.tsx`

**Interfaces:** deadline dialog: `DatePicker` labelled "Welke datum noemt de brief?", the term quote above it, 422 messages through `ErrorAlert`; assign dialog: `listTaskAssignees` in a `RadioGroup` ("Aan wie geef je deze taak?", hint "Alleen wie deze brief mag lezen, staat in de lijst."), "Niet meer aan mij" when assigned to me.

- [ ] **Step 1: Failing tests:** `test("Datum invullen sets a date and the row moves")`, `test("Geef aan lists only the offered members and hands over")`, `test("a 422 assignee_cannot_read is shown in Dutch")`, `test("the letter's task list shows amount and for whom")`.
- [ ] **Steps 2–4**; `pnpm extract`.
- [ ] **Step 5:** Commit `feat(web): give a task a date or a person`.

### Task 16: Documents of record

**Files:** `docs/DECISIONS.md` (MT1–MT14 not yet recorded, one line each), `docs/PRIVACY.md` ("Mijn taken" shows a task only to who may read its letter; an assignment ends with sight; reminders on the phone hold no letter text), `docs/GLOSSARY.md` (if not in Task 1), `CLAUDE.md` (two rules: "`apps.tasks.mine` stays pure: no database." and "Every writer that narrows sight of a letter calls `tasks.assignment.release_unseeing_assignees`.")

- [ ] **Step 1:** Write; **Step 2:** commit `docs: record Mijn taken`.

---

## iOS (planned separately in `Brievie_ios`)

The app decodes with swift-openapi-generator from `Packages/BrievieKit/Sources/BrievieAPI/openapi.json` (`make sync-api HOST=../Brievie_host`); until synced it ignores the new fields and keeps working.

- **Fields:** `TaskItemOut.deadline_quote`, `term_quote`, `needs_deadline`, `amount` (decimal string → `Decimal`), `days_left`, `group`, `reason`, `for_names`, `assigned_to_id`, `assigned_to_name`, `assigned_by_name`, `completed_by_name`, `document_type`, `can_edit`; `MyTasksOut` (`items`, `today`, `has_person`, `truncated`, `waiting_letters`, `split`); `MyTaskCountsOut`; `TaskOut.amount`, `term_quote`, `assigned_to_name`; `TaskIn.amount`; event kinds `task_assigned`, `task_unassigned`, `task_deadline_set` (skip unknown kinds, as now).
- **Operations** to add to `openapi-generator-config.yaml`: `listMyTasks`, `countMyTasks`, `assignTask`, `listTaskAssignees`, `setTaskDeadline`.
- **Screens:** "Mijn taken" as the first entry of the scope menu (above "Te doen") with the groups, rows, empty states and texts of spec sections 6, 9 and 13; swipe "Klaar" with "Ongedaan maken"; "Afgerond"; "Op te pakken" with "Ik doe dit" when `split`; "Datum invullen" sheet; "Geef aan…" sheet. The opening screen follows the owner's answer to Q1.
- **Reminders and calendar:** `ReminderPlanner` and `CalendarSync` read `listMyTasks(part: mine)`; on 404 (older server) fall back to `listTasks`; calendar setting "Welke taken: Mijn taken / Alle taken" (`part=all`). `CalendarSyncPlanner.plan` keeps deleting events whose task no longer comes back (a released or handed-over task leaves the phone).
- **Review:** an amount field on a `pay` task, prefilled from `invoice_amount_incl`'s proposal, sent as `TaskIn.amount`.
- **Letter detail:** amount, "Voor …", "Ik doe dit" on each task row.
- **Pairing screen:** "Koppel alleen je eigen telefoon. Wie een andere telefoon gebruikt, koppelt die met een eigen account."

## Not in this plan (and where each hooks in)

- **Team overview per department** (organisation layer, spec S 9): reads `count_my_tasks`-style aggregates per department/member under the viewer's own policy; leads via `DepartmentMember.is_lead` (WZW T25).
- **Stand-in during absence**: delegation (R43, later).
- **Notice or push on hand-over; daily/weekly digest**: with B2 (push) or a mail sender; counts only.
- **Tasks made by hand and letterless assignment**: a `createTask` would set `created_by` and obey R39.
- **Setup, invitation and pairing sentences of spec S 9**: added by WZW T47 (`/setup`) and A3 (invitation with device); the web pairing page (`routes/pair-app.tsx`) can take its sentence in Task 12 if the builder has room.
- **Q1 (start screen)**: a one-line change in `routes/home.tsx` and `guards` once the owner answers.

## Self-review

- **Spec coverage:** S 4 → Tasks 12, 14; S 5.1–5.2 → Tasks 4, 5; S 5.3 → Tasks 7, 9; S 6 → Tasks 3, 13, 14, 15; S 7 → Tasks 5, 9, 11; S 8 → Tasks 5, 7, 11; S 9 → Tasks 5 (`has_person`), 14, "Not in this plan"; S 10 → iOS; S 11 → Tasks 5–8, 10; S 12 → Tasks 1–3; S 13 → Tasks 12–15; S 15 → Tasks 7, 9; S 17 → every task's tests.
- **Types agree:** `reason`/`group` strings come only from `mine.Reason`/`Group`; `amount` is a `Decimal` in the model and service, a string only in schemas; `part` values `mine`, `to_pick_up`, `all` everywhere.
- **Known risks:** Task 10 may fail on `count_my_tasks` for a sorter (the `NOT &&` arm cannot use the index); the fallback is in Task 10, step 2. Task 3's repeat-key widening must not merge two distinct undated tasks of the same text: the test covers the same body only.
- **No placeholders:** every step names files, interfaces and tests; Dutch sentences marked "(plan)" are new and go through `gettext`/Lingui.
