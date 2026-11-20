# Host "Wie ziet wat" (part A: switches, departments, private letters) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every letter has one answer to "who may see it", set per workspace by three switches and a sorting flag (Thuis: everyone sees all; Organisatie: own post, departments, sorters), with departments, department labels, a per-letter private mark and emergency access, enforced by PostgreSQL row-level security on every path, switchable both ways with a preview, a confirmation when access widens, audit rows and notices.

**Architecture:** A second transaction-local setting `brievie.actor` joins `brievie.workspace_id`. Four `LANGUAGE sql` helper functions read the switches, roles, sorting flags, person links and department memberships live from their tables; one condition (rendered from one Python template in `apps.workspaces.visibility_sql`) is the document's restrictive SELECT/UPDATE/DELETE policy and, rendered per member, the `viewers()` query that answers "who can see this letter" for everyone else. A derived `Document.addressee_ids` array (GIN) makes non-private sight an array overlap; `DocumentReader` rows remain only for private letters, shares and emergency access. Switching goes through one service that previews (pure `visibility_rules.diff`), demands confirmation when anyone gains, audits and notifies. The measurement at 100,000 letters runs first, in CI, on the exact SQL that ships.

**Tech Stack:** Python 3.13, Django 6.1 (`django.contrib.postgres` `ArrayField`, `GinIndex`), Django Ninja, PostgreSQL 18 (forced RLS), Procrastinate, pytest; React 19, TanStack Router/Query, React Aria Components, Lingui, Vitest, MSW. No new dependency.

**Spec:** `docs/superpowers/specs/2026-11-19-zichtbaarheid-postkamer-inrichting-design.md`, part A (sections 3, 4.1–4.18 and 4.19 "Account actions" only, 5.1–5.2, 10, 11, 13, 14), cited "S 4.4". Plain-language companion: `docs/superpowers/specs/2026-11-19-rechten-in-gewone-taal.md`. Base plan, amended here: `docs/superpowers/plans/2026-11-17-host-1d-b-prive-afdelingen.md`, cited "PB T7". Its "PostgreSQL facts", lock order and plan decisions 1–29 hold unless a task below says otherwise. Where a task says "as PB Tn" or "PB Tn's tests", the orchestrator hands the implementer PB's Task n text together with this plan's task.

## Global Constraints

From `CLAUDE.md` (verbatim where quoted):

