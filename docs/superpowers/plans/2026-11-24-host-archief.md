# Host "Archief" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Brievie gets "Archief": one page over every reviewed letter and every own document the member may see, with archive-style filters (kind, type, person, sender, year, label, expiring) and full-text search; a member can add an own document (title, type, date, person, labels, end date, "bewaren tot") that skips the Inbox and carries no tasks; an end date becomes a reminder task in "Mijn taken"; "bewaren tot" stops a permanent delete; a full export in an open format leaves the archive.

**Architecture:** One `Document` table gets a `kind` (`letter` or `document`) plus `title`, `expires_on`, `remind_days`, `retain_until`; nothing is moved or copied, the archive is `visible_documents` filtered on `status = 'done'`. A pure validator (`documents/own.py`) guards what an own document may carry; the pipeline gets one `kind` branch that ends in `done` (confirmed by the uploader, AI only proposes). Partial indexes and the unread, Inbox, "Blijft liggen" and "Mijn post" conditions learn `kind = 'letter'`. A daily job makes the expiry reminder through one named task writer. The export extends the existing building block (`export.py`, `export_files.py`) to version 2 with a PDF per document and an admin scope.

**Tech Stack:** Python 3.13, Django 6.1, Django Ninja, PostgreSQL 18 (forced RLS), Procrastinate, pytest; React 19, TanStack Router/Query, React Aria Components, Lingui, Vitest, MSW; Swift/SwiftUI for the iOS section (`Brievie_ios`). No new dependency (the PDF writer is ours).

**Spec:** `docs/superpowers/specs/2026-11-24-archief-design.md`, cited "S 6.2". Neighbours: navigation (`2026-11-21-navigatie-en-persoonlijke-inbox-design.md`, "NAV", on `main`), letter status (`2026-11-21-briefstatus-design.md`, "LS", unmerged), intake (`2026-11-20-inname-zonder-iphone-design.md`, "INT", unmerged), rights (`2026-11-19-zichtbaarheid-postkamer-inrichting-design.md`, "RS 4.9.3"); DECISIONS "Trash for letters", "Duplicate letters", "Search joins the visibility rules", "Navigation, Mijn post, Ongelezen and Blijft liggen".

## Global Constraints

From `CLAUDE.md` (verbatim where quoted):

- "Every table: UUID `id`, `created_at`, `updated_at`. Every workspace content table: `workspace_id` (subclass `WorkspaceModel`) and RLS enabled **and forced**." No new table in this plan.
- "`mypy --strict` and `ruff check` clean. No `Any`, no bare `except`." Datetimes timezone-aware.
- "API only under `/api/v1`, additive changes only. Every endpoint: typed schemas and an explicit camelCase `operation_id`." Every non-2xx via `ApiError` `{"code", "message"}`, message a Dutch sentence.
- "A field added to an existing API schema has a default (never newly required; the contract test compares with `backend/tests/fixtures/openapi_1b.json` and `openapi_1c.json`)." "Nullable fields in API schemas are primitives only, and nullable inputs have a default; the contract test forbids `anyOf`." Hence clearing a date is a flag (`clear_expires_on`), not `null` meaning two things.
- "Thin endpoints: validate, call one function in the app's `services.py`, return." "Every mutating service function is wrapped in `transaction.atomic`."
- "No document content, tokens, passwords or BSNs in logs." Text is BSN-masked before it is stored, logged or sent to an AI service; the AI step keeps the recorded-consent rule (`AISettings.consented_base_url`).
- "A member-facing read of documents starts from `apps.documents.visibility.visible_documents`." Every list, count, facet and reminder of this plan starts there (or at the task policy, which runs the document policy). "A trashed letter is out of every read" (`models.LIVE` off the seam).
- "Only `apps.documents.organise` adds or removes a document's labels and addressees (it writes the history)." The add flow sets persons and labels through it.
- "Automatic changes have no actor and are marked." The reminder task: no creator, `deadline_source = calculated`, history origin `system`. "Never record a user for what nobody did."
- "No new dependency without a line in `docs/DECISIONS.md`." None planned; if Task 10 prefers `img2pdf`, the DECISIONS line comes first.
- "Brievie never logs, audits or echoes a search term" and search is `POST searchDocuments`, never a `q` on a `GET`; the sender filter is chosen from a facet list, never typed into a URL.
- "Export files are made, read and removed only through the guarded functions in `apps/documents/export_files.py`."
- "Document files are sealed before they touch disk." The PDF is built in memory from opened pages.
- User-facing text in Dutch B1 (the source) and English: backend `gettext` and `make messages`, web Lingui `t` and `pnpm extract`; terms from `docs/GLOSSARY.md` "English in the interface". No invented person names in example texts (roles: "je dochter", "de huurder").
- After every API change: `make openapi`, `cd web && pnpm gen:api`, commit both generated files with the change.
- Tests first for logic. Conventional commits, each ending with the trailer `Co-Authored-By: Claude <noreply@anthropic.com>`.

From the environment:

- **Backend tests run in CI only.** Never connect to the PostgreSQL on this Mac (test server `brievie`), never `make dev`, never run docker or a server. A backend "run" step means: commit, push the branch `feat/archive`, read the `backend` job (and `visibility-perf` where named). `make lint`, `make typecheck` and every `pnpm` command run locally.
- **Start point.** Branch `feat/archive` from `main`. Tasks 1-12 and web Tasks W1, W2, W4 need nothing unmerged. Tasks marked **needs LS** or **needs INT** wait for those branches to be on `main`; see "What depends on what". Migration numbers are written as "next"; take the next free number of the app at build time and renumber in the same commit if another branch merged first.

From the spec, not left to the builder:

- **Archive** = `status = 'done'`, both kinds (S 5). Unreviewed letters are Inbox, never archive.
- **`kind` is set by the server's validation and never changes** (AR2). **`private` only narrows** (S 8).
- **Sensitive types** with the switch preselected: `identity`, `medical`, `payslip`, `tax_return` (S 8, B3).
- **Own document types** `DOCUMENT_TYPES`: `identity`, `diploma`, `warranty`, `tax_return`, `medical`, `contract`, `policy`, `payslip`, `annual_statement`, `receipt`, `information`, `other` (S 4).
- **Reminder lead** choices 7, 30, 60, 90 days (default 30); the task is of the new type `expiry`, `deadline = expires_on`, `deadline_source = calculated`, no creator, no assignee (S 7.1).
- **Retention** table of S 7.2 (trash warns, purge refuses, expiry purge skips).
- **Copy** in the spec (S 11.4) is used verbatim; sentences this plan adds are marked "(plan)".

