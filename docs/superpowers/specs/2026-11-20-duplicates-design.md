# Duplicate letters

Date: 2026-11-20. Branch `feat/duplicates` (host), on top of `feat/trash`. iOS follows
separately (fields at the end). The plan comes later.

Builds on: the trash (`docs/superpowers/specs/2026-11-20-trash-design.md`, `move_to_trash`)
and on "wie ziet wat" (`feat/wie-ziet-wat`, PR #26: the visibility policies, actors in the
workspace context, `visibility.viewers`, the lock order). Building starts after both are in
`main`.

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
  (30 days to take it back).
- Non-goal: removing or merging anything automatically; comparing across workspaces;
  finding duplicates among letters that are already there (a one-off sweep is a follow-up);
  undoing a merge in one click (restore from the trash is the way back); iOS screens.
- Anti-goal: a proposal that tells somebody a letter exists they may not see; a reminder
  or a second instalment proposed as "the same letter" when its date or amount clearly
  differs; double payment tasks after a merge; letter text, sender or amounts in an audit
  row or a log line.

## Approaches

1. **A pair model of its own, detection inside the pipeline's last step, candidates from the
   search index, scored with `pg_trgm` similarity and a pure decision function (chosen).**
   The search index (`search_vector`, GIN) finds the few letters that share the new
   letter's distinctive words; only those get a full-text trigram similarity and a
   comparison of the numbers in the text and the extracted fields. Fast at any size, no new
   dependency, explainable.
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

### Model (`apps/documents/models.py`)

`DuplicateCandidate(WorkspaceModel)`, RLS enabled and forced:

- `first`, `second`: `ForeignKey(Document, on_delete=CASCADE, related_name="+")`, stored
  ordered (`first_id < second_id`, check constraint), unique `(first, second)`: one row per
  pair, ever. A purge of either letter removes the row (nothing is left to propose).
- `state`: `open` | `merged` | `dismissed` | `withdrawn` (a re-check found a guard).
- `text_similarity: Decimal(4,3)`, `number_overlap: Decimal(4,3)`: the scores at detection.
- `kept`: `ForeignKey(Document, null=True, on_delete=SET_NULL)`: the letter that stayed.
- `decided_by`: `ForeignKey(User, null=True, on_delete=PROTECT)`, `decided_at`.
- RLS policy: the workspace policy plus `EXISTS (document first) AND EXISTS (document
  second)` as subqueries on `documents_document`, so the document policies of the current
  actor decide: a row is readable only for an actor who reads both letters. Services filter
  through the seam as well (`visible_documents` for both ids); RLS is the second layer.
- `DocumentEvent`: new kinds `merged` (on the kept letter), `duplicate_dismissed` (on
  both), new origin `merge` (labels, addressees and tasks the merge moved), and a new field
  `related_document: ForeignKey(Document, null=True, on_delete=SET_NULL, related_name="+")`
  (the other letter of the pair; null once it is purged).

### What is compared (`apps/documents/duplicates_facts.py`, pure)

`LetterFacts` (frozen dataclass), built from the stored letter as the system:

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

- `find_candidates(document: Document) -> list[Candidate]`, as the system, inside the
  pipeline's last transaction (the one that holds the letter's row lock), after the
  proposals are stored and **before** the autopilot decides:
  1. Pool: live letters of the workspace (`LIVE`), not the letter itself, not `processing`,
     created within `WINDOW = 180 days`.
  2. Candidates from the search index: a `tsquery` of the new letter's `TERMS = 16` most
     distinctive lexemes (longest, least frequent in its own text; numbers and the `bsn`
     lexeme left out) joined with OR, ranked with `ts_rank_cd`, the best `MAX_CANDIDATES =
     20`. Pairs that already have a row (any state) are left out: a "no" is never asked
     again.
  3. For those: `similarity(a.ocr_text, b.ocr_text)` (`pg_trgm`) in one query, and
     `number_overlap` (Jaccard of the two `numbers` sets) in Python.
  4. `decide(new: LetterFacts, other: LetterFacts, *, text_similarity, number_overlap) ->
     Verdict` (pure, below). At most `MAX_OPEN_PER_LETTER = 3` rows, best first.
  `insert ... on conflict do nothing` on the pair.
- `mark_failed` runs the same detection (the text came with the upload; a failed AI step
  only means fewer facts).