- "Every table: UUID `id`, `created_at`, `updated_at`. Every workspace content table: `workspace_id` (subclass `WorkspaceModel`) and RLS enabled **and forced**." New tables end their first migration with `enable_rls(<table>)`.
- "`mypy --strict` and `ruff check` clean. No `Any`, no bare `except`." Datetimes timezone-aware.
- "API only under `/api/v1`, additive changes only. Every endpoint: typed schemas and an explicit camelCase `operation_id`. Endpoints without auth say `auth=None`."
- "Every non-2xx body: `{"code": "<snake_case>", "message": "<Dutch sentence>"}` via `apps.common.errors.ApiError`."
- "Thin endpoints: validate, call one function in the app's `services.py`, return."
- "Every mutating service function is wrapped in `transaction.atomic` … An error response (`ApiError`, Ninja `HttpError`, `Http404`) does not roll back the request transaction; only a 500 does."
- "No document content, tokens, passwords or BSNs in logs." Counts only in log lines of the nightly check.
- "Only `apps.documents.organise` adds or removes a document's labels and addressees (it writes the history)." Visibility, shares, readers and `addressee_ids` have one writer: `apps.documents.visibility`.
- "A label or addressee proposal is a `DocumentSuggestion`; nothing automatic repeats a dismissed one." "The rules engine (`apps.rules.engine`) stays pure: no Django import."
- "Background jobs: Procrastinate, in an app's `jobs.py`; a job takes ids only and enters `workspace_context` itself, in short transactions (none open during an external call)."
- "Text is BSN-masked (`apps.common.privacy.mask_bsns`) before it is stored, logged or sent to an AI service."
- "A field added to an existing API schema has a default (never newly required; the contract test compares with `backend/tests/fixtures/openapi_1b.json`)." "Nullable fields in API schemas are primitives only, and nullable inputs have a default; the contract test forbids `anyOf`."
- "No new dependency without a line in `docs/DECISIONS.md`." (None is added; `ArrayField` is Django's PostgreSQL contrib, S 14.)
- User-facing text Dutch B1 through `gettext` (backend) and Lingui `t` (web; `pnpm extract` after every text change). After every API change: `make openapi`, `cd web && pnpm gen:api`, commit both generated files with the change.
- Tests first for logic. Conventional commits, each ending with the trailer `Co-Authored-By: Claude <noreply@anthropic.com>`.

From the environment (binding for every task):

- **Backend tests run in CI only.** The one PostgreSQL on this Mac belongs to the test server (Docker project `brievie`): never connect to it, never `make dev`, never start a container here. Execution starts with one question to the owner: may the branch `feat/wie-ziet-wat` (from `main` after `feat/ai-autopilot` merged) be pushed with a draft PR, so CI runs on every push. A backend "run" step below means: commit, push, read the `backend` job (and `visibility-perf` where named). `make lint`, `make typecheck` and every `pnpm` command run locally.
- Migration numbers below assume `feat/ai-autopilot` is merged (it adds `documents/0009`, `pipeline/0004`, `tasks/0002`). If a later merge adds migrations first, renumber and re-point dependencies in the same commit.

From the spec, not left to the builder:

- **The rule of sight (S 4.1).** A member sees a non-private letter when: (1) `everyone_sees_all`, or the member is admin and `admins_read_all`; (2) it is on a person linked to the member; (3) `departments_enabled` and it is on a department the member is in; (4) the member sorts post (flagged, or an admin while nobody is flagged) and no addressee is covered (a person with an account; a department with members while departments are on); (5) the member scanned it and `status <> 'done'`; (6) a `share` or unexpired `emergency` reader row. A private letter (`visibility = 'restricted'`) only by reader rows. Empty actor and non-members: nothing (R6).
- **Presets (S 4.2).** Thuis: E on, D off, A on, `care_is_private` on, `emergency_access` off, `allow_member_export` on. Organisatie (`business`): E off, D on, A off, `care_is_private` off, `emergency_access` on, `allow_member_export` off. Existing workspaces after migration: E on, D off, A on, whatever their kind (S 4.16).
- **Nothing is deleted by a switch** (S 4.4 point 5); switching back restores exactly the earlier sight.
- **Write order** (S 4.9.3): user-level writes first; then as the system `sync_addressee_ids`, `recompute`, and in confirm `status = done`, `confirmed_by`, `confirmed_at`; nothing after that as the user except computing `can_read`.
- **`system_actor()` callers** (closed list, `tests/test_actor_callers.py`): PB's list plus `apps/documents/review.py`, `apps/tasks/services.py`, `apps/pipeline/autopilot.py`, `apps/workspaces/visibility_settings.py`, `apps/documents/member_export.py`.
- **Copy** in the spec is used verbatim where quoted below; sentences this plan adds are marked "(plan)".

## Review Focus

1. **A member confirms a letter that then goes to someone else** (the receptionist case, E off): 200 with `can_read: false`, the suggestion dismissals and tasks stored, no 42501, no 500, and the answer holds no page, label, addressee or task; a repeat answers the same. → Task 9 (`test_confirm_handing_over_answers_can_read_false`, `test_repeat_confirm_after_hand_over`).
2. **Two admins change sight at once**: the second confirmation was given for a preview that no longer holds → 409 `preview_outdated`, nothing changed. → Task 37 (`test_stale_preview_token_is_409_and_changes_nothing`), Task 38 (`test_role_change_with_stale_preview_token_is_409`).
3. **Departments switched off and on again with letters only on departments**: sorters get them while off; switching back gives the department members exactly their earlier sight; no `DepartmentMember`, `Label.department` or rule row lost. → Task 39 (`test_switch_back_restores_sight` over every row of S 4.4).
4. **The last member leaves a department** (or the last account-holder of a person is unlinked): the letters go to the sorters, never to nobody, and the sorters get a notice. → Task 38 (`test_last_department_member_removed_sends_post_to_sorters`), Task 39 (`test_no_orphan_in_any_state`).
5. **An older client against the new server** (1c/1d web or the current iOS app): new fields absent from its requests leave settings untouched, a hidden letter in a list it had cached answers 404 `document_not_found`, a task that vanished from `listTasks` is removed from the phone's calendar. → Task 31 (`test_old_settings_body_keeps_new_switches`), Task 13 (`test_every_document_operation_answers_a_hidden_letter_like_an_unknown_id`), Task 16 (`test_hidden_letters_task_leaves_list_tasks`).

---

## How this plan relates to PB (the 1d-B plan of 2026-11-17)

| PB task | Here | What changed |
|---|---|---|
| 1 actor helpers | Task 3 | unchanged; migration `workspaces/0005` |
| 2 actor on every context | Task 4 | manual rule run takes the starter's `user_id` (R7); system callers list widened |
| 3 visibility, readers, policies | Tasks 6–7 | the S 4.9.3 condition, `addressee_ids`, task CASE policy with `Task.created_by`, the four visibility functions, switches; "empty actor sees public letters" becomes "sees no letter" |
| 4 measurement | Tasks 1–2 (first, scratch schema) and 11 (real schema) | S 4.9.5 data and states, CI runner, member filter, preview count |
| 5 readers, recompute, nightly check | Task 8 | + `addressee_scope`/`sync_addressee_ids`, `viewers()`; share readers on every letter |
| 6 lock order | Task 5 | unchanged |
| 7 private/public/shares | Task 24 | `keep_own_access` for private letters only; shares on every letter (R44) |
| 8 grants, forget member | Tasks 24, 27 | unchanged functions; folded |
| 9 writes the user cannot see | Task 10 | unchanged |
| 10 automatic actions never widen readers | Task 32 | folded with PB 19 |
| 11 departments | Tasks 25, 26 | + `is_lead`, archive rules, `departments_enabled`, department labels, `confirmed`/`preview_token` (Task 38) |
| 12 linking is a grant | Task 27 | + the S 4.4 helpers (Task 38) |
| 13 member removal, own export | Tasks 20, 27 | own export read as the member, switches never widen it |
| 14 account actions | Task 28 | conditions 2 and 3 of S 4.19 |
| 15 settings | Tasks 6, 29, 31 | + the three switches, `sorts_post` |
| 16 leak suite | Tasks 12–23 | one task per leak path of S 4.10, in state E off |
| 17 private by hand, Zorg | Task 30 | unchanged |
| 18 confirm with visibility | Tasks 9, 30 | the S 4.9.3 write order, `can_read`, repeat as the system |
| 19 rule action privé maken | Task 32 | unchanged |
| 20 emergency access | Task 33 | opens all hidden post of the person; `require_regular_reader` → `can_edit` |
| 21 back-up verify | Task 34 | also checks the four visibility functions |
| 22–24 web | Tasks 42–47 | the pages of S 4.15 |
| dropped | — | nothing of PB is dropped outright; PB's "Zakelijk" wording becomes "Organisatie" |

## Plan rulings (decisions the spec leaves open; one `DECISIONS.md` line each, in the task named)

- **P1 (measurement, Task 2).** Pass = every measured median ≤ **250 ms** (S 4.9.5) on a GitHub-hosted `ubuntu-latest` runner with the `postgres:18` service at default settings (no development machine has a database), median of 5 runs after 1 warm-up; the preview's letter count ≤ **2,000 ms** (plan: it runs once per admin change, not per page). A list that fails with the plain policy but passes with the member filter of S 4.9.5 counts as passing (the filter ships in Task 11). Fail → stop before Task 3; report the table to the owner with the options in Task 2.
- **P2 (Task 1).** The measurement runs on a scratch schema built from the same SQL constants the migrations import later, so it measures what ships and costs no product migration if it fails.
- **P3 (Task 8).** `addressee_ids` is synced by the outermost service through `visibility.addressee_scope(document)`; `organise` refuses to change addressees outside a scope (`RuntimeError`), so no writer can forget the sync.
- **P4 (Task 12).** `inbox_state = "unassigned"` keeps its 1c meaning (no addressee at all). A letter on an uncovered addressee reaches the sorters through the policy and shows with its name; the chip "Zonder naam" filters `unassigned`.
- **P5 (Task 21).** A queued `apply_rule_to_existing` without `user_id` (queued before the update) does nothing and logs "Rule run skipped: started before the update" (fail closed, R6); the admin starts it again.
- **P6 (Task 35).** Confirmation is needed when anyone gains, except when every gain is a fallback gain of a member who already sorted post, caused by a narrowing (unlink, department member removed, member removed, demotion, one of several sorters removed). This reproduces every "Confirm" cell of S 4.4 (e.g. "person unlinked: no", "last sorter removed: yes when an admin gains").
- **P7 (Task 38).** Removing the last member of a department, or unlinking the last account of a person, sends their letters to the sorters (rule 4); those sorters get `sight_widened` though S 4.4's row says "Gains: nobody".
- **P8 (Task 6).** Until Task 40, `create_workspace` gives every new workspace E on, D off, A on (both kinds): no installation can reach "own post" before every leak path is closed.
- **P9 (Task 37).** `getVisibility` answers every member with the switches and the sentences: the switches are stated in the member's own sentences, so hiding the booleans protects nothing.
- **P10 (Task 37).** Notice values `sight_widened`/`sight_narrowed`: `category` (`all`, `all_as_admin`, `department`, `person`, `unassigned`) plus `department` or `person` (a name); S 4.14 lists only `department`, person names are a directory like department names.
- **P11 (Task 15).** No task-create or task-assign endpoint exists; the letterless-task policy and `Task.created_by` are built (the trash will create letterless tasks), `assignee_cannot_read` is not (see "Not in this plan").

---

## Merge points

Each merge point leaves `main` in a state where nobody sees a letter they must not see.

| Merge | After task | State on `main` |
|---|---|---|
| **M0** | 2 | the measurement and its CI job; no product change |
| **M1** (S 13 "A1") | 11 | actor, functions, policies, `addressee_ids`, readers, write order; every workspace E on (new ones too, P8); nothing a user sees changes |
| **M2** | 23 | every leak path of S 4.10 tested in state E off (set by tests only); still no way for a user to switch E off |
| **M3** | 34 | private letters, shares, departments, department labels, emergency access; E still on everywhere, so departments only sort |
| **M4** | 41 | switching with preview, sorters, presets at setup, "Wie kan mijn post zien?" API; an Organisatie installation now starts in "own post" |
| **M5** | 47 | the web pages |

Migrations are reversible except the data steps noted in their task (a reverse that would drop derived data recomputes it or is a no-op, never a guess).

---

## File structure

```
backend/apps/
  workspaces/
    rls.py                  # + ACTOR_SETTING, actor_functions(), restrict_to_visible_documents(), restrict_reader_rows()
    visibility_sql.py       # NEW: the four visibility functions, document_condition(), viewer_pairs_sql(): one source
    context.py              # + Actor, SYSTEM, NOBODY, workspace_context(actor=), system_actor(), current_actor()
    locks.py                # NEW: lock_own_membership
    deps.py                 # workspace_member sets the user as actor
    models.py               # Workspace switches, care_is_private, emergency_access; Membership.sorts_post
    presets.py              # NEW: apply_preset_settings, PRESETS
    visibility_rules.py     # NEW, pure: Switches, MemberFacts, PolicySnapshot, LetterFacts, expected_sight, sight_categories, diff, needs_confirmation
    visibility_settings.py  # NEW: snapshot(), fingerprint(), preview(), change(), notify_changes()
    policy_text.py          # NEW, pure: describe(policy) -> list[str]
    access.py               # PB seams filled in
    members.py  services.py  settings_service.py  api.py  schemas.py
    management/commands/create_workspace.py   # NEW (S 5.2)
    migrations/0005_actor_functions.py  0006_visibility_switches.py  0007_visibility_functions.py
  accounts/authority.py  accounts/notices.py  accounts/models.py (+ notice codes)  accounts/migrations/0010_visibility_notices.py
  audit/codes.py            # + workspace_preset_applied, post_sorter_changed, department_lead_changed
  documents/
    models.py               # visibility, addressee_ids, DocumentShare, DocumentReader, DocumentEvent.note + kinds
    visibility.py           # NEW: addressee_scope, sync_addressee_ids, viewers, can_read, can_edit, readers, private, shares, grants, emergency
    organise.py  review.py  services.py  intake.py  member_export.py  export.py  jobs.py  api.py  schemas.py
    migrations/0010_visibility.py
  people/  models.py (Person.kind, DepartmentMember)  services.py  api.py  schemas.py  migrations/0003_departments.py
  labels/  models.py (Label.department)  services.py  api.py  schemas.py  migrations/0002_label_department.py
  rules/   jobs.py  services.py  executor.py  engine.py  definition.py  facts.py  migrations/0004_rulerun_visibility.py
  tasks/   models.py (created_by)  services.py  migrations/0003_task_visibility.py
  pipeline/ steps.py  jobs.py  autopilot.py
  backup/verify.py
backend/tests/
  perf/test_visibility_at_scale.py  perf/scratch_schema.py (Task 1, removed in Task 11)  perf/data.py
  visibility_support.py     # office() world, set_state(), as_actor(), private_letter()
  test_visibility_*.py  test_privacy_*.py  test_ci_visibility_perf_job.py
.github/workflows/ci.yml    # + job visibility-perf
web/src/
  routes/visibility.tsx  departments.tsx  post-access.tsx  setup-summary.tsx  (+ tests)
  components/visibility-preview-dialog.tsx  private-lock.tsx  share-dialog.tsx  emergency-dialog.tsx  document-access.tsx
  routes/setup.tsx  inbox.tsx  members.tsx  labels.tsx  persons.tsx  settings.tsx  document.tsx  components/person-picker.tsx  page.tsx
docs/ DECISIONS.md  GLOSSARY.md  PRIVACY.md  api/openapi.json
```

---

## Slice M: measure first

### Task 1: The visibility SQL, measured at 100,000 letters on a scratch schema

**Files:**
- Create: `backend/apps/workspaces/visibility_sql.py`, `backend/tests/perf/__init__.py`, `backend/tests/perf/scratch_schema.py`, `backend/tests/perf/data.py`, `backend/tests/perf/test_visibility_at_scale.py`, `backend/tests/test_visibility_sql.py`

**Interfaces:**
- Produces in `apps.workspaces.visibility_sql` (strings only; no Django model import):
  - `ACTOR: Final = "(SELECT public.brievie_actor_id())"`.
  - `visibility_functions_sql() -> list[str]`: the four `CREATE FUNCTION` statements of S 4.9.1 **with the user as parameter**: `public.brievie_sees_all_post(uid uuid) RETURNS boolean`, `public.brievie_my_post_ids(uid uuid) RETURNS uuid[]`, `public.brievie_covered_post_ids() RETURNS uuid[]`, `public.brievie_actor_sorts_post(uid uuid) RETURNS boolean`; each `LANGUAGE sql STABLE PARALLEL SAFE SECURITY INVOKER SET search_path = ''`; bodies as S 4.9.1 with `public.brievie_actor_id()` replaced by `uid`, and in `brievie_my_post_ids` the person arm limited to `p.kind = 'person'`.
  - `DROP_VISIBILITY_FUNCTIONS: Final[list[str]]` (reverse).
  - `document_condition(*, user: str, sees_all: str, my_ids: str, sorts: str, covered: str, table: str = '"documents_document"') -> str`: the S 4.9.3 condition **without** the leading `brievie_is_system()` arm, every column qualified with `table`.
  - `POLICY_CONDITION: Final[str]` = `"(SELECT public.brievie_is_system()) OR (" + document_condition(user=ACTOR, sees_all=f"(SELECT public.brievie_sees_all_post({ACTOR}))", my_ids=f"(SELECT public.brievie_my_post_ids({ACTOR}))", sorts=f"(SELECT public.brievie_actor_sorts_post({ACTOR}))", covered="(SELECT public.brievie_covered_post_ids())") + ")"`.
  - `viewer_pairs_sql(document_filter: str) -> str`: `WITH m AS (SELECT user_id, brievie_sees_all_post(user_id) AS sees_all, brievie_my_post_ids(user_id) AS my_ids, brievie_actor_sorts_post(user_id) AS sorts FROM workspaces_membership WHERE workspace_id = <current workspace>), c AS (SELECT brievie_covered_post_ids() AS covered) SELECT d.id, m.user_id FROM documents_document d CROSS JOIN m CROSS JOIN c WHERE <document_filter> AND <document_condition(user="m.user_id", sees_all="m.sees_all", my_ids="m.my_ids", sorts="m.sorts", covered="c.covered", table="d")>`; `document_filter` is a fixed SQL fragment with `%s` placeholders (never user text).
- `tests.perf.scratch_schema.install(cursor) -> None` (as the owner role, in the test database): `ALTER TABLE` adds `workspaces_workspace.everyone_sees_all/departments_enabled/admins_read_all`, `workspaces_membership.sorts_post`, `people_person.kind varchar(20) NOT NULL DEFAULT 'person'`, `documents_document.visibility varchar(20) NOT NULL DEFAULT 'workspace'`, `documents_document.addressee_ids uuid[] NOT NULL DEFAULT '{}'` + GIN index; `CREATE TABLE people_departmentmember` and `documents_documentreader` with the columns of Tasks 6–7 and `enable_rls`; then PB T1's two helpers, `visibility_functions_sql()`, the reader policy, the three document policies with `POLICY_CONDITION`, PB's child policies, and the task CASE policy of S 4.9.3.
- `tests.perf.data.build(workspace_id: UUID) -> PerfWorld` (as the system, `INSERT … SELECT generate_series`, every `NOT NULL` column explicit, then `ANALYZE`): S 4.9.5's data: 100,000 letters (10 % private), 50 persons of whom 25 linked to the 25 members, 5 departments of 5 members, 2 admins, 1 flagged sorter; addressees: 60 % one person, 25 % one department, 10 % person + department, 5 % none; 20 labels with 100,000 links; 20,000 tasks; 300,000 events; private letters get one `addressee` and one `share` reader row; `addressee_ids` filled from the links. `PerfWorld` (frozen dataclass): `workspace_id`, `plain_member`, `department_member`, `sorter`, `admin` (user ids), `plain_person_id`.

- [ ] **Step 1: Write the tests.** `test_visibility_sql.py` (no database):

```python
def test_policy_and_viewer_rendering_share_one_condition(): ...   # document_condition with markers -> both renderings equal after substituting the markers
def test_functions_pin_search_path_and_take_the_user(): ...       # every statement has "SET search_path = ''" and "(uid uuid)" except covered
def test_condition_has_no_system_arm(): ...                       # "brievie_is_system" not in document_condition(...)
```

`perf/test_visibility_at_scale.py`, skipped unless `BRIEVIE_PERF=1` (module-level `pytestmark = pytest.mark.skipif(...)`), `@pytest.mark.django_db(transaction=True)`, one module-scoped world:

```python
STATES = {"E": dict(everyone_sees_all=True), "own+D": dict(everyone_sees_all=False, departments_enabled=True),
          "own": dict(everyone_sees_all=False, departments_enabled=False)}            # admins_read_all False in the last two
ACTORS = ("plain_member", "department_member", "sorter", "admin")
LISTS = ("list_documents", "list_documents_unassigned", "list_documents_own_person", "inbox_counts",
         "list_persons", "list_labels", "list_tasks_all")
@pytest.mark.parametrize("state", STATES)
@pytest.mark.parametrize("actor", ACTORS)
@pytest.mark.parametrize("name", LISTS)
def test_measure_list(state, actor, name, perf_world, perf_report): ...
    # the real service functions (documents.services.list_documents etc.) inside workspace_context(ws, actor=<user id>);
    # 1 warm-up + 5 timed runs; records the median; for list_documents* and inbox_counts with E off and an actor who
    # sorts nothing, also the same query plus RawSQL("addressee_ids OPERATOR(pg_catalog.&&)
    # (SELECT public.brievie_my_post_ids(%s))") as a filter (the "member filter" median)
def test_measure_preview_letter_count(perf_world, perf_report): ...
    # as the system: letters whose viewer set (viewer_pairs_sql) differs between "E" and "own+D",
    # as two md5-per-letter maps; median of 5 recorded
def test_explain_member_filter_uses_the_gin_index(perf_world): ...  # EXPLAIN (FORMAT JSON) contains "Bitmap Index Scan" on the addressee_ids index
def test_verdict(perf_report): ...
    # runs last (file order); writes perf-report.md (state × actor × list -> ms, both variants) to $BRIEVIE_PERF_REPORT;
    # asserts P1: every list median <= 0.250 s (or its member-filter median <= 0.250 s), preview count <= 2.0 s
```

- [ ] **Step 2:** `make lint && make typecheck` locally → PASS. Push; the `backend` job → PASS (perf tests skipped there; `test_visibility_sql.py` passes).
- [ ] **Step 3:** Commit `test(perf): visibility SQL and the 100,000-letter measurement on a scratch schema`.

### Task 2: Run the measurement in CI and decide

**Files:**
- Modify: `.github/workflows/ci.yml` (job `visibility-perf`), `docs/DECISIONS.md` (P1, P2 and the measured table)
- Create: `backend/tests/test_ci_visibility_perf_job.py`

**Interfaces:**
- Job `visibility-perf`: `if: github.event_name != 'schedule'` (runs on pull requests, pushes to `main` and `workflow_dispatch`), `runs-on: ubuntu-latest`, `timeout-minutes: 30`, default permissions (`contents: read`), the same `postgres:18` service, role set-up and migrate steps as `backend`, then `BRIEVIE_PERF=1 BRIEVIE_PERF_REPORT=$RUNNER_TEMP/perf-report.md uv run pytest tests/perf -p no:cacheprovider` and `cat "$RUNNER_TEMP/perf-report.md" >> "$GITHUB_STEP_SUMMARY"`. Actions pinned by commit SHA as the other jobs.

- [ ] **Step 1: Failing tests** (`test_ci_visibility_perf_job.py`, reading `ci.yml` as `test_ci_restore_job.py` does):

```python
def test_the_job_runs_on_pull_requests_with_a_timeout_and_default_permissions(): ...
def test_it_sets_brievie_perf_and_runs_only_tests_perf(): ...
def test_it_uses_the_postgres_18_service_and_never_docker_compose(): ...   # no "docker compose", no "./brievie"
def test_it_publishes_the_report_to_the_step_summary(): ...
```

- [ ] **Step 2:** Push the test commit; `backend` → FAIL in exactly these tests.
- [ ] **Step 3:** Add the job; push; `backend` → PASS; read `visibility-perf`.
- [ ] **Step 4: Decide.** PASS (P1) → add the DECISIONS entry with P1, P2 and the summary table (median per state × actor × list, the runner type), commit `ci: measure the visibility check at 100,000 letters`, **merge point M0**. FAIL → commit the job and the report anyway (the job is then marked `continue-on-error: true` with a comment naming the failure), stop, and report to the owner: which lists fail in which state, and the options: (a) list services query each arm separately and `UNION` (each arm indexable); (b) PB's fallback, a copied visibility column on the child tables (PB T4); (c) narrower lists (100 rows, a `created_at` window). No Task 3 without the owner's choice.

---

## Slice A1: enforcement, dormant (all workspaces stay "Iedereen ziet alle post")

### Task 3: Actor helper functions

As PB T1, unchanged: `rls.ACTOR_SETTING = "brievie.actor"`, `rls.actor_functions()` creating `public.brievie_actor_id()` and `public.brievie_is_system()` exactly as PB T1; migration `workspaces/0005_actor_functions` depending on `("workspaces", "0004_workspace_allow_member_export")`.

**Files:** as PB T1. **Test:** `backend/tests/test_workspaces_actor.py` with PB T1's seven tests.

- [ ] **Step 1:** Write PB T1's failing tests; push → FAIL.
- [ ] **Step 2:** Implement; push → PASS (`make lint && make typecheck` locally first).
- [ ] **Step 3:** Commit `feat(workspaces): actor helper functions for row-level security`.

### Task 4: Every workspace context names its actor

As PB T2, with these changes:

**Files:** PB T2's list, plus `backend/apps/rules/jobs.py`, `backend/apps/rules/services.py` (`run_rule`), `backend/apps/pipeline/autopilot.py` (no context of its own; listed so the scan sees it), and the autopilot's tests.

**Interfaces:**
- `apps.workspaces.context`: PB T2's `SYSTEM`, `NOBODY`, `Actor`, `workspace_context(workspace_id, *, actor)`, `enter_workspace_for_transaction(workspace_id, *, actor: UUID)`, `system_actor()`, `current_actor()`.
- Rule runs (R7): `apply_rule_to_existing(*, workspace_id: str, rule_id: str, after: str = "", user_id: str = "") -> None`; `defer_rule_run(workspace_id: UUID, rule_id: UUID, *, user_id: UUID, after: UUID | None = None) -> bool`; `apply_rule_batch(workspace_id: UUID, rule_id: UUID, *, user_id: UUID, after: UUID | None) -> None` enters `workspace_context(workspace_id, actor=user_id)` after checking `Membership(workspace, user)` exists (else logs "Rule run skipped: the member left" and returns); the next batch is deferred with the same `user_id`. Empty `user_id` → P5. `run_rule` passes `actor.user_id`.
- Call-site table: PB T2's, except `rules/jobs.py apply_rule_batch` → the starter's id; `documents/export.py export_context(…, reader_id=None)` → `SYSTEM` stays (no caller passes None yet). `ALLOWED_SYSTEM_CALLERS` starts as PB T2's list minus `apps/rules/jobs.py`.

- [ ] **Step 1: Failing tests:** PB T2's twelve, plus in `test_rules_jobs.py`:

```python
def test_manual_run_runs_as_its_starter(): ...                    # current_actor() inside the executor == starter id (monkeypatched probe)
def test_manual_run_of_a_starter_who_left_writes_nothing(): ...   # membership deleted before the job: no RuleRun, log line
def test_queued_run_without_user_id_is_skipped(caplog): ...       # P5
```

- [ ] **Step 2:** Push → FAIL.
- [ ] **Step 3:** Implement; rewrite tests' `workspace_context(...)` calls to `actor=SYSTEM` with PB T2's one-off script (not committed).
- [ ] **Step 4:** `make lint && make typecheck`; push → PASS.
- [ ] **Step 5:** Commit `feat(workspaces): every workspace context names its actor`.

### Task 5: One lock order

As PB T6, unchanged (`apps.workspaces.locks.lock_own_membership(workspace_id, user_id)`, `no_key=True` on every `Document`/`Person` row lock, PB T6's four tests), plus `apps/pipeline/autopilot.py` and `reopenDocument`'s service in the scans.

- [ ] **Step 1:** PB T6's failing tests (the `reopenDocument` call added to `test_request_paths_take_the_membership_before_the_document`); push → FAIL.
- [ ] **Step 2:** Implement; push → PASS.
- [ ] **Step 3:** Commit `refactor: one lock order, memberships before documents, documents without key locks`.

### Task 6: Switches, the sorting flag, departments and letterless-task makers (fields only)

**Files:**
- Modify: `backend/apps/workspaces/models.py`, `backend/apps/people/models.py`, `backend/apps/tasks/models.py`, `backend/apps/workspaces/services.py` (`create_workspace`, P8), `backend/apps/documents/review.py` (`Task.created_by` on confirm), `docs/DECISIONS.md` (P8), `docs/GLOSSARY.md` (S 14's lines for the three switches, verdeelt post, afdeling/groep, leidinggevende)
- Create: `backend/apps/workspaces/migrations/0006_visibility_switches.py`, `backend/apps/people/migrations/0003_departments.py`, `backend/apps/tasks/migrations/0003_task_created_by.py`, `backend/tests/test_visibility_fields.py`

**Interfaces:**
- `Workspace.everyone_sees_all = BooleanField(default=True)`, `departments_enabled = BooleanField(default=False)`, `admins_read_all = BooleanField(default=True)`, `care_is_private = BooleanField(default=False)`, `emergency_access = BooleanField(default=False)`; `Workspace.Kind.BUSINESS` label `_("Organisatie")`. `Membership.sorts_post = BooleanField(default=False)`; `Membership.Role.VIEWER` label `_("Alleen lezen")` (R29). No `db_default` on any field (PB decision 5).
- Migration `workspaces/0006_visibility_switches` (depends on `0005_actor_functions`): add fields; data step: every workspace `everyone_sees_all=True, departments_enabled=False, admins_read_all=True`; `home` → `care_is_private=True, emergency_access=False`, `business` → `False, True` (PB T15's values); reverse: no-op (fields are dropped by the schema reverse).
- `Person.kind = CharField(max_length=20, choices=Kind.choices, default="person")` (`PERSON`, `DEPARTMENT`); `Person.save` raises `ValueError` for a department with a `user`. `DepartmentMember(WorkspaceModel)`: `department` FK `Person` CASCADE `related_name="department_members"`, `user` FK user CASCADE `related_name="+"`, `is_lead = BooleanField(default=False)`; unique `people_departmentmember_unique` (`department`, `user`); `same_workspace_fields = ("department",)`; `save` raises `ValueError` unless the department is `kind="department"` and the user a member. Migration `people/0003_departments` (depends on `("people", "0002_persons_for_members")`, `("workspaces", "0006_visibility_switches")`) ends with `enable_rls("people_departmentmember")`.
- `Task.created_by = ForeignKey(user, null=True, blank=True, on_delete=SET_NULL, related_name="+")`; confirm sets it to the confirming user; autopilot tasks leave it null. Migration `tasks/0003_task_created_by` (depends on `("tasks", "0002_task_created_by_ai")`).
- `create_workspace(*, name, kind, admin, extra_labels=())`: unchanged switches (E on, D off, A on) for both kinds (P8); `care_is_private`/`emergency_access` per kind as the migration.

- [ ] **Step 1: Failing tests:**

```python
def test_existing_workspaces_migrate_to_everyone_sees_all(): ...      # migration function called directly, home and business
def test_new_workspaces_of_both_kinds_start_with_everyone_sees_all(): ...  # P8
def test_privacy_defaults_per_kind(): ...
def test_department_cannot_have_an_account(): ...                     # ValueError
def test_department_member_must_be_a_member_and_a_department(): ...
def test_department_member_table_has_forced_rls(): ...                # test_rls_coverage picks it up
def test_confirm_records_the_task_maker(): ...
def test_business_kind_reads_organisatie(): ...                       # str(Workspace.Kind.BUSINESS.label) == "Organisatie"
```

- [ ] **Step 2:** Push → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** `make lint && make typecheck && make openapi` (no API change: `git diff docs/api` empty); push → PASS.
- [ ] **Step 5:** Commit `feat(workspaces): visibility switches, the sorting flag and departments as fields`.

### Task 7: The visibility functions, `addressee_ids`, readers, shares and the policies

PB T3, amended by S 4.9: the document condition is `visibility_sql.POLICY_CONDITION`; reader rows are for private letters, shares and emergency only.

**Files:**
- Modify: `backend/apps/workspaces/rls.py` (PB T3's `restrict_to_visible_documents`, `restrict_reader_rows`; `document_visibility_policy()` now renders `POLICY_CONDITION`; `task_visibility_policy()`), `backend/apps/common/models.py` (PB T3's `WorkspaceModel.save`), `backend/apps/documents/models.py`, `backend/tests/test_rls_coverage.py`, `test_actor_callers.py`, `backend/tests/perf/scratch_schema.py` (imports the real policy builders), `docs/DECISIONS.md` (R6, R31, R33, R38, the two-layer policy, no `WITH CHECK`, no `db_default`)
- Create: `backend/apps/workspaces/migrations/0007_visibility_functions.py`, `backend/apps/documents/migrations/0010_visibility.py`, `backend/apps/rules/migrations/0004_rulerun_visibility.py`, `backend/apps/tasks/migrations/0004_task_visibility.py`, `backend/tests/visibility_support.py`, `backend/tests/test_visibility_rls.py`

**Interfaces:**
- `workspaces/0007_visibility_functions` (depends on `workspaces/0006`, `people/0003`): `RunSQL(visibility_functions_sql(), reverse_sql=DROP_VISIBILITY_FUNCTIONS)`.
- `Document.visibility` (PB T3), `Document.addressee_ids = ArrayField(UUIDField(), default=list, blank=True)` with `GinIndex(fields=["addressee_ids"], name="documents_document_addressees")`; `DocumentShare`, `DocumentReader` (reasons `addressee`, `department`, `share`, `emergency`), `DocumentEvent.note` and PB T3's new event kinds.
- `documents/0010_visibility` (depends on `("documents", "0009_document_confirmed_by_ai_documentaddressee_automatic_and_more")`, `("workspaces", "0007_visibility_functions")`): fields and tables; `enable_rls` on both new tables; `restrict_reader_rows()`; data step `fill_addressee_ids`: per workspace inside `workspace_context(ws, actor=SYSTEM)`, one `UPDATE documents_document d SET addressee_ids = s.ids FROM (SELECT document_id, array_agg(person_id ORDER BY person_id) ids FROM documents_documentaddressee GROUP BY document_id) s WHERE s.document_id = d.id` (reverse: no-op; the column is dropped by the schema reverse); then `document_visibility_policy()` and `restrict_to_visible_documents()` on `documents_documentpage`, `documents_extraction`, `documents_documentaddressee`, `documents_documentlabel`, `documents_documentevent`, `documents_documentsuggestion`, `documents_documentshare`.
- `rules/0004_rulerun_visibility`: `restrict_to_visible_documents("rules_rulerun")`. `tasks/0004_task_visibility` (depends on `tasks/0003`, `documents/0010`): `task_visibility_policy()` = the S 4.9.3 `CASE` as three restrictive policies (`FOR SELECT`, `FOR UPDATE`, `FOR DELETE`, no `WITH CHECK`) named as `VISIBILITY_POLICIES`.
- `tests.visibility_support`: `as_actor(workspace, actor)`, `restrict_raw(document, readers)` (PB T3), `set_state(workspace, *, e: bool, d: bool, a: bool, sorters: Sequence[User] = ())` (writes the fields as the system, no service), `ALL_16_STATES: Final[list[dict[str, bool]]]` (every combination of `e`, `d`, `a` and `s` = Sandra flagged or nobody flagged), `office() -> Office`: workspace `business` in state E off, D on, A off; users `admin` (Anne), `sorter` (Sandra, flagged), `finance` (Femke, in department Finance), `hr` (Hans, in HR), `plain` (Piet), `viewer` (Vera, role viewer); persons linked to each; departments Finance, HR, and Directie (no members); letters `own_piet`, `finance_letter`, `hr_letter`, `unassigned`, `only_directie` (department without members), `person_without_account` (person "Jan"), `scanned_in_review` (scanned by Piet, `needs_review`, on Femke), `private_femke` (restricted, reader Femke); each letter has one row in every child table (page, extraction, addressee, label, event, suggestion, task).

- [ ] **Step 1: Failing tests** in `test_visibility_rls.py`, each asserting `Document` ids visible per actor in `office()`:

```python
def test_plain_member_sees_only_own_post(): ...                          # Piet: own_piet, scanned_in_review
def test_department_member_sees_department_post(): ...                   # Femke: finance_letter, private_femke, ...
def test_sorter_sees_uncovered_post(): ...                               # Sandra: unassigned, only_directie, person_without_account
def test_admin_without_admins_read_all_sorts_only_when_nobody_is_flagged(): ...
def test_everyone_sees_all_except_private(): ...                         # set_state(e=True)
def test_admins_read_all_shows_admins_non_private_post(): ...
def test_departments_off_send_department_post_to_sorters(): ...          # set_state(d=False)
def test_scanner_sees_letter_until_done(): ...
def test_empty_actor_sees_no_letter(): ...                               # R6, replaces PB's "public letters"
def test_non_member_actor_sees_no_letter(): ...
def test_private_letter_only_by_reader_rows_in_every_state(): ...
@pytest.mark.parametrize("model", [DocumentPage, Extraction, DocumentAddressee, DocumentLabel, DocumentEvent,
                                   DocumentSuggestion, DocumentShare, RuleRun, Task])
def test_child_rows_follow_their_document(model): ...
def test_letterless_task_visible_to_maker_assignee_and_sees_all(): ...   # R39
def test_letterless_task_without_maker_hidden_once_everyone_is_off(): ...
def test_migrated_database_shows_every_member_what_it_showed(): ...    # S 4.16: rows made before the migration, data steps run: every member sees every non-private letter
def test_fill_addressee_ids_matches_the_links(): ...
```

  plus PB T3's write tests (update/delete without WHERE, `select_for_update`, `save(update_fields)`, 42501 on a flip, FK at commit, no recursion) rewritten against `office()`, and PB T3's `test_rls_coverage.py` changes with `tasks_task` expecting the CASE policies.
- [ ] **Step 2:** Push → FAIL.
- [ ] **Step 3:** Implement models, `rls.py`, migrations, `WorkspaceModel.save`.
- [ ] **Step 4:** `make lint && make typecheck`; push → PASS, and `visibility-perf` still PASS on the scratch schema.
- [ ] **Step 5:** Commit `feat(documents): visibility functions, addressee_ids and the second row-level-security layer`.

### Task 8: One writer for `addressee_ids`, readers and the nightly check; `viewers()`

PB T5 amended (S 4.9.2, 4.9.4, P3).

**Files:**
- Create: `backend/apps/documents/visibility.py`, `backend/tests/test_visibility_sync.py`, `backend/tests/test_visibility_viewers.py`
- Modify: `backend/apps/documents/organise.py`, `backend/apps/documents/services.py` (organising services), `backend/apps/documents/suggestions.py` (accept), `backend/apps/pipeline/steps.py`, `backend/apps/pipeline/autopilot.py`, `backend/apps/rules/executor.py`, `backend/apps/documents/jobs.py` (`verify_visibility_job`), `test_actor_callers.py` (+ `apps/documents/visibility.py`, `apps/documents/jobs.py`)

**Interfaces** (`apps.documents.visibility`; writes inside `system_actor()`):
- `addressee_scope(document: Document) -> AbstractContextManager[None]`: re-entrant per document id within a transaction; on the outermost normal exit calls `sync_addressee_ids(document)` and `recompute(document)`. `organise.set_addressees`, `organise.add_addressee` and the addressee branch of `organise.undo_event` raise `RuntimeError("addressee change outside addressee_scope")` when no scope is open for that document.
- `sync_addressee_ids(document: Document) -> None`: as system, `UPDATE … SET addressee_ids = (SELECT COALESCE(array_agg(person_id ORDER BY person_id), '{}') FROM documents_documentaddressee WHERE document_id = %s)`; sets the attribute.
- Outermost scopes: `documents.services` set-addressees, accept-suggestion, undo; `review.confirm_document` (Task 9 places it); `pipeline.steps` organising step and `autopilot.apply_open_proposals` (one scope around the pipeline's organising); `rules.executor.execute` (one scope per document run).
- `desired_readers`, `recompute`, `recompute_where`, `can_read(document_id) -> bool`, `reader_names`: PB T5, except `desired_readers` gives `share` readers for **every** letter and `addressee`/`department` readers only for restricted ones (S 4.8).
- `viewers(document_ids: Collection[UUID]) -> dict[UUID, frozenset[UUID]]`: as system, `visibility_sql.viewer_pairs_sql("d.id = ANY(%s)")` united with unexpired reader rows; current members only.
- `verify_visibility(workspace_id: UUID) -> VisibilityCheck` (`@dataclass(frozen=True)`: `addressee_ids_fixed: int`, `reader_rows_fixed: int`): as system, repairs every `addressee_ids` that differs from the links, then PB T5's `verify_readers`.
- `apps.documents.jobs.verify_visibility_job(timestamp: int)` (replaces PB's `verify_readers_job`; `@app.periodic(cron="40 3 * * *")`): logs `"Visibility check: %d addressee lists and %d reader rows differed"` (warning when either > 0).

- [ ] **Step 1: Failing tests:**

```python
def test_addressee_change_outside_a_scope_raises(): ...
def test_scope_syncs_once_at_the_outermost_exit(): ...                   # nested scopes: one UPDATE (CaptureQueriesContext)
def test_set_addressees_service_syncs(): ...
def test_accept_suggestion_syncs(): ...
def test_undo_syncs(): ...
def test_pipeline_and_autopilot_sync(): ...
def test_rule_run_syncs(): ...
def test_rollback_leaves_no_half_sync(): ...
def test_share_reader_rows_on_a_non_private_letter(): ...                # R44
def test_nightly_check_repairs_a_hand_damaged_array(caplog): ...         # log has digits only
def test_nightly_check_counts_reader_differences(): ...
# test_visibility_viewers.py
@pytest.mark.parametrize("state", ALL_16_STATES)
def test_viewers_equal_the_policy_for_every_actor_and_letter(state): ...  # office(): viewers(ids)[d] == {u | d visible as u}
```

- [ ] **Step 2:** Push → FAIL.
- [ ] **Step 3:** Implement; wrap every outermost addressee writer in `addressee_scope`.
- [ ] **Step 4:** Push → PASS.
- [ ] **Step 5:** Commit `feat(documents): addressee_ids and readers kept by one writer, checked nightly`.

### Task 9: The write order: confirming, organising and reopening a letter one may lose

S 4.9.3 "Write order", PB T18's order amended. Built now, while every workspace is E on, so the first workspace that switches E off (Task 37) already has it.

**Files:**
- Modify: `backend/apps/documents/review.py`, `backend/apps/documents/services.py` (`set_addressees`, `accept_suggestion`, `undo`, `reopen_document`, `document_out`), `backend/apps/documents/schemas.py`, `backend/apps/documents/api.py` (descriptions), `test_actor_callers.py` (+ `apps/documents/review.py`)
- Test: `backend/tests/test_visibility_write_order.py`

**Interfaces:**
- `DocumentOut.can_read: bool = True` (S 4.13). `services.document_out_for(document: Document) -> DocumentOut`: computes `visibility.can_read(document.id)` **as the current actor, last**; when False returns, built as the system, `DocumentOut` with the id, `client_id`, `status`, `page_count=0`, `created_at`, empty strings, `letter_date=None`, empty lists for pages, proposals, tasks, suggestions, addressee ids and label ids, `inbox_state=""`, `can_read=False`. Every organising endpoint (`confirmDocument`, `setDocumentAddressees`, `setDocumentLabels`, `acceptSuggestion`, `dismissSuggestion`, `undoDocumentEvent`, `reopenDocument`) answers through it.
- `confirm_document(*, workspace, user, document_id, data) -> Document`, order inside its `atomic`: `lock_own_membership` → the document `select_for_update(no_key=True)` as the user (404 when hidden, after the repeat check below) → validate → **step 1, as the user:** confirmed fields except `status`/`confirmed_by`/`confirmed_at` (`save(update_fields=[…])`), inside `addressee_scope(document)`: `_set_organisation` (labels, addressees, `mark_accepted`, the dismissals), `record_confirmed`, tasks (with `created_by`), rules at confirm (`_rules_guarded`, as the user, D 8.3) → scope exit: **step 2, as the system:** `sync_addressee_ids`, `recompute`, then `status=done`, `confirmed_by`, `confirmed_at` with `save(update_fields=…)` inside `system_actor()`.
- Repeat: the document is first looked up **as the system** after the membership lock; `status == done and _is_repeat(...)` → return it (the endpoint then answers `can_read` False when the user lost it). Otherwise the lookup as the user decides 404.
- `reopen_document`: as the user; when the AI-confirmed letter is not visible to the user, 404 like an unknown id.
- `keep_own_access` (PB T7) is **not** used here; it applies to private letters only and arrives in Task 24.

- [ ] **Step 1: Failing tests** (`office()` from Task 7):

```python
def test_confirm_handing_over_answers_can_read_false(client): ...
    # Piet confirms scanned_in_review with addressee Femke only: 200, can_read False, pages == [], label_ids == [],
    # addressee_ids == [], tasks == []; as SYSTEM: status done, tasks stored with created_by Piet, the open
    # suggestions dismissed (count > 0), the confirmed event written
def test_confirm_never_raises_42501(client): ...                       # same, with a rule at confirm adding a label
def test_repeat_confirm_after_hand_over(client): ...                    # same body again: 200, identical answer, no second event
def test_other_review_after_hand_over_is_404(client): ...               # different body: 404 document_not_found
def test_set_addressees_handing_over_answers_can_read_false(client): ...
def test_confirm_keeping_the_letter_answers_the_full_document(client): ...
def test_status_written_last_as_system(): ...                           # CaptureQueriesContext: the UPDATE setting status follows the addressee UPDATE
def test_reopen_of_a_hidden_letter_is_404(client): ...
```

- [ ] **Step 2:** Push → FAIL.
- [ ] **Step 3:** Implement; `make openapi`, `cd web && pnpm gen:api`.
- [ ] **Step 4:** Push → PASS.
- [ ] **Step 5:** Commit `feat(documents): confirming a letter for someone else answers can_read false`.

### Task 10: Writes the user cannot see: deletes, "in use", archiving, repeated upload

As PB T9, unchanged (deletes of person, label and rule as the system after the authorisation; `deletePerson` admin-only; archiving closes suggestions as the system; `intake` duplicate lookup as the system then `can_read`, else the existing 409 `document_conflict`), run against `office()` in state E off instead of PB's private letter, plus one case: `test_repeated_upload_by_the_scanner_after_hand_over_is_409_without_data`.

- [ ] **Step 1:** PB T9's failing tests plus the case above; push → FAIL.
- [ ] **Step 2:** Implement; `make openapi`, `pnpm gen:api`.
- [ ] **Step 3:** Push → PASS.
- [ ] **Step 4:** Commit `fix: deletes, archiving and repeated uploads see hidden letters as the system`.

### Task 11: The measurement on the real schema, and the member filter

PB T4 adapted (S 4.9.5).

**Files:**
- Modify: `backend/apps/documents/services.py` (`list_documents`, `inbox_counts`), `backend/apps/documents/visibility.py` (`member_filter`), `backend/tests/perf/test_visibility_at_scale.py`, `backend/tests/perf/data.py`, `docs/DECISIONS.md` (the new table)
- Delete: `backend/tests/perf/scratch_schema.py`

**Interfaces:**
- `visibility.member_filter(workspace: Workspace) -> Q | None`: for a user actor in a workspace with E off who is not an admin under A and does not sort post (one query on the membership and the workspace), `Q(addressee_ids__overlap=<the actor's my_post_ids, read with one SELECT public.brievie_my_post_ids(%s)>) | Q(scanned_by_id=<actor>, ~Q(status="done")) | Q(visibility="restricted") | Q(readers__user_id=<actor>)`; otherwise `None`. `list_documents` and `inbox_counts` apply it when not None. The policy still decides; the filter only lets the planner use the GIN index.
- The perf world is built with the real models' tables (`migrate` has run); `data.build` unchanged in what it creates.

- [ ] **Step 1: Failing tests:**

```python
def test_member_filter_is_none_when_everyone_sees_all(): ...
def test_member_filter_never_changes_the_result(): ...    # office(), every state and actor: list with filter == list without
```

  and in the perf module the member-filter variant now measures the services themselves.
- [ ] **Step 2:** Push → FAIL (`backend`).
- [ ] **Step 3:** Implement; delete the scratch schema.
- [ ] **Step 4:** Push → `backend` PASS, `visibility-perf` PASS by P1 (remove `continue-on-error` if Task 2 set it). A FAIL here stops the plan as Task 2 does.
- [ ] **Step 5:** Commit `perf(documents): a member filter the index can use, measured on the real schema`. **Merge point M1.**

---

## Slice A2a: every leak path closed, in state "Eigen post"

Each task tests one row of S 4.10 as Piet (plain member, no department) or Hans (HR) against Femke's Finance post in `office()` (E off, D on, A off), plus a control that the owner of the letter still sees it. A failing test is a leak: fix it in the owning code within the task. Task 13's list of document operations comes from the OpenAPI schema, so a new operation (search, trash) without a case fails it.

### Task 12: The post list, Te doen, counts and filters

**Files:**
- Test: `backend/tests/test_leak_lists.py`
- Modify (only if a test fails): `backend/apps/documents/services.py`, `backend/apps/people/services.py`, `backend/apps/labels/services.py`

**Interfaces:** none new. P4 written in `docs/DECISIONS.md`.

- [ ] **Step 1: Write the tests** (API calls as Hans unless noted):

```python
def test_post_list_omits_finance_post(client): ...                  # listDocuments
def test_te_doen_lists_omit_it(client): ...                         # inbox_state=review, unassigned, new
def test_inbox_counts_omit_it(client): ...                          # getInboxCounts
def test_person_filter_on_femke_shows_nothing(client): ...          # listDocuments?person_id=<Femke's person>
def test_department_filter_on_finance_shows_nothing(client): ...    # person_id=<Finance>
def test_label_filter_omits_it(client): ...
def test_persons_document_count_counts_only_visible(client): ...    # listPersons
def test_labels_document_count_counts_only_visible(client): ...     # listLabels
def test_sorter_sees_unassigned_in_te_doen(client): ...             # Sandra: unassigned, only_directie, person_without_account
def test_the_owner_still_sees_it(client): ...                       # Femke
```

- [ ] **Step 2:** Push → PASS, or fix and push again.
- [ ] **Step 3:** Commit `test: lists and counts show no colleague's post`.

### Task 13: One letter by id, through every document operation

**Files:**
- Test: `backend/tests/test_leak_document_operations.py`

**Interfaces:**
- `DOCUMENT_OPERATION_CASES: dict[str, Callable[[Client, UUID, UUID], HttpResponse]]` and `document_operations_from_schema() -> set[str]` (PB T16). Today's set: `getDocument`, `getDocumentPage`, `retryDocumentProcessing`, `confirmDocument`, `reopenDocument`, `setDocumentAddressees`, `setDocumentLabels`, `acceptSuggestion`, `dismissSuggestion`, `listDocumentEvents`, `undoDocumentEvent`; Tasks 30 and 41 add `setDocumentVisibility` and `getDocumentAccess` (the emergency operation has no `{document_id}`).

- [ ] **Step 1: Write the tests:**

```python
def test_every_document_operation_has_a_case(): ...
@pytest.mark.parametrize("operation", sorted(DOCUMENT_OPERATION_CASES))
@pytest.mark.parametrize("who", ["plain", "hr", "viewer", "admin"])
def test_every_document_operation_answers_a_hidden_letter_like_an_unknown_id(operation, who): ...
    # status and JSON body for finance_letter == for uuid4(); nothing changed (compared as SYSTEM before/after)
```

- [ ] **Step 2:** Push → PASS, or fix and push again.
- [ ] **Step 3:** Commit `test: every document operation answers a hidden letter like an unknown id`.

### Task 14: Page images and files

**Files:**
- Test: `backend/tests/test_leak_pages.py`
- Modify (if needed): `backend/apps/documents/services.py` (`read_page` loads the `DocumentPage` row as the user before opening the file)

- [ ] **Step 1: Write the tests:**

```python
def test_page_of_a_hidden_letter_is_404_before_the_file_is_opened(client, monkeypatch): ...  # storage open() never called (recorder)
def test_no_answer_holds_a_storage_path(client): ...          # every JSON answer of office() as Femke: no "documents/" path string
def test_page_answer_is_not_cacheable(client): ...            # Cache-Control "private, no-store" (existing), as Femke
```

- [ ] **Step 2:** Push → PASS, or fix.
- [ ] **Step 3:** Commit `test: page images of a colleague's letter stay closed`.

### Task 15: Tasks, "Vandaag" and letterless tasks

**Files:**
- Test: `backend/tests/test_leak_tasks.py`
- Modify: `backend/apps/tasks/services.py` (`_get_for_update` as the user; a reassign seam is not built, P11), `docs/DECISIONS.md` (P11)

- [ ] **Step 1: Write the tests:**

```python
def test_list_tasks_omits_tasks_of_hidden_letters(client): ...      # listTasks status=all as Hans
def test_complete_and_reopen_a_hidden_task_are_404(client): ...     # same body as an unknown task id
def test_letterless_task_visible_to_its_maker_only(client): ...     # Task(document=None, created_by=Piet): Piet yes, Hans no
def test_letterless_task_visible_to_its_assignee(client): ...
def test_letterless_task_without_maker_visible_when_everyone_sees_all(client): ...
def test_task_of_a_deleted_letter_follows_the_letterless_rule(): ...   # document deleted as SYSTEM -> SET_NULL; Hans cannot see it
```

- [ ] **Step 2:** Push → PASS, or fix.
- [ ] **Step 3:** Commit `test: tasks follow their letter, letterless tasks their maker`.

### Task 16: The phone's calendar feed

The iOS app feeds the phone's calendar from `listTasks` and deletes an event whose task no longer comes back (`CalendarSyncPlanner.plan`). The ICS feed is not built (S 4.10).

**Files:**
- Test: `backend/tests/test_leak_calendar.py`

- [ ] **Step 1: Write the tests:**

```python
def test_hidden_letters_task_leaves_list_tasks(client): ...       # Femke's task listed; after a hand-over to HR it is gone for Femke, shape of TaskItemOut unchanged
def test_task_item_carries_no_text_of_a_hidden_letter(client): ...  # as Hans: no item has Finance's sender or description
def test_list_tasks_answer_matches_the_released_contract(client): ...  # validates against openapi_1c TaskItemOut
```

- [ ] **Step 2:** Push → PASS, or fix.
- [ ] **Step 3:** Commit `test: the calendar feed drops tasks of letters one can no longer see`.

### Task 17: Suggestions and history

**Files:**
- Test: `backend/tests/test_leak_suggestions.py`

- [ ] **Step 1: Write the tests:**

```python
def test_suggestion_rows_of_a_hidden_letter_are_invisible(): ...    # DocumentSuggestion count as Hans excludes them
def test_accept_and_dismiss_of_a_hidden_suggestion_are_404(client): ...
def test_history_of_a_hidden_letter_is_404(client): ...             # listDocumentEvents
def test_undo_of_a_hidden_event_is_404(client): ...
def test_archiving_a_person_closes_suggestions_on_hidden_letters(client): ...  # admin Anne archives Femke's person: suggestion on finance_letter gone (as SYSTEM)
```

- [ ] **Step 2:** Push → PASS, or fix.
- [ ] **Step 3:** Commit `test: suggestions and history follow their letter`.

### Task 18: Notices carry no letter content

**Files:**
- Modify: `backend/apps/accounts/notices.py` (`NOTICE_VALUE_KEYS: Final[dict[str, frozenset[str]]]`, enforced in `notify`)
- Test: `backend/tests/test_leak_notices.py`

**Interfaces:**
- `NOTICE_VALUE_KEYS`: per `AccountNotice.Code` the allowed value keys; the union may only contain `{"workspace", "person", "department", "category", "count", "document_id", "member", "setting"}`. `notify` raises `ValueError` for any other key.

- [ ] **Step 1: Failing tests:**

```python
def test_every_notice_code_has_its_value_keys(): ...
def test_no_value_key_can_hold_content(): ...         # union ⊆ the fixed set; "sender", "summary", "label", "text" never present
def test_notify_refuses_an_unknown_key(): ...
def test_every_message_formats_with_only_its_keys(): ...   # MESSAGES[code] % sample values: no KeyError
```

- [ ] **Step 2:** Push → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Push → PASS.
- [ ] **Step 5:** Commit `feat(accounts): notice values come from a fixed list without letter content`.

### Task 19: Audit rows

**Files:**
- Test: `backend/tests/test_leak_audit.py`

- [ ] **Step 1: Write the tests:**

```python
def test_document_targets_carry_the_id_only(client): ...      # every row with target kind document: target_name == ""
def test_no_audit_value_holds_a_sender_summary_or_label_name(client): ...  # run every audited action of office(); scan values
def test_workspace_audit_log_is_admin_only(client): ...       # listWorkspaceAuditEvents as Piet: 403 forbidden
```

- [ ] **Step 2:** Push → PASS, or fix.
- [ ] **Step 3:** Commit `test: audit rows name letters by id only`.

### Task 20: The member's own export (and the workspace export seam)

PB T13's export part, amended by S 4.18 "Task 13".

**Files:**
- Modify: `backend/apps/documents/member_export.py` (`member_document_ids`), `backend/apps/documents/export.py` (docstring of `export_context`: `reader_id=None` is the server-side full copy only), `test_actor_callers.py` (+ `apps/documents/member_export.py` for the system read of reader rows)
- Test: `backend/tests/test_leak_export.py`

**Interfaces:**
- `member_document_ids(workspace_id: UUID, user_id: UUID) -> list[UUID]`: inside `export_context(workspace_id, reader_id=user_id)` (RLS as the member): documents on a person linked to the member **plus** restricted documents with a non-emergency, unexpired reader row for the member; never "every letter the member sees" (PB decision 26: the switches do not widen it).
- `build_member_export` re-checks the membership before reading (exists already: keep the test).

- [ ] **Step 1: Failing tests:**

```python
def test_own_export_holds_own_post_and_private_letters_read(): ...
def test_own_export_does_not_grow_with_everyone_sees_all(): ...    # set_state(e=True): same ids
def test_own_export_omits_letters_the_member_cannot_read(): ...
def test_export_job_for_a_member_who_left_writes_nothing(): ...
```

- [ ] **Step 2:** Push → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Push → PASS.
- [ ] **Step 5:** Commit `feat(documents): the own export reads as the member`.

### Task 21: Jobs, rule tests and manual rule runs

**Files:**
- Modify: `backend/apps/rules/services.py` (`test_rule` counts under the policy as the user, unchanged code if already so)
- Test: `backend/tests/test_leak_jobs.py`; `docs/DECISIONS.md` (P5, R7)

- [ ] **Step 1: Write the tests:**

```python
def test_rule_test_counts_only_visible_letters(client): ...         # testRule as Anne (admin, A off) with Finance's sender: total and matched exclude Finance post
def test_manual_run_touches_only_letters_the_starter_sees(): ...    # runRule by Anne: no RuleRun, label or event on finance_letter
def test_automatic_rules_still_reach_every_letter(): ...            # pipeline trigger as SYSTEM labels finance_letter
def test_job_with_an_empty_actor_sees_no_document(): ...            # R6: count 0, children 0
def test_member_removed_between_enqueue_and_run_writes_nothing(): ...
def test_rule_list_counts_only_visible_runs(client): ...            # listRules applied counts as Anne
```

- [ ] **Step 2:** Push → PASS, or fix.
- [ ] **Step 3:** Commit `test: jobs and rule runs see what their actor sees`.

### Task 22: The AI call

The AI service reads the text of every letter it processes, under the workspace's recorded consent (S 4.10, `PRIVACY.md`); what must not happen is a member causing another member's letter to be sent, or a request carrying other letters.

**Files:**
- Test: `backend/tests/test_leak_ai.py`

- [ ] **Step 1: Write the tests:**

```python
def test_retry_by_a_member_who_cannot_read_the_letter_is_404_and_sends_nothing(client): ...
    # failed finance_letter; retryDocumentProcessing as Hans: 404, no job deferred, no provider call (recording provider)
def test_ai_request_holds_only_its_own_letter(): ...      # run_pipeline on one letter: provider texts contain none of the other letters' OCR text
def test_autopilot_runs_as_system_and_syncs(): ...        # autopilot adds Finance as addressee: addressee_ids updated in the same transaction
```

- [ ] **Step 2:** Push → PASS, or fix.
- [ ] **Step 3:** Commit `test: nobody sends a colleague's letter to the AI`.

### Task 23: Seams for search, trash, duplicate detection and later paths

Search, a trash and duplicate detection land separately and must obey the same rule. This task gives them one seam and makes forgetting it fail a test.

**Files:**
- Modify: `backend/apps/documents/visibility.py` (`visible_documents`), `backend/apps/documents/services.py` (`list_documents`, `inbox_counts`, `get_document` start from it), `backend/tests/test_rls_coverage.py`
- Test: `backend/tests/test_visibility_seams.py`; `docs/DECISIONS.md` (the seam, for the three branches)

**Interfaces:**
- `visibility.visible_documents(workspace: Workspace) -> QuerySet[Document]`: `Document.objects.filter(workspace=workspace)` plus `member_filter` (Task 11); docstring: "Every query that returns or counts letters to a user starts here (search, trash, duplicates included). It runs under the caller's actor; the policy decides, this adds only an index hint. A count of hidden matches is never returned. A system-side comparison (duplicate detection) runs inside `system_actor()` and answers the user only through `can_read`." 
- `test_rls_coverage.test_every_table_with_a_document_foreign_key_has_the_visibility_policies` (PB T3) covers any new table with a document FK (a search index table, a trash table).
- Trash contract written in the docstring of `documents/models.py` `Task.document`: a letter in the trash keeps its `Task` links; a hard delete deletes the letter's tasks as the system instead of leaving them letterless when the letter was private (else the description would reach "Iedereen").

- [ ] **Step 1: Failing tests:**

```python
def test_document_lists_start_from_visible_documents(): ...      # source scan: Document.objects in documents/services.py only via visible_documents or as SYSTEM
def test_raw_sql_on_documents_runs_with_an_actor(): ...          # source scan of apps/: every "documents_document" in a raw string is in a module on the system list or in rls.py/visibility_sql.py
def test_a_new_document_child_table_without_policies_fails_coverage(): ...  # coverage function called on a fake FK list
```

- [ ] **Step 2:** Push → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Push → PASS.
- [ ] **Step 5:** Commit `feat(documents): one seam for every query that shows letters`. **Merge point M2.**

---

## Slice A2b: private letters, shares, departments, labels, emergency access (E still on in production)

### Task 24: Private, public, shares on every letter, and grants

PB T7 and PB T8, amended: `keep_own_access` and `409 would_lose_access` apply to restricted letters only (S 4.9.3); `set_shares` works on every letter (R44); `has_private_post`, `reads_private_post`, `private_post_readers`, `forget_member`, `record_grant` as PB T8.

**Files:** PB T7 + PB T8's, test files `backend/tests/test_visibility_private.py`, `backend/tests/test_visibility_grants.py`.

**Interfaces:** PB T7 and PB T8's signatures, with `set_shares(document, *, user_ids, actor)` accepting a `workspace` letter (share rows + `share` reader rows via `recompute`; history `share_added`/`share_removed`; audit `visibility_changed {"visibility": <current>, "share": "added"|"removed", "member": <name>}`). `privacy_support.private_letter` lives in `tests/visibility_support.py`.

- [ ] **Step 1:** PB T7's and PB T8's failing tests, plus:

```python
def test_share_on_a_non_private_letter_lets_piet_read_it(): ...       # office(): Femke shares finance_letter with Piet
def test_removing_the_share_hides_it_again(): ...
def test_losing_a_non_private_letter_by_organising_is_not_409(): ...  # Femke sets addressee HR only: 200, can_read False
```

- [ ] **Step 2:** Push → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Push → PASS.
- [ ] **Step 5:** Commit `feat(documents): private letters, shares on every letter, and grants`.

### Task 25: Departments: API, leads, archive, matching

PB T11 amended (S 4.5).

**Files:** PB T11's (without the model, made in Task 6), plus `backend/apps/people/matching.py` (departments while `departments_enabled`, archived departments not proposed). Test: `backend/tests/test_people_departments.py`.

**Interfaces:**
- PB T11's `add_department_member`, `remove_department_member` (signatures widened in Task 38 with `confirmed`, `preview_token`), `link_member` on a department → 409 `is_a_department`, `PersonIn.kind: str = "person"`.
- `update_department_member(*, workspace: Workspace, person_id: UUID, user_id: UUID, is_lead: bool, actor: AuditActor) -> Person`; audit `department_lead_changed` (values `member`, `value`; new `AuditCode` with `ALLOWED_VALUES`).
- Schemas (additive): `PersonOut.kind: str = "person"`, `member_user_ids: list[UUID] = []`, `lead_user_ids: list[UUID] = []`.
- Endpoints (admin): `addDepartmentMember` `POST /workspaces/{workspace_id}/persons/{person_id}/members` body `DepartmentMemberIn {user_id: UUID, is_lead: bool = False}`; `updateDepartmentMember` `PUT …/persons/{person_id}/members/{user_id}` body `DepartmentMemberUpdateIn {is_lead: bool}`; `removeDepartmentMember` `DELETE …/persons/{person_id}/members/{user_id}`; each → 200 `PersonOut`; errors 404 `person_not_found`, 404 `member_not_found`, 409 `not_a_department` ("Dit is geen afdeling."), 409 `cannot_grant_self` (PB T11's sentence).
- Archiving (S 4.5): an archived department keeps its members' sight; `set_addressees` refuses an archived person or department with the existing archived-person error; deleting a department with letters → 409 `person_in_use`; deleting an empty one removes its `DepartmentMember` rows and sets its labels' `department` to null.

- [ ] **Step 1:** PB T11's failing tests plus:

```python
def test_lead_is_set_and_audited(): ...
def test_archived_department_is_not_proposed_and_not_assignable(): ...
def test_archived_department_members_keep_seeing_its_letters(): ...
def test_department_on_several_letters_with_two_departments(): ...      # R36: a letter on Finance and HR is seen by both
def test_matcher_ignores_departments_while_they_are_off(): ...
```

- [ ] **Step 2:** Push → FAIL.
- [ ] **Step 3:** Implement; `make openapi`, `pnpm gen:api`.
- [ ] **Step 4:** Push → PASS.
- [ ] **Step 5:** Commit `feat(people): departments with members, leads and archiving`.

### Task 26: Labels of a department

S 4.6, R42.

**Files:**
- Modify: `backend/apps/labels/models.py`, `services.py`, `api.py`, `schemas.py`
- Create: `backend/apps/labels/migrations/0002_label_department.py`, `backend/tests/test_labels_department.py`

**Interfaces:**
- `Label.department = ForeignKey("people.Person", null=True, blank=True, on_delete=SET_NULL, related_name="+")`; `same_workspace_fields = ("department",)`. Migration depends on `("labels", "0001_initial")`, `("people", "0003_departments")`; no data change.
- `LabelIn.department_id: UUID | None = None`, `LabelOut.department_id: UUID | None = None`.
- `create_label(*, workspace, name, color, department_id: UUID | None, actor_user: User, actor_role: str)` and `update_label(…)` with the same two: a `department_id` that is not a department of this workspace → 422 `validation_error` "Kies een afdeling." (plan); creating or changing a department label needs an admin or a lead of that department, else 403 `forbidden`.
- `list_labels(workspace, *, offered_to: User | None = None)`: when `offered_to` is a non-admin and departments are on, labels whose `department` is set and the user is not a member of it are left out; `listLabels` passes the caller; with departments off every label is listed (S 4.4). A label on a letter the user sees is still returned with that letter (`label_ids`), whatever its owner.
- On a name clash, the existing 409 keeps its sentence; the web suggests the prefix.

- [ ] **Step 1: Failing tests:**

```python
def test_department_label_offered_to_members_and_admins_only(client): ...
def test_department_label_listed_for_everyone_while_departments_are_off(client): ...
def test_lead_creates_a_label_for_their_department(client): ...
def test_member_cannot_create_a_department_label(client): ...      # 403
def test_department_label_grants_nothing(client): ...               # finance label on hr_letter: Femke still cannot see hr_letter
def test_deleting_the_department_keeps_the_label_for_everyone(): ...
```

- [ ] **Step 2:** Push → FAIL.
- [ ] **Step 3:** Implement; `make openapi`, `pnpm gen:api`.
- [ ] **Step 4:** Push → PASS.
- [ ] **Step 5:** Commit `feat(labels): labels that belong to a department`.

### Task 27: Linking a person is a grant; removing a member; invitations

PB T12 and PB T13 (removal and invitation parts), unchanged in rules; their sight effects on non-private post are previewed in Task 38.

**Files:** PB T12's and T13's (without `member_export`, done in Task 20). Tests: `backend/tests/test_people_grants.py`, `backend/tests/test_privacy_members.py`.

- [ ] **Step 1:** PB T12's and T13's failing tests (export cases excluded); push → FAIL.
- [ ] **Step 2:** Implement; `make openapi`, `pnpm gen:api`.
- [ ] **Step 3:** Push → PASS.
- [ ] **Step 4:** Commit `feat(people): linking and removing members change access through one path`.

### Task 28: Account actions by a workspace admin

PB T14 with S 4.19's three conditions.

**Files:** `backend/apps/accounts/authority.py`, `backend/apps/workspaces/services.py` (`list_members`), `backend/apps/workspaces/access.py`. Test: `backend/tests/test_privacy_account_actions.py`.

**Interfaces:**
- `account_action_requires_server_admin(target: User, *, actor: User) -> bool` (the seam gains the actor): True unless (1) the target's single membership is one workspace and the target is no server admin, (2) `access.reads_private_post(ws, target.id)` is False, and (3) in that workspace `everyone_sees_all` or (`admins_read_all` and the actor is admin). `account_authority` raises 403 `server_admin_only` with "Dit kan alleen de serverbeheerder." (plan) when True; `list_members` uses the same function for `can_manage_account`.

- [ ] **Step 1:** PB T14's failing tests plus:

```python
def test_admin_who_does_not_read_all_post_cannot_reset_anybody(client): ...   # office(): Anne -> Piet 403 server_admin_only
def test_admin_may_reset_when_admins_read_all(client): ...
```

- [ ] **Step 2:** Push → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Push → PASS.
- [ ] **Step 5:** Commit `feat(accounts): account actions follow who reads which post`.

### Task 29: Privacy settings: Zorg and the emergency-access switch

PB T15 without the model change (Task 6 made the fields): `WorkspaceSettingsOut.care_is_private: bool = False`, `emergency_access: bool = False`; `WorkspaceSettingsIn.care_is_private: bool | None = None`; `setEmergencyAccess` exactly as PB T15.

- [ ] **Step 1:** PB T15's failing tests (minus the migration test, done in Task 6); push → FAIL.
- [ ] **Step 2:** Implement; `make openapi`, `pnpm gen:api`.
- [ ] **Step 3:** Push → PASS.
- [ ] **Step 4:** Commit `feat(workspaces): Zorg and emergency-access settings`.

### Task 30: Private by hand and by the Zorg label; confirm with visibility

PB T17 and the visibility part of PB T18 (`ConfirmIn.visibility: str = ""`), inside Task 9's write order: `make_private`/`make_public` run in step 1 as the user (the flip as the current actor, PB decision 6), before step 2.

**Files:** PB T17's and T18's; `DOCUMENT_OPERATION_CASES` gains `setDocumentVisibility`. Tests: `backend/tests/test_documents_visibility_api.py`, `backend/tests/test_documents_confirm_private.py`, `backend/tests/test_privacy_concurrency.py`.

**Interfaces:** PB T17 (`DocumentSummaryOut.visibility: str = "workspace"`, `reader_names: list[str] = []`, `DocumentOut.shared_user_ids: list[UUID] = []`, `can_make_public: bool = False`, `setDocumentVisibility`), PB T18 (`ConfirmIn.visibility`). `setDocumentVisibility` accepts `shared_user_ids` on a `workspace` letter too (R44): `visibility: "workspace"` with a non-empty list sets shares instead of answering 422.

- [ ] **Step 1:** PB T17's and T18's failing tests, plus `test_share_without_making_private(client)`; push → FAIL.
- [ ] **Step 2:** Implement; `make openapi`, `pnpm gen:api`.
- [ ] **Step 3:** Push → PASS (Task 13's suite included).
- [ ] **Step 4:** Commit `feat(documents): private letters by hand, by the Zorg label and at confirm`.

### Task 31: Old clients keep new settings

**Files:**
- Test: `backend/tests/test_visibility_old_clients.py`

- [ ] **Step 1: Write the tests:**

```python
def test_old_settings_body_keeps_new_switches(client): ...    # PUT updateWorkspaceSettings {"allow_member_export": false}: switches, care_is_private unchanged
def test_old_person_body_keeps_a_department(client): ...      # updatePerson without kind
def test_old_label_body_keeps_its_department(client): ...     # updateLabel without department_id: department unchanged
def test_answers_validate_against_released_schemas(client): ... # DocumentOut, PersonOut, LabelOut, MemberOut against openapi_1b/1c fixtures
```

- [ ] **Step 2:** Push → PASS, or fix (`update_label` must treat a missing `department_id` as "leave": `LabelUpdateIn.department_id` has no field at all; only `LabelIn` on create has it).
- [ ] **Step 3:** Commit `test: older clients never wipe the new settings`.

### Task 32: Rules and private letters: automatic actions never widen readers; "privé maken"

PB T10 and PB T19, unchanged (engine pure; `make_private` action only in `auto` rules; web rule editor option "Privé maken" and sentence "maak de brief privé").

- [ ] **Step 1:** PB T10's and T19's failing tests (Python and the four web tests); push → FAIL; `cd web && pnpm test` → FAIL.
- [ ] **Step 2:** Implement; `make openapi`, `pnpm gen:api`, `pnpm extract`.
- [ ] **Step 3:** Push → PASS; `pnpm test && pnpm lint && pnpm build` → PASS.
- [ ] **Step 4:** Commit `feat(rules): automatic actions keep private letters private; the action privé maken`.

### Task 33: Emergency access

PB T20 amended (S 4.7, R45).

**Files:** PB T20's. Test: `backend/tests/test_privacy_emergency.py`.

**Interfaces:**
- `visibility.open_emergency(*, workspace, person, actor, reason) -> EmergencyResult` (PB T20), opening, as `emergency` reader rows for one hour, **every** letter on the person's or department's name that the admin cannot see at that moment (private or not): the set is `{d | person ∈ d.addressee_ids} − {d visible as the admin}` computed with `viewers()`.
- `visibility.can_edit(document: Document, user: User) -> bool` (S 4.9.3): role `member` or `admin`, the letter visible, and not only through an `emergency` row; replaces PB's `require_regular_reader` at every call site PB T20 lists, raising PB's 403 `emergency_read_only`.

- [ ] **Step 1:** PB T20's failing tests plus:

```python
def test_emergency_opens_non_private_post_the_admin_cannot_see(): ...   # office(): Anne opens Femke's person: finance_letter readable 1 h
def test_emergency_skips_letters_the_admin_already_sees(): ...          # opened_count excludes them
def test_emergency_reader_cannot_assign_or_share_a_non_private_letter(client): ...  # 403 emergency_read_only
```

- [ ] **Step 2:** Push → FAIL.
- [ ] **Step 3:** Implement; `make openapi`, `pnpm gen:api`.
- [ ] **Step 4:** Push → PASS.
- [ ] **Step 5:** Commit `feat(documents): emergency access to a person's hidden post`.

### Task 34: The back-up check knows the visibility functions and policies

PB T21, amended: `helpers_present` counts six functions (`brievie_actor_id`, `brievie_is_system`, `brievie_sees_all_post`, `brievie_my_post_ids`, `brievie_covered_post_ids`, `brievie_actor_sorts_post`), `privacy_expected` keys on migration `("workspaces", "0007_visibility_functions")`; `tasks_task` is checked for its three CASE policies like the child tables. Problem sentence for missing functions: "De database mist de functies voor wie welke post ziet." (plan).

- [ ] **Step 1:** PB T21's failing tests, with `DROP FUNCTION public.brievie_my_post_ids(uuid) CASCADE` as the helper case; push → FAIL.
- [ ] **Step 2:** Implement.
- [ ] **Step 3:** Push → PASS.
- [ ] **Step 4:** Commit `feat(backup): verify a restore keeps the visibility functions and policies`. **Merge point M3.**

---

## Slice A2c: switching safely

### Task 35: The rule as data: snapshot, categories, diff, confirmation (pure)

S 4.4 point 1, S 4.14 `visibility_rules`, P6.

**Files:**
- Create: `backend/apps/workspaces/visibility_rules.py` (no Django import, like `rules.engine`), `backend/tests/visibility_transitions.py` (the S 4.4 table as data, shared with Task 39), `backend/tests/test_visibility_rules.py`; `docs/DECISIONS.md` (P6, P7, R34, R35, R37)

**Interfaces:**
- `@dataclass(frozen=True) class Switches: everyone_sees_all: bool; departments_enabled: bool; admins_read_all: bool`.
- `@dataclass(frozen=True) class MemberFacts: user_id: UUID; name: str; role: str; sorts_post: bool; person_ids: frozenset[UUID]; department_ids: frozenset[UUID]`.
- `@dataclass(frozen=True) class PolicySnapshot: switches: Switches; members: tuple[MemberFacts, ...]; person_names: Mapping[UUID, str]; department_names: Mapping[UUID, str]` (persons of kind person and departments, archived included) with properties `covered_ids: frozenset[UUID]` (persons linked to a member; departments with members while on) and `sorter_ids: frozenset[UUID]` (flagged members, else the admins).
- `@dataclass(frozen=True) class LetterFacts: id: UUID; private: bool; addressee_ids: frozenset[UUID]; scanned_by: UUID | None; done: bool; reader_ids: frozenset[UUID]`.
- `expected_sight(snapshot: PolicySnapshot, letter: LetterFacts) -> frozenset[UUID]` (S 4.1 rules 1–6; tests only).
- `sight_categories(snapshot: PolicySnapshot, member: MemberFacts) -> frozenset[str]`: `"all"` (E), else `"all_as_admin"` (admin and A), else the union of `"person:<id>"` for the member's persons, `"department:<id>"` for their departments while D, and for a sorter `"unassigned"` plus `"person:<id>"` for every person without an account and `"department:<id>"` for every department not covered.
- `@dataclass(frozen=True) class Change: user_id: UUID; name: str; gains: tuple[str, ...]; loses: tuple[str, ...]`.
- `diff(before: PolicySnapshot, after: PolicySnapshot) -> list[Change]` (members only in `before` lose everything; only in `after` gain everything; unchanged members omitted; sorted by name).
- `needs_confirmation(changes: Sequence[Change], *, before: PolicySnapshot, narrowing: bool) -> bool` (P6).

- [ ] **Step 1: Failing tests:**

```python
@pytest.mark.parametrize("row", TRANSITIONS, ids=lambda r: r.name)
def test_gains_and_losses_match_the_table(row): ...          # on the snapshot of office(): who gains, who loses, per S 4.4
@pytest.mark.parametrize("row", TRANSITIONS, ids=lambda r: r.name)
def test_confirmation_matches_the_table(row): ...
def test_everyone_sees_all_subsumes_every_category(): ...
def test_admin_is_the_fallback_sorter_only_while_nobody_is_flagged(): ...
def test_department_without_members_is_not_covered(): ...
def test_departments_off_count_as_nobody(): ...
def test_expected_sight_never_includes_a_non_reader_for_a_private_letter(): ...
def test_module_imports_no_django(): ...
```

  `TRANSITIONS` (in `tests/visibility_transitions.py`): one `Transition(name, start: Callable[[Switches], bool], apply_to_snapshot: Callable[[PolicySnapshot], PolicySnapshot], apply_to_db: Callable[[Office, Client], None], narrowing: bool, confirm: bool | Literal["when_admin_gains"], notice_to: str)` per row of S 4.4, named as the rows ("E off → on", "sorter added", …).
- [ ] **Step 2:** Push → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Push → PASS.
- [ ] **Step 5:** Commit `feat(workspaces): the rule of sight as data, with diff and confirmation`.

### Task 36: The sentences: `policy_text.describe`

S 4.2, 4.3, 4.15.

**Files:**
- Create: `backend/apps/workspaces/policy_text.py` (pure; gettext only), `backend/tests/test_policy_text.py`

**Interfaces:**
- `@dataclass(frozen=True) class PolicyFacts: snapshot: PolicySnapshot; kind: str; viewer_id: UUID | None; care_is_private: bool; emergency_access: bool; ai_supplier_name: str` (`""` = AI off) `; has_backup: bool; server_admin_names: tuple[str, ...]`.
- `describe(facts: PolicyFacts, *, audience: Literal["summary", "member"]) -> list[str]`; `category_phrase(category: str, snapshot: PolicySnapshot) -> str` ("alle post die niet privé is", "alle post als beheerder", "post van Finance", "post van Jan", "post zonder naam").
- `summary` reproduces S 4.2 verbatim: Thuis "Iedereen in Brievie ziet alle post, behalve post die privé is." / "Zorgpost is privé." / "Je bent beheerder." / "Brieven worden niet door AI gelezen." / "Er is nog geen back-up."; Organisatie "Ieder ziet zijn eigen post en die van zijn afdelingen." / "Er zijn nog geen afdelingen." / "Post zonder naam zie jij, tot je iemand aanwijst die post verdeelt." / "Beheerders lezen geen post die niet voor hen is." / "Noodtoegang staat aan." and the same AI and back-up lines. With E off and D off: "Ieder ziet zijn eigen post." (plan). With an AI supplier: "Tekst van brieven gaat naar %(supplier)s." (S 4.15).
- `member` reproduces S 4.15's lines for the viewer: "Iedereen in Brievie ziet alle post, behalve privé-post." or "Je post zien alleen jij en, als hij op naam van een afdeling staat, de mensen van die afdeling."; "De beheerders %(names)s lezen alle post die niet privé is." / "De beheerders lezen je post niet."; "Post zonder naam zien: %(names)s (verdeelt post)."; "Privé-post zie alleen jij, en wie je die brief geeft."; "Noodtoegang staat aan. De beheerders kunnen je post openen. Ze moeten een reden geven, en jij krijgt dan een melding." / "Noodtoegang staat uit."; "Wie de server zelf beheert, kan technisch bij alle gegevens. Dat is %(names)s."; the AI line. Names joined "Erik en Sanne" / "Anne, Bob en Carla".

- [ ] **Step 1: Failing tests:**

```python
def test_thuis_summary_after_setup(): ...            # == the five S 4.2 lines
def test_organisatie_summary_after_setup(): ...      # == the seven S 4.2 lines
@pytest.mark.parametrize("state", ALL_16_STATES)
def test_member_lines_for_each_state(state): ...     # each S 4.15 line present exactly when its condition holds
def test_ai_line_names_the_supplier(): ...
def test_names_are_joined_in_dutch(): ...
def test_category_phrases(): ...
```

- [ ] **Step 2:** Push → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Push → PASS.
- [ ] **Step 5:** Commit `feat(workspaces): the sentences that say who sees what`.

### Task 37: Changing the switches: preview, confirmation, audit, notices

S 4.4, 4.13, 4.14; P9, P10.

**Files:**
- Create: `backend/apps/workspaces/visibility_settings.py`, `backend/apps/accounts/migrations/0010_visibility_notices.py`, `backend/tests/test_visibility_settings.py`, `backend/tests/test_visibility_api.py`
- Modify: `backend/apps/workspaces/api.py`, `schemas.py`, `backend/apps/audit/codes.py` (`WORKSPACE_PRESET_APPLIED` `{"preset"}`, `POST_SORTER_CHANGED` `{"member", "value"}`), `backend/apps/accounts/models.py` and `notices.py` (codes and sentences below; `NOTICE_VALUE_KEYS`), `test_actor_callers.py` (+ `apps/workspaces/visibility_settings.py`), `docs/DECISIONS.md` (P9, P10)

**Interfaces** (`apps.workspaces.visibility_settings`):
- `snapshot(workspace: Workspace) -> PolicySnapshot` (as the system, three queries).
- `fingerprint(snapshot: PolicySnapshot) -> str`: sha256 hex of the canonical JSON of the switches and of every member's role, flag, persons and departments (S 4.4 point 2).
- `@dataclass(frozen=True) class Proposal: kind: str | None = None; everyone_sees_all: bool | None = None; departments_enabled: bool | None = None; admins_read_all: bool | None = None; care_is_private: bool | None = None; allow_member_export: bool | None = None; apply_preset: bool = False; add_start_set: bool = False`.
- `@dataclass(frozen=True) class PreviewResult: changes: list[Change]; letter_count: int; needs_confirmation: bool; sentences: list[str]; preview_token: str`.
- `preview(workspace: Workspace, apply: Callable[[], None], *, narrowing: bool) -> PreviewResult`: in a savepoint: `before = snapshot`, letter viewer map before (md5 per letter from `viewer_pairs_sql("TRUE")`, as the system), `apply()`, the same after, roll the savepoint back; `preview_token = fingerprint(before)`; `sentences` = `describe(..., audience="summary")` of the after-state.
- `require_confirmation(*, changes: Sequence[Change], before: PolicySnapshot, narrowing: bool, confirmed: bool, preview_token: str) -> None`: when `needs_confirmation(...)`: not `confirmed` → `ApiError(409, "confirmation_required", "Bevestig eerst dat deze mensen meer post gaan zien.")` (plan); `preview_token != fingerprint(before)` → `ApiError(409, "preview_outdated", "Er is intussen iets veranderd. Bekijk de wijziging opnieuw.")`.
- `notify_changes(workspace: Workspace, changes: Sequence[Change], *, actor_name: str, admins_gained_all: bool) -> None`: `sight_widened` to each member with gains, `sight_narrowed` to each with losses (one notice per member, the first category; P10 values), and `admins_read_all_on` to every member when `admins_gained_all`.
- `change(*, workspace: Workspace, proposal: Proposal, actor: AuditActor, confirmed: bool, preview_token: str) -> Workspace` (`transaction.atomic`): `lock_memberships(workspace)`, then the workspace `select_for_update()`; `before = snapshot`; apply the proposal (with `apply_preset`: the other kind's values where the proposal leaves None; `add_start_set` runs `labels.presets.apply_preset` and `rules.presets.ensure_preset_rules`); `after = snapshot`; `require_confirmation(narrowing=False)`; audit one `workspace_setting_changed {"setting", "value"}` per changed field (`kind` included) and `workspace_preset_applied {"preset": kind}` when `apply_preset`; `notify_changes`. Changing only D while E is on writes the audit row and no notice (S 4.4).
- Notices (codes, sentences): `sight_widened`: category `all` "Je kunt nu alle post zien die niet privé is. Dat heeft %(name)s op %(date)s ingesteld." (S 4.4); `department` "Je ziet nu ook de post van %(department)s. Dat heeft %(name)s op %(date)s ingesteld."; `person` "Je ziet nu ook de post van %(person)s. …"; `unassigned` "Je ziet nu post zonder naam, om er een naam op te zetten. …"; `all_as_admin` "Je kunt als beheerder nu alle post zien die niet privé is. …" (plan). `sight_narrowed`: `department` "Je ziet de post van %(department)s niet meer." (S 4.4); `all` "Je ziet de post van anderen niet meer."; `person` "Je ziet de post van %(person)s niet meer."; `unassigned` "Je ziet post zonder naam niet meer."; `all_as_admin` "Je leest als beheerder niet meer alle post." (plan). `admins_read_all_on`: "De beheerders kunnen nu alle post lezen die niet privé is, ook de jouwe. Dat heeft %(name)s op %(date)s ingesteld." (plan). `self_grant` (values `category`, `department` or `person`; used by Task 38): "%(name)s heeft zichzelf toegang gegeven tot de post van %(department)s." / "… tot de post van %(person)s." / "… tot post zonder naam." (plan).
- Schemas: `VisibilityIn` exactly S 4.13 (with `confirmed: bool = False`, `preview_token: str = ""`); `VisibilityPreviewIn` = `VisibilityIn`'s setting fields plus `member_change: str = ""` (`role`, `sorts_post`, `department_add`, `department_remove`, `link`, `unlink`, `remove`; Task 38 fills them), `member_user_id: UUID | None = None`, `member_person_id: UUID | None = None`, `member_role: str = ""`, `member_sorts_post: bool = False`; `MemberChangeOut {user_id: UUID, name: str, gains: list[str], losses: list[str]}` (Dutch `category_phrase`s); `VisibilityPreviewOut {changes: list[MemberChangeOut], letter_count: int, needs_confirmation: bool, sentences: list[str], preview_token: str}`; `VisibilityOut {kind: str, everyone_sees_all: bool, departments_enabled: bool, admins_read_all: bool, sorter_user_ids: list[UUID], sentences: list[str]}`; `WorkspaceSettingsOut` gains `everyone_sees_all: bool = True`, `departments_enabled: bool = False`, `admins_read_all: bool = True`.
- Endpoints: `getVisibility` `GET /workspaces/{workspace_id}/visibility` (any member, P9; `sentences` with `audience="member"` for the caller); `previewVisibilityChange` `POST /workspaces/{workspace_id}/visibility/preview` (admin); `updateVisibility` `PUT /workspaces/{workspace_id}/visibility` (admin; 409 `confirmation_required`, 409 `preview_outdated`). Unknown `kind` or `member_change` → 422.

- [ ] **Step 1: Failing tests:**

```python
def test_preview_lists_who_gains_and_loses_and_counts_letters(client): ...   # office(): E off -> on: 5 people gain, letter_count == hidden letters
def test_preview_changes_nothing(client): ...
def test_widening_without_confirmation_is_409(client): ...
def test_stale_preview_token_is_409_and_changes_nothing(client): ...        # another admin flags a sorter between preview and update
def test_narrowing_needs_no_confirmation(client): ...
def test_audit_row_per_changed_setting(client): ...
def test_preset_switch_writes_preset_row_and_never_removes_labels_or_rules(client): ...
def test_admins_read_all_on_notifies_every_member(client): ...
def test_notices_go_to_exactly_the_changed_members(client): ...
def test_departments_switch_while_everyone_sees_all_only_audits(client): ...
def test_member_gets_sentences_from_get_visibility(client): ...
def test_viewer_cannot_change_visibility(client): ...                       # 403 forbidden
def test_emergency_access_is_not_in_visibility_in(): ...
```

- [ ] **Step 2:** Push → FAIL.
- [ ] **Step 3:** Implement; `make openapi`, `pnpm gen:api`.
- [ ] **Step 4:** Push → PASS.
- [ ] **Step 5:** Commit `feat(workspaces): change who sees what with a preview, confirmation and notices`.

### Task 38: Sorters, and every member change through the same three helpers

S 4.4 (writers), S 4.7 "Self-grants are visible", P7.

**Files:**
- Modify: `backend/apps/workspaces/members.py` (`change_role`, `remove_member`, new `set_sorts_post`), `backend/apps/people/services.py` (`add_department_member`, `remove_department_member`, `link_member`), the three apps' `api.py`/`schemas.py`, `backend/apps/workspaces/services.py` (`list_members`)
- Test: `backend/tests/test_visibility_member_changes.py`

**Interfaces:**
- `members.set_sorts_post(*, workspace: Workspace, user_id: UUID, sorts_post: bool, actor: AuditActor, confirmed: bool, preview_token: str) -> Membership`: viewer → 422 `viewer_cannot_sort` "Iemand met Alleen lezen kan geen post verdelen." (plan); audit `post_sorter_changed {"member": <name>, "value": bool}`; flagging oneself notifies the other admins (`sight_widened`, category `unassigned`).
- Every writer: `locked = lock_memberships(workspace)` → `before = snapshot(workspace)` → its own change → `after = snapshot(workspace)` → `require_confirmation(changes=diff(before, after), before=before, narrowing=<False for adds, links, promotions, flagging; True for removals, unlinks, demotions, unflagging>, confirmed=…, preview_token=…)` → its audit row → `notify_changes`. `change_role` to `viewer` clears `sorts_post` in the same transaction.
- `preview` for a member change: `previewVisibilityChange` with `member_change` calls the same writer's apply step inside `visibility_settings.preview`.
- Schemas (additive): `SortsPostIn {sorts_post: bool, confirmed: bool = False, preview_token: str = ""}`; `MemberRoleIn`, `DepartmentMemberIn`, `PersonMemberIn` gain `confirmed: bool = False`, `preview_token: str = ""`; `MemberOut.sorts_post: bool = False`, `MemberOut.department_ids: list[UUID] = []`.
- Endpoint: `updateMemberSortsPost` `PUT /workspaces/{workspace_id}/members/{user_id}/sorts-post` (admin) → `MemberOut`; errors 404 `member_not_found`, 409 `confirmation_required`, 409 `preview_outdated`, 422 `viewer_cannot_sort`.
- Self-grants (S 4.7): when the actor is the member concerned, besides the usual notices, `self_grant` (Task 37) goes to the department's members (department add) or to the other admins (person link, self-flagging); the audit rows stay the usual ones.

- [ ] **Step 1: Failing tests:**

```python
def test_flag_a_sorter_with_confirmation(client): ...
def test_viewer_cannot_sort(client): ...                                      # 422
def test_making_a_sorter_viewer_clears_the_flag(client): ...
def test_promotion_while_admins_read_all_needs_confirmation(client): ...      # 409 without; with: every member notified
def test_department_add_needs_confirmation_and_notifies(client): ...
def test_last_department_member_removed_sends_post_to_sorters(client): ...    # P7: no confirmation, sorters notified
def test_unlink_sends_post_to_sorters_without_confirmation(client): ...
def test_self_grant_notifies_department_members(client): ...
def test_self_flagging_notifies_the_other_admins(client): ...
def test_role_change_with_stale_preview_token_is_409(client): ...
def test_preview_of_a_member_change_changes_nothing(client): ...
def test_member_out_lists_departments_and_flag(client): ...
```

- [ ] **Step 2:** Push → FAIL.
- [ ] **Step 3:** Implement; `make openapi`, `pnpm gen:api`.
- [ ] **Step 4:** Push → PASS.
- [ ] **Step 5:** Commit `feat(workspaces): sorters, and every change of sight previewed and confirmed`.

### Task 39: The transition test

S 4.17's first, second and ninth ⚠ tests. Written against the real services; any failure is a bug in Tasks 7, 35, 37 or 38.

**Files:**
- Create: `backend/tests/test_visibility_transitions.py`

- [ ] **Step 1: Write the tests:**

```python
from tests.visibility_support import ALL_16_STATES
@pytest.mark.parametrize("state", ALL_16_STATES)
@pytest.mark.parametrize("row", TRANSITIONS, ids=lambda r: r.name)
def test_transition(state, row): ...
    # skip when row.start(state) is False; office() in `state`; for every actor (admin, sorter, department member,
    # plain member, viewer) and every letter of office(): visible as actor (policy) == expected_sight(snapshot)
    # before and after row.apply_to_db; the preview's needs_confirmation == row.confirm; notice recipients ==
    # members with a non-empty diff; the audit rows of the row exist; DepartmentMember, Label, Rule counts (as SYSTEM)
    # unchanged; private_femke's viewers identical before and after
@pytest.mark.parametrize("row", REVERSIBLE_TRANSITIONS, ids=lambda r: r.name)
def test_switch_back_restores_sight(row): ...
def test_no_orphan_in_any_state(): ...
    # random.Random(20261120): 300 random states (switches, flags, department members, links) over office()'s letters:
    # every non-private letter has at least one viewer (viewers()); every private letter only its readers
def test_viewers_equal_the_policy_in_every_transition(): ...
```

- [ ] **Step 2:** Push → PASS, or fix the owning task's code and push again.
- [ ] **Step 3:** Commit `test: every change of sight, in every state, against the database`.

### Task 40: Presets at installation and on the command line

S 4.2, S 5.2, R40, R41; P8 ends here.

**Files:**
- Create: `backend/apps/workspaces/presets.py`, `backend/apps/workspaces/management/commands/create_workspace.py`, `backend/tests/test_workspaces_presets.py`
- Modify: `backend/apps/workspaces/services.py` (`create_workspace` calls `apply_preset_settings`), `backend/apps/audit/codes.py` (`WORKSPACE_CREATED` server code, values `{"kind"}`), `docs/DECISIONS.md` (R40, R41, "P8 ended"), `docs/SELFHOST.md` (the command)

**Interfaces:**
- `@dataclass(frozen=True) class PresetValues: everyone_sees_all: bool; departments_enabled: bool; admins_read_all: bool; care_is_private: bool; emergency_access: bool; allow_member_export: bool`; `PRESETS: Final[dict[str, PresetValues]]` with the Global Constraints' values.
- `apply_preset_settings(workspace: Workspace) -> None`: sets the six fields from `PRESETS[workspace.kind]` (creation only, no audit; `visibility_settings.change` uses `PRESETS` for a later switch).
- `setupServer` unchanged; `extra_labels` still accepted (R40).
- `manage.py create_workspace --name NAME --kind home|business --admin-email EMAIL`: in one transaction: `create_workspace(...)` with the existing account of that address as admin, or, when none exists, a workspace whose first admin is invited: an `Invitation(role=admin, created_by=None, created_by_name="Serverbeheerder", email=<address>)` and the link printed once on stdout; server audit `workspace_created {"kind"}` (actor system). Exit code 2 with a Dutch sentence for an unknown kind.

- [ ] **Step 1: Failing tests:**

```python
def test_thuis_preset(): ...
def test_organisatie_preset(): ...                         # E off, D on, A off, care off, emergency on, export off
def test_setup_of_an_organisation_starts_in_own_post(client): ...
def test_setup_still_accepts_extra_labels(client): ...
def test_create_workspace_command_with_an_existing_account(capsys): ...
def test_create_workspace_command_prints_an_invitation_once(capsys): ...
def test_create_workspace_command_refuses_an_unknown_kind(capsys): ...
def test_create_workspace_command_audits(): ...
```

- [ ] **Step 2:** Push → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Push → PASS.
- [ ] **Step 5:** Commit `feat(workspaces): Thuis and Organisatie set the switches at installation`.

### Task 41: "Wie kan mijn post zien?" and "Wie kan deze brief zien?"

**Files:**
- Modify: `backend/apps/workspaces/api.py`, `schemas.py`, `backend/apps/documents/api.py`, `schemas.py`, `backend/apps/documents/visibility.py` (`viewer_reasons`), `backend/tests/test_leak_document_operations.py` (case for `getDocumentAccess`)
- Test: `backend/tests/test_visibility_access_api.py`

**Interfaces:**
- `getMyPostAccess` `GET /workspaces/{workspace_id}/post-access` (any member) → `PostAccessOut {sentences: list[str]}` = `describe(…, audience="member")` with the caller as viewer, the AI supplier from `apps.pipeline.settings_service.status(workspace_id)`, `has_backup` from `BackupRun.objects.filter(status=SUCCEEDED).exists()`, the server admins' names.
- `visibility.viewer_reasons(document: Document) -> dict[UUID, list[str]]`: the viewers from `viewers([document.id])`, each with the reasons that hold (from the S 4.1 arms on the snapshot and the reader rows): "op naam van %(person)s", "afdeling %(department)s", "verdeelt post", "heeft de brief gescand", "is gedeeld", "ziet alle post", "beheerder, leest alle post", "noodtoegang tot %(time)s" (S 4.15 plus plan for the last two).
- `getDocumentAccess` `GET /workspaces/{workspace_id}/documents/{document_id}/access` (any member who can read the letter; else 404 `document_not_found`) → `DocumentAccessOut {viewers: list[DocumentViewerOut]}`, `DocumentViewerOut {user_id: UUID, name: str, reasons: list[str]}`, sorted by name.

- [ ] **Step 1: Failing tests:**

```python
def test_post_access_sentences_for_a_member_in_own_post(client): ...
def test_post_access_names_the_server_admin(client): ...
def test_document_access_lists_viewers_with_reasons(client): ...   # finance_letter as Femke: Femke (afdeling Finance), …
def test_every_viewer_has_a_reason_and_nobody_else_is_listed(): ...  # over office() in all 16 states
def test_document_access_of_a_hidden_letter_is_404(client): ...
```

- [ ] **Step 2:** Push → FAIL.
- [ ] **Step 3:** Implement; `make openapi`, `pnpm gen:api`.
- [ ] **Step 4:** Push → PASS.
- [ ] **Step 5:** Commit `feat: who can see my post, and who can see this letter`. **Merge point M4.**

---

## Slice A2d: the web

Fluent 2 building blocks from `web/src/components/ui.tsx`, React Aria dialogs as in `persons.tsx`, `ConfirmDialog` from `components/confirm-dialog.tsx`, server refusals through `ApiFailure.code`/`ErrorAlert`; Dutch B1, je-form; tests query by role with MSW (`test-utils.tsx`: `renderApp`, `loggedInHandlers`, `asRole(role, kind)`); `pnpm extract` after every text change. A Thuis workspace says "Groepen"/"Groep" wherever an Organisatie says "Afdelingen"/"Afdeling" (R51). Each task: Step 1 failing tests, Step 2 `pnpm test` → FAIL, Step 3 implement, Step 4 `pnpm extract && pnpm test && pnpm lint && pnpm build` → PASS, Step 5 commit.

### Task 42: Instellingen → "Wie ziet wat", with the preview dialog

**Files:**
- Create: `web/src/routes/visibility.tsx` (`/w/$workspaceId/instellingen/wie-ziet-wat`, admin-only `beforeLoad` as `settings.tsx`), `web/src/components/visibility-preview-dialog.tsx`, tests next to each
- Modify: `web/src/router.tsx`, `web/src/routes/settings.tsx` (link "Wie ziet wat"), `web/src/api/queries.ts` (`visibilityQuery`)

**Interfaces:**
- Page top to bottom (S 4.3): the sentences of `getVisibility`; "Soort gebruik: Thuis / Organisatie" with "Dit bepaalt welke standaard Brievie voorstelt. Het verandert zelf niemands rechten."; switch "Iedereen ziet alle post" with "Aan: iedereen in Brievie ziet elke brief, behalve brieven die privé zijn. Uit: je ziet alleen post op je eigen naam en van je afdelingen."; switch "Afdelingen gebruiken" ("Groepen gebruiken") with "Zet post op naam van een afdeling, zoals Finance. Wie in die afdeling zit, ziet die post." and, while E is on, "Nu zien nog steeds alle mensen alle post; afdelingen helpen dan alleen bij het sorteren."; switch "Beheerders lezen alle post" (hidden while E on) with "Aan: <admin names> zien alle post die niet privé is. Uit: zij regelen wie wat ziet, maar lezen alleen post die voor hen is."; "Post zonder naam" (hidden while E on): the sorters with "Verdeelt post", "Iemand toevoegen", and "Wie hier staat, ziet brieven waar nog geen naam of afdeling op staat, en zet er een naam op. Staat hier niemand, dan doen de beheerders dat: <admin names>."; links "Privé", "Afdelingen", "Wie kan mijn post zien?".
- `<VisibilityPreviewDialog preview={VisibilityPreviewOut} confirmLabel={string} onConfirm />`: title "Dit verandert voor N mensen wat ze zien." (N = `changes.length`), lists "Gaan meer zien" and "Gaan minder zien" (one line per person: name and the phrases), and when `needs_confirmation` a confirm button whose label says what happens ("Ja, iedereen mag alle post zien" for E on; plan for the others: "Ja, <naam> verdeelt post", "Ja, zet afdelingen aan", "Ja, beheerders lezen alle post"); otherwise "Wijzigen". A 409 `preview_outdated` reloads the preview and shows the server's sentence. Changing Soort gebruik opens the dialog with one checkbox per differing preset value (all checked; "Noodtoegang" shown as the line "De serverbeheerder zet noodtoegang aan of uit.") and "Ook de startset labels toevoegen".

- [ ] **Step 1: Failing tests:**

```tsx
test("an admin sees the three switches with their sentences")
test("Beheerders lezen alle post and Post zonder naam hide while Iedereen ziet alle post is on")
test("turning Iedereen ziet alle post on asks for confirmation")      // dialog "Dit verandert voor 5 mensen wat ze zien.", PUT with confirmed and preview_token
test("turning it off shows who sees less and needs no Ja-button")
test("a stale preview is shown again with the server's sentence")
test("switching to Organisatie offers the preset values as checkboxes")
test("a member is redirected to Te doen")
test("a Thuis workspace says Groepen gebruiken")
```

- [ ] **Steps 2–5** as the slice says; commit `feat(web): Wie ziet wat, with a preview before every change`.

### Task 43: Afdelingen (Groepen)

**Files:**
- Create: `web/src/routes/departments.tsx` (`/w/$workspaceId/afdelingen`, nav item next to Personen while `departments_enabled`), test next to it
- Modify: `web/src/router.tsx`, `web/src/components/page.tsx` (nav), `web/src/routes/persons.tsx` (departments no longer listed there while departments are on)

**Interfaces:** list with members, leads and letter counts (`listPersons` filtered on `kind`); "Afdeling maken" (name, name variants, members; `createPerson {kind: "department"}` then `addDepartmentMember` per member, each through the preview dialog when it needs confirmation); per department: members (add, remove, "Leidinggevende" switch via `updateDepartmentMember`), its labels (link to Labels filtered), "Opbergen" (archive), "Verwijderen" only when it has no letters; the sentence "Wil je post scheiden binnen je organisatie? Maak afdelingen." (S 5.1); 409 `cannot_grant_self` and `person_in_use` show the server's sentence.

- [ ] **Step 1: Failing tests:**

```tsx
test("lists departments with members, leads and letter counts")
test("making a department adds its members after a preview")
test("adding yourself shows the refusal for a department with private post")
test("a lead is marked Leidinggevende")
test("a department with letters can be archived but not deleted")
test("the page is absent while departments are off")
test("a Thuis workspace calls them Groepen")
```

- [ ] **Steps 2–5**; commit `feat(web): the Afdelingen page`.

### Task 44: Te doen: "Naam kiezen" / "Toewijzen", the picker and the hand-over sentence

**Files:**
- Modify: `web/src/routes/inbox.tsx`, `web/src/components/person-picker.tsx`, `web/src/components/document-table.tsx`, `web/src/routes/document.tsx` (review), tests next to each

**Interfaces:**
- Row status for `unassigned`: "Naam kiezen" while departments are off, "Toewijzen" while on (S 4.5).
- Filter chips (department members and sorters only): "Voor mij" (`person_id` = own person), one per department (`person_id` = the department), "Zonder naam" (`inbox_state=unassigned`) for sorters.
- Picker: departments first while on, labelled as departments; before saving, when `getDocumentAccess`-style knowledge is not needed: the client compares the chosen addressees with the user's own person and departments and, when the user will lose the letter, shows "Na toewijzen ziet HR deze brief niet meer. Hij gaat naar Finance." (S 4.5, moving between departments) or, in the review, "Na bevestigen kun je deze brief niet meer zien. Hij gaat naar Jan." (S 4.8). An answer with `can_read: false` returns to Te doen with `MessageBar` "Je kunt deze brief niet meer openen." (PB T23).

- [ ] **Step 1: Failing tests:**

```tsx
test("an unassigned letter reads Naam kiezen without departments and Toewijzen with them")
test("chips filter on Voor mij, a department and Zonder naam")
test("the picker shows departments first")
test("moving a letter to another department warns before saving")
test("confirming a letter for someone else warns and then returns to Te doen")
```

- [ ] **Steps 2–5**; commit `feat(web): assign post to a person or department in Te doen`.

### Task 45: Labels "Van", Leden "Verdeelt post" and "Afdelingen", role words

**Files:**
- Modify: `web/src/routes/labels.tsx`, `web/src/routes/members.tsx`, `web/src/components/role-dialog.tsx`, `web/src/routes/automations.tsx`, tests next to each

**Interfaces:** Labels: a column "Van" ("Iedereen" or the department), the department choice in the label dialog for admins and leads, the sentence "Noem een label niet naar een persoon of een gevoelig onderwerp." (S 4.6), on a name clash the suggestion "Finance: Facturen" (prefix with the department). Leden: column "Verdeelt post" (a switch through `updateMemberSortsPost` and the preview dialog; disabled for "Alleen lezen"), column "Afdelingen" (from `MemberOut.department_ids`), the role named "Alleen lezen" (R29); a role change opens the preview dialog when the server answers 409 `confirmation_required`. Automatisering (`web/src/routes/automations.tsx`): while departments are off, a rule whose action adds a department shows "Afdelingen staan uit: deze post gaat nu naar wie post verdeelt." (S 4.4).

- [ ] **Step 1: Failing tests:**

```tsx
test("labels show Van Iedereen or the department")
test("a lead can create a label for their department")
test("Verdeelt post switches with a preview")
test("a viewer cannot be a sorter")                       // switch disabled, text "Alleen lezen"
test("members list their departments")
test("promoting a member while admins read all asks for confirmation")
test("a department rule says where the post goes while departments are off")
```

- [ ] **Steps 2–5**; commit `feat(web): department labels, sorters and departments on Leden`.

### Task 46: On a letter: the lock, sharing, "Wie kan deze brief zien?", emergency access, Privé settings

PB T22, T23 and T24 (emergency and privacy settings parts), amended: "Delen met" on every letter (R44); "Wie kan deze brief zien?" from `getDocumentAccess`.

**Files:** PB T22's, T23's and T24's web files (without the Personen departments part, done in Task 43), plus `web/src/components/document-access.tsx` and test.

**Interfaces:** PB T22–T24's components; `<DocumentAccess documentId />`: a disclosure "Wie kan deze brief zien?" listing names with their reasons from the server.

- [ ] **Step 1:** PB T22's, T23's and T24's web tests (department-list cases excluded), plus:

```tsx
test("Delen met is offered on a letter that is not private")
test("Wie kan deze brief zien lists names with reasons")
```

- [ ] **Steps 2–5**; commit `feat(web): private letters, sharing, emergency access and who can see a letter`.

### Task 47: "Wie kan mijn post zien?", `/setup` and "Zo is Brievie nu ingesteld"

**Files:**
- Create: `web/src/routes/post-access.tsx` (`/w/$workspaceId/wie-ziet-mijn-post`, in the account menu), `web/src/routes/setup-summary.tsx` (`/w/$workspaceId/ingesteld`), tests next to each
- Modify: `web/src/routes/setup.tsx`, `web/src/router.tsx`, `web/src/components/page.tsx` (account menu), `docs/GLOSSARY.md` (remaining S 14 lines of part A)

**Interfaces:**
- `/setup`: the two cards (S 4.2) "Thuis" — "Voor jezelf en je gezin. Iedereen ziet alle post, behalve wat privé is." and "Organisatie" — "Voor een bedrijf, praktijk of vereniging. Ieder ziet zijn eigen post en die van zijn afdeling."; workspace name label "Naam, bijvoorbeeld 'Familie de Vries' of 'Bakkerij Jansen'"; no "Afdelingen" field (no `extra_labels` sent); success navigates to `/w/<id>/ingesteld`.
- `/ingesteld`: heading "Zo is Brievie nu ingesteld", the sentences of `getVisibility`, then links: Thuis "Iemand uitnodigen · Personen toevoegen · Back-up instellen · Wie ziet wat wijzigen"; Organisatie "Afdelingen maken · Mensen uitnodigen · Iemand post laten verdelen · Back-up instellen" (S 4.2).
- "Wie kan mijn post zien?": the sentences of `getMyPostAccess`, read-only, for every member.
- Every visible "Zakelijk" becomes "Organisatie" (workspace list, setup, settings).

- [ ] **Step 1: Failing tests:**

```tsx
test("setup shows Thuis and Organisatie with their sentences and no Afdelingen field")
test("setup sends no extra_labels and opens Zo is Brievie nu ingesteld")
test("the summary shows the sentences and the links for Organisatie")
test("Wie kan mijn post zien is in the account menu for a member")
test("no screen says Zakelijk")
```

- [ ] **Steps 2–5**; commit `feat(web): one choice at installation, the summary, and who can see my post`. **Merge point M5.**

---

## iOS follow-up (planned separately in `Brievie_ios`)

The app decodes with swift-openapi-generator from `Packages/BrievieKit/Sources/BrievieAPI/openapi.json` (`make sync-api HOST=../Brievie_host`); new fields are ignored until synced, so nothing above breaks the current app. It needs:

- Fields: `PersonOut.kind`, `member_user_ids`, `lead_user_ids`; `DocumentSummaryOut.visibility`, `reader_names`; `DocumentOut.can_read`, `shared_user_ids`, `can_make_public`; `DocumentEventOut.note` and the new event kinds (skip unknown kinds); `ConfirmIn.visibility`; `WorkspaceSettingsOut.everyone_sees_all`, `departments_enabled`, `admins_read_all`, `care_is_private`; `WorkspaceOut.kind` shown "Organisatie" for `business`.
- Operations to add to `openapi-generator-config.yaml`: `getMyPostAccess` (read-only "Wie kan mijn post zien?" under Werkruimte), `getDocumentAccess`, `setDocumentVisibility`, `getVisibility` (only to show the sentences and decide "Naam kiezen"/"Toewijzen"), `listMembers` (for "Delen met").
- Behaviour: departments first in `PersonPickerView` while `departments_enabled`; row status "Toewijzen" instead of "Naam kiezen" (already in `DocumentsModel.swift`) while departments are on; the review's hand-over sentence and `can_read: false` after `confirmDocument`/`setDocumentAddressees` (close the letter with "Je kunt deze brief niet meer openen."); the lock and "Privé" (PB "What the iOS plan for part B must cover"); "Wie ziet wat" opens the web for admins.
- Already right: a 404 on a listed letter closes it (`ReviewModel.swift:349`); `CalendarSyncPlanner.plan` deletes events of tasks that no longer come back and deletes nothing when `listTasks` fails; no letter data on disk.

## Not in this plan (and where each hooks in)

- **Part B, the mailroom** (S 7): `Membership.mail_desk` next to `sorts_post`; its own restrictive policy on `MailItem` built from `brievie_actor_id()`; escalations to `DepartmentMember.is_lead` (Task 25); a content upload with `mail_item_id` sets no `scanned_by`, so rule 5 never applies; the mail desk line in `policy_text.describe`.
- **Section 6, directory groups to departments** (C2): writes through `people.services.add_department_member`/`remove_department_member` with actor "SSO" and `require_confirmation` skipped (S 4.4 point 2), adds 409 `department_managed_by_directory` to `addDepartmentMember`/`removeDepartmentMember`, and the "Beheerd via Microsoft" line on the Afdelingen page (Task 43); R52 does not touch part A.
- **Part D, folders on a NAS**: the archiver runs as `SYSTEM` (closed list) and takes a letter's folders from `Document.addressee_ids`; a letter without addressee gets no copy.
- **A3, invitation with device (v2 QR) and app-only accounts** (S 4.19 except account actions): `accept_invitation_with_device` calls `access.on_member_added` like `accept_invitation`, and the person link goes through Task 38's helpers (confirmed when the invitation was made). Account-action conditions 2 and 3 are in Task 28 already.
- **Several servers in the iOS app** (S 5.3): iOS only, M–L.
- **Workspace export (Beheer 5) and `manage.py export_workspace --all`**: `export_context(workspace_id, reader_id=<requester>)` follows the switches; the full copy uses `reader_id=None` (`SYSTEM`), with audit `workspace_full_export` and a notice to every member (R8).
- **Search, trash, duplicate detection** (separate branches): start from `visibility.visible_documents` and are caught by the coverage and source-scan tests of Task 23; Task 13's operation list makes each new `{document_id}` operation prove it answers a hidden letter like an unknown id.
- **Task assignment and the ICS feed**: no endpoint exists (P11). A future `assignTask` checks `visibility.viewers([document_id])` and answers 422 `assignee_cannot_read` (also for viewers); an ICS feed runs as the feed token's user and shows tasks of private letters as "Taak".
- **After a restore**, the admin checklist "Controleer wie wat kan zien" (R28) belongs to the back-up work; it lists the three switches, sorters, departments and emergency access.
- **Delegation** ("meekijken", R43): later.
- **The recovery mode suggestion** (S 4.2, Herstel): the back-up card offers "own password" first for Thuis and "recovery sheet" first for Organisatie; it belongs to the Herstel plan (branch `docs/recovery-password-design`) and reads `Workspace.kind`.
- **A3's audit and notice codes** `invitation_device_used`, and the server code `workspace_full_export`: with their features.

## What in the spec is unbuildable or contradicts the code

1. **No task-assign or task-create endpoint exists** (`apps/tasks/api.py` has `listTasks`, `completeTask`, `reopenTask`), so S 4.17's "⚠ Assigning a task to a member who cannot read the letter, or to a viewer: 422" has nothing to test; P11.
2. **S 4.4 table, "member removed from a department: gains nobody"**: when it was the department's last member, rule 4 sends the letters to the sorters, who gain; P7. The "Confirm" column treats fallback gains inconsistently (unlink: no; last sorter removed: yes) unless read as P6.
3. **S 4.9.5's "development machine"**: no database may run on this Mac; the threshold applies to the CI runner (P1).
4. **S 4.14 notice values** name only a category and a department; person-level changes need a person name (P10).
5. **S 4.9.1's bodies** read `brievie_actor_id()` while its text says the built functions take `uid`; the plan builds the `uid` form (Task 1). `brievie_my_post_ids` as written would also return departments' own ids through `p.user_id` only if a department had a user, which `Person.save` forbids; the plan adds `p.kind = 'person'` anyway.
6. **The workspace export of Beheer 5 and the archive folder of part D are not built**; S 4.10's rows for them become seams (above).
7. **`inbox_state` "unassigned"** means "no addressee" in the code and the iOS app; S 4.5's "unassigned post (no addressee anybody reads through)" is a wider set. Kept apart (P4) so the contract and the iOS filter do not change.
8. **`feat/ai-autopilot`** (not yet merged) adds addressees in the pipeline and `reopenDocument`; both are wrapped in Task 8's `addressee_scope` and Task 13's operation list. If it merges after Task 8 starts, its two call sites are added in the task that rebases onto it.
