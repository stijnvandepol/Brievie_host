# Archief: every reviewed letter, and documents of your own, findable in one place

Date: 2026-11-24. Covers `Brievie_host` (backend, `web/`) and, as a short list of fields and
screens, `Brievie_ios`. Design only; nothing here is built. Plan:
`docs/superpowers/plans/2026-11-24-host-archief.md`.

**Neighbours.** Navigation, Mijn post, Ongelezen and Blijft liggen (`2026-11-21-navigatie-en-persoonlijke-inbox-design.md`,
"NAV"; **on `main` since PR #44**); the letter status (`2026-11-21-briefstatus-design.md`, "LS";
**docs only**, branch `docs/letter-status-design`); intake without an iPhone
(`2026-11-20-inname-zonder-iphone-design.md`, "INT"; **docs only**, branch
`docs/intake-and-2fa-design`); the rights design (`2026-11-19-zichtbaarheid-postkamer-inrichting-design.md`,
"RS"); trash, duplicates, search (their sections in DECISIONS). Research: `.superpowers/onderzoek/reports/Brievie vergeleken met
Paperless-ngx.md` ("PL"; local, not in the repo).

## 1. Intent

Stijn (24-11-2026): "I also want to be able to use Brievie simply as an archive to keep
documents. Add an archive option. Make sure letters end up there by default, but that people
can also scan in important documents or things themselves, more in the direction of
Paperless."

Two things in one sentence:

1. Reviewed letters are *already* an archive; what is missing is a place that is built for
   looking things up (by person, sender, kind, year, label, text) instead of for working
   through post.
2. Some paper is not post: a contract, a diploma, a passport copy, a warranty receipt, an
   insurance policy, a payslip, a tax return. Nobody has to answer it, so it must skip the
   Inbox and carry no tasks, but it still needs a name, a date, an owner and (sometimes) an
   end date.

PL's advice stands: Brievie is the post process first; the archive is the quiet end of it,
not a second product. Take from Paperless what a paper archive needs (a document kind and
types, a date, an expiry, a full export); leave out what makes Paperless hard (section 15).

## 2. What exists (verified on `main`, `b43c92b`; navigation merged)

- `documents.Document`: `status` (`processing`, `needs_review`, `done`, `failed`), `source`
  (`scan`, `upload`, `mail`, `watch_folder`; today only `scan`), `sender`, `document_type`
  (14 types, among them `policy`, `payslip`, `contract`, `receipt`, `annual_statement`),
  `letter_date`, `summary`, `visibility`, `addressee_ids` (GIN), `trashed_at`,
  `addressee_opened_at`, `search_vector` (a **generated column** over `sender` A, `summary` B,
  `ocr_text` C, Dutch). There is no title and no expiry.
- Partial indexes on `status = 'done'`: `documents_document_unassigned` (Inbox "Naam
  kiezen"), `documents_document_unopened`, `documents_document_opened` ("Blijft liggen").
  `_in_state(UNASSIGNED)`, `reading.unread_q`, `lingering` and `my_post_filter` read them.
- Intake today: `createDocument` takes client-OCR'd page images (`UploadMetadataIn`: `client_id`,
  `pages`). `create_document_from_upload` stores sealed pages, marks the uploader's own read
  mark, queues `process_document`. INT (not built) adds `uploadDocumentFile` (multipart file,
  server render and OCR, originals) and the scan folder.
- Pipeline (`pipeline.steps`): runs only on a `processing` document under its row lock; ends in
  `needs_review` unless the autopilot confirms. Duplicate detection runs on arrival. Tasks have
  one creator, `documents.review._write_confirmation`.
- `Folder` (model, `listFolders`, `createFolder`) exists; SPEC section 8 still lists
  `folder_id`. No page uses folders; no document points to one.
- `retention.check_removal(document, step)` is asked at trash, purge-now, empty and expiry;
  it allows everything. SPEC 8 reserves `bewaren_tot`.
- Export: `documents/export.py` builds a member's own post as a ZIP with `brievie-export.json`
  and `LEESMIJ.txt` (`EXPORT_VERSION = 1`); the docstring promises "part E: PDF per letter and
  the workspace export". Files only through `export_files.py`.
- Web: "Alle post" (`routes/post.tsx`, path `/post` kept for bookmarks, filters person and
  label), "Mijn post" (`my-post.tsx`), a letter page (`document.tsx`), `search.tsx`.
  Menu per NAV 4.1: Inbox, Mijn post, Mijn taken, Alle post, Labels, Mensen, Afdelingen,
  Automatisering, Prullenbak, Instellingen. iPhone: tabs Inbox, Mijn post, Taken, Meer (Alle
  post, Labels, Prullenbak, Instellingen).
- `visibility-perf` (`.github/workflows/visibility-perf.yml`): median at most 250 ms per list at
  100,000 documents; every list starts at `visibility.visible_documents`.

## 3. Goals, non-goals, anti-goals

- Goal: one page, "Archief", that lists every reviewed letter and every own document the
  member may see, with archive-style filters and the existing full-text search.
- Goal: a member adds an own document straight to the archive (iPhone scan now; browser upload
  when INT lands): no Inbox, no check, no AI tasks, but title, kind of document, date, person,
  labels, optional end date and "bewaren tot".