- **Autopilot:** a letter with an open candidate is never confirmed automatically
  (`decide_confirmation` gets `has_open_duplicate: bool`; stays pure). Its labels and
  addressees may still be applied. Reason: a confirmed duplicate creates a second payment
  task.
- `recheck(document: Document) -> None` after a person confirms a letter (its facts got
  strong): each open candidate of it is decided again; a guard now firing sets
  `withdrawn`. No new search at that moment.

### The decision (`duplicates_facts.decide`, pure, no database)

Guards first (any one: no proposal, `Verdict.NONE`), each only when **both** values are
strong (table above):

- `letter_date` differs;
- `amount` differs;
- `invoice_number` differs, or `payment_reference` differs;
- `document_type` differs (an invoice and a reminder).

Then a proposal (`Verdict.LIKELY`) when `text_similarity >= TEXT_SIMILAR_FROM` and
(`number_overlap >= NUMBERS_SHARED_FROM` or both letters have fewer than 3 numbers).
Starting values `TEXT_SIMILAR_FROM = Decimal("0.80")`, `NUMBERS_SHARED_FROM =
Decimal("0.70")`; the measurement below sets them, DECISIONS records the result.

`differences(a, b) -> list[Difference]` and `same(a, b) -> list[str]`: every fact present
on both sides, with `uncertain=True` when one side is weak. A weak difference does not
block (it may be an OCR misread) but is shown on top in the proposal.

### How the thresholds are measured

A script (controller task, like the search measurement) on a throw-away database:

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
  without the guards; time of `find_candidates` per arrival at 10,000 and 100,000 letters
  (warm, median of 7). **Aim:** recall >= 0.95; false proposals <= 2% of hard negatives
  and 0 of random pairs; < 50 ms per arrival at 100,000 letters. If the aim is out of reach
  with approach 1, approach 3 is next.

### Visibility (frame 4)

- Detection runs as the system over all live letters of the workspace (it must, to find a
  pair at all) and writes only the pair row; nothing is shown at that moment.
