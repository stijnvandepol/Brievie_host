# Duplicate letters

Date: 2026-11-20, aligned 2026-11-21. Branch `feat/duplicates` (host). iOS follows
separately (fields at the end). Plan: `docs/superpowers/plans/2026-11-21-duplicates.md`.

Builds on what is in the branch now (HEAD `58917dd`: main `efdd05e` plus the finished
trash): the visibility policies and the seam `visibility.visible_documents` (with
`member_filter`), `visibility.viewers`, actors in the workspace context (`system_actor`,
`ALLOWED_SYSTEM_CALLERS`), and the trash (`apps/documents/trash.py`: `move_to_trash`,
`trashed_documents`, `retention.check_removal`; `purge.py` runs as the system).

## Intent

Stijn (19-11): "ik wil iets voor dubbele invoer, denk aan twee personen die dezelfde brief
scannen, wat kan gebeuren; laat iemand dan checken of het echt zo is om de dubbele te
verwijderen." Brievie notices on arrival that a letter is probably already there, shows the
two side by side with what is the same and what differs, and a person decides: the same
letter (merge; the other one goes to the trash) or two letters (never asked again).

## Goals, non-goals, anti-goals

- Goal: detect a likely duplicate when a letter arrives, on content (two scans of one
  letter have different page hashes), with hard features as a guard.
- Goal: one proposal per pair, visible under "Te doen" as "Dubbel?", decided by a member who
  may see both letters; the choice is recorded and a "no" is remembered for that pair.
- Goal: a merge that loses nothing: labels and addressees united through
  `apps.documents.organise`, tasks and history traceable, the other letter in the trash
  through the trash service (30 days to take it back).
- Non-goal: removing or merging anything automatically; comparing across workspaces;
  finding duplicates among letters that are already there (an archive sweep is a
  follow-up); undoing a merge in one click (restore from the trash is the way back); iOS
  screens; the "postkamer" case (a private and a normal scan of one letter; the postkamer
  design decides it).
- Anti-goal: a proposal, count, chip or error that tells somebody a letter exists they may
  not see; a reminder or a second instalment proposed as "the same letter" when its date
  or amount clearly differs; double payment tasks after a merge; a merge that changes who
  sees a letter; letter text, sender or amounts in an audit row or a log line.

## Approaches

1. **A pair model of its own, detection inside the pipeline's last step, candidates from the
   search index, scored with `pg_trgm` similarity and a pure decision function (chosen).**
   The search index (`search_vector`, GIN) finds the few letters that share the new
   letter's distinctive words; only those get a full-text trigram similarity and a
   comparison of the numbers in the text and the extracted fields. Fast at any size, no new
   dependency (`pg_trgm` is installed since `documents/0010_search`), explainable.
2. A new kind of `DocumentSuggestion` (`action="same_letter"`, target another document).
   Rejected: the suggestion table is per letter with a check constraint "label or person",
   the autopilot, the inbox `open_suggestion_count` and the dismissed-memory rules all read
   it as label/addressee proposals; a pair belongs to two letters and needs its own
   visibility rule (both readable). Bending it would touch the autopilot's guarantees.
3. A text fingerprint per letter (MinHash or SimHash) stored at arrival. Rejected for now:
   a new algorithm to maintain (or a dependency) for a volume of tens of letters a day; the
   index plus trigram on at most 20 candidates is cheap enough. Kept as the fallback if the
   measurement says otherwise.
4. Page image hashes or perceptual hashes. Rejected: two scans differ in light, crop and
   angle; perceptual hashing needs image work on the server the pipeline does not do.

## Design

### Model (`apps/documents/models.py`, migration `documents/0016_duplicates`)

`DuplicateCandidate(WorkspaceModel)`, RLS enabled and forced:

- `first`, `second`: `ForeignKey(Document, on_delete=CASCADE, related_name="+")`, stored
  ordered (`first_id < second_id`, check constraint `documents_duplicate_ordered`), unique
  `(first, second)` (`documents_duplicate_pair`): one row per pair, ever. The purge of
  either letter removes the row through the cascade (nothing is left to propose).