- Goal: an end date ("Geldig tot") becomes a task in "Mijn taken" in time to act on it.
- Goal: a full export in an open format (zip with the files and a JSON file), so nobody is
  locked in (PL item 1).
- Non-goal: anything in section 15.
- Anti-goal: an own document that is more visible than the existing rights make it; a count,
  facet, filter or reminder that reaches someone who may not read the document; an own
  document that counts as post that waits ("Ongelezen", "Blijft liggen", Inbox); anything that
  files, tags, deletes or moves documents on its own.

## 4. The model: one `Document`, a `kind`

**Decision: one table.** Everything that makes a letter findable and protected (sealed pages,
OCR, BSN mask, search, visibility, readers, trash, duplicates, labels, addressees, history,
purge, export, backup, RLS) is on `Document`. A second table would copy every one of those
seams (and every leak test of "Wie ziet wat"). An own document is a `Document` that is
*not post*: `kind = "document"`.

New columns on `documents_document` (all additive):

| Column | Type, default | Meaning |
|---|---|---|
| `kind` | char(10), default `letter` | `letter` (post: reviewed, may have tasks) or `document` (own document). UI word "Soort". Never changes after creation (no "turn a document into a letter" in v1). |
| `title` | char(200), default `""` | The name of an own document ("Paspoort", "Polis opstal"). Required for `kind = document` (422 `validation_error` when blank), empty for letters (a letter is shown by sender and type as now). |
| `expires_on` | date, null | "Geldig tot": the end date of a document (`kind = document` only). |
| `remind_days` | smallint, default 30 | How many days before `expires_on` the reminder task appears (choices 7, 30, 60, 90). |
| `retain_until` | date, null | "Bewaren tot": a hint that stops a permanent delete (section 7.2). Both kinds. |

Not new: **the document date is `letter_date`** (API name `letter_date` stays; the UI says
"Datum" for a document and "Datum brief" for a letter). **The person it belongs to is
`DocumentAddressee`** (a person or department; UI "Van wie"). **Labels are `DocumentLabel`.**
**`source`** keeps meaning how it came in (`scan`, `upload`, later `watch_folder`); `kind` says
what it is. No `folder_id` (see "Folders" below). No free-form custom fields (section 15).

