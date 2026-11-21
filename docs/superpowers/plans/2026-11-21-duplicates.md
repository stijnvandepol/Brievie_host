# Duplicate letters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Brievie notices on arrival that a letter is probably already there, proposes the pair under "Te doen" to a member who sees both letters, and lets that member merge them (the other letter to the trash) or say "two letters" for good.

**Architecture:** A pair table `DuplicateCandidate` (one row per pair, RLS on both letters). Detection runs as the system in the pipeline's last transaction (`apps/documents/duplicates.py`, pure scoring in `duplicates_facts.py`) and blocks the AI's auto-confirm through the caller's rail in `steps._confirm_guarded`. Every member read goes through `duplicates.visible_pairs(membership)`, built on `visibility.visible_documents`; merge unites through `organise` and discards through `trash.move_to_trash(reason="duplicate")`.

**Tech Stack:** Django 6.1, Django Ninja, PostgreSQL 18 (forced RLS, `pg_trgm`, full-text search), Procrastinate, React 19 + TanStack Query/Router + React Aria + Lingui, Vitest + MSW.

**Spec:** `docs/superpowers/specs/2026-11-20-duplicates-design.md` (aligned 21-11; its Rulings and "Decided" bind). Also read `docs/superpowers/specs/2026-11-20-trash-design.md` (rulings 17, 18, 20) and `CLAUDE.md`.

**Start condition:** met. Branch `feat/duplicates` at `58917dd` holds main `efdd05e` and the finished trash. Latest migrations: `documents/0015_trash`, `audit/0004_trash_codes`, `tasks/0004_task_visibility`, `workspaces/0007_visibility_functions`. New: `documents/0016_duplicates` (one migration: model, constraints, index, event field and choices, RLS). No audit migration (no new code; `reason` values are not choices).

**Batches:** A = Tasks 1, 2 (independent: model/seam and pure facts). B = Tasks 3, 4 (detection and merge; both consume 1 and 2). C = Tasks 5, 6, 7 (API, web, docs).

## Global Constraints