## Review Focus

1. **No privileged default and no widening.** `kind`, `status` and `visibility` of a new document are set by the server; `private` only narrows; an own document is seen exactly as a letter on the same persons; `require_editable` is the first line of every write. → Tasks 4, 6, 11.
2. **Nothing counts, lists or reminds outside `visible_documents`.** Lists, facets, years, senders, `expiring`, the export and the reminder. → Tasks 7, 8, 10, 11 (leak tests with the switches in the state "everyone sees all" off).
3. **Own documents are not post that waits.** Not in the Inbox, not unread, not in "Blijft liggen", not in "Mijn post", `letter_status` empty. → Tasks 2, 8, 13.
4. **One writer per fact.** `reminders.start_reminder` is the only new task writer and is named in the task source test; `own.create_document` is the only creator of a `kind = document`; retention is read only through `check_removal`. → Tasks 4, 8, 9.
5. **The migration is safe to apply.** Not-null columns with a Python default and a database default by `RunSQL`; indexes recreated with `kind`; the generated `search_vector` rewrite measured. → Tasks 1-3, 12.
6. **250 ms.** The archive lists, facets, search and the re-measured Inbox, Mijn post and "Blijft liggen" lists at 100,000 documents. → Task 12.
7. **Older clients.** An app that sends no new field gets a letter exactly as today; every new `Out` field is defaulted; no `anyOf`. → Tasks 4, 7.

---

## What depends on what