**Types.** `document_type` stays one field. Own documents choose from a small closed list
`DOCUMENT_TYPES`; letters keep the list they have (`LETTER_TYPES`, what the AI may return; the
AI's schema is not widened). `Document.Type` gains five values, additive:
`identity` ("Identiteitsbewijs"), `diploma` ("Diploma"), `warranty` ("Garantiebewijs"),
`tax_return` ("Belastingaangifte"), `medical` ("Medisch"). `DOCUMENT_TYPES` = `identity`,
`diploma`, `warranty`, `tax_return`, `medical`, `contract`, `policy`, `payslip`,
`annual_statement`, `receipt`, `information`, `other`. A type outside the list of the
document's kind is 422 `validation_error`. Why closed: a free type list is Paperless' "tags
for everything" (PL); types stay few so the filter stays one tap. Labels are the open
dimension.

**Folders: not added.** Person + type + year + label cover what a folder tree does for paper
at home or in a small office, and a tree needs moving, renaming and rights per folder (PL:
"no storage paths"). The old `Folder` model and `listFolders`/`createFolder` stay as they
are (the API is additive; nothing is removed); no `folder_id` is added to `Document`, and the
SPEC line is struck in the plan (Task 1). If a real need shows (a kantoor wants a dossier
per client), the answer is a *label with a person*, or the client-dossier idea from
`onderzoek-klantdossier`, not a folder.

**Database defaults.** The model keeps Python `default=` for every new field (no `db_default`:
Django would insert with `RETURNING`, which reads the new row through the SELECT policies;
PB decision 5, DECISIONS 2026-11-20 "Switches as fields first"). The migration adds each
column `NOT NULL DEFAULT …` for the existing rows and then `ALTER COLUMN … SET DEFAULT` by
`RunSQL` so a raw insert (a seed, the perf data, a restore script) keeps working, exactly as
the duplicate columns (`tests/test_duplicates_db_defaults.py`). A test per new column.

**Search.** `title` is searched by the user, so `search_vector` must contain it: a generated
column cannot be altered, so the migration drops and re-adds it (`title` and `sender` weight A,
then `summary` B, `ocr_text` C) and rebuilds `documents_document_search`. That rewrites the
table once and rebuilds the GIN index; at 100,000 documents it is minutes, at home seconds,
and it needs the usual `make migrate` window. It is a plan task with its own measurement
(section 12) and a line in the release notes. Alternative rejected: searching the title through
a second trigram index only (no rewrite) gives no stemming and ranks the title below text.

**Indexes that must learn `kind`.** The three partial indexes on `status = 'done'` serve letters
that wait; an own document with no person would otherwise fall into Inbox "Naam kiezen", and
an own document would count as "not opened by an addressee". They are recreated with
`kind = 'letter'` in their condition, and `_in_state`, `unread_q`, `lingering` and
`my_post_filter` add the same condition (section 8). A new index serves the archive list:
`documents_document_archive` on (`workspace`, `-created_at`) `WHERE status = 'done' AND
trashed_at IS NULL`, and `documents_document_letter_date` on (`workspace`, `-letter_date`)
`WHERE status = 'done'` for the year filter (the filter works without it; the index is in the
plan only if the measurement asks for it).

## 5. "Archief": what it is, and Alle post

**Definition (v1): the archive is the reviewed documents, `status = 'done'`, of both kinds.**
Nothing is moved or copied; it is a view over the same rows through
`visibility.visible_documents`. A letter is in the archive from the moment it is reviewed
(confirmed by a person or the autopilot), also while it has open tasks. The recommendation
asked for "once handled"; this design is wider on purpose (Besluit B2): a letter with an open
task is the one people look up ("where was that reminder?"), and hiding it until it is handled
makes the archive worse at its one job. The *status* separates them: once LS is merged, the
page has the filter "Afgehandeld / Openstaand / Wacht op reactie" and a status badge, and
"Afgehandeld" is one tap. Letters not yet reviewed (`processing`, `needs_review`, `failed`)
are Inbox, never archive. Own documents are in the archive at once.

**Relation to Alle post: replace.** Two lists that are nearly the same is the menu problem
the owner wants to avoid, and "Alle post" is *already* the archive-shaped page (everything the
member sees, filter by person and label) with a name that suggests incoming mail. So:

- The menu entry **Alle post** becomes **Archief** at the same place (after Mijn taken). Route
  `/w/$id/archief`; `/w/$id/post` redirects there with its query kept (`?persoon=` and
  `?label=` mean the same) so bookmarks survive; `/post/$documentId` (a letter's page) keeps
  its path and works for both kinds.
- The page keeps what Alle post has (person, label, automatic) and gains the filters below.
  Unreviewed letters stop showing there (they are in the Inbox, where the member can already
  see them); that is the only thing "Alle post" showed that "Archief" does not, and it is
  stated in the release notes.
- The iPhone "Meer" row **Alle post** becomes **Archief** (same screen, same filters).
- "Alle post" / "All mail" stays in the GLOSSARY as "Alle post, the old name of Archief" only
  until the first release that ships this; then the line is replaced (section 11.4).

**Filters ("Zoeken en filteren").** One row of controls and the existing search box:

| Filter | Param (`listDocuments`) | Source |
|---|---|---|
| Soort: Brieven / Documenten | `kind` | `Document.kind` |
| Type (one) | `document_type` | `document_type` (counts per type shown) |
| Van wie (one person or department) | `person_id` | GIN `addressee_ids` (exists) |
| Afzender (one, chosen from a list) | `sender` | exact match on a name from the facet list |
| Jaar | `year` | `letter_date` year; falls back to `created_at` year when `letter_date` is null |
| Label | `label_id` | exists |
| Status (after LS) | `letter_status`, `overdue` | LS section 9 |
| "Loopt af" (expires within 90 days, or past) | `expiring` | `expires_on` |
| Zoeken | `searchDocuments` (POST, never a `q` on a GET) | `search_vector` incl. title |

`sender` is a choice from a list, never typed free text in a query string: the list is
`listArchiveFacets` (below). Free text goes only through `searchDocuments`, whose body takes
the same filters (additive fields with defaults) so "search inside Diploma" works; the search
rules (no logging, no echo, BSN stripped from the term) are unchanged.

**Facets.** `getArchiveFacets` (GET `/workspaces/{id}/documents/archive-facets`): counts of
kinds, types, years, and the top 50 senders, all over `visible_documents(membership)` with
`status = 'done'`, in one pass with filtered aggregates (like `inbox_counts`). Senders are
returned by count; the rest are reached through search. Nothing outside the member's sight is
counted (a count of hidden matches is never returned, RS).

**Order and size.** Newest first by `created_at` (the 200 cap and its index, as every list).
Sorting by document date is a later option; the year filter covers the common need.

## 6. Adding an own document

### 6.1 The flow (what the member does)

- **iPhone:** the Archief screen has a **+** ("Document toevoegen"). It opens the same
  scanner as post; after the scan one sheet "Dit document bewaren" with: Titel (required,
  prefilled "Scan 24 nov 2026"), Soort document (the `DOCUMENT_TYPES` list, default
  Overig), Datum (default today), Van wie (default the member's own person; any person the
  member may name), Labels (optional), Geldig tot (optional) with "Herinner me" (7, 30, 60 or
  90 days before), Bewaren tot (optional), a switch "Alleen voor deze persoon" (section 8). One
  button: "Bewaren". The round scan button on Inbox and Mijn post stays letters only (Besluit
  B5).
- **Web (needs INT, `uploadDocumentFile`):** Archief gets "Document toevoegen" (a file picker
  and drag and drop: PDF, JPEG, PNG), then the same form. Several files are several
  documents, one form each, in a short list (INT 1.3 "one file is one letter" holds: one
  file is one document).
- A member with the role *viewer* ("Alleen lezen") cannot add. Anyone who may confirm a letter
  may add a document for any person they may name in a review (the same rule, no wider).

### 6.2 What the server does

`createDocument` (iPhone, client-OCR'd pages) and INT's `uploadDocumentFile` (browser) both take
the new fields in their metadata, all with defaults: `kind: str = "letter"`, `title: str = ""`,
`document_type: str = ""`, `document_date: date | None = None`, `person_ids: list[UUID] = []`,
`label_ids: list[UUID] = []`, `expires_on: date | None = None`, `remind_days: int = 30`,
`retain_until: date | None = None`, `private: bool = False`. An older app sends none of them and
gets a letter, as today. `kind = "document"` is validated in one function,
`documents.own.validate` (title, type in `DOCUMENT_TYPES`, dates sane: `expires_on` not before
`document_date`, `remind_days` in the list, persons exist and are active, labels exist).

The pipeline gets a **document branch** (`pipeline.steps`, one `if document.kind`):

1. As for a letter: OCR text is BSN-masked before storage; the AI step runs only under the
   existing consent rules (`AISettings`, consent per address) and the masked text.
2. **Nothing is proposed as a task and the autopilot is not asked.** The AI (when on) may
   *propose* for an own document: a title when none was given, a type, the document date, an
   end date, labels. They are `Extraction`/`DocumentSuggestion` rows shown on the document's
   page as "Brievie stelt voor: Geldig tot 12 maart 2031 [Overnemen]" and applied only by a
   tap (the "proposal, not automatism" rule). A summary is stored only when the member leaves
   "Samenvatting" on "Laat Brievie een samenvatting maken" (the form's switch, off in Zakelijk
   unless the workspace's summary setting says on), `summary_by_ai = true`.
3. Duplicate detection runs on arrival as for a letter, but **only against documents of the
   same kind**: a pair of a letter and a document is never made (a merge would trash one of
   two different things). A pair of two own documents shows the existing "Dubbel?" block.
4. The document becomes **`done`** at the end of processing, not `needs_review`, with
   `confirmed_by = scanned_by`, `confirmed_at = now`, `confirmed_by_ai = false`. This is true,
   not a shortcut: the member filled in title, type and person in the form; that *is* the
   confirmation, made by a person, who is recorded. (Contrast "never record a user for what
   nobody did": the AI's proposals above are not applied, so nothing automatic carries a
   user.) The history gets one line, `document_added` (origin `user`, actor the member).
5. **Failure.** A document whose processing fails still becomes `done` (the member already
   named it, so it is findable by title and person) with `failure_code` set and no text; its
   page says "De tekst is niet gelezen" with "Opnieuw proberen" (`retryDocumentProcessing`
   accepts a `done` document of kind `document` with a `failure_code`). It never enters the Inbox: the
   Inbox is for post.

`kind = "document"` rows never get tasks from review (`confirmDocument` on one is 409
`document_not_reviewable`) and `createTask`-like paths do not exist for them. The only task a
document can have is the expiry reminder (section 7.1).

### 6.3 Why not "just a letter with a flag"

A letter flows through review because Brievie *guesses* sender, type, tasks and person and a
human must agree before a deadline is trusted. For an own document the human already
supplied the facts at upload, and nothing in it is a deadline the AI found. Forcing it through
the Inbox would put a diploma in the same queue as an unpaid invoice.

## 7. Two small extras a paper archive needs

### 7.1 "Geldig tot" becomes a task in "Mijn taken"

- `expires_on` is set at upload or later (the document's page, "Geldig tot"; any editor).
- A daily job (`documents.jobs.start_expiry_reminders`, Procrastinate periodic, like the
  nightly `verify_visibility_job`: no id payload, it enters `workspace_context` per workspace in
  short transactions) finds documents with `expires_on - remind_days <= today` and no reminder
  task yet, and creates **one** `Task` per document through one function,
  `documents.reminders.start_reminder(document)`: `type = renew`, description "Geldig tot 12
  maart 2031: Paspoort" (title and date, no document text), `deadline = expires_on`,
  `deadline_source = calculated`, `created_by` null, `created_by_ai = false`, no assignee. The
  task follows the existing "Mijn taken" rules (it reaches the owners and managers of the
  document's persons, department rules as for letters); the system does not pick a person.
  "Calculated, no creator" is how the page and the history mark it "Door Brievie gemaakt".
- Idempotent: for a `kind = document` the only `renew` task with `deadline_source =
  calculated` is the reminder; the job looks it up before creating (and a partial unique index
  `tasks_task_one_reminder` on `document_id` for that combination makes a race a harmless
  error). Changing `expires_on` moves the open reminder's deadline (or ends it when the date is
  cleared); ticking it off completes it. A new reminder is made again only when `expires_on`
  changes to a later date after the old one was completed (the document was renewed in place).
- It is **not** a letter that waits: LS's status columns and `letter_state.sync` apply to
  `kind = letter` only, so `letter_status` of a document is `""` and the reminder never makes it
  "Openstaand". The task still counts in "Mijn taken" and in the menu number and goes red when
  late, like any task. The tasks module's source test of task writers lists
  `reminders.start_reminder` as a new, named writer (and `tasks.services`' complete, reopen and
  deadline functions work on it unchanged).
- The task and its history line (`reminder_started`, origin `system`, no actor) are the only
  things the job writes. It sends nothing outside (no mail; the iPhone's local reminders come
  from `listMyTasks` as for every task).
- Renewing is "upload the new document, then end the old reminder"; replacing a document
  with a new version is a non-goal (section 15).

### 7.2 "Bewaren tot" and `check_removal`

`retain_until` (either kind) hooks into the existing seam `retention.check_removal`:

| Step | `retain_until` today or later | Answer |
|---|---|---|
| `trash` (move to the trash, restorable) | allowed, with `warning` "Bewaren tot 1 jan 2031. Weet je het zeker?" | the web and the app show the warning before the move (they read `retain_until` on the document); the server moves it |
| `purge` (purge now, empty the trash) | refused: 409 `document_retained` | the document stays in the trash |
| `purge` by expiry (`purge.mark_expired`, 30 days in the trash) | skipped | it stays in the trash until the date passes, then the normal expiry purges it |

It is a hint with one hard edge: a permanent delete before the date needs the date removed
first. Any editor may change or clear `retain_until` (one history line, `retention_changed`,
with the date, no document text), so nobody is trapped; Brievie does not know the law and does
not pretend to (the SPEC says to have a bookkeeper check defaults). No default retention per
type in v1 (no "7 jaar voor facturen"): an empty field is the default (Besluit B4 for the hard
edge).

## 8. Who sees it

**No new rule and no new default that widens.** An own document is a `Document`; it is seen
exactly as a letter on the same persons is seen: `visibility.visible_documents`, the
workspace's switches (RS 4.1), departments, "Beheerd door" managers, private (`restricted`)
letters with their reader rows, emergency access, `require_editable` on every write. The
uploader sees their own upload through `member_filter`'s existing `scanned_by_id` arm.

- **Home (Thuis) and organisation (Zakelijk) presets are unchanged.** In Thuis, where
  everyone may read all post, an own document is as visible as a letter would be; in
  Zakelijk, where members see their own persons' post, a document "Van wie: Anne" is seen by
  Anne's account, her manager, the sorters and admins by the usual rule, and a document
  "Van wie: Financiën" by that department's members.
- **The sensitive types ask once, visibly.** For `identity`, `medical`, `payslip`,
  `tax_return` the add form shows the switch "Alleen voor deze persoon" **already on**, with
  the plain sentence "Alleen {naam} en wie voor {naam} beheert kunnen dit zien" (the existing
  "Wie kan dit zien?" line of the letter page, computed by `getDocumentAccess` for the chosen
  persons). The member can turn it off before saving. It uses the existing path: the document
  is created `workspace` and `make_private` flips it in the same transaction as the
  creation, `keep_own_access` keeps the uploader reading it. A preselected switch the member
  sees and can change narrows access; it does not widen anything and is not automatic
  (Besluit B3 asks whether to keep it preselected).
- **No other default of its own.** Not "private by default for all documents", not "visible
  to the whole household because it is the archive". `setDocumentVisibility`, shares, emergency
  access and their audit lines work on documents unchanged.
- **Unread and "Blijft liggen" ignore documents.** `reading.unread_q` and `unread_ids` add
  `kind = letter`; `lingering` and its three indexes add it; `addressee_opened_at` is never
  set for a document (`reading` returns early); Inbox states (`_in_state`, `inbox_state`)
  return "sorted" for a document; `menu_counts` therefore cannot count one. `Mijn post` is
  letters only (`my_post_filter` adds `kind = letter`): it is the personal Inbox of *post*;
  an own document of the member is in Archief under "Van wie: ik".
- **Counts, facets and the reminder obey the seam.** `archive-facets`, the year and sender
  lists and `expiring` start from `visible_documents`; the reminder task is visible by the
  task policy, which runs the document policy.
- **The trash and purge** work unchanged (`trash.move_to_trash`, `LIVE` filter); the document's
  tasks go with it.

## 9. Getting out: the full export

PL item 1 is "a full export in an open form" and belongs to this feature: an archive you
cannot leave is a lock-in, and the sealed pages make "just copy the folder" impossible.

Extend the existing building block (`documents/export.py`, part E of Beheer 5), only through
`export_context` and `export_files.py`:

- **PDF per document.** `documents/<year>/<date>_<sender-or-title>_<id8>.pdf`, built from
  the sealed JPEG pages in memory, one page at a time (no unsealed file on disk). Own
  minimal PDF writer for embedded JPEG (about 80 lines, no new dependency); no text layer
  yet. When INT's originals exist, the original is exported as it came in, next to the PDF
  (`.../<name>.original.pdf`). The text layer arrives with server OCR (INT).
- **`brievie-export.json`, `EXPORT_VERSION = 2`** (additive): per document `id`, `kind`,
  `title`, `sender`, `document_type`, `document_date` (`letter_date`), `expires_on`,
  `retain_until`, `summary`, `ocr_text`, `source`, `created_at`, `status`, person and
  department names (with ids), label names, tasks (type, description, deadline, status,
  completed_at), history lines (kind, date, no free text of others), `pdf` (path),
  `original` (path or null). The text is the already BSN-masked text.
- **Two scopes.** "Mijn archief exporteren" (a member: what they may see on their own persons,
  the existing member export, now with PDFs and documents) and **"Alles exporteren"** (an
  admin, session only: everything the admin may read; **private letters the admin may not read
  are not in it**, and the page says so in one sentence). It is not a back-up (the back-up
  system stays the way to restore), and not a way around private post.
- Built by a job in short transactions; the file lives in `<storage root>/exports/`, kept out
  of the back-up, with the existing expiry and one-time download rules; `LEESMIJ.txt` explains
  the layout and how to open the JSON in a spreadsheet or hand it to another system (Paperless'
  `post_document` API takes the PDF and these fields).
- An audit row per export (`export_created`, scope, count; existing code) and `no-store` on
  the download.

## 10. API (additive, `/api/v1`)

| Operation | Method, path | Auth | Answer |
|---|---|---|---|
| `createDocument` | + metadata fields (section 6.2) | member+, session or device | `DocumentOut`; 422 `validation_error` |
| `uploadDocumentFile` (INT) | + the same as form fields | member+ | as INT |
| `listDocuments` | + query `kind: str = ""`, `document_type: str = ""`, `year: int = 0`, `sender: str = ""`, `person_id` (exists), `archived: bool = False` (only `status = done`), `expiring: bool = False` | as now | `list[DocumentSummaryOut]`; 422 for an unknown kind or type |
| `searchDocuments` | + body `kind`, `document_type`, `year`, `person_id`, `label_id` (defaults empty) | as now | as now |
| `getArchiveFacets` | GET `/workspaces/{id}/documents/archive-facets` | any role | `ArchiveFacetsOut` |
| `updateDocumentDetails` | PATCH `/workspaces/{id}/documents/{document_id}/details` body `DocumentDetailsIn` | member+, `require_editable` | `DocumentOut`; 404, 422 `validation_error` |
| `createMyExport` (exists) | + `scope: str = "mine"` (`mine`, `all`) | `all`: admin, `auth=session_auth` | as now; 403 `not_admin` |

- `DocumentSummaryOut` and `DocumentOut` gain `kind: str = "letter"`, `title: str = ""`,
  `expires_on: date | None = None`, `retain_until: date | None = None`, `remind_days: int =
  30`, `reminder_task_id: UUID | None = None` (the task, only when it exists). `Out`
  schemas default every new field; a document shows `title` where a letter shows sender.
- `DocumentDetailsIn`: `title: str = ""`, `document_type: str = ""`, `document_date: date | None
  = None`, `expires_on: date | None = None`, `remind_days: int = 0` (0: unchanged),
  `retain_until: date | None = None`, plus `clear_expires_on: bool = False` and
  `clear_retain_until: bool = False` (a nullable input cannot also mean "unchanged", so
  clearing is a flag; nullable inputs keep a primitive and a default). Applies to
  `kind = document` for every field; a letter accepts only `retain_until`. One service
  function in `documents/own.py`, `transaction.atomic`, row lock, `require_editable` first;
  history `details_changed` (names the fields, never their values except dates).
- Every operation has an explicit camelCase `operation_id`; every non-2xx is `ApiError`
  `{"code", "message"}` in Dutch; `ArchiveFacetsOut` is typed (`kinds`, `types`, `years`,
  `senders: list[FacetOut]`, `FacetOut` = `value: str`, `count: int`); no `anyOf`; the contract
  test fixtures (`openapi_1b.json`, `openapi_1c.json`) stay satisfied because every addition has a
  default.
- New event kinds `document_added`, `details_changed`, `reminder_started`, `retention_changed`
  (clients skip unknown kinds). Origin `system` for the reminder.
- iOS: all fields optional on decode; `kind` missing means `letter`.

## 11. Web and app

### 11.1 Menu and tab bar

- **Web menu** (NAV 4.1, only the entry changes): Inbox, Mijn post, Mijn taken, **Archief**,
  Labels, Mensen, Afdelingen, Automatisering, Prullenbak, Instellingen. No count on Archief.
  Mijn taken counts the reminder like any task.
- **iPhone:** the four tabs stay (Inbox, Mijn post, Taken, Meer); **Meer** lists Archief (where
  Alle post was), Labels, Prullenbak, Instellingen. Archief is not a fifth tab: it is a place
  you go to *look something up*, a few times a month, and a tab bar of post is the daily path.
  iPad: sidebar entry in the web's order.

### 11.2 Archief page

Header "Archief" and one line "12.480 documenten", the filter row (section 5), a list newest
first. Row: title or sender (no unread marker: that is for Mijn post only), type chip, "Van wie", date, a badge "Geldig tot 12 mrt 2031" (neutral; "Verlopen" in
words and warning when past; never colour alone), "Bewaren tot" as a small lock in the
details, status badge (after LS), and "Eigen document" vs "Brief" through the type chip and an
icon. Empty state: "Nog niets in je archief. Brieven komen hier zodra je ze hebt gecontroleerd.
Voeg een document toe met 'Document toevoegen'." Filters live in the URL (`?soort=documenten
&type=&persoon=&afzender=&jaar=&label=`).

### 11.3 The document's page

The existing letter page (`document.tsx`), for `kind = document` without tasks, review and
inbox blocks: title (editable by an editor), type, Datum, Van wie, Labels, "Geldig tot" with
its reminder, "Bewaren tot", the summary if there is one (marked "Door AI gemaakt"), proposals
("Brievie stelt voor"), the pages, "Wie kan dit zien?", history, Dubbel? block, "Naar de
prullenbak" (with the retention warning). On iPhone the same blocks in the existing detail
screen.

### 11.4 Texts (Dutch B1 is the source; English added in the same change)

For `docs/GLOSSARY.md` (table of code terms and "English in the interface"); examples use roles,
never invented names ("je dochter", "de huurder", "de verhuurder").

| Dutch | English | Note |
|---|---|---|
| Archief | Archive | Menu, page, tab row. Replaces "Alle post" / "All mail" as the list of everything; the GLOSSARY line "Alle post / All mail" is changed to "(old name of Archief)" and removed one release later. |
| Eigen document / document (soort) | Own document / document (kind) | Code: `kind = document`. A letter stays "brief" / "letter". Never "stuk". |
| Soort (brief of document) | Kind (letter or document) | Code `kind`. The filter name "Soort"; not "Type". |
| Type | Type | The existing `document_type`; the chips: Identiteitsbewijs / ID document, Diploma / Diploma, Garantiebewijs / Warranty, Belastingaangifte / Tax return, Medisch / Medical. |
| Document toevoegen | Add a document | The action on Archief. |
| Dit document bewaren / Bewaren | Keep this document / Keep | The form title and button. |
| Titel | Title | |
| Datum / Datum brief | Date / Letter date | `letter_date` shows "Datum" for a document. |
| Van wie | Whose | The person or department; for a letter the existing "Op naam van" stays. |
| Geldig tot | Valid until | `expires_on`. |
| Herinner me … vooraf | Remind me … ahead | 7, 30, 60 or 90 days; `remind_days`. |
| Bewaren tot | Keep until | `retain_until`. "Bewaren tot 1 jan 2031. Weet je het zeker?" / "Keep until Jan 1, 2031. Are you sure?" |
| Alleen voor deze persoon | Only for this person | The private switch of the form. |
| Loopt af / Verlopen | Expiring / Expired | Filter and badge. |
| Alles exporteren / Mijn archief exporteren | Export everything / Export my archive | Admin / member. |
| Brievie stelt voor | Brievie suggests | Proposals on a document. |
| De tekst is niet gelezen | The text could not be read | Failure banner of a document. |

## 12. Performance

- Budget: median at most **250 ms** at 100,000 documents, the slowest of the four actors, for
  every list and count (`visibility-perf`, DECISIONS 2026-11-20 "M0" and the measured table of
  2026-11-21). The world generator (`tests/perf`) gains documents of both kinds (20 % own
  documents, 5 % with `expires_on`, a spread of years and 400 distinct senders), then the lists:
  `list_archive` (default, newest 200), `list_archive_kind_type`, `list_archive_person_year`,
  `archive_facets`, `search_with_title`, plus the existing `menu_counts`, `list_my_post`,
  `lingering_letters`, `inbox` lists **re-measured**, because three indexes are rebuilt and
  `unread_q` gains a condition.
- Risky points, with the answer in advance: the facets' `GROUP BY sender` over 100,000 visible
  rows (answer: one pass with `COUNT … FILTER`, top 50 by count, and if over budget the sender
  list comes from a partial index scan on (`workspace`, `sender`) `WHERE status = 'done'`);
  the `year` filter on `letter_date` (answer: the `(workspace, -letter_date)` partial index,
  or `created_at` ranges); the generated-column rewrite (answer: measured as a one-off, not a
  budget item).
- A list that misses the budget does not ship slower: its count (not the list) is dropped
  first, as LS2 does for "Afgehandeld". The task in the plan says so and names what may be
  dropped (the senders facet beyond 20, the per-type counts).

## 13. Security and privacy review points (lessons from DECISIONS)

- **No privileged defaults** (reviews of PR #38, #39, #40, #43, #44): no `visibility`,
  `kind` or `status` that a request can choose to widen access; `private` can only *narrow*;
  `kind` and `status` are set by the server; the documents of one `kind` never leak into
  another's lists (a test per list).
- **`require_editable` first on every write** (`updateDocumentDetails`, retry, trash,
  `setDocumentVisibility`, the reminder's complete/reopen through `tasks.services`); a
  document read through emergency access is read-only; a write on a document the caller may
  not see answers `document_not_found`, not 403.
- **Lists and counts only over `visible_documents`**; a source test (`test_visibility_seams`)
  names the new functions (`own.list_archive`, `facets`, `expiring`) as seam users; `searchDocuments`
  keeps its rules (no term logged, echoed or put in a URL).
- **Write order of S 4.9.3** for the create: the member's own writes first (persons, labels,
  history), then as the system the addressee list, readers and last the status `done`; the
  private flip in the same transaction.
- **No document content in logs, audit or history**: the reminder's description holds title and
  date only (the title is the member's own words and can name a person: it is a document field,
  visible to those who see the document, in "Mijn taken" only through the task policy; it is
  never written to the audit log, which holds ids and names of people only).
- **AI**: unchanged consent (`AISettings.consented_base_url`), BSN mask before storage and
  before any send; the AI proposes, a tap applies; no `ai` origin on a title or an end date until
  a person accepts it; a summary the AI wrote is marked `summary_by_ai`.
- **Export** only through `export_context` and `export_files.py`; the admin export never
  includes what the admin may not read; no sealed file written unsealed outside the ZIP's own
  output.
- **No new dependency.** The PDF writer is ours; a dependency (`img2pdf`) would need its
  DECISIONS line first.
- **Database:** `kind` and `title` are not workspace tables of their own; RLS on `documents_document`
  already forces both; the new partial unique index on `tasks_task` keeps RLS as it is.

## 14. What depends on what (merge order)

| Part | Needs | Why |
|---|---|---|
| Kind, types, title, search, filters, facets, Archief page and menu, iPhone add flow, visibility rules, reminder, retention, export (member and admin) | **nothing unmerged** (navigation is on `main`) | `createDocument` with client-OCR'd pages already exists |
| Status filter and badge on Archief ("Afgehandeld", "Te laat"), `letter_status` empty for documents | **letter status (LS)** | `task_open_count` and `letter_state.sync`; until then the archive shows no status |
| Browser "Document toevoegen" (file picker) | **intake (INT)** `uploadDocumentFile` | there is no file upload yet; web cannot OCR |
| Original in the export, text layer | **intake (INT)** | originals and server OCR |
| Scan folder for documents (a subfolder `archief` per workspace or person) | **intake (INT)**, later slice | design when INT's folder layout is built |

The plan marks these tasks and orders them so that the first part ships alone.

## 15. Not in this design

- **Folders, a storage path, custom fields, a free tag-everything model** (PL: deliberately not).
- **Self-learning matcher, free workflow engine, public share links, chat over the archive**
  (PL).
- **Versions of a document ("vervangen door") and a supersede chain**: renew by adding the new
  document and ending the old reminder.
- **A retention default per type** and legal retention rules.
- **Notes per document**, "origineel bewaren" with a place or number, a reference number field
  (PL items 7 and 8): small and useful, not part of this slice; the closed `title` + `labels`
  carry it for now.
- **Bulk actions** (label or person on a selection) and "run a rule on a selection": separate
  design.
- **E-mail intake, barcodes, splitting**: INT section 4 and later.
- **Turning a letter into a document or the reverse.**
- **Notifications by mail for the end date**; the iPhone's local reminders already come from
  tasks.
- **A document without sealed pages** (a note, a number): everything here is a scan or a file.

## 16. Rulings (calls made on the owner's behalf)

| # | Ruling | Why | Cost if wrong |
|---|---|---|---|
| AR1 | One `Document` with `kind`, not a second model | every protection is on `Document`; a second table copies every leak test | a few `kind` conditions in lists and indexes |
| AR2 | `kind` never changes after creation | a letter's review, tasks and status have no meaning for a document | re-scan to convert |
| AR3 | An own document is `done` at the end of processing, confirmed by the uploader | the member supplied the facts; AI proposals are not applied | a wrong title is fixed by the member, not caught by a review step |
| AR4 | AI proposes title, type, dates, labels as suggestions; none is applied by itself | "proposal, not automatism" | one extra tap |
| AR5 | Document duplicates are compared with documents only | merging a letter into a document would trash the wrong thing | a letter and a document that are the same paper stay two |
| AR6 | The expiry reminder is a `Task` made by one named writer, calculated, with no creator | it shows in "Mijn taken" without a second reminder system | the task writer list has a second entry |
| AR7 | `letter_status` is empty for a document; the reminder does not make it "Openstaand" | an end date is not post that waits | a late reminder shows in Mijn taken, not as a status on Archief |
| AR8 | A document never counts as unread, in Inbox or in "Blijft liggen" | it is not post | a document someone added for you shows no dot |
| AR9 | `title` joins `search_vector` through a drop and re-add of the generated column | users search by title; weight A | a one-time table rewrite and index rebuild |
| AR10 | The PDF for export is our own writer, no text layer until INT | no new dependency; the JSON holds the text | the exported PDFs are not searchable by themselves |
| AR11 | The old `Folder` endpoints stay; no `folder_id` | the API is additive; no use | dead code stays until a major version |
| AR12 | The archive is every reviewed (`done`) document, status is a filter | archive must find open letters too | "Afgehandeld" needs one more tap (until LS lands, no status at all) |

## 17. Besluiten voor Stijn

Only the questions whose answer changes what gets built. Each has my recommendation; if you
do not answer, the recommendation is built.

- **B1. "Alle post" vervangen door "Archief", of ernaast?** *Aanbeveling: vervangen.* Eén lijst
  met alles wat je mag zien, met de nieuwe filters; "Alle post" bestaat dan niet meer in het
  menu en de oude link werkt nog. Ernaast zou twee bijna gelijke lijsten geven. Kost bij
  ernaast: een tweede lijst, een tweede telling en uitleg waarom er twee zijn.
- **B2. Welke brieven staan in het archief?** *Aanbeveling: alle gecontroleerde brieven, ook
  met een open taak; "Afgehandeld" is een filter (zodra de briefstatus er is).* Alternatief:
  alleen afgehandelde brieven. Dan verdwijnt een brief met een open taak uit het archief tot
  hij af is, en het archief vindt juist de brief niet waar je een herinnering van kreeg.
- **B3. Staat "Alleen voor deze persoon" alvast aan bij identiteitsbewijs, medisch,
  loonstrook en belastingaangifte?** *Aanbeveling: ja, zichtbaar aan, de gebruiker kan hem
  uitzetten.* Dan is een paspoortkopie standaard niet voor het hele huishouden zichtbaar, ook
  niet in Thuis. Alternatief: nooit voorgeselecteerd; dan geldt altijd wat de werkruimte
  instelt en moet de gebruiker zelf aan de bel trekken.
- **B4. Hoe hard is "Bewaren tot"?** *Aanbeveling: naar de prullenbak mag (met een
  waarschuwing); definitief wissen en leegmaken weigert tot de datum voorbij is of iemand de
  datum weghaalt.* Alternatief: alleen een waarschuwing, nooit weigeren (eenvoudiger, maar
  dan is het geen bewaartermijn maar een notitie).
- **B5. Hoe voeg je op de iPhone een document toe?** *Aanbeveling: een + op het Archief-scherm
  (onder Meer); de ronde scanknop op Inbox en Mijn post blijft voor post.* Alternatief: de
  scanknop vraagt eerst "Brief of document?" (twee taps voor elke brief, terwijl post de
  dagelijkse route is).