- `state`: `open` | `merged` | `dismissed` | `withdrawn` (a re-check found a guard).
- `text_similarity: Decimal(4,3)`, `number_overlap: Decimal(4,3)`: the scores at detection.
- `kept`: `ForeignKey(Document, null=True, on_delete=SET_NULL, related_name="+")`.
- `decided_by`: `ForeignKey(User, null=True, on_delete=PROTECT, related_name="+")`,
  `decided_at: DateTimeField(null=True)`.
- Partial index `documents_duplicate_open` on `(workspace, first, second)` where
  `state = 'open'`.
- RLS: the workspace policy plus a restrictive policy from a new helper
  `apps.workspaces.rls.restrict_to_visible_document_pair(table)`: `EXISTS (document first)
  AND EXISTS (document second)` as subqueries on `documents_document`, so the document
  policies of the current actor decide. A row is readable and writable only for an actor
  who reads both letters (the system reads all). RLS is the second layer; the seam below
  is the first.
- `DocumentEvent`: new kinds `merged` (on the kept letter), `duplicate_dismissed` (on both),
  `task_moved` (on both letters, for a task the merge moved); new origin `merge`; new field
  `related_document: ForeignKey(Document, null=True, on_delete=SET_NULL, related_name="+")`
  (the other letter of the pair; null once purged), added to `same_workspace_fields`.

### The seam for pairs (`apps/documents/duplicates.py`)

`visible_pairs(membership) -> QuerySet[DuplicateCandidate]`: open rows whose `first_id`
**and** `second_id` are both in `visibility.visible_documents(membership)` (two `__in`
subqueries on the seam). It inherits the seam's guarantees: it runs only as that member
(`RuntimeError` otherwise), leaves out letters the member may not see and letters in the
trash. Every member-facing read and change of a pair starts from it: `listDuplicates`,
`getDuplicate`, `InboxCountsOut.duplicate`, `DocumentSummaryOut.open_duplicate_count`,
merge and dismiss. A pair the member cannot see in full answers exactly as a missing one
(404 `duplicate_not_found`, same body as a random id), is not listed and not counted, and
the letter they do see carries no chip and no count for it. So a member is never told that
a letter they cannot see is a duplicate of theirs.