| Tasks | Needs | Note |
|---|---|---|
| 1-12, W1, W2, W4, W5, I1-I5 | `main` (navigation merged, PR #44) | the iPhone add flow uses the existing `createDocument` with client-OCR'd pages |
| **13** (status filter and badge on Archief, `letter_status` empty for documents) | **letter status (LS)** merged | needs `task_open_count`, `letter_state.sync`, `listDocuments` `letter_status` |
| **14** (browser file upload with the new fields, originals and text layer in the export) | **intake (INT)** merged | needs `uploadDocumentFile`, originals, server OCR |
| **W3** (web "Document toevoegen") | Task 14 | there is no file upload on the web before INT |
| **15** (scan folder for documents) | INT merged and its folder layout built | later slice; may be dropped without touching 1-12 |

Order: 1 → 2 → 3 → 4 → 5 → 6 → 7 → (8, 9, 10 in any order) → 11 → 12 → W1 → W2 → W4 → W5; iOS after 7; 13, 14, W3, 15 when their neighbours land. Each of 1-12 leaves `main` green on its own (a column nobody reads is harmless).

---

### Task 1: Fields, types, database defaults

**Files:**
- Modify: `backend/apps/documents/models.py`, `backend/apps/documents/schemas.py` (constants only), `docs/SPEC.md` (strike `folder_id` in section 8; add `kind`, `title`, `expires_on`, `retain_until`), `docs/DECISIONS.md` (AR1, AR2, AR11; the Folder decision), `docs/GLOSSARY.md` (table: kind, archive; "English in the interface": the rows of S 11.4)
- Create: `backend/apps/documents/migrations/<next>_archive_fields.py`, `backend/tests/test_documents_kind_fields.py`, `backend/tests/test_archive_db_defaults.py`

**Interfaces:**
- `Document.Kind(models.TextChoices)`: `LETTER = "letter"`, `DOCUMENT = "document"`.
- `Document.kind = CharField(max_length=10, choices=Kind.choices, default=Kind.LETTER)`; `title = CharField(max_length=200, blank=True, default="")`; `expires_on = DateField(null=True, blank=True)`; `remind_days = PositiveSmallIntegerField(default=30)`; `retain_until = DateField(null=True, blank=True)`. **No `db_default`** (PB decision 5: `RETURNING` reads through the SELECT policies).
- `Document.Type` + `IDENTITY = "identity"`, `DIPLOMA = "diploma"`, `WARRANTY = "warranty"`, `TAX_RETURN = "tax_return"`, `MEDICAL = "medical"` (Dutch labels per S 4, `gettext_lazy`).
- Module constants `LETTER_TYPES` and `DOCUMENT_TYPES` (frozensets of the string values) next to the model; `SENSITIVE_TYPES = {"identity","medical","payslip","tax_return"}`; `REMIND_DAYS = (7, 30, 60, 90)`.
- Migration: `AddField` for each column (`NOT NULL DEFAULT` for existing rows), then `RunSQL` `ALTER COLUMN kind SET DEFAULT 'letter'`, `title SET DEFAULT ''`, `remind_days SET DEFAULT 30` (reverse: `DROP DEFAULT`), as the duplicate columns.

- [ ] **Step 1: Failing tests:**

```python
def test_new_document_is_a_letter_with_no_title_or_dates(): ...        # kind "letter", title "", expires_on/retain_until None, remind_days 30
def test_document_types_are_a_subset_of_the_model_types(): ...         # DOCUMENT_TYPES <= set(Document.Type.values); LETTER_TYPES unchanged (14 old values)
def test_sensitive_types_are_document_types(): ...
def test_db_defaults_for_new_columns(): ...                           # information_schema: kind 'letter', title '', remind_days 30 (as test_duplicates_db_defaults)
def test_raw_insert_without_the_new_columns_still_works(): ...        # INSERT INTO documents_document (...) leaves them out
def test_existing_rows_become_letters(): ...                          # migration test: a row from before has kind "letter"
```

- [ ] **Step 2:** Commit, push `feat/archive` → backend job FAIL. **Step 3:** Implement; `makemigrations`; reversible. **Step 4:** Push → PASS. `make lint`, `make typecheck`.
- [ ] **Step 5:** Strike `folder_id` in SPEC 8 and record in DECISIONS: "`Folder` and `listFolders`/`createFolder` stay (additive API); no `folder_id`; labels, person, type, year instead."
- [ ] **Step 6:** Commit `feat(documents): kind, title, expiry and retention fields`.

### Task 2: Inbox, unread, "Blijft liggen" and "Mijn post" learn `kind`

**Files:**
- Modify: `backend/apps/documents/models.py` (index conditions), `backend/apps/documents/services.py` (`_in_state`, `inbox_state`, `my_post_filter`), `backend/apps/documents/reading.py` (`unread_q`, `unread_ids`, the `addressee_opened_at` writer and recompute), `backend/apps/documents/lingering.py`
- Create: `backend/apps/documents/migrations/<next>_kind_indexes.py`, `backend/tests/test_documents_kind_is_not_post.py`

**Interfaces:**
- `documents_document_unassigned`, `documents_document_unopened`, `documents_document_opened` recreated with `AND kind = 'letter'` in their condition (`RemoveIndex` then `AddIndex`; a plain migration, no `CONCURRENTLY`: a rebuild of three partial indexes). Name unchanged.
- `inbox_state(document)` returns `InboxState.SORTED` for `kind == "document"`; `_in_state` adds `Q(kind="letter")` to `unassigned`; `new` and `review` need no change (an own document is never `needs_review`).
- `reading.unread_q` = `Q(kind="letter") & ...`; `unread_ids` skips documents; `reading` sets `addressee_opened_at` only for letters; `lingering` queries add `kind="letter"`.
- `my_post_filter` = `Q(kind="letter") & Q(addressee_ids__overlap=ids)`.

- [ ] **Step 1: Failing tests** (`office()` world, E off; one letter and one own document on the same person):

```python
def test_own_document_is_never_in_the_inbox_lists_or_counts(): ...          # naam kiezen: a document with no person is not "unassigned"
def test_own_document_is_never_unread_for_anyone(): ...                      # unread_q, unread_ids, menu_counts.my_post_unread
def test_own_document_is_never_lingering(): ...                              # not opened by the addressee for 10 days: still not lingering
def test_mijn_post_lists_letters_only(): ...
def test_opening_an_own_document_sets_no_addressee_opened_at(): ...
def test_the_three_partial_indexes_have_kind_in_their_condition(): ...       # pg_indexes definition contains kind
def test_letters_behave_as_before(): ...                                     # the existing inbox, unread and lingering tests stay green unchanged
```

- [ ] **Step 2:** Push → FAIL. **Step 3:** Implement + migration. **Step 4:** Push → PASS. Run `visibility-perf` once on the branch (workflow dispatch) and write the numbers in the task's commit message; the full measurement is Task 12.
- [ ] **Step 5:** Commit `feat(documents): own documents are not post that waits`.

### Task 3: The title is searchable

**Files:**
- Modify: `backend/apps/documents/models.py` (`search_vector`), `backend/apps/documents/search.py` (filters only if the title needs ranking changes; none expected)
- Create: `backend/apps/documents/migrations/<next>_search_vector_title.py`, `backend/tests/test_documents_search_title.py`

**Interfaces:**
- `search_vector` = `SearchVector("sender", "title", weight="A", config="dutch")` + summary B + `ocr_text` C. (A generated column cannot be altered: the migration `RemoveIndex(documents_document_search)`, `RemoveField(search_vector)`, `AddField(search_vector)`, `AddIndex(documents_document_search)`.)

- [ ] **Step 1: Failing tests:**

```python
def test_a_title_is_found_by_word_start(): ...                  # "Garantie" finds the document titled "Garantiebewijs wasmachine"
def test_title_ranks_above_the_text(): ...
def test_search_filters_still_use_the_visible_documents_seam(): ...
def test_migration_is_reversible(): ...
```

- [ ] **Step 2:** Push → FAIL. **Step 3:** Implement; note in the migration docstring that it rewrites the table once and rebuilds the GIN index. **Step 4:** Push → PASS.
- [ ] **Step 5:** `docs/DECISIONS.md` AR9 with the release-notes sentence ("this update takes a little longer to apply"). Commit `feat(search): documents are found by title`.

### Task 4: Adding an own document (server)

**Files:**
- Create: `backend/apps/documents/own.py`, `backend/tests/test_documents_own_validate.py`, `backend/tests/test_documents_own_create_api.py`
- Modify: `backend/apps/documents/schemas.py` (`UploadMetadataIn`), `backend/apps/documents/intake.py` (`create_document_from_upload`), `backend/apps/documents/api.py` (`createDocument` passes the new fields; thin)

**Interfaces:**
- `UploadMetadataIn` + `kind: str = "letter"`, `title: str = ""`, `document_type: str = ""`, `document_date: date | None = None`, `person_ids: list[UUID] = []`, `label_ids: list[UUID] = []`, `expires_on: date | None = None`, `remind_days: int = 30`, `retain_until: date | None = None`, `private: bool = False`, `summary_by_ai: bool = False` ("Laat Brievie een samenvatting maken").
- `own.validate(metadata, workspace) -> OwnDocumentData` (pure part first: `validate_fields(...)` has no database): `kind` in {letter, document}; for a letter every new field must be at its default (else 422 `validation_error`: an old client never sends them; a new one may not smuggle a title into a letter); for a document: `title` non-blank (strip, at most 200), type in `DOCUMENT_TYPES` (blank not allowed), `document_date` not after today + 1 day, `expires_on >= document_date` when both, `remind_days in REMIND_DAYS`, persons exist, are active and are ones the caller may name (the same check as `confirmDocument`'s `addressee_ids`), labels exist.
- `own.create_document(...)`: called by `create_document_from_upload` after the row exists, in its transaction: sets `kind`, `title`, `letter_date = document_date`, `expires_on`, `remind_days`, `retain_until`, `source`; writes persons and labels **through `organise`** (history), then (S 4.9.3 write order) as the system the addressee list and readers; when `private` the `make_private` flip in the same transaction with `keep_own_access`; history `document_added` (origin `user`, actor the member). Repeating the upload (same `client_id`, same pages) returns the existing document and changes nothing.
- A viewer ("Alleen lezen") gets 403 as for every upload today.
- `DocumentEvent.Kind` + `DOCUMENT_ADDED = "document_added"`.

- [ ] **Step 1: Failing tests:**

```python
def test_old_client_without_new_fields_creates_a_letter(client): ...                  # as today, identical response
def test_a_letter_with_a_title_is_422(client): ...
def test_document_needs_title_and_a_document_type(client): ...                        # 422 each, Dutch message
def test_dates_and_lead_are_validated(): ...                                          # pure
def test_document_gets_persons_labels_and_history_through_organise(client): ...       # DocumentEvent kinds, actor is the member
def test_document_for_a_person_the_member_may_not_name_is_422(client): ...
def test_private_switch_makes_it_restricted_and_the_uploader_still_reads_it(client): ...
def test_private_false_never_widens_a_workspace_that_hides_post(client): ...          # restricted workspaces keep their own rules
def test_kind_and_status_cannot_be_chosen_by_the_request_to_widen(client): ...        # status stays processing at creation
def test_repeating_the_upload_changes_nothing(client): ...
def test_viewer_cannot_add(client): ...
def test_uploader_does_not_see_their_own_document_as_unread(client): ...
```

- [ ] **Step 2:** Push → FAIL. **Step 3:** Implement; `make openapi`, `pnpm gen:api`. **Step 4:** Push → PASS; contract test against `openapi_1b.json` and `openapi_1c.json` stays green.
- [ ] **Step 5:** Commit `feat(documents): add an own document to the archive`.

### Task 5: The pipeline's document branch

**Files:**
- Modify: `backend/apps/pipeline/steps.py` (one `if document.kind == "document"` branch; it skips `organise_processed`'s rules step), `backend/apps/documents/services.py` (`retry_processing`), `backend/apps/documents/duplicates.py` (`detect` compares within `kind`), `backend/apps/pipeline/schema.py` / `prompts/` (a document prompt that asks only for title, type, date, end date, labels; no tasks)
- Create: `backend/tests/test_pipeline_document_branch.py`, `backend/tests/test_duplicates_kind.py`

**Interfaces:**
- `run_pipeline` for a document: OCR text masked as now; AI (only under the consent rules) returns proposals stored as `Extraction`/`DocumentSuggestion` rows (fields `title`, `document_type`, `document_date`, `expires_on`; label suggestions); **no autopilot call, no tasks, no `confirm_automatically`**; at the end, under the row lock and only while `processing`: `status = DONE`, `confirmed_by = scanned_by`, `confirmed_at = now`, `confirmed_by_ai = False`. Summary stored only when `summary_by_ai` was asked for and the AI is on (`summary_by_ai = True`).
- `mark_failed` for a document: `status = DONE` with `failure_code` set (S 6.2 point 5); `retry_processing` accepts `status == DONE and kind == "document" and failure_code != ""`, sets it back to `processing` and clears the code.
- `duplicates.detect(document)` considers only candidates of the same `kind`.
- `documents.suggestions.accept` for a document field proposal applies it through `own.apply_suggestion` (writes history `details_changed`, actor the member); never applied by the pipeline.

- [ ] **Step 1: Failing tests:**

```python
def test_document_ends_done_confirmed_by_the_uploader_not_the_ai(): ...
def test_document_never_gets_tasks_or_autopilot_even_when_autopilot_is_on(): ...
def test_ai_proposals_for_a_document_are_not_applied_by_themselves(): ...
def test_ai_off_or_no_consent_leaves_no_proposals_and_still_ends_done(): ...
def test_failure_ends_done_with_failure_code_and_retry_works(): ...
def test_text_is_masked_and_no_bsn_reaches_the_ai_for_a_document(): ...
def test_duplicates_only_between_the_same_kind(): ...
def test_two_identical_documents_get_the_dubbel_block(): ...
def test_confirm_document_on_a_document_is_409_not_reviewable(client): ...
def test_rules_never_run_on_a_document(): ...                         # neither processed nor confirmed; a rule that would propose a task proposes nothing
def test_summary_marked_by_ai_only_when_asked(): ...
```

- [ ] **Step 2:** Push → FAIL. **Step 3:** Implement. **Step 4:** Push → PASS. The existing pipeline, autopilot and duplicates tests stay unchanged and green.
- [ ] **Step 5:** Commit `feat(pipeline): own documents end done and are only proposed to`.

### Task 6: Editing a document's details

**Files:**
- Create: `backend/apps/documents/own_api.py` (or in `api.py`), `backend/tests/test_documents_details_api.py`
- Modify: `backend/apps/documents/own.py`, `backend/apps/documents/models.py` (event kinds), `backend/apps/documents/schemas.py`

**Interfaces:**
- `DocumentDetailsIn`: `title: str = ""`, `document_type: str = ""`, `document_date: date | None = None`, `expires_on: date | None = None`, `clear_expires_on: bool = False`, `remind_days: int = 0` (0 = unchanged), `retain_until: date | None = None`, `clear_retain_until: bool = False`. Empty string or `None` means "unchanged".
- `PATCH /workspaces/{id}/documents/{document_id}/details`, `operation_id="updateDocumentDetails"`, member+, session or device. Service `own.update_details(membership, document_id, data)`: `transaction.atomic`, row lock, **`visibility.require_editable(document)` first**, then validation (a letter accepts only `retain_until`; a document everything; types per kind); writes `details_changed` (field names; dates for the date fields; never title or text) and for `retain_until` also `retention_changed`; calls `reminders.on_expiry_changed(document)` (Task 8).
- `DocumentEvent.Kind` + `DETAILS_CHANGED`, `RETENTION_CHANGED`.
- Trashed or invisible document: 404 `document_not_found`. Emergency access: read-only, 403 as the other writes.

- [ ] **Step 1: Failing tests:**

```python
def test_editor_changes_title_type_and_dates(client): ...
def test_viewer_and_emergency_reader_cannot_write(client): ...
def test_invisible_or_trashed_document_is_404_not_403(client): ...
def test_letter_accepts_only_retain_until(client): ...
def test_clear_flags_clear_and_empty_means_unchanged(client): ...
def test_type_must_belong_to_the_kind(client): ...
def test_history_names_fields_never_the_title(client): ...
def test_kind_cannot_be_changed(client): ...                              # no field for it; extra keys ignored or 422 as the schema says
```

- [ ] **Step 2:** Push → FAIL. **Step 3:** Implement; `make openapi`, `pnpm gen:api`. **Step 4:** Push → PASS.
- [ ] **Step 5:** Commit `feat(documents): edit an own document's details`.

### Task 7: The archive list, facets and search filters

**Files:**
- Modify: `backend/apps/documents/services.py` (`list_documents`, new `list_archive_facets`), `backend/apps/documents/search.py`, `backend/apps/documents/api.py` (`listDocuments` query, `getArchiveFacets`, `searchDocuments` body), `backend/apps/documents/schemas.py` (`DocumentSummaryOut`, `DocumentOut`, `ArchiveFacetsOut`, `FacetOut`)
- Create: `backend/tests/test_documents_archive_list.py`, `backend/tests/test_documents_archive_facets.py`

**Interfaces:**
- `list_documents(..., kind="", document_type="", year=0, sender="", archived=False, expiring=False)`: all on top of `visible_documents`; `archived` = `status = done`; `year` = `letter_date` year, else `created_at` year (`Q(letter_date__year=y) | Q(letter_date__isnull=True, created_at__year=y)`); `sender` exact; `expiring` = `expires_on <= today + 90 days` (including past, `kind = document`); unknown kind or type 422 `validation_error`; cap 200 and order as now.
- `list_archive_facets(membership) -> ArchiveFacetsOut`: one pass, filtered aggregates over `visible_documents(membership).filter(status="done")`: `kinds`, `types`, `years` (each `list[FacetOut]`), `senders` (top 50 by count). `FacetOut` = `value: str`, `count: int`.
- `getArchiveFacets` GET `/workspaces/{id}/documents/archive-facets`, any role, session or device. `searchDocuments` body + `kind: str = ""`, `document_type: str = ""`, `year: int = 0`, `person_id: UUID | None = None`, `label_id: UUID | None = None`.
- `DocumentSummaryOut`/`DocumentOut` + `kind: str = "letter"`, `title: str = ""`, `expires_on: date | None = None`, `retain_until: date | None = None`, `remind_days: int = 30`, `reminder_task_id: UUID | None = None`.

- [ ] **Step 1: Failing tests:**

```python
def test_archived_lists_done_documents_of_both_kinds_and_no_unreviewed_letter(): ...
def test_filters_combine_and_each_narrows_never_widens(): ...
def test_year_uses_letter_date_then_created_at(): ...
def test_expiring_lists_documents_ending_within_90_days_or_past(): ...
def test_unknown_kind_or_type_is_422(): ...
def test_facets_count_only_what_the_member_sees(client): ...            # E off world: every actor's facets equal their own list's counts
def test_facets_never_include_trashed_or_private_hidden_documents(client): ...
def test_senders_facet_is_capped_and_ordered_by_count(): ...
def test_search_respects_the_new_filters_and_logs_no_term(client): ...
def test_new_out_fields_have_defaults_and_old_fixtures_still_match(): ...
```

- [ ] **Step 2:** Push → FAIL. **Step 3:** Implement; `make openapi`, `pnpm gen:api`; add `archive_*` functions to the `test_visibility_seams` list. **Step 4:** Push → PASS.
- [ ] **Step 5:** Commit `feat(documents): archive list, facets and search filters`.

### Task 8: The expiry reminder (one task writer, one daily job)

**Files:**
- Create: `backend/apps/documents/reminders.py`, `backend/apps/tasks/migrations/<next>_one_reminder_per_document.py`, `backend/tests/test_documents_reminders.py`
- Modify: `backend/apps/documents/jobs.py` (periodic job), `backend/apps/documents/own.py` (calls `on_expiry_changed`), `backend/apps/tasks/models.py` (constraint), `backend/tests/test_task_writers_source.py` (or the existing source test of task writers; add the new writer by name), `backend/apps/documents/models.py` (event kind)

**Interfaces:**
- `reminders.start_reminder(document) -> Task | None`: runs as the system under the document's row lock and `transaction.atomic`; creates `Task(type=EXPIRY, description=_("Geldig tot {datum}: {titel}"), deadline=expires_on, deadline_source=CALCULATED, created_by=None, created_by_ai=False, status=OPEN, document=document)` unless one exists; history `reminder_started` (origin `system`, no actor). Description is title and date only (no document text), at most 500 characters.
- `reminders.on_expiry_changed(document)`: moves the open reminder's deadline; ends it (deletes the open system task, history line) when the date is cleared; creates nothing by itself (the job decides); when the reminder was completed and the date moved later, the job may make a new one.
- `Task.Type.EXPIRY = "expiry"` ("Verloopt" / "Expires"), additive; migration for the choice and the partial unique index `tasks_task_one_reminder` on `document_id` where `type = 'expiry' AND status = 'open'` (letters' `renew` and `calculated` tasks are untouched).
- Job `start_expiry_reminders_job` (Procrastinate periodic, daily at 04:10 Europe/Amsterdam; no payload): per workspace, enters `workspace_context`, short transactions, selects `kind = document, status = done, trashed_at is null, expires_on - remind_days <= today` without an open reminder, batch of at most 500 per run; no external call.
- Source test: `start_reminder` and `on_expiry_changed` are the only code that creates or deletes a task of type `expiry`; `review._write_confirmation` stays the only other task creator.

- [ ] **Step 1: Failing tests:**

```python
def test_reminder_appears_when_the_lead_time_starts_and_not_before(): ...    # freeze time: day before lead, day of lead
def test_reminder_is_one_task_even_if_the_job_runs_twice_or_races(): ...     # the partial unique index
def test_a_letters_renew_task_is_not_blocked_or_touched_by_the_index(): ...
def test_reminder_has_no_creator_no_ai_flag_no_assignee_and_a_system_history_line(): ...
def test_reminder_shows_in_mijn_taken_for_the_documents_owners_and_nobody_else(client): ...
def test_changing_the_date_moves_the_open_reminder_and_clearing_it_ends_it(): ...
def test_a_completed_reminder_is_not_recreated_for_the_same_date(): ...
def test_a_trashed_document_makes_no_reminder_and_its_task_hides_with_it(): ...
def test_reminder_never_changes_letter_status_or_counts_the_document_as_waiting(): ...
def test_description_holds_only_title_and_date(): ...
def test_source_test_names_the_new_task_writers(): ...
```

- [ ] **Step 2:** Push → FAIL. **Step 3:** Implement. **Step 4:** Push → PASS.
- [ ] **Step 5:** Commit `feat(tasks): an end date becomes a reminder task`.

### Task 9: "Bewaren tot" in `check_removal`

**Files:**
- Modify: `backend/apps/documents/retention.py`, `backend/apps/documents/trash.py` (only to surface the warning), `backend/apps/documents/purge.py`, `backend/apps/documents/api.py` (`trashDocument` answer)
- Create: `backend/tests/test_documents_retention.py`

**Interfaces:**
- `check_removal(document, *, step)`: `retain_until is None` or `retain_until < today` (Europe/Amsterdam) → `RemovalVerdict()`; `step == "trash"` → `allowed=True, warning=_("Bewaren tot {datum}. Weet je het zeker?")`; `step == "purge"` → `allowed=False, warning=_("Dit document moet bewaard worden tot {datum}. Haal de datum weg om het nu te wissen.")`.
- `trashDocument` answers the warning in a new `TrashOut.warning: str = ""` when the move was allowed with one (the clients show the question *before*, from `retain_until` on the document; the field is for logs of an older client). `purgeDocument` and `emptyTrash` answer 409 `document_retained` for a retained document (empty trash skips it and says how many stayed in a new `retained: int = 0`); `purge.mark_expired` leaves it in the trash.

- [ ] **Step 1: Failing tests:**

```python
def test_trash_is_allowed_with_a_warning_before_the_date(client): ...
def test_purge_now_and_empty_refuse_before_the_date(client): ...              # 409 document_retained; empty trash reports retained count
def test_expiry_job_skips_a_retained_document_and_purges_it_after_the_date(): ...
def test_clearing_the_date_allows_the_purge(client): ...                      # via updateDocumentDetails
def test_no_date_changes_nothing_for_letters_and_documents(): ...
def test_retention_message_names_no_content(): ...
```

- [ ] **Step 2:** Push → FAIL. **Step 3:** Implement; `make openapi`, `pnpm gen:api`. **Step 4:** Push → PASS.
- [ ] **Step 5:** Commit `feat(documents): a retention date stops a permanent delete`.

### Task 10: Full export in an open format

**Files:**
- Create: `backend/apps/documents/pdf.py` (minimal PDF writer for JPEG pages), `backend/tests/test_documents_pdf.py`, `backend/tests/test_documents_export_v2.py`
- Modify: `backend/apps/documents/export.py` (`EXPORT_VERSION = 2`, fields, PDFs), `backend/apps/documents/member_export.py`, `backend/apps/documents/api.py` (`createMyExport` + `scope`), `backend/apps/documents/export_files.py` only if a path function is needed (guarded functions only), `docs/DECISIONS.md` (AR10), `docs/api/openapi.json`

**Interfaces:**
- `pdf.write_pdf(pages: Iterable[bytes], out: BinaryIO) -> None`: one JPEG per page (DCT embedded, page size from the JPEG header at 144 dpi), streamed, pure, no file written.
- Export layout: `LEESMIJ.txt`, `brievie-export.json` (`version: 2`), `documents/<year>/<date>_<slug>_<id8>.pdf`; slug from sender or title (ASCII, at most 60 characters, the id8 makes it unique). JSON per document: `id`, `kind`, `title`, `sender`, `document_type`, `document_date`, `expires_on`, `retain_until`, `summary`, `ocr_text`, `source`, `created_at`, `status`, `persons` (name, id), `labels`, `tasks`, `history` (kind, date), `pdf`, `original` (null until INT).
- `createMyExport` body + `scope: str = "mine"` (`mine`, `all`); `mine` obeys `allow_member_export` as today, `all` does not (AR13); `all` is `auth=session_auth`, admin only (403 `not_admin`), and includes only what `export_context(reader=admin)` lets the admin read (no private letter the admin may not read); an audit row `export_created` with `scope` and the count (existing code; value keys of the closed list).
- `LEESMIJ.txt` (Dutch, B1) says what the files are, how to open the JSON in a spreadsheet, that private post the admin may not read is not in it, and that it is not a back-up.

- [ ] **Step 1: Failing tests:**

```python
def test_pdf_has_one_page_per_jpeg_and_opens_in_pypdf_free_check(): ...      # header, xref, page count by parsing our own output
def test_export_v2_json_has_the_documented_fields_and_both_kinds(): ...
def test_member_export_holds_only_what_the_member_reads(client): ...
def test_admin_export_excludes_private_letters_the_admin_cannot_read(client): ...  # state E off, A off
def test_all_scope_is_session_only_and_admin_only(client): ...                    # a device token gets 401, a member 403
def test_pages_are_opened_one_at_a_time_and_nothing_unsealed_is_written(tmp_path): ...
def test_files_go_only_through_export_files_guarded_functions(): ...              # source test
def test_text_in_the_export_is_the_masked_text(): ...
def test_mine_obeys_allow_member_export_and_all_does_not(client): ...   # AR13
def test_v1_readers_still_find_their_fields(): ...                                # additive: version 2 keeps every v1 key
```

- [ ] **Step 2:** Push → FAIL. **Step 3:** Implement; `make openapi`, `pnpm gen:api`. **Step 4:** Push → PASS.
- [ ] **Step 5:** Commit `feat(export): PDF per document and an export of everything the admin may read`.

### Task 11: Leak tests for every new path

**Files:**
- Create: `backend/tests/test_leak_archive.py`

- [ ] **Step 1: Tests** (WZW `office()`: E off, departments on, a private letter and a private own document, a manager, a department, a viewer; every member as the actor):

```python
def test_archive_list_and_facets_show_only_what_the_actor_may_read(client): ...     # equal to visible_documents counts, per actor
def test_a_hidden_documents_title_sender_or_reminder_never_appears(client): ...      # list, facets, search, expiring, tasks, export
def test_private_own_document_is_seen_only_by_its_readers_and_the_uploader(client): ...
def test_private_false_in_the_request_does_not_widen_a_private_workspace(client): ...
def test_document_for_a_department_is_seen_by_its_members_only_when_the_rules_say(client): ...
def test_reminder_task_follows_the_task_policy(client): ...
def test_details_update_on_a_hidden_document_is_404(client): ...
def test_export_scopes_do_not_leak_across_actors(client): ...
def test_trashed_documents_are_out_of_every_archive_read(client): ...
def test_emergency_access_reads_but_cannot_write_a_document(client): ...
```

- [ ] **Step 2:** Push → read the `backend` job. A failure here is a bug in Tasks 4-10, fixed there, not in the test.
- [ ] **Step 3:** Commit `test(archive): every new read and write path under the rights rules`.

### Task 12: Measured at 100,000 documents

**Files:**
- Modify: `backend/tests/perf/data.py` (20 % own documents, 5 % with `expires_on`, years spread over 8, 400 distinct senders, titles), `backend/tests/perf/test_visibility_at_scale.py` (`list_archive`, `list_archive_kind_type`, `list_archive_person_year`, `archive_facets`, `search_with_title`, `expiring`), `docs/DECISIONS.md` (the table)

- [ ] **Step 1:** Add the lists, and keep the existing ones in the run (`menu_counts`, `list_my_post`, `list_my_post_unread`, `list_all_unread`, `lingering_letters`, Inbox lists): they changed (Task 2). Push; run `visibility-perf` on demand; read the job.
- [ ] **Step 2: Decide.** Every median at most 250 ms (the slowest of the four actors, jit off) → record the table in DECISIONS. Over → in this order: the senders facet from a partial index on (`workspace`, `sender`) `WHERE status = 'done'`; the `(workspace, -letter_date)` partial index for `year`; drop the per-type counts and cap senders at 20; still over → **stop and report the numbers to the owner** (no redesign without his choice). Also record the one-off time of the Task 3 migration at 100,000 documents.
- [ ] **Step 3:** Commit `test(perf): the archive at 100,000 documents`.

### Task 13: Status on Archief (**needs LS**)

**Files:**
- Modify: `backend/apps/tasks/letter_state.py` (LS), `backend/apps/documents/services.py`, `backend/tests/test_documents_archive_status.py`

**Interfaces:**
- `letter_state.sync` and the three cached columns apply to `kind = letter` only; for a document `letter_status = ""`, `overdue = False`, `open_task_count = 0` (the reminder is not a waiting letter). `listDocuments` filter `letter_status` applies on top of `archived`; `status-counts` counts letters only.
- The nightly repair (`verify_visibility_job`) skips documents.

- [ ] **Step 1: Failing tests:** `test_document_has_no_letter_status_even_with_an_open_reminder`, `test_archived_with_letter_status_handled_lists_only_handled_letters`, `test_status_counts_ignore_documents`, `test_repair_skips_documents`.
- [ ] **Step 2:** Push → FAIL. **Step 3:** Implement. **Step 4:** Push → PASS. **Step 5:** Commit `feat(archive): status filter for letters, none for documents`.

### Task 14: Files from the browser and what INT adds to the export (**needs INT**)

**Files:**
- Modify: `backend/apps/documents/api.py` (`uploadDocumentFile` form fields), `backend/apps/documents/export.py` (originals and text layer), `backend/tests/test_documents_upload_file_own.py`

**Interfaces:**
- `uploadDocumentFile` form fields mirror section 6.2 (`kind`, `title`, `document_type`, `document_date`, `person_ids` (repeated), `label_ids`, `expires_on`, `remind_days`, `retain_until`, `private`), all defaulted; the same `own.validate` and `own.create_document`; INT's `prepare` step then renders pages and OCRs (server side), and the document branch of Task 5 continues unchanged.
- The export adds `original` (the file as it came in, opened and written once) and uses INT's text layer for the PDF when present.

- [ ] **Step 1: Failing tests:** `test_file_upload_creates_an_own_document_with_the_same_rules`, `test_file_upload_without_new_fields_is_a_letter`, `test_ocr_failure_ends_done_with_failure_code_for_a_document`, `test_export_holds_the_original_next_to_the_pdf`.
- [ ] **Step 2:** Push → FAIL. **Step 3:** Implement; `make openapi`, `pnpm gen:api`. **Step 4:** Push → PASS. **Step 5:** Commit `feat(intake): own documents from the browser`.

### Task 15: Scan folder for documents (**needs INT**, later slice)

**Files:** to be fixed against INT's built layout (`intake/` app, `InboxFolder`); the design here is only the rule.

**Interfaces (rule):**
- Files in a subfolder `archief` of a workspace folder (or of a person's folder) become `kind = document`: title from the file name, type `other`, person from the folder (none for the workspace folder), `done` at once, no tasks, the INT reserved-name rules apply (`archief` joins `mislukt` as a reserved name that is never a person). Switch per workspace, off by default, audit `workspace_setting_changed`. `scanned_by` is null for such a file, so `confirmed_by` stays null (AR3) and the history line has origin `watch_folder` and no actor.

- [ ] **Steps:** failing tests first (file in `archief/` becomes a document, a person named "archief" is refused, a file elsewhere stays a letter), implement, CI, commit `feat(intake): scan folder for documents`. May be dropped without touching Tasks 1-14.

---

## Web (`web/`)

Every task: tests first (Vitest, queries by role), strings in Lingui `t` (Dutch source, then `pnpm extract` and the English line from S 11.4), `pnpm lint`, `pnpm typecheck`, `pnpm test`, `pnpm build` green, `pnpm gen:api` after an API change. Start from Task 7's regenerated `schema.d.ts`.

### Task W1: Archief replaces Alle post

**Files:**
- Modify: `web/src/routes/post.tsx` → `archive.tsx` (git mv), the router (`/w/$id/archief`; `/w/$id/post` redirects with its search params kept: `persoon`, `label`), the side menu (entry name, route), `web/src/routes/post.test.tsx` → `archive.test.tsx`, `web/src/routes/shell.test.tsx`, locale catalogues
- Create: `web/src/components/archive-filters.tsx`, `web/src/components/archive-filters.test.tsx`, `web/src/api/archive.ts` (query hooks for `listDocuments`, `getArchiveFacets`)

**Interfaces:**
- Page "Archief": heading, count line, filter row (Soort, Type, Van wie, Afzender, Jaar, Label, "Loopt af"; Status after Task 13), search box (existing `searchDocuments`), list with title or sender, type chip, "Van wie", date, "Geldig tot" badge ("Verlopen" in words and the warning style), kind icon. URL: `?soort=documenten&type=&persoon=&afzender=&jaar=&label=&loopt-af=true`. Filter options come from `getArchiveFacets` (counts shown); one facets request per page.
- Empty state sentence (S 11.2). `usePageTitle(t\`Archief\`)`.
- The redirect keeps `/post/$documentId` (a document's page) working for both kinds.

- [ ] **Step 1: Failing tests:** `shows documents and reviewed letters, no unreviewed letter`; `filters write to the URL and narrow the list`; `facet counts come from the server`; `/post redirects to /archief keeping persoon and label`; `menu says Archief and has no Alle post`; `empty archive shows the empty sentence`; `an expired document is marked in words`.
- [ ] **Step 2-4:** Implement, run. **Step 5:** Commit `feat(web): Archief replaces Alle post`.

### Task W2: A document's page

**Files:**
- Modify: `web/src/routes/document.tsx`, `web/src/routes/document.test.tsx`, locale catalogues
- Create: `web/src/components/document-details-form.tsx` (+ test)

**Interfaces:**
- For `kind = document`: no review, task or inbox blocks; title (inline edit), type, Datum, Van wie, Labels, "Geldig tot" with "Herinner me … vooraf", "Bewaren tot", summary with "Door AI gemaakt", "Brievie stelt voor" proposals with `[Overnemen]`, "Wie kan dit zien?", history, "Dubbel?" block, retry banner "De tekst is niet gelezen" with "Opnieuw proberen". Saves through `updateDocumentDetails`. "Naar de prullenbak" asks the retention question first when `retain_until` is today or later (S 7.2). A viewer sees everything read-only.

- [ ] **Step 1: Failing tests:** `a document shows no review or task blocks`; `editing the title calls updateDocumentDetails`; `clearing the end date sends clear_expires_on`; `proposals apply only on a tap`; `trash asks the retention question`; `viewer sees no edit controls`; `failure banner offers retry`.
- [ ] **Step 2-4:** Implement, run. **Step 5:** Commit `feat(web): the page of an own document`.

### Task W3: "Document toevoegen" (**needs Task 14**)

**Files:** Create `web/src/components/add-document-dialog.tsx` (+ test); modify `archive.tsx` (button), locale catalogues.

**Interfaces:** file picker and drop zone (PDF, JPEG, PNG), one form per file (S 6.1): Titel (prefilled from the file name), Soort document, Datum, Van wie (default the member's own person), Labels, Geldig tot with Herinner me, Bewaren tot, the switch "Alleen voor deze persoon" **preselected for `SENSITIVE_TYPES`** with the live "Wie kan dit zien?" sentence from `getDocumentAccess`-style data, button "Bewaren". Posts `uploadDocumentFile` with a new `client_id` per file. Hidden for viewers.

- [ ] **Step 1: Failing tests:** `title is required`; `choosing identity preselects the private switch and the member can turn it off`; `each file posts once with its own client id`; `a viewer sees no button`; `error codes show Dutch messages`.
- [ ] **Step 2-4:** Implement, run. **Step 5:** Commit `feat(web): add a document to the archive`.

### Task W4: Export screens

**Files:** Modify the export page (member: "Mijn archief exporteren") and the admin page ("Alles exporteren", with the one-sentence limit about private post), tests, catalogues.

- [ ] **Step 1: Failing tests:** `member sees Mijn archief exporteren and starts scope mine`; `admin sees Alles exporteren with the explanation`; `a member never sees the admin scope`; `finished export offers one download and says it expires`.
- [ ] **Step 2-4:** Implement, run. **Step 5:** Commit `feat(web): export the archive`.

### Task W5: Texts, glossary and the release notes

**Files:** `docs/GLOSSARY.md` (both tables; the "Alle post" row changed as in S 11.4), `web/src/locales/*`, `backend/locale/en`, `CHANGELOG.md`, `docs/DECISIONS.md` (record "Archive": the owner's answers B1-B5, rulings AR1-AR12, the measured table of Task 12).

- [ ] **Step 1:** `make messages` (twice, for the `.mo`) and `pnpm extract`; the missing-English tests must pass. **Step 2:** `pnpm test`, `make lint`, `make typecheck`. **Step 3:** Commit `docs(archive): glossary, changelog and decisions`.

---

## iOS (`Brievie_ios`, a separate repository and plan; the host tasks 4, 6, 7, 9 come first)

Same rules as the host plan: tests first (XCTest), strings in the String Catalog with the English from `docs/GLOSSARY-en.md` (copy the S 11.4 rows both ways), no invented names, every new JSON field optional on decode (`kind` missing means `letter`).

- **I1. Models and client.** `Document` gains `kind`, `title`, `expiresOn`, `retainUntil`, `remindDays`, `reminderTaskId`; `DocumentKind`, `DocumentType` (five new cases; an unknown value decodes to `other`); `listDocuments` parameters; `getArchiveFacets`; `updateDocumentDetails`; `createDocument` metadata fields. Tests: decoding an old payload, an unknown type, a document payload.
- **I2. Archief screen** under **Meer** (replacing the "Alle post" row), the same list as the web with the filters as a sheet (Soort, Type, Van wie, Afzender, Jaar, Label, Loopt af), facets from the server, the search field. The tab bar does not change (S 11.1, B5). Tests: row mapping, filter state, empty state.
- **I3. "Document toevoegen".** A **+** on the Archief screen opens the scanner (the existing flow, client OCR), then the sheet "Dit document bewaren" (S 6.1; sensitive types preselect "Alleen voor deze persoon" with the "Wie kan dit zien?" sentence), then `createDocument` with `kind = document`. The round scan button on Inbox and Mijn post stays letters only. Tests: required title, preselected private switch, one `client_id` per scan, retry after a lost answer finds the same document.
- **I4. The document's detail screen.** The existing detail screen without task and review blocks; edit title, type, dates; proposals with "Overnemen"; the retention question before "Naar de prullenbak"; the retry banner. Tests: view state per kind.
- **I5. Reminders and texts.** The reminder task already reaches the app through `listMyTasks` and the local reminders (`kind = document` tasks need no code); verify with a fixture. String Catalog, English, accessibility labels in words ("Verlopen", "Geldig tot"), VoiceOver order of the row.

---

## Self-review checklist (before handing over)

- Spec coverage: S 4 → Tasks 1-3; S 5 → Tasks 7, W1; S 6 → Tasks 4, 5, W3, I3, 14; S 7.1 → Task 8; S 7.2 → Task 9; S 8 → Tasks 2, 4, 11; S 9 → Task 10, W4; S 10 → Tasks 4, 6, 7, 9, 10; S 11 → W1-W5, I1-I5; S 12 → Task 12; S 13 → Review Focus and Task 11; S 14 → "What depends on what"; Besluiten B1-B5 → W1 (B1), Task 7 (B2), Tasks 4 and W3 (B3), Task 9 (B4), I3 (B5).
- Counts: 20 numbered tasks (15 host: 12 independent and 3 dependent; 5 web, one of them dependent) plus 5 iOS parts in their own section.