- `CLAUDE.md` "Rules in short" applies in full: API additive under `/api/v1`, explicit camelCase `operation_id`, new fields on existing schemas have defaults, nullable only primitives, no `anyOf`, errors via `ApiError` with a Dutch B1 sentence, thin endpoints, every mutating service in `transaction.atomic`.
- Constants exact: `WINDOW = timedelta(days=180)`, `TERMS = 16`, `MAX_CANDIDATES = 20`, `MAX_OPEN_PER_LETTER = 3`, `TEXT_SIMILAR_FROM = Decimal("0.80")`, `NUMBERS_SHARED_FROM = Decimal("0.70")`, strong = confirmed or confidence `>= NEEDS_ATTENTION_BELOW` (`Decimal("0.85")`, import it, do not copy), `LIST_LIMIT = 100`.
- Error codes exact: 404 `duplicate_not_found` ("We kunnen deze dubbele brieven niet vinden."), 409 `duplicate_not_open` ("Over deze twee brieven is al gekozen."), 409 `duplicate_visibility_differs` ("Deze twee brieven zijn niet voor dezelfde mensen zichtbaar. Kies 'Het zijn twee brieven', of laat ze eerst voor dezelfde mensen zichtbaar zijn."), 422 `validation_error`, 409 `document_processing`, 409 `document_retained` (from the trash's hook).
- Visibility: every member-facing read or change of a pair starts from `duplicates.visible_pairs(membership)`. A pair the member cannot see in full is not listed, not counted, gives no chip/count on the letter they do see, and answers 404 `duplicate_not_found` with the same body as a random id.
- Detection and `has_open_pair` run as the system only because their callers already are (`steps.py` in `workspace_context(..., actor=SYSTEM)`, `review.py` inside its `system_actor()`). `apps/documents/duplicates.py` never calls `system_actor` or uses `actor=SYSTEM`; `ALLOWED_SYSTEM_CALLERS` does not change.
- Only `organise` writes history and links; only `audit.services.record()` writes audit rows; the merge trashes only through `trash.move_to_trash`. No new audit code.
- `apps.pipeline.autopilot.decide_confirmation` and `mentions_term` do not change.
- No letter text, sender, amount or reference in a log line or audit row: ids, counts and exception type names only.
- `mypy --strict`, `ruff check`, `ruff format --check`, `pnpm lint`, `pnpm typecheck` clean. No `Any`, no bare `except`.
- Web text Dutch B1 via Lingui `t`; `pnpm extract`. "Te doen" and "Alle post" keep their names.
- Never touch Docker; never print `deploy/.env`; no push.
- Conventional commits ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Run the test files you touch plus those named in the task (`cd backend && uv run pytest tests/<file> -q`); the controller runs the full suite.

## Review Focus

1. A member who sees only one letter of a pair (private letter, another department, a viewer): expected no list row, no count, no chip, `getDuplicate`/merge/dismiss 404 identical to a random id. Pinned in Task 1 (`test_visible_pairs_follow_the_policies`) and Task 5 (`test_unseen_pair_leaves_no_trace`).
2. Two letters with different viewers that a member sees both of (an admin with a private letter of their own and a normal scan): expected `can_merge` false and 409 on merge, nothing written. Pinned in Task 4 (`test_merge_refuses_different_viewers`) and Task 5 (`test_merge_visibility_differs_409`).
3. The autopilot confirming a letter while its pair is open, or after the duplicate check crashed: expected status `needs_review`, `confirmed_by_ai` false, no tasks. Pinned in Task 3 (`test_open_pair_blocks_auto_confirm`, `test_failed_detection_blocks_auto_confirm`).
4. Restoring the discarded letter after a merge: expected it is back as its own letter, the pair stays `merged` and is not shown again, no second open payment task across the two. Pinned in Task 4 (`test_restore_after_merge_is_safe`).
5. A reminder or second instalment of the same invoice (near-identical text, other date or amount, both strong): expected no proposal; with one side weak a proposal whose difference is `uncertain`. Pinned in Task 2 (`test_guard_needs_both_strong`, `test_reminder_is_not_a_duplicate`).

---

### Task 1: Pair model, RLS and the pair seam

**Files:**
- Modify: `backend/apps/documents/models.py` (`DuplicateCandidate`; `DocumentEvent.Kind.MERGED = "merged"`, `DUPLICATE_DISMISSED = "duplicate_dismissed"`, `TASK_MOVED = "task_moved"`; `DocumentEvent.Origin.MERGE = "merge"`; `DocumentEvent.related_document`, added to `same_workspace_fields`)
- Modify: `backend/apps/workspaces/rls.py` (`restrict_to_visible_document_pair`)
- Create: `backend/apps/documents/migrations/0016_duplicates.py` (after `0015_trash`; `enable_rls`/forced as the other workspace tables, plus the pair policy)
- Create: `backend/apps/documents/duplicates.py` (only the read side in this task)
- Modify: `backend/tests/documents_support.py` (helper `pair(first, second, *, state="open") -> DuplicateCandidate`, written as the system through `as_actor`, ids ordered)
- Test: `backend/tests/test_duplicates_model.py`

**Interfaces:**
- Produces: `DuplicateCandidate` with `State` (`OPEN = "open"`, `MERGED = "merged"`, `DISMISSED = "dismissed"`, `WITHDRAWN = "withdrawn"`), fields per spec, constraints `documents_duplicate_ordered` (`first_id < second_id`), `documents_duplicate_pair` (unique), partial index `documents_duplicate_open`. `rls.restrict_to_visible_document_pair(table: str) -> migrations.RunSQL`. In `duplicates.py`: `visible_pairs(membership: Membership) -> QuerySet[DuplicateCandidate]` (state open, `first_id__in` and `second_id__in` `visible_documents(membership).values("id")`); `ordered(a: UUID, b: UUID) -> tuple[UUID, UUID]`; `duplicate_not_found() -> ApiError`.

- [ ] **Step 1: Write the failing tests**

```python
def test_pair_is_stored_ordered(): ...              # pair(b, a) with a.id < b.id -> row.first_id == a.id; update(first=b, second=a) raises IntegrityError
def test_one_row_per_pair(): ...                    # second insert of the same pair raises IntegrityError
def test_purge_of_a_letter_removes_the_pair(): ...  # delete one letter as the system -> DuplicateCandidate count 0
def test_rls_needs_both_letters(): ...              # office(); pair(private_femke, own_piet); as plain: DuplicateCandidate.objects.count() == 0; as each Office user with both names in office.visible(user): 1; as the system: 1
def test_rls_refuses_update_by_one_sided_actor(): ...  # as plain: filter(id=...).update(state="dismissed") == 0
def test_visible_pairs_follow_the_policies(): ...   # office(); pairs for (private_femke, finance_letter), (own_piet, unassigned), (hr_letter, finance_letter); for every Office user: {ids of visible_pairs} == {pairs whose both names are in office.visible(user)}
def test_visible_pairs_hide_trashed_letter(): ...   # trashed(one letter) -> visible_pairs empty; restore (trashed_at=None as system) -> back
def test_visible_pairs_only_open(): ...             # dismissed and merged rows not in visible_pairs
def test_visible_pairs_refuses_the_system(): ...    # as SYSTEM -> RuntimeError
def test_event_related_document_same_workspace(): ...  # DocumentEvent with related_document of ws_b raises (same_workspace_fields)
```

- [ ] **Step 2: Run, expect FAIL** — `cd backend && uv run pytest tests/test_duplicates_model.py -q` (no `DuplicateCandidate`).

- [ ] **Step 3: Implement** the model and choices; `restrict_to_visible_document_pair` = `_restrictive(table, 'EXISTS (... d WHERE d.id = "<t>".first_id) AND EXISTS (... d WHERE d.id = "<t>".second_id)')` with `_checked(table)`; `makemigrations documents --name duplicates`, then add the RLS operations by hand as earlier migrations do; `visible_pairs` and `ordered`.

- [ ] **Step 4: Run, expect PASS**; plus `tests/test_rls_coverage.py tests/test_actor_callers.py tests/test_documents_trash_reads.py tests/test_documents_events_api.py -q`; `make lint typecheck`.

- [ ] **Step 5: Commit** `feat(documents): duplicate pairs, visible only to who sees both letters`

---

### Task 2: Facts and the decision (pure)

**Files:**
- Create: `backend/apps/documents/duplicates_facts.py` (no Django model import beyond the extraction field names and `NEEDS_ATTENTION_BELOW`; no database)
- Test: `backend/tests/test_duplicates_facts.py`

**Interfaces:**
- Produces: `@dataclass(frozen=True) class Fact: value: str; strong: bool`; `@dataclass(frozen=True) class LetterFacts: text: str; numbers: frozenset[str]; payment_reference: Fact | None; sender: Fact | None; letter_date: Fact | None; document_type: Fact | None; amount: Fact | None; invoice_number: Fact | None; page_count: int`; `class Verdict(StrEnum): NONE = "none"; LIKELY = "likely"`; `@dataclass(frozen=True) class Difference: field: str; first: str; second: str; uncertain: bool`; `numbers_in(text: str) -> frozenset[str]`; `payment_reference_in(text: str) -> str | None`; `number_overlap(a: frozenset[str], b: frozenset[str]) -> Decimal` (Jaccard, 3 decimals, `Decimal("0")` when both empty); `decide(new: LetterFacts, other: LetterFacts, *, text_similarity: Decimal, number_overlap: Decimal) -> Verdict`; `differences(a: LetterFacts, b: LetterFacts) -> list[Difference]`; `same(a: LetterFacts, b: LetterFacts) -> list[str]`; `strong(confirmed: bool, confidence: Decimal | None) -> bool`. Dates normalised to ISO, amounts to `Decimal` text (`"1234.56"`), digit runs without separators.

- [ ] **Step 1: Write the failing tests**

```python
def test_numbers_normalise_dates_amounts_and_runs():   # numbers_in("Datum 12-03-2026, 12 maart 2026, € 1.234,56, klant 00123456") == {"2026-03-12", "1234.56", "00123456"}
def test_payment_reference_grouped_or_not():           # "1234 5678 9012 3456" and "1234567890123456" -> "1234567890123456"; 15 digits -> None
def test_number_overlap_is_jaccard():                  # {"a","b","c"} vs {"b","c","d"} -> Decimal("0.500"); empty vs empty -> Decimal("0")
def test_likely_when_text_and_numbers_match():         # sim 0.80, overlap 0.70 -> LIKELY; sim 0.79 -> NONE; overlap 0.69 (both >= 3 numbers) -> NONE
def test_few_numbers_need_text_only():                 # both with 2 numbers, overlap 0, sim 0.85 -> LIKELY
@pytest.mark.parametrize("field", ["letter_date", "amount", "invoice_number", "payment_reference", "document_type"])
def test_guard_needs_both_strong(field):               # both strong and different -> NONE; one weak -> LIKELY and differences() has field with uncertain=True
def test_reminder_is_not_a_duplicate():                # same invoice_number and amount, letter_date "2026-03-01" vs "2026-04-01" both strong, sim 0.93 -> NONE
def test_page_count_never_guards():                    # 3 vs 2 pages -> LIKELY; differences has "page_count" uncertain False
def test_strong_is_confirmed_or_085():                 # strong(True, None) True; strong(False, Decimal("0.85")) True; strong(False, Decimal("0.849")) False
def test_same_lists_equal_facts():                     # equal sender and letter_date -> same() == ["sender", "letter_date"] (table order)
def test_differences_put_uncertain_first():
def test_regexes_are_linear(): ...                     # numbers_in("1" * 100_000 + "x") and a 100_000-char text without digits each under 0.5 s
```

- [ ] **Step 2: Run, expect FAIL** — `uv run pytest tests/test_duplicates_facts.py -q`.

- [ ] **Step 3: Implement** with bounded quantifiers and no nested quantifiers (as `mentions_term`); Dutch month names `januari`..`december`; ignore an invalid date (`31-02-2026`) instead of raising.

- [ ] **Step 4: Run, expect PASS**; `make lint typecheck`.

- [ ] **Step 5: Commit** `feat(documents): pure facts and decision for duplicate letters`

---

### Task 3: Detection in the pipeline, the autopilot rail, re-check and the measurement

**Files:**
- Modify: `backend/apps/documents/duplicates.py` (`facts_of`, `detect`, `has_open_pair`, `recheck`)
- Modify: `backend/apps/pipeline/steps.py` (`_detect_guarded` in `run_pipeline` after `store_proposals` and before `addressee_scope`; in `mark_failed` before its organise step; `_confirm_guarded` gets `blocked_by_duplicate: bool`)
- Modify: `backend/apps/documents/review.py` (`confirm_document` calls `duplicates.recheck(document)` inside its existing `system_actor()` block, after the status is written)
- Create: `backend/tests/perf/test_duplicates_measure.py` (skipped unless `BRIEVIE_PERF=1`; corpus and report per spec "How the thresholds are measured"; writes the table to the artifact directory as `tests/perf/report.py` does)
- Test: `backend/tests/test_duplicates_detect.py`, `backend/tests/test_duplicates_autopilot.py`

**Interfaces:**
- Consumes: Task 1 model and `ordered`; Task 2 `LetterFacts`, `decide`, `numbers_in`, `number_overlap`.
- Produces: `facts_of(document: Document) -> LetterFacts`; `detect(document: Document) -> int` (rows created); `has_open_pair(document_id: UUID) -> bool` (open rows with this letter whose other letter is `LIVE`; under whatever actor the caller holds); `recheck(document: Document) -> int` (rows withdrawn). Constants `WINDOW`, `TERMS`, `MAX_CANDIDATES`, `MAX_OPEN_PER_LETTER` in `duplicates.py`.

- [ ] **Step 1: Write the failing tests** (letters through the upload helper and `run_pipeline` with a fake provider, as `test_pipeline_steps.py` does; the "scan again" text = the original with 2% character confusions)

```python
# test_duplicates_detect.py
def test_second_scan_gets_a_pair():               # done letter A, upload rescanned B -> one open row (A, B) ordered; text_similarity >= Decimal("0.800")
def test_different_letter_gets_none():            # another sender's letter -> 0 rows
def test_reminder_gets_none():                    # same template, other letter_date and amount (strong extractions) -> 0 rows
def test_window_is_180_days():                    # A created 181 days ago -> 0 rows; 179 days -> 1
def test_pool_leaves_out_trash_and_processing():  # A trashed -> 0; A processing -> 0
def test_private_letter_is_compared():            # A private (office private_femke text), B a normal rescan -> 1 row (shown only to who sees both: Task 5)
def test_at_most_three_open_pairs():              # 5 earlier copies -> 3 rows, the 3 highest text_similarity
def test_existing_pair_never_again():             # row (A, B) dismissed; detect(B) again -> still 1 row, state "dismissed"
def test_other_workspace_never_compared():        # same text in ws_b -> 0 rows
def test_mark_failed_detects_too():               # provider raises -> status "failed" and 1 row
def test_detect_logs_no_content(caplog):          # force an exception inside detect -> log has the type name and id, not the sender or a word of the text
def test_recheck_withdraws_on_strong_guard():     # pair open with weak dates; person confirms B with another letter_date -> state "withdrawn"; confirming member cannot see A (office) and it still works
def test_recheck_keeps_pair_without_guard():
# test_duplicates_autopilot.py (auto_confirm on, a payload that would confirm)
def test_open_pair_blocks_auto_confirm():         # rescan B with an open pair -> status "needs_review", confirmed_by_ai False, Task.objects.filter(document=B).count() == 0
def test_open_pair_still_allows_auto_organise():  # auto_organise on -> the ai label pick >= 0.80 is applied to B
def test_failed_detection_blocks_auto_confirm(monkeypatch):  # duplicates.detect raises -> status "needs_review", no rows, other proposals stored
def test_no_pair_confirms_as_before():            # unrelated letter -> confirmed_by_ai True
def test_pair_with_trashed_other_does_not_block(): # row with A, A trashed before B's run ends -> has_open_pair(B) False
```

- [ ] **Step 2: Run, expect FAIL** — `uv run pytest tests/test_duplicates_detect.py tests/test_duplicates_autopilot.py -q`.

- [ ] **Step 3: Implement** — `facts_of`: confirmed field values when `document.confirmed_at` is set, else the strongest extraction per field (`strong()` from Task 2). `detect`: pool and candidates as the spec (one raw SQL: the letter's lexemes from `unnest(to_tsvector('dutch', ocr_text))`, digits and `bsn` excluded, the `TERMS` longest joined into an OR `tsquery` against `search_vector`, ranked with `ts_rank_cd`, `LIMIT MAX_CANDIDATES`, `NOT EXISTS` a row for the pair; one query `similarity(%s, ocr_text)` for the candidate ids), `bulk_create(ignore_conflicts=True)`. `_detect_guarded(document) -> bool` mirrors `_organise_guarded` (savepoint, `except Exception` with `# noqa: BLE001`, type name only); `run_pipeline` passes `blocked_by_duplicate = not detected_ok or duplicates.has_open_pair(document.id)` to `_confirm_guarded`, which returns False when it is true, before `decide_confirmation`.

- [ ] **Step 4: Run, expect PASS**; plus `tests/test_pipeline_steps.py tests/test_autopilot_confirm.py tests/test_autopilot_organise.py tests/test_documents_review_api.py tests/test_actor_callers.py -q`; `make lint typecheck`. The controller runs `BRIEVIE_PERF=1 uv run pytest tests/perf/test_duplicates_measure.py` once and puts the numbers in DECISIONS (Task 7); if the aim is missed, stop and report before Task 4.

- [ ] **Step 5: Commit** `feat(pipeline): detect duplicate letters on arrival; an open pair blocks the AI's confirm`

---

### Task 4: Merge and dismiss (services, trash, history, twin tasks)

**Files:**
- Modify: `backend/apps/documents/duplicates.py` (`merge`, `dismiss`, `viewers_differ`, `task_twins`)
- Modify: `backend/apps/documents/trash.py` (`move_to_trash`: origin from `reason`, `related_document`, `user=None` raises `ValueError`)
- Modify: `backend/apps/documents/organise.py` (`record_trashed(document, *, actor: User | None, origin: str = USER, related_document: Document | None = None)`, `record_merged`, `record_duplicate_dismissed`, `record_task_moved`, `record_task_event` gains `origin: str = USER`; `_write_event` gains `related_document`)
- Modify: `backend/apps/audit/codes.py` (comment only: `reason: user, duplicate`)
- Test: `backend/tests/test_duplicates_merge.py`

**Interfaces:**
- Consumes: Task 1 (`visible_pairs`, `duplicate_not_found`), `visibility.viewers`, `visibility.addressee_scope`, `organise.add_label`/`add_addressee`, `review._key_of` (import it; rename to `task_key` if mypy/ruff object to the private import, keeping one definition), `trash.move_to_trash`, `lock_own_membership`.
- Produces: `merge(*, membership: Membership, user: User, actor: AuditActor, candidate_id: UUID, keep_id: UUID, complete_twin_tasks: bool = True) -> Document`; `dismiss(*, membership: Membership, user: User, candidate_id: UUID) -> None`; `viewers_differ(a: Document, b: Document) -> bool` (`restricted` differs, or `visibility.viewers` sets differ); `@dataclass(frozen=True) TaskTwin(done_document_id: UUID, open_document_id: UUID, type: str, description: str)`; `task_twins(first: Document, second: Document) -> list[TaskTwin]` (both directions).

- [ ] **Step 1: Write the failing tests**

```python
def test_merge_unites_labels_and_addressees():    # K has label L1 and person P1; O has L1, L2, P2 -> K has L1, L2, P1, P2; events label_added L2 and addressee_added P2 on K with origin "merge", actor user; links automatic False; can_undo False
def test_merge_puts_other_in_trash():             # O.trashed_at set, trashed_by == user; one audit row document_trashed values == {"reason": "duplicate"}; candidate state "merged", kept == K, decided_by == user
def test_merge_history_on_both():                 # K: event "merged" origin "user" actor user related_document O; O: event "trashed" origin "merge" actor user related_document K
def test_merge_moves_open_tasks_once():           # O open pay task (not on K) -> task.document == K, "task_moved" origin "merge" on both; an equal open task on both -> stays on O, K has exactly one
def test_merge_completes_twin_when_asked():       # O pay task done, K's equal open; complete_twin_tasks=True -> K's task done, event task_completed origin "merge"
def test_merge_leaves_twin_open_when_not_asked(): # complete_twin_tasks=False -> K's task still open
def test_merge_refuses_different_viewers():       # office(); pair(private_femke, finance_letter) as finance -> ApiError 409 "duplicate_visibility_differs"; nothing changed (O.trashed_at None, state "open", no events)
def test_merge_refuses_when_union_widens_viewers(monkeypatch):  # viewers(K) after addressee scope differs -> 409, K's labels and addressees as before
def test_merge_refuses_processing():              # one letter processing -> 409 "document_processing"
def test_merge_refuses_keep_outside_pair():       # keep_id of a third letter -> 422 "validation_error"
def test_merge_not_open_409():                    # dismissed row -> 409 "duplicate_not_open"
def test_merge_unseen_pair_404():                 # plain on (private_femke, own_piet) -> 404 "duplicate_not_found"
def test_merge_refused_by_retention_hook(monkeypatch):  # check_removal refuses -> 409 "document_retained", no label added to K, state "open"
def test_restore_after_merge_is_safe():           # merge, then trash.restore_document(O) -> O live; candidate still "merged"; visible_pairs empty; K keeps L2 and the moved task; Task open pay count over K and O == 1
def test_dismiss_records_on_both():               # state "dismissed", decided_by; "duplicate_dismissed" events on K and O with related_document the other; no audit row
def test_move_to_trash_without_user_refuses():    # move_to_trash(doc, user=None, ...) raises ValueError
def test_plain_trash_history_unchanged():         # trash_document -> event "trashed" origin "user", related_document None
```

- [ ] **Step 2: Run, expect FAIL** — `uv run pytest tests/test_duplicates_merge.py -q`.

- [ ] **Step 3: Implement** per spec "Merge": locks (own membership, both documents via `visible_documents(membership).select_for_update(no_key=True).filter(id__in=[...]).order_by("id")`, then the candidate via `visible_pairs(membership).select_for_update()`), refusals in the spec's order, steps 1 to 4. The viewers check before step 1 and again after the `addressee_scope` block ends (the scope's sync has run then). `dismiss`: same locks.

- [ ] **Step 4: Run, expect PASS**; plus `tests/test_documents_trash.py tests/test_documents_trash_api.py tests/test_documents_events_api.py tests/test_tasks_api.py tests/test_audit_record.py -q`; `make lint typecheck`.

- [ ] **Step 5: Commit** `feat(documents): merge duplicate letters into one, the other to the trash`

---

### Task 5: API, counts and the summary field

**Files:**
- Modify: `backend/apps/documents/schemas.py` (`DuplicateOut`, `DuplicateDifferenceOut`, `TaskTwinOut`, `MergeDuplicateIn`; `DocumentSummaryOut.open_duplicate_count: int = 0`; `InboxCountsOut.duplicate: int = 0`; `DocumentEventOut.related_document_id: UUID | None = None`)
- Modify: `backend/apps/documents/duplicates.py` (`list_duplicates`, `get_duplicate`, `duplicate_out`, `count_pairs`, `pair_counts`)
- Modify: `backend/apps/documents/services.py` (`inbox_counts` adds `duplicate`; `list_documents` and `get_document` fill `open_duplicate_count` from one grouped query of `visible_pairs`)
- Create: `backend/apps/documents/duplicates_api.py` (router mounted where `api.py` mounts the documents routers)
- Modify: `docs/api/openapi.json` (`make openapi`)
- Test: `backend/tests/test_duplicates_api.py`; `backend/tests/test_openapi_contract.py` (unchanged, must pass)

**Interfaces:**
- Consumes: Tasks 1 to 4.
- Produces: operations `listDuplicates`, `getDuplicate`, `mergeDuplicate`, `dismissDuplicate` (paths and roles per spec table); `pair_counts(membership: Membership, document_ids: Collection[UUID]) -> dict[UUID, int]`; `count_pairs(membership: Membership) -> int`.

- [ ] **Step 1: Write the failing tests**

```python
def test_list_shows_pair_to_who_sees_both():      # member sees A and B -> 200, one item, first.id < second.id, text_similarity int 0..100, can_merge True, blocked_reason ""
def test_unseen_pair_leaves_no_trace():           # office(); pair(private_femke, own_piet); as plain (sees own_piet, not private_femke): listDuplicates == []; getInboxCounts["duplicate"] == 0; listDocuments item own_piet open_duplicate_count == 0; getDocument(own_piet) open_duplicate_count == 0; getDuplicate, mergeDuplicate, dismissDuplicate -> 404 "duplicate_not_found" with the same JSON body as a random uuid
def test_seen_pair_counts():                      # who sees both: inbox duplicate == 1, both letters open_duplicate_count == 1
def test_viewer_sees_nothing_and_cannot_decide(): # viewer: list == [], getDuplicate 404, merge/dismiss 403 "forbidden"
def test_merge_visibility_differs_409():          # finance on (private_femke, finance_letter): getDuplicate can_merge False, blocked_reason "visibility_differs"; mergeDuplicate -> 409 "duplicate_visibility_differs", message as in Global Constraints
def test_merge_returns_kept_letter():             # 200 DocumentOut id == keep id; listTrash holds the other; listDuplicates == []
def test_merge_with_device_token():               # member's bearer -> 200
def test_merge_body_defaults_complete_twins():    # body {"keep_document_id": ...} only -> twin task completed
def test_task_twins_listed():                     # done on O, open on K -> task_twins == [{done_document_id: O, open_document_id: K, type: "pay", description: ...}]
def test_dismiss_204_and_gone():                  # 204; list empty; second dismiss -> 409 "duplicate_not_open"
def test_differences_and_same():                  # letter_date strong on both, equal; amount weak on one, different -> same contains "letter_date"; differences[0].field == "amount", uncertain True
def test_list_capped_and_newest_first():          # monkeypatch LIST_LIMIT 2 with 3 pairs -> 2 newest
def test_event_out_has_related_document():        # listDocumentEvents of K after merge: "merged" item related_document_id == O
```

- [ ] **Step 2: Run, expect FAIL** — `uv run pytest tests/test_duplicates_api.py -q`.

- [ ] **Step 3: Implement** thin endpoints (`workspace_member(...)`, one service call); `listDuplicates`/`getDuplicate` any role, returning `[]`/404 for a viewer; merge and dismiss `min_role=Membership.Role.MEMBER`, `actor_from_request` for the audit actor; response maps like the trash endpoints (`SESSION_WRITE_ERRORS`, `AUTH_ERRORS`). `duplicate_out` reads both summaries through `document_summary_out`, `suggested_keep_id` (confirmed, else further in review, else older), `can_merge = not viewers_differ(...)`. Then `make openapi`.

- [ ] **Step 4: Run, expect PASS**; plus `tests/test_openapi_contract.py tests/test_api_docs.py tests/test_documents_api.py tests/test_documents_visibility.py -q`; `make lint typecheck`.

- [ ] **Step 5: Commit** `feat(api): list, show, merge and dismiss duplicate letters`

---

### Task 6: Web

**Files:**
- Modify: `web/src/api/schema.d.ts` (`pnpm gen:api`), `web/src/api/queries.ts` (`duplicatesQuery(workspaceId)`, `duplicateQuery(workspaceId, duplicateId)`), `web/src/router.tsx`, `web/src/routes/inbox.tsx` (filter "Dubbel?" with count, chip), `web/src/components/document-history.tsx` (new lines)
- Create: `web/src/routes/duplicate.tsx` (path `dubbel/$duplicateId`)
- Test: `web/src/routes/duplicate.test.tsx`, `web/src/routes/inbox.test.tsx`, `web/src/components/document-history.test.tsx` (create if absent)

**Interfaces:**
- Consumes: the four operations and the three additive fields of Task 5; the trash's `showUndo(workspaceId, documentId)`.

- [ ] **Step 1: Write the failing tests** (MSW, queries by role)

```tsx
test("Te doen shows Dubbel? with its count and hides it at 0")
test("a letter with an open pair carries the chip Dubbel?")
test("pair page shows both columns and marks Verschilt and Verschilt misschien (slecht leesbaar)")
test("warning names the differing fields") // "Let op: bedrag verschilt."
test("Samenvoegen sends keep id and complete_twin_tasks true, opens kept letter with undo bar") // dialog "Samenvoegen?" with "30 dagen"; status "De brief staat in de prullenbak."
test("Taak ook afronden is shown only for a twin done on the other letter, on by default, and can be turned off") // unchecked -> body complete_twin_tasks false
test("Het zijn twee brieven dismisses and returns to Te doen") // status "Goed. We vragen het niet meer voor deze twee brieven."
test("can_merge false disables Samenvoegen and shows the reason")
test("404 shows Deze dubbele brieven zijn er niet (meer).")
test("history shows the merge lines") // "Samengevoegd met een dubbele brief door Anna", "Als dubbele brief naar de prullenbak verplaatst door Anna"
```

- [ ] **Step 2: Run, expect FAIL** — `cd web && pnpm test src/routes/duplicate.test.tsx src/routes/inbox.test.tsx src/components/document-history.test.tsx`.

- [ ] **Step 3: Implement** — `pnpm gen:api`; texts exactly as spec "Web"; `ConfirmDialog` tone `primary`; checkbox through React Aria `Checkbox`; after merge or dismiss invalidate documents, inbox counts, duplicates, search, tasks and trash; `pnpm extract`.

- [ ] **Step 4: Run, expect PASS** — `cd web && pnpm test && pnpm lint && pnpm typecheck && pnpm build`.

- [ ] **Step 5: Commit** `feat(web): Dubbel? in Te doen, side-by-side page, merge and dismiss`

---

### Task 7: Documents of record

**Files:**
- Modify: `CLAUDE.md`, `docs/DECISIONS.md`, `docs/GLOSSARY.md`, `docs/PRIVACY.md`, `docs/SPEC.md` (section 12: the pair and its endpoints)

- [ ] **Step 1: Write** — CLAUDE.md: in the autopilot line "The pipeline applies only on a `processing` document ..." append "; no confirm while the letter has an open duplicate pair (`duplicates.has_open_pair`) or the duplicate check failed". Add a rule: "Every member read or change of a duplicate pair starts from `apps.documents.duplicates.visible_pairs` (both letters through `visible_documents`); a merge never changes who sees a letter (409 `duplicate_visibility_differs`), trashes only through `trash.move_to_trash(reason="duplicate")`, and completes a twin task only when `complete_twin_tasks` is true. Detection runs only on arrival, as the system through the pipeline." DECISIONS: one entry with spec rulings 1 to 12, the five owner decisions of 21-11, the M8 ruling, and the measured thresholds and timings of Task 3 (machine, 10,000 and 100,000 letters, recall and false proposal rates). GLOSSARY: dubbel = duplicate, samenvoegen = merge, "Het zijn twee brieven" = dismiss. PRIVACY: detection compares letters across a workspace as the system but shows a pair only to who sees both; no content in logs or audit.

- [ ] **Step 2: Verify** — `make lint typecheck test`; `cd web && pnpm test`. Expected: all green.

- [ ] **Step 3: Commit** `docs: record duplicate letters (rules, decisions, glossary, privacy)`