- Who sees a proposal: an actor who reads **both** letters (RLS policy plus the seam). So
  nobody learns of a letter they may not see; a pair only one person can see in full is
  shown only to that person. A pair nobody with the member role sees in full stays
  unseen and unchosen (shown in the admin's count only when the admin sees both).
- Who decides: a member (role `member` or higher) who sees both. Viewers see nothing of it.
- **Different visibility:** when one letter is private (`restricted`) and the other is not,
  merging is refused (409 `duplicate_visibility_differs`): a merge never changes who sees a
  letter. The proposal says so and offers "Het zijn twee brieven" or aligning them first.
- The choice is recorded on the row (`state`, `kept`, `decided_by`, `decided_at`) and in
  the history of both letters.

### Merge (`apps/documents/duplicates.py`, `merge(*, membership, user, actor, candidate_id, keep_id) -> Document`)

`transaction.atomic`. Lock order as everywhere: the member's own membership `FOR SHARE`,
then both documents `FOR NO KEY UPDATE` in id order, then the candidate row. Refused when
the candidate is not `open` (409 `duplicate_not_open`), `keep_id` is not one of the pair
(422), either letter is `processing` (409 `document_processing`) or trashed (404), or the
visibility differs (409). Then, for kept `K` and other `O`:

1. Inside `visibility.addressee_scope(K)`: every label and addressee of `O` that `K` lacks
   is added through `organise.add_label` / `organise.add_addressee` with origin `merge`
   (the history says where it came from). Dismissed suggestions of `K` are not overruled by
   this: the person chose the merge, which counts as their choice (same as accepting).
2. Tasks: an open task of `O` with no equal task on `K` (the key of `review._key_of`: type,
   description, deadline, source) moves to `K`, with a history line of origin `merge` on
   both letters (`organise.record_task_event`). A **done** task of `O` whose equal is open on `K` completes
   that task on `K` as well, with the merging user as actor and a history line: the bill was
   paid through the other scan. Every other task of `O` stays with `O` (in the trash).
3. `move_to_trash(O, user=user, actor=actor, reason="duplicate")` (trash design, ruling 17):
   history `trashed` on `O`, audit `document_trashed {reason: "duplicate"}`. The retention
   hook (`retention.check_removal`) applies as for any trash.
4. History `merged` on `K` with `related_document=O`; candidate `merged`, `kept=K`.
5. Other open candidates that contain `O` become `withdrawn`.

`O` can be restored from the trash for 30 days; the merge itself is not undone (`K` keeps
the united labels and addressees; each can be removed by hand or undone in the history).

### Dismiss (`dismiss(*, membership, user, candidate_id) -> None`)

Same locks; refused when not `open`. State `dismissed`, `decided_by`, `decided_at`; history
`duplicate_dismissed` on both letters. The row stays, so the pair is never proposed again.
No audit row (ruling 6).

### Audit

No new code. A merge writes `document_trashed` with `reason: "duplicate"` (the allowed
value is added to the closed list): ids only. The rest of the choice is on the row and in
the history.

### API (additive, `/api/v1`)

| Method and path | `operation_id` | Auth, role | Out |
|---|---|---|---|
| `GET /workspaces/{workspace_id}/duplicates` | `listDuplicates` | session or device, any member | `list[DuplicateOut]` (open, both visible, newest first, at most 100) |
| `GET /workspaces/{workspace_id}/duplicates/{duplicate_id}` | `getDuplicate` | same | `DuplicateOut`; 404 `duplicate_not_found` |
| `POST /workspaces/{workspace_id}/duplicates/{duplicate_id}/merge` | `mergeDuplicate` | session or device, `member` | `DocumentOut` (the kept letter) |
| `POST /workspaces/{workspace_id}/duplicates/{duplicate_id}/dismiss` | `dismissDuplicate` | session or device, `member` | 204 |

- `DuplicateOut`: `id: UUID`, `state: str`, `created_at: datetime`, `first:
  DocumentSummaryOut`, `second: DocumentSummaryOut`, `suggested_keep_id: UUID` (the
  confirmed one, else the one further in review, else the older), `text_similarity: int`
  (0 to 100), `same: list[str]` (default empty), `differences: list[DuplicateDifferenceOut]`
  (default empty), `can_merge: bool = True` (false when the visibility differs),
  `blocked_reason: str = ""` (`visibility_differs`).
- `DuplicateDifferenceOut`: `field: str` (`sender`, `letter_date`, `document_type`,
  `amount`, `invoice_number`, `payment_reference`, `page_count`), `first: str`, `second:
  str` (dates ISO, amounts as decimal text), `uncertain: bool = False`.
- `MergeDuplicateIn`: `keep_document_id: UUID`.
- Additive on existing schemas: `DocumentSummaryOut.open_duplicate_count: int = 0`;
  `InboxCountsOut.duplicate: int = 0`; `listDocuments` accepts `inbox_state=duplicate`.
- Errors: 404 `duplicate_not_found`; 409 `duplicate_not_open` ("Over deze twee brieven is
  al gekozen."); 409 `duplicate_visibility_differs` ("Deze twee brieven zijn niet voor
  dezelfde mensen zichtbaar. Maak dat eerst gelijk, of kies 'Het zijn twee brieven'.");
  422 `validation_error` for a `keep_document_id` outside the pair; 409
  `document_processing`.

### Web (Dutch B1, Lingui)

- "Te doen": a filter "Dubbel?" with its count (`InboxCountsOut.duplicate`); a letter with
  an open pair carries a chip "Dubbel?". The list "Dubbel?" shows pairs: both senders and
  dates, "Lijkt op elkaar voor {n}%".
- Page "Mogelijk dubbel" (`routes/duplicate.tsx`, path `dubbel/$duplicateId`): two columns
  (stacked on a phone), each with the first page, afzender, soort, datum, bedrag, kenmerk,
  pagina's, labels, op naam van, taken. A row that differs is marked "Verschilt"; an
  uncertain one "Verschilt misschien (slecht leesbaar)".
- When anything differs, on top: "Let op: {velden} verschilt. Een herinnering of een tweede
  termijn lijkt vaak op de eerste brief, maar het is een andere brief."
- Per column a choice "Deze brief houden" (the suggested one chosen).
- "Het is dezelfde brief": confirm (primary): title "Samenvoegen?", text "De brief die je
  houdt, krijgt de labels en personen van allebei. De andere brief gaat naar de
  prullenbak. Daar kun je hem 30 dagen terugzetten.", button "Samenvoegen". Afterwards the
  kept letter opens with the trash's undo bar.
- "Het zijn twee brieven": no question; message "Goed. We vragen het niet meer voor deze
  twee brieven." and back to "Te doen".
- `can_merge` false: the merge button is off and the reason is shown.
- History lines: "Samengevoegd met een dubbele brief door {naam}", "Gekozen: geen dubbele
  brief, door {naam}", origin merge: "uit de dubbele brief".

## Rulings

From the assignment (kept): detect on arrival, never remove automatically; compare on
content with hard features; guard on a differing date or amount and show what differs; a
"Dubbel?" proposal under "Te doen" with the two letters side by side; merge through
`organise` with the other one to the trash via `move_to_trash`, or dismiss for good; never
reveal a letter the viewer may not see; API additive, web first.

Added:

1. **Its own model, not a `DocumentSuggestion` kind.** A pair belongs to two letters and
   needs "both readable" as its visibility rule; the suggestion table's constraint, the
   autopilot and the inbox count read suggestions as label/addressee proposals. Cost if
   wrong: a second table to show in "Te doen".
2. **Detection runs in the pipeline's last transaction, before the autopilot, and an open
   pair blocks automatic confirmation.** Prevents a second payment task from the autopilot.
   Cost if wrong: the last transaction grows by the measured detection time (aim < 50 ms).
3. **Guards only when both values are strong (confirmed or confidence >= 0.85); weak
   differences are shown, not blocking.** An OCR misread of a date must not hide a real
   duplicate; a clear different date must never be proposed. Cost if wrong: a missed
   duplicate (cheap) or a proposal with a visible warning (a person still decides).
4. **The proposal is visible only to an actor who reads both letters, enforced by RLS and
   the seam.** Cost if wrong: a pair nobody may decide stays open, invisible.
5. **A merge never changes visibility: different visibility refuses the merge.** Cost if
   wrong: in the "postkamer" case (a private letter and a normal scan of the same letter)
   the person must first make both private; see "Points for Stijn".
6. **No audit code of its own.** The merge's trash already writes `document_trashed`
   (`reason: duplicate`); the choice lives on the row and in both histories. Cost if wrong:
   one code later (additive).
7. **A done task on the trashed scan completes its equal on the kept letter.** Otherwise the
   kept letter says "betalen" for a bill that was paid. Cost if wrong: a task ticked off
   that the person must reopen (the history says why).
8. **One row per pair forever; only new arrivals are compared (window 180 days, at most 3
   open pairs per letter).** Cost if wrong: duplicates already in the archive need a
   one-off sweep (follow-up).
9. **Numbers in the text count as a feature.** Template letters (monthly invoices,
   reminders) are textually near-identical; their dates, amounts and references are what
   differ. Cost if wrong: a pure regex to tune with the measurement.

## Left to the builder

- The exact regexes for dates, amounts and payment references, with tests from the
  measurement corpus.
- The lexeme choice for the candidate query (longest versus rarest) within `TERMS = 16`.
- The layout of the side-by-side page and the chip in "Te doen".
- The plan, after the measurement script exists (the thresholds come from it).

## iOS (later; fields the app needs)

- `getInboxCounts.duplicate` and `DocumentSummaryOut.open_duplicate_count` for a badge.
- `listDuplicates` / `getDuplicate`: `DuplicateOut` (`first`, `second`,
  `suggested_keep_id`, `text_similarity`, `same`, `differences[]` with `field`, `first`,
  `second`, `uncertain`, `can_merge`, `blocked_reason`); page images through
  `getDocumentPage` of each letter.
- `mergeDuplicate` (`keep_document_id`) answering `DocumentOut`, then the trash's undo
  (`restoreDocument` of the other id); `dismissDuplicate`.

## Points for Stijn

- **Private and normal scan of the same letter.** If Anna's letter is private and Bob scans
  the same letter normally, everyone already reads its content through Bob's scan. This
  design refuses the merge and shows it to whoever sees both; it does not warn Anna that
  her private letter is readable as a copy. Decide whether such a pair should also notify
  the private letter's readers (and whether the fix is "make the copy private too").
- **Done task completes its twin (ruling 7):** automatic on the person's merge, recorded in
  the history. Confirm that this is wanted rather than a question in the merge dialog.