A pair with a letter in the trash is hidden (the seam's `LIVE`) and comes back when that
letter is restored. Nothing withdraws it on a trash or a merge (a write of the rows of
other pairs would need the system); the purge removes it.

### What is compared (`apps/documents/duplicates_facts.py`, pure)

`LetterFacts` (frozen dataclass), built from the stored letter in the pipeline (as the
system) or in `recheck` (as the system):

| Fact | Source | Strong when |
|---|---|---|
| `text` | `ocr_text` (BSN-masked) | always |
| `numbers` | pure regex over the text: dates (`12-03-2026`, `12 maart 2026`), amounts (`€ 1.234,56`), digit runs of 6 or more, normalised | always |
| `payment_reference` | pure regex: 16 digits, optionally in groups of four | found |
| `sender` | confirmed `sender`, else the strongest `SENDER` extraction | confirmed, or confidence >= `NEEDS_ATTENTION_BELOW` (0.85) |
| `letter_date` | confirmed, else the `LETTER_DATE` extraction | same |
| `document_type` | confirmed, else the extraction | same |
| `amount` | `INVOICE_AMOUNT_INCL` extraction (Decimal) | confidence >= 0.85 |
| `invoice_number` | `INVOICE_NUMBER` extraction | confidence >= 0.85 |
| `page_count` | `page_count` | always (shown, never a guard: one scan may miss a page) |

### Detection (`apps/documents/duplicates.py`)

- `detect(document: Document) -> int`, run by the pipeline **as the system** (main's
  pattern: `steps.run_pipeline` and `steps.mark_failed` already hold
  `workspace_context(workspace_id, actor=SYSTEM)`; `duplicates.py` itself never calls
  `system_actor` and does not join `ALLOWED_SYSTEM_CALLERS`). It runs in the last
  transaction, on the locked `processing` letter, after `store_proposals` and **before** the
  autopilot, in a savepoint (`steps._detect_guarded`):
  1. Pool: live letters of the workspace (`LIVE`), not the letter itself, not
     `processing`, `created_at` within `WINDOW = timedelta(days=180)`. Private letters
     included: detection must see them to find a pair; nothing is shown at this moment.
  2. Candidates from the search index: a `tsquery` of the new letter's `TERMS = 16` most
     distinctive lexemes (numbers and the `bsn` lexeme left out) joined with OR, ranked
     with `ts_rank_cd`, the best `MAX_CANDIDATES = 20`. Pairs that already have a row (any
     state) are left out: a "no" is never asked again.
  3. For those: `similarity(a.ocr_text, b.ocr_text)` (`pg_trgm`) in one query, and
     `number_overlap` (Jaccard of the two `numbers` sets) in Python.
  4. `decide(...)` (pure, below); at most `MAX_OPEN_PER_LETTER = 3` rows, best first,
     `bulk_create(..., ignore_conflicts=True)` on the pair. Returns the number of rows.
  A failure rolls back only the savepoint and logs the exception type and the id; the
  letter then is treated as if it had an open pair (no automatic confirm, fail closed).
- `mark_failed` runs the same detection (the text came with the upload; a failed AI step
  only means fewer facts).
- **Compared only on arrival** (owner, 21-11): within 180 days, at most 3 open pairs per
  arriving letter (an older letter can gain a pair from each later arrival). A restore
  from the trash or a confirm does not search again; a re-try of failed processing runs
  the pipeline, so it does (a pair is unique, so nothing doubles). An archive sweep is a
  later step.
- **Autopilot** (owner, 21-11): an open duplicate proposal blocks the AI's automatic
  confirm. The rail is the caller's, like the open addressee proposal: `_confirm_guarded`
  returns False when `duplicates.has_open_pair(document.id)` (as the system, open rows
  of this letter whose other letter is live). `decide_confirmation` and `mentions_term`
  are untouched and stay pure. Labels and addressees may still be applied. Reason: a
  confirmed duplicate creates a second payment task.
- `recheck(document: Document) -> int` after a person confirms a letter (its facts got
  strong): `review.confirm_document` calls it inside its existing `system_actor()` block
  (`review.py` is an allowed system caller), because the confirming member may not see the
  other letter. Each open pair of the letter is decided again with the stored scores; a
  guard now firing sets `withdrawn`. No new search.

### The decision (`duplicates_facts.decide`, pure, no database)

`decide(new: LetterFacts, other: LetterFacts, *, text_similarity: Decimal,
number_overlap: Decimal) -> Verdict`. Guards first (any one: `Verdict.NONE`), each only
when **both** values are strong (owner, 21-11: confirmed, or confidence >= 0.85):

- `letter_date` differs;
- `amount` differs;
- `invoice_number` differs, or `payment_reference` differs;
- `document_type` differs (an invoice and a reminder).

Then `Verdict.LIKELY` when `text_similarity >= TEXT_SIMILAR_FROM` and (`number_overlap >=
NUMBERS_SHARED_FROM` or both letters have fewer than 3 numbers). Starting values
`TEXT_SIMILAR_FROM = Decimal("0.80")`, `NUMBERS_SHARED_FROM = Decimal("0.70")`; the
measurement sets them, DECISIONS records the result.

`differences(a, b) -> list[Difference]` and `same(a, b) -> list[str]`: every fact present
on both sides, with `uncertain=True` when one side is weak. A weak difference does not
block (it may be an OCR misread) but is shown on top in the proposal.

### How the thresholds are measured

A measurement under `backend/tests/perf/` that runs only with `BRIEVIE_PERF=1`, like
the visibility measurement (`test_visibility_at_scale.py`), on a throw-away database:

- **Corpus:** 2,000 generated Dutch letters from 40 senders with real-world templates
  (tax assessment, health insurer monthly invoice, energy annual statement, municipality,
  bank, school), 1 to 4 pages.
- **Positives (300 pairs):** the same letter "scanned again": an OCR noise model (0.5 to 3%
  character confusions such as rn/m, l/1, O/0, comma/period; changed line breaks; a header
  or footer line lost), a last page missing in 10%, pages in another order in 5%.
- **Hard negatives (300 pairs):** a reminder of the same invoice (same number and amount,
  other date and wording), a second instalment (same text, other amount and date), next
  month's invoice of the same sender, the same template for another person, the same
  annual letter a year later.
- **Random pairs:** 10,000.
- **Real check (local only):** Stijn scans 20 of his own letters twice, with two phones;
  the same script runs on that set on his machine. Only the numbers leave it; no letter
  goes into the repository.
- **Read off:** recall on positives and the false proposal rate on hard negatives and on
  random pairs, per threshold pair (text 0.60 to 0.95, numbers 0.40 to 0.90), with and
  without the guards; time of `detect` per arrival at 10,000 and 100,000 letters (warm,
  median of 7). **Aim:** recall >= 0.95; false proposals <= 2% of hard negatives and 0 of
  random pairs; < 50 ms per arrival at 100,000 letters. If the aim is out of reach with
  approach 1, approach 3 is next.

### Visibility (frame 4)

- Detection runs as the system over all live letters of the workspace and writes only the
  pair row; nothing is shown at that moment.
- Who sees a proposal: a member who sees **both** letters (`visible_pairs`, RLS behind
  it). A pair only one person can see in full is shown only to that person; a pair nobody
  sees in full stays unseen and undecided (it blocks the AI's confirm of the new letter all
  the same).
- Who decides: role `member` or higher who sees both. A viewer gets an empty list, a zero
  count and 403 on merge and dismiss.
- **Different visibility refuses the merge** (owner, 21-11): when the sets of members who
  see each letter differ (`visibility.viewers({K, O})`, the policy's own condition, as
  the system), or one letter is `restricted` and the other not, the merge answers 409
  `duplicate_visibility_differs`. After uniting the addressees the merge checks again
  that `viewers(K)` is the set from before; a difference raises the same 409 and the
  merge's own transaction rolls back. A merge never changes who sees a letter. The
  proposal shows `can_merge: false` with the reason and offers "Het zijn twee brieven";
  the postkamer case (a private letter and a normal scan of the same letter) is left to
  the postkamer design.
- The choice is recorded on the row (`state`, `kept`, `decided_by`, `decided_at`) and in
  the history of both letters.

### Merge (`merge(*, membership, user, actor, candidate_id, keep_id, complete_twin_tasks) -> Document`)

Runs as the caller, `transaction.atomic`. Lock order as everywhere: the member's own
membership (`lock_own_membership`), then both documents `FOR NO KEY UPDATE` in id order
through `visible_documents(membership)` (a letter not found there: 404
`duplicate_not_found`), then the candidate row through `visible_pairs`. Refused when the
candidate is not `open` (409 `duplicate_not_open`), `keep_id` is not one of the pair (422
`validation_error`), either letter is `processing` (409 `document_processing`), or the
visibility differs (409 `duplicate_visibility_differs`). Then, for kept `K` and other `O`:

1. Inside `visibility.addressee_scope(K)`: every label and addressee of `O` that `K` lacks
   is added through `organise.add_label` / `organise.add_addressee` with origin `merge`,
   actor the user, `automatic=False` (a person chose it). Not undoable through
   `undoDocumentEvent` (`merge` is not in `UNDOABLE_ORIGINS`); removing by hand works as
   always. Then the viewers check above.
2. Tasks: an open task of `O` with no equal task on `K` (`duplicates.twin_key`: type and
   deadline; not the description, which separate AI runs word differently, nor the
   deadline's source; a task has no amount of its own) moves to `K` (`task.document = K`), with a `task_moved`
   line of origin `merge` on both letters. A **done** task of `O` whose equal is open on
   `K` completes that task on `K` only when `complete_twin_tasks` is true (owner, 21-11:
   not silent; the merge dialog has a checkbox "Taak ook afronden", on by default, shown
   only when such a twin exists): `task_completed`, origin `merge`, actor the user. Every
   other task of `O` stays with `O` (in the trash).
3. `trash.move_to_trash(O, user=user, actor=actor, reason="duplicate")`: audit
   `document_trashed {reason: "duplicate"}`, the retention hook as for any trash (a
   refusal raises 409 `document_retained` and rolls the merge back).
4. History `merged` on `K` (origin `user`, actor the user, `related_document=O`); candidate
   `merged`, `kept=K`, `decided_by`, `decided_at`.

Other open pairs that contain `O` are left as they are: hidden while `O` is in the trash
(the seam), back when it is restored, gone with the purge.

**Restore after a merge.** `O` can be restored from the trash for 30 days, as an
independent letter. The merge is not undone: `K` keeps the united labels and addressees
and the moved tasks; `O` comes back without the open tasks that moved (so no second
payment task), with its own labels, addressees and remaining tasks. The pair stays
`merged`, so it is never proposed again; no new detection runs on a restore.

### History of a trash nobody did by hand (trash review "M8")

Decided here. `move_to_trash` derives the history origin from `reason`: `user` ->
origin `user`, `duplicate` -> origin `merge`. `organise.record_trashed(document, *, actor,
origin, related_document=None)`. A merge has a user, so the discarded letter's history
shows: kind `trashed`, origin `merge`, actor the merging user, `related_document` the kept
letter ("Als dubbele brief naar de prullenbak verplaatst door {naam}"). The kept letter
shows `merged` by the same user. `move_to_trash` with `user=None` raises `ValueError`:
Brievie trashes nothing on its own today, and a `trashed` line with origin `user` and no
user would record a person for what nobody did. A future system trash brings its own
origin.

### Dismiss (`dismiss(*, membership, user, candidate_id) -> None`)

Same locks and seam; refused when not `open` (409 `duplicate_not_open`). State
`dismissed`, `decided_by`, `decided_at`; history `duplicate_dismissed` (origin `user`,
`related_document` the other letter) on both letters. The row stays, so the pair is never
proposed again. No audit row.

### Audit

No new code (owner, 21-11). A merge writes `document_trashed` with `reason: "duplicate"`
(ids only; the comment in `apps/audit/codes.py` lists the value). The rest of the choice
is on the row and in both histories.

### API (additive, `/api/v1`)

| Method and path | `operation_id` | Auth, role | Out |
|---|---|---|---|
| `GET /workspaces/{workspace_id}/duplicates` | `listDuplicates` | session or device, any member (a viewer gets `[]`) | `list[DuplicateOut]` (open, both visible, newest first, at most 100) |
| `GET /workspaces/{workspace_id}/duplicates/{duplicate_id}` | `getDuplicate` | same; a viewer 404 | `DuplicateOut`; 404 `duplicate_not_found` |
| `POST /workspaces/{workspace_id}/duplicates/{duplicate_id}/merge` | `mergeDuplicate` | session or device, `member` | `DocumentOut` (the kept letter) |
| `POST /workspaces/{workspace_id}/duplicates/{duplicate_id}/dismiss` | `dismissDuplicate` | session or device, `member` | 204 |

- `DuplicateOut`: `id: UUID`, `state: str`, `created_at: datetime`, `first:
  DocumentSummaryOut`, `second: DocumentSummaryOut`, `suggested_keep_id: UUID` (the
  confirmed one, else the one further in review, else the older), `text_similarity: int`
  (0 to 100), `same: list[str]` (default empty), `differences: list[DuplicateDifferenceOut]`
  (default empty), `task_twins: list[TaskTwinOut]` (default empty), `can_merge: bool =
  True`, `blocked_reason: str = ""` (`visibility_differs`).
- `DuplicateDifferenceOut`: `field: str` (`sender`, `letter_date`, `document_type`,
  `amount`, `invoice_number`, `payment_reference`, `page_count`), `first: str`, `second:
  str` (dates ISO, amounts as decimal text), `uncertain: bool = False`.
- `TaskTwinOut`: `done_document_id: UUID`, `open_document_id: UUID`, `type: str`,
  `description: str`: a done task on one letter with an open equal on the other.
- `MergeDuplicateIn`: `keep_document_id: UUID`, `complete_twin_tasks: bool = True`.
- Additive on existing schemas: `DocumentSummaryOut.open_duplicate_count: int = 0` (pairs
  of `visible_pairs` with this letter); `InboxCountsOut.duplicate: int = 0` (pairs of
  `visible_pairs`); `DocumentEventOut.related_document_id: UUID | None = None`.
- Errors: 404 `duplicate_not_found` ("We kunnen deze dubbele brieven niet vinden."); 409
  `duplicate_not_open` ("Over deze twee brieven is al gekozen."); 409
  `duplicate_visibility_differs` ("Deze twee brieven zijn niet voor dezelfde mensen
  zichtbaar. Kies 'Het zijn twee brieven', of laat ze eerst voor dezelfde mensen zichtbaar
  zijn."); 422 `validation_error` for a `keep_document_id` outside the pair; 409
  `document_processing`; 409 `document_retained` (the trash's hook).
- Dropped from the first draft: `listDocuments` with `inbox_state=duplicate`; the
  "Dubbel?" list reads `listDuplicates`.

### Web (Dutch B1, Lingui)

- "Te doen": a filter "Dubbel?" with its count (`InboxCountsOut.duplicate`, hidden at 0);
  a letter with `open_duplicate_count > 0` carries a chip "Dubbel?". The list "Dubbel?"
  shows pairs: both senders and dates, "Lijkt op elkaar voor {n}%".
- Page "Mogelijk dubbel" (`routes/duplicate.tsx`, path `dubbel/$duplicateId`): two columns
  (stacked on a phone), each with the first page, afzender, soort, datum, bedrag, kenmerk,
  pagina's, labels, op naam van, taken. A row that differs is marked "Verschilt"; an
  uncertain one "Verschilt misschien (slecht leesbaar)".
- When anything differs, on top: "Let op: {velden} verschilt. Een herinnering of een tweede
  termijn lijkt vaak op de eerste brief, maar het is een andere brief."
- Per column a choice "Deze brief houden" (the suggested one chosen).
- "Het is dezelfde brief": confirm (primary): title "Samenvoegen?", text "De brief die je
  houdt, krijgt de labels en personen van allebei. De andere brief gaat naar de
  prullenbak. Daar kun je hem 30 dagen terugzetten.", when a twin is done on the letter
  not kept a checkbox "Taak ook afronden" (on) with "Op de andere scan is '{taak}' al
  afgerond.", button "Samenvoegen". Afterwards the kept letter opens with the trash's undo
  bar (for the other letter).
- "Het zijn twee brieven": no question; message "Goed. We vragen het niet meer voor deze
  twee brieven." and back to "Te doen".
- `can_merge` false: the merge button is off and the reason is shown.
- A 404 on the page: "Deze dubbele brieven zijn er niet (meer)." with a link to "Te doen".
- History lines: "Samengevoegd met een dubbele brief door {naam}", "Als dubbele brief naar
  de prullenbak verplaatst door {naam}", "Gekozen: geen dubbele brief, door {naam}",
  "Taak verplaatst uit de dubbele brief", origin merge: "uit de dubbele brief".

## Rulings

From the assignment (kept): detect on arrival, never remove automatically; compare on
content with hard features; guard on a differing date or amount and show what differs; a
"Dubbel?" proposal under "Te doen" with the two letters side by side; merge through
`organise` with the other one to the trash via `move_to_trash`, or dismiss for good; never
reveal a letter the viewer may not see; API additive, web first.

Added:

1. **Its own model, not a `DocumentSuggestion` kind.** A pair belongs to two letters and
   needs "both readable" as its visibility rule. Cost if wrong: a second table in "Te doen".
2. **Detection runs in the pipeline's last transaction as the system, before the
   autopilot, and an open pair blocks automatic confirmation** (owner, 21-11). The rail is
   the caller's (`_confirm_guarded`); `decide_confirmation` stays pure and unchanged. A
   failed detection blocks the confirm too. Cost if wrong: the last transaction grows by
   the measured detection time (aim < 50 ms).
3. **Guards only when both values are strong (confirmed or confidence >= 0.85); weak
   differences are shown, not blocking** (owner, 21-11). Cost if wrong: a missed
   duplicate (cheap) or a proposal with a visible warning (a person still decides).
4. **Every member read of a pair goes through `visible_pairs`, on top of
   `visible_documents`, with RLS on both letters behind it.** Not listed, not counted, no
   chip, 404 like a missing one. Cost if wrong: a pair nobody may decide stays open,
   invisible (and blocks only the AI's confirm of that letter).
5. **A merge never changes visibility: different viewers refuse it with 409
   `duplicate_visibility_differs`** (owner, 21-11), checked before and after uniting the
   addressees. The postkamer case goes to the postkamer design. Cost if wrong: the person
   aligns visibility first or says "two letters".
6. **No audit code of its own** (owner, 21-11). The merge's trash writes `document_trashed`
   (`reason: duplicate`). Cost if wrong: one code later (additive).
7. **A done task on the discarded scan completes its open twin on the kept letter only
   through a checkbox "Taak ook afronden", on by default** (owner, 21-11: not silent).
   Cost if wrong: one unticked box.
8. **One row per pair forever; only new arrivals are compared (180 days, at most 3 open
   pairs per letter)** (owner, 21-11). An archive sweep is later. Cost if wrong:
   duplicates already in the archive wait for the sweep.
9. **Numbers in the text count as a feature.** Template letters are textually
   near-identical; their dates, amounts and references are what differ.
10. **The discarded letter's history says `trashed` with origin `merge` and the merging
    user; `move_to_trash` refuses `user=None`** (trash review M8). Cost if wrong: a system
    trash later adds its own origin.
11. **A pair with a letter in the trash is hidden, not withdrawn; it returns on restore.**
    Withdrawing another member's pair would need the system in a request path. Cost if
    wrong: a restored letter shows an old open pair again, which a person can dismiss.
12. **Labels and addressees a merge adds are a person's choice: origin `merge`, not
    automatic, not undoable through the history.** Cost if wrong: removed by hand.

## Left to the builder

- The exact regexes for dates, amounts and payment references, with tests from the
  measurement corpus.
- The lexeme choice for the candidate query (longest versus rarest) within `TERMS = 16`.
- The layout of the side-by-side page and the chip in "Te doen".

## iOS (later; fields the app needs)

- `getInboxCounts.duplicate` and `DocumentSummaryOut.open_duplicate_count` for a badge.
- `listDuplicates` / `getDuplicate`: `DuplicateOut` (`first`, `second`,
  `suggested_keep_id`, `text_similarity`, `same`, `differences[]`, `task_twins[]`,
  `can_merge`, `blocked_reason`); page images through `getDocumentPage` of each letter.
- `mergeDuplicate` (`keep_document_id`, `complete_twin_tasks`) answering `DocumentOut`,
  then the trash's undo (`restoreDocument` of the other id); `dismissDuplicate`.

## Decided (21-11, owner and main session)

- An open duplicate proposal blocks the AI's auto-confirm (CLAUDE.md autopilot line).
- Different visibility refuses the merge (409 `duplicate_visibility_differs`); the
  postkamer case is for the postkamer design.
- The twin task is completed only through the checkbox "Taak ook afronden" (on by default).
- Guards block only when both values are strong.
- Compare only on arrival, 180 days, at most 3 open pairs per letter, no audit code of its
  own; an archive sweep is later.
