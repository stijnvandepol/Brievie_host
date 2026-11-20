# Trash for letters

Date: 2026-11-20. Branch `feat/trash` (host). iOS follows separately (fields at the end).
Assignment: `.superpowers/sdd/2026-11-20-trash/assignment.md` (frame 3, safety rules, rulings).

## Intent

Stijn (19-11): two people scan the same letter; somebody checks and removes the double. That
needs deleting, which does not exist yet. This step builds only the trash: a letter is moved
to the trash, stays there 30 days and can be put back; after that (or earlier by an admin)
it is gone for good, its sealed page files included. Duplicates come later and end in
"the other one goes to the trash", so the trash is a service another feature can call.

## Goals, non-goals, anti-goals

- Goal: soft delete with one field, so a trashed letter disappears from every member-facing
  read through the one seam (`visible_documents`) and comes back unchanged on restore.
- Goal: a final purge (after 30 days, or now by an admin) that removes the rows and the
  sealed files, the files only through one guarded function that refuses on its own.
- Goal: rights (member trashes and restores, admin purges), history lines, audit rows with
  ids only, and a written effect for tasks, agenda, export, back-up, archive, queued AI jobs
  and the upload dedupe.
- Non-goal: duplicate detection or merging; bulk select in lists; a trash for persons,
  labels or rules; a per-workspace retention setting; a detail page of a trashed letter;
  iOS screens; S3 storage.
- Anti-goal: a trashed letter that still shows up somewhere (a count, a search hit, a task,
  an export, a rule run, an AI call); a purge that touches a file that is not that letter's
  page under the configured storage root; a test that runs the removal on a real path; a
  letter's sender, summary or text in an audit row or log line; a restore that half-works
  because files were already removed.

## Approaches

1. **A field on `Document` plus the seam, with explicit live filters on the off-seam paths
   (chosen).** `trashed_at` is set; `visible_documents` excludes it, `trashed_documents`
   returns it. The paths DECISIONS lists as "not on the seam yet" (locked lookups behind
   changes, pipeline, rules, export, intake, tasks) each get the filter, and one
   parametrised test proves every mutating endpoint refuses a trashed letter. Small,
   explicit, and restore is clearing two fields.
2. A default manager that hides trashed rows (`Document.objects` = live only). Catches
   future code by default, but hides rows from code that must see them: the upload dedupe
   would miss a trashed `client_id`, hit the unique constraint, and its winner lookup would
   find nothing (a 500); RLS-style surprises in related lookups and cascades. Rejected: an
   invisible filter is the wrong default for code that deletes.
3. A status value `trashed` in `Document.Status`. Loses the status to return to, and every
   inbox state, the pipeline lock and the rules read status. Rejected.
4. Moving the rows to trash tables. Breaks every foreign key and makes restore a copy.
   Rejected.

## Design

### Model (`apps/documents/models.py`, migration `0011_trash`)

- `Document.trashed_at: DateTimeField(null=True, blank=True)`: moved to the trash then.
- `Document.trashed_by: ForeignKey(User, null=True, blank=True, on_delete=PROTECT,
  related_name="+")`: who did it; null when Brievie did it (a later duplicate merge job).
- `Document.purge_started_at: DateTimeField(null=True, blank=True)`: the purge decision was
  taken (expired, admin, emptied). From then on the letter cannot be restored and is not
  listed anywhere; the purge job finishes it.
- Check constraint `documents_document_purge_needs_trash`:
  `Q(purge_started_at__isnull=True) | Q(trashed_at__isnull=False)`.
- Partial index `documents_document_trash` on `(workspace, trashed_at)` where
  `trashed_at IS NOT NULL` (trash list and the expiry query).
- `LIVE = models.Q(trashed_at__isnull=True)` in `models.py`: the one spelling of "not in
  the trash" for paths off the seam (greppable); through a relation:
  `models.Q(document__trashed_at__isnull=True)`.
- `DocumentEvent.Kind`: `TRASHED = "trashed"`, `RESTORED = "restored"` (origin `user`;
  never undoable through `undoDocumentEvent`).
- `TRASH_RETENTION = timedelta(days=30)` in `apps/documents/trash.py`. A constant, not a
  setting (ruling 9).

### Seam (`apps/documents/visibility.py`)

```
def _readable(membership) -> QuerySet[Document]        # the rights model, one place
def visible_documents(membership) -> QuerySet[Document]  # _readable(...).filter(trashed_at__isnull=True)
def trashed_documents(membership) -> QuerySet[Document]  # _readable(...).filter(trashed_at__isnull=False, purge_started_at__isnull=True)
```

Private post later changes `_readable` only, so the trash never shows a letter the viewer
may not see (frame 4). List, counts, detail, search and the events read lose trashed
letters through `visible_documents` without further change.

### Services (`apps/documents/trash.py`, new)

All mutating functions are `transaction.atomic` and lock the row (`select_for_update`).
`move_to_trash`, `purge_now`, `empty_trash` and `purge.mark_expired` first ask
`retention.check_removal` (ruling 18), which allows everything today.

- `move_to_trash(document: Document, *, user: User | None, actor: AuditActor, reason: str = "user") -> None`:
  the building block on a locked live document (the duplicate merge will call it with
  `reason="duplicate"`). Sets `trashed_at=now`, `trashed_by=user`; history line through
  `organise.record_trashed`; audit `document_trashed` with `reason`. Nothing else changes:
  status, labels, addressees, suggestions, tasks and extractions stay as they are.
- `trash_document(*, membership, user, actor, document_id) -> Document`: locks the letter in
  `visible_documents`; when it is already in `trashed_documents` it returns it unchanged
  (idempotent: a double click); else 404 `document_not_found`. A `processing` letter may be
  trashed (a misfired scan): the pipeline skips it (below).
- `restore_document(*, membership, user, actor, document_id) -> Document`: locks the letter in
  `trashed_documents`; a letter already visible is returned unchanged; else 404
  `document_not_found` (also once the purge started). Clears `trashed_at` and `trashed_by`;
  history `restored`; audit `document_restored`. When the letter is `processing` and has no
  live job (`apps.pipeline.jobs._has_live_job`, made public as `has_live_job`), it is queued
  again (`defer_processing`).
- `list_trash(membership) -> list[Document]`: `trashed_documents`, newest `trashed_at`
  first, at most 500, with `trashed_by` selected and the summary prefetches.
- `purge_now(*, membership, actor, document_id) -> None`: admin. The letter must be in
  `trashed_documents` (no skipping the trash; else 404). Sets `purge_started_at`, audit
  `document_purged` `{reason: "admin"}`, defers the workspace's purge job (in the request
  transaction, so a rollback takes the job back too).
- `empty_trash(*, membership, actor, up_to: datetime | None) -> int`: admin. Marks every
  letter of `trashed_documents` with `trashed_at <= up_to` (all when `up_to` is None): a
  letter trashed while the admin read the dialog is not emptied. One `document_purged`
  `{reason: "emptied"}` row per letter; defers the purge job; returns the count.

### Purge (`apps/documents/purge.py`, new) and its jobs

- `mark_expired(workspace_id: UUID, *, now: datetime) -> int`: one short transaction in
  `workspace_context`: rows with `trashed_at <= now - TRASH_RETENTION` and no
  `purge_started_at` get `purge_started_at=now`; audit `document_purged`
  `{reason: "expired"}`, actor system.
- `purge_marked(workspace_id: UUID, *, limit: int = 100) -> PurgeResult`: for every marked
  letter, in this order:
  1. short transaction: lock the row, still marked; read its pages' `storage_path`s;
  2. no transaction: `page_files.remove_document_files(workspace_id, document_id, paths)`;
  3. short transaction: lock the row, still marked; delete its `Task` rows (the foreign key
     is `SET_NULL`, they would outlive it), then the document (cascade: pages, extractions,
     addressees, labels, events, suggestions, rule runs).
  A refusal or `OSError` in step 2 logs the error type and the ids only, skips step 3 and
  leaves the row marked: the letter stays hidden and the next run tries again. Files go
  before rows, so a crash in between never leaves a restorable letter without pages; a
  marked row is never restorable.
- `purge_all(*, now: datetime) -> None`: under `backup_lock()` (a back-up or restore never
  sees half a purge; `BackupError("busy")` means "skip this run", logged at info), for every
  workspace: `mark_expired`, then `purge_marked` until nothing is left.
- Jobs in `apps/documents/jobs.py` (queue `pipeline`, priority `EXPORT_PRIORITY`):
  `purge_trash` periodic `cron="41 * * * *"`, `queueing_lock="purge_trash"`, calls
  `purge_all`; `purge_workspace_trash(workspace_id)` deferred by `purge_now`/`empty_trash`
  with `queueing_lock=f"purge-trash-{workspace_id}"` (`AlreadyEnqueued` is fine: the waiting
  one takes every marked row), runs `purge_marked` under `backup_lock()`. A busy lock is
  finished by the hourly run at the latest.

### Guarded file removal (`apps/documents/page_files.py`, new)

The only code that removes a letter's sealed files. `DocumentStorage.delete` stays for the
upload rollback only; the purge never calls it.

- `class PageFileRefused(Exception)`: never carries the path (it holds ids).
- `judge_storage_root(root: Path) -> Path`: `safepaths.require_emptiable(root,
  allowed=[configured BRIEVIE_STORAGE_ROOT], avoid=[configured BRIEVIE_BACKUP_MOUNT],
  fixed=[safepaths.DOCUMENTS])`, as the sweep judges it; a `BackupError` becomes
  `PageFileRefused`. So: `/data/documents` in production, strictly inside the test root in
  tests, nothing else, never a home directory, `/`, a link or the back-up mount.
- `judge_page_path(storage_path: str, *, workspace_id: UUID, document_id: UUID) -> PurePosixPath`:
  pure (no file system call). Accepts exactly `f"{workspace_id}/{document_id}/{n}.page"`
  (lowercase UUIDs of the arguments, `n` digits); refuses `\`, NUL, `..`, an absolute path,
  another workspace's or letter's path, any other name.
- `remove_document_files(workspace_id: UUID, document_id: UUID, storage_paths: Sequence[str]) -> int`:
  first `judge_page_path` for every path (all or nothing: one refusal refuses the call
  before anything is touched), then `judge_storage_root`, then opens the root, the workspace
  directory and the letter's directory each with `O_DIRECTORY | O_NOFOLLOW` relative to its
  parent (as `sweep._unlink_if_old` does) and checks the root's identity
  (`safepaths.identity`, `open_dir_nofollow`). In the letter's directory it unlinks the
  judged page names and `.tmp-*` leftovers that are regular files (`lstat`, never a link,
  never a directory), then `rmdir` of the letter's directory when it is empty (a directory
  with anything else stays and is logged as a count). A file already gone is fine; a
  missing letter directory is fine (returns 0). Returns how many files went.
- Tests only in `tmp_path` (the autouse fixture points the storage root there and installs
  the test root). Every refusal test calls `judge_page_path` or `judge_storage_root`, never
  `remove_document_files` with a path outside `tmp_path`.

### Effects

| Where | While in the trash | On restore | On purge |
|---|---|---|---|
| Lists, counts, detail, search, events, persons and labels counts | gone (seam; counts get `LIVE` through the relation) | back | gone |
| `getDocument`, `getDocumentPage`, `listDocumentEvents` | 404 `document_in_trash` when it is in `trashed_documents`, else `document_not_found` | normal | 404 `document_not_found` |
| Changes (confirm, reopen, retry, labels, addressees, suggestions, undo) | 404 `document_not_found` (lock queries filter `LIVE`) | normal | 404 |
| Tasks (`listTasks`, complete, reopen) | hidden: `list_tasks` takes the membership and keeps tasks without a letter or with a visible letter; complete/reopen 404 `task_not_found` | back, as they were | deleted with the letter |
| Agenda, reminders | none exist yet; they read tasks through `tasks.services` and inherit the filter (CLAUDE.md rule) | | |
| Member export | left out (`member_document_ids` and the reads in `export.py` filter `LIVE`); a ZIP made before lives at most 24 h | in the next export | gone |
| Back-up | stays in the back-up as it is | | stays in older back-ups until they age out (monthly snapshots up to a year); a restore of an older back-up brings it back. Said in PRIVACY and SELFHOST |
| Archive ("Alle post", the folder of kept letters) | gone from it (seam) | back | gone |
| Queued AI job | skipped: `_lock_processing` filters `LIVE`, so no text goes to an AI service and a result in flight is dropped; `requeue_abandoned_documents` skips trashed letters | re-queued when still `processing` without a live job | the job finds nothing |
| Rules (preview, run on existing letters) | left out | not re-run | gone |
| Upload dedupe on `client_id` | 409 `document_trashed` (same pages) or `document_conflict` (other pages) | normal 200 | the `client_id` is free: a new upload is a new letter |
| Person delete | a person on a trashed letter is still in use (PROTECT); message names the trash | | free after purge |

### Audit (closed list, `apps/audit/codes.py`)

| Code | Label | Target | Values |
|---|---|---|---|
| `document_trashed` | "Brief naar de prullenbak" | `document` (id only) | `reason`: `user` (later `duplicate`) |
| `document_restored` | "Brief teruggezet" | `document` | none |
| `document_purged` | "Brief definitief verwijderd" | `document` | `reason`: `admin`, `emptied`, `expired` |

Actor: the request's (`actor_from_request`, a device included) for trash and restore; the
admin's session for `admin`/`emptied`; system for `expired`. No sender, summary, type or
text. The web's `audit-codes.ts` gets the three codes and labels.

### History

`organise.record_trashed(document, *, actor: User | None)` and
`organise.record_restored(document, *, actor: User)` write `trashed` / `restored`
(`organise` stays the only writer of a letter's history). The history is shown after a
restore; on purge it goes with the letter (cascade). The audit row is what remains.

### API (additive, `/api/v1`)

| Method and path | `operation_id` | Auth, role | In | Out |
|---|---|---|---|---|
| `POST /workspaces/{workspace_id}/documents/{document_id}/trash` | `trashDocument` | session or device, `member` | none | 200 `TrashedDocumentOut`; 404 |
| `POST /workspaces/{workspace_id}/documents/{document_id}/restore` | `restoreDocument` | session or device, `member` | none | 200 `DocumentOut`; 404 |
| `GET /workspaces/{workspace_id}/trash` | `listTrash` | session or device, any member | none | 200 `TrashListOut` |
| `DELETE /workspaces/{workspace_id}/trash/{document_id}` | `purgeDocument` | `auth=session_auth`, `admin` | none | 204; 404; 401 for a device token |
| `POST /workspaces/{workspace_id}/trash/empty` | `emptyTrash` | `auth=session_auth`, `admin` | `EmptyTrashIn` | 200 `EmptyTrashOut` |

- `TrashedDocumentOut(DocumentSummaryOut)`: `trashed_at: datetime`,
  `trashed_by_name: str = ""` (empty when Brievie did it), `purge_after: datetime`
  (`trashed_at + 30 days`; clients never compute the retention themselves).
- `TrashListOut`: `items: list[TrashedDocumentOut]` (default empty), `retention_days: int = 30`.
- `EmptyTrashIn`: `up_to: datetime | None = None` (the newest `trashed_at` the caller showed).
- `EmptyTrashOut`: `document_count: int`.
- New error codes: 404 `document_in_trash` ("Deze brief staat in de prullenbak."), 409
  `document_trashed` ("Deze brief staat in de prullenbak. Zet hem daar terug als je hem
  weer wilt zien."). Reserved for the retention hook, not raised today: 409
  `document_retained`.
- Every new field has a default or is in a new schema; no `anyOf`; nullable only primitives.

### Web (Dutch B1, Lingui; `pnpm extract`)

- Letter page (`routes/document.tsx`), for `canEdit`: button "Verwijderen" (icon `delete`).
  `ConfirmDialog` tone `primary` (it can be undone): title "Brief verwijderen?", text "De
  brief gaat naar de prullenbak. Daar blijft hij 30 dagen. Zo lang kun je hem terugzetten.
  Daarna wordt hij voorgoed gewist.", button "Naar de prullenbak". After success: go to
  "Alle post" and show the undo bar.
- Undo bar (`components/undo-bar.tsx` + a small store `web/src/undo-trash.ts`, like
  `search-term.ts`): a `MessageBar` with `role="status"`: "De brief staat in de
  prullenbak." and buttons "Ongedaan maken" and "Sluiten". No timer (WCAG 2.2.1): it stays
  until closed, until another letter is trashed, or the workspace changes. "Ongedaan maken"
  calls `restoreDocument` and opens the letter; a failure shows the server's sentence in
  the bar.
- Nav: "Prullenbak" (icon `delete`) below "Alle post". "Te doen" and "Alle post" keep
  their names.
- Page "Prullenbak" (`routes/trash.tsx`, path `prullenbak`): intro "Brieven in de
  prullenbak worden na 30 dagen voorgoed gewist. Tot die tijd kun je ze terugzetten."
  Rows: sender (or the proposed sender, or "Onbekende afzender"), soort, datum of the
  letter; under it "Verwijderd op {datum} door {naam}" (without "door" when the name is
  empty) and "Nog {n} dagen" / "Nog 1 dag" / "Wordt vandaag gewist" from `purge_after`.
  Per row "Terugzetten" (not for a viewer); for an admin also "Definitief verwijderen".
  Header button "Prullenbak legen" for an admin when the list is not empty. Empty state:
  "De prullenbak is leeg." For an admin, under the intro: "Brievie houdt nog geen
  bewaartermijn bij. Moet je een brief bewaren, bijvoorbeeld een factuur voor de
  belasting? Let daar dan zelf op voordat je hem wist."
- "Definitief verwijderen" dialog (danger): title "Brief voorgoed wissen?", text "De brief
  en de gescande pagina's worden voorgoed uit Brievie gewist. Dit kun je niet ongedaan
  maken. Een back-up die eerder is gemaakt, heeft de brief nog. Daar blijft hij in tot die
  back-up verloopt.", button "Definitief verwijderen".
- "Prullenbak legen" dialog (danger): title "Prullenbak legen?", text "{n} brieven in de
  prullenbak worden voorgoed uit Brievie gewist. Dit kun je niet ongedaan maken. Een
  back-up die eerder is gemaakt, heeft deze brieven nog. Daar blijven ze in tot die
  back-up verloopt." (plural), button "Prullenbak legen"; sends `up_to` = the newest
  `trashed_at` on screen.
- A link to a trashed letter (`document_in_trash`): "Deze brief staat in de prullenbak."
  with a link "Naar de prullenbak".
- History lines: "Naar de prullenbak verplaatst door {naam}", "Teruggezet uit de
  prullenbak door {naam}".
- After trash, restore, purge or empty: invalidate documents, inbox counts, search, tasks,
  trash, persons and labels queries.

## Rulings

From the assignment (kept): 1 soft delete with `trashed_at` (+ `trashed_by`) and the seam
with `trashed_documents`; 2 member trashes and restores, admin purges and empties; 3 hourly
purge job, short transactions, one guarded removal function with its own refusal, tests in
`tmp_path`; 4 audit codes with ids only, history lines for trash and restore; 5 the effects
in the table; 6 the web as above.

Added:

7. **`purge_started_at`, files before rows.** The purge decision is a row state; the job
   removes the files and then the rows, and a marked letter is never restorable. Reason: no
   crash order leaves a restorable letter without pages or a forgotten set of sealed files
   (the orphan sweep is a manual command). Cost if wrong: one nullable column.
8. **Admin purge and emptying are session-only (`auth=session_auth`) and only from the
   trash.** Reason: a stolen device token cannot destroy post, and nothing skips the 30
   days of second thought except a deliberate second step. Cost if wrong: iOS gets the
   endpoints later (additive) or a one-step "delete now".
9. **30 days is a constant, not a setting.** Reason: the spec names 30 days; a setting is a
   support question without a user. Cost if wrong: a setting later, the constant becomes its
   default.
10. **Mutations of a trashed letter answer 404 `document_not_found`; only the reads give
    `document_in_trash`, through `trashed_documents(membership)`.** Reason: the write paths
    take the workspace, not the membership, and are off the seam; a 404 reveals nothing when
    private post arrives. Cost if wrong: a stale tab gets "not found" instead of "in the
    trash".
11. **Upload dedupe on a trashed letter: 409 `document_trashed`, never an automatic
    restore.** Reason: an app queue resending an old scan must not undo a deliberate delete,
    and a 409 stops its retries. It reveals only that the uploader's own `client_id` exists,
    as `document_conflict` already does. Cost if wrong: the app shows a sentence instead of
    a silent success.
12. **Purge holds `backup_lock`.** Reason: a back-up or restore never captures half a
    purge (the row without files or the reverse). Cost if wrong: a purge waits up to an
    hour behind a long back-up; the letter is hidden meanwhile.
13. **Tasks go with the letter on purge, also done ones.** Their foreign key is `SET_NULL`;
    a task without its letter names something that no longer exists. Cost if wrong: a
    history of paid bills is lost with the letter, which is what deleting means.
14. **No detail view and no page view of a trashed letter in this step.** The row (sender,
    soort, letter date, page count through `DocumentSummaryOut`) is enough to recognise; restore is
    one click and undoable by trashing again. Cost if wrong: one endpoint later.
15. **The undo bar has no timer.** WCAG 2.2.1; it closes on "Sluiten", another trash or a
    workspace switch. Cost if wrong: a bar that lingers.
16. **`empty_trash` takes `up_to`.** The admin empties what they saw, not a letter trashed
    during the dialog. Cost if wrong: none for clients that leave it out (None = all).
17. **`move_to_trash` is the building block with a `reason`.** The duplicate merge calls it
    with `reason="duplicate"` and its own actor; nothing in the trash knows about
    duplicates. Cost if wrong: an extra allowed reason value.
18. **One hook for a retention term: `apps/documents/retention.py`.**
    `check_removal(document: Document, *, step: RemovalStep) -> RemovalVerdict` with
    `RemovalStep = Literal["trash", "purge"]` and
    `@dataclass(frozen=True) RemovalVerdict(allowed: bool = True, warning: str = "")`.
    Today it returns `RemovalVerdict()` for every letter. It is called in exactly four
    places: `move_to_trash` (`"trash"`), `purge_now`, `empty_trash` and `mark_expired`
    (`"purge"`), on the locked row, before anything changes. A refused verdict raises
    `ApiError(409, "document_retained", <the verdict's sentence>)` in the two request paths,
    skips the letter in `empty_trash` (not counted) and in `mark_expired` (stays in the
    trash). A warning is not shown yet: when terms arrive, the endpoints add an additive
    `warnings` field and a confirm step. Cost if wrong: one function to widen; no caller
    has to be found again.
19. **The back-up sentence is in the confirm questions, not only in PRIVACY.** "Definitief"
    is only honest with it. Cost if wrong: a longer dialog.

## Left to the builder

- The exact Lingui plural forms and where the "Verwijderen" button sits in the action row.
- Whether `TrashListOut` caps at 500 with a sentence "Alleen de nieuwste 500 worden
  getoond." (a full trash of a large workspace is unlikely; the cap must exist).
- Moving `_has_live_job` to a public name in `apps.pipeline.jobs` without changing it.
- Test helpers: a `trashed(document, by=user, days_ago=n)` helper in
  `tests/documents_support.py`.
- When S3 storage arrives, the guarded removal moves behind the storage interface with the
  same refusal (only the letter's own keys).

## iOS (later; fields the app needs)

- `trashDocument` (device token): swipe or menu "Verwijderen" on a letter; answer
  `TrashedDocumentOut` (`id`, `trashed_at`, `purge_after`, summary fields) for an undo
  banner that calls `restoreDocument` (answer `DocumentOut`).
- `listTrash`: `items[]` with the `DocumentSummaryOut` fields plus `trashed_at`,
  `trashed_by_name`, `purge_after`; `retention_days` for the explanation text.
- Errors: `document_in_trash` (404) on opening a letter; `document_trashed` (409) on an
  upload from the queue: drop the queued scan and say the letter is in the trash.
- `purgeDocument` and `emptyTrash` stay web-only (session).

## Decided on Stijn's mandate (20-11)

- **Retention.** The trash ships for Thuis and Zakelijk alike. A retention term per letter
  (spec section 8, `bewaren_tot`) is a later feature; the hook for it is
  `retention.check_removal` (ruling 18), and the admin text says honestly that Brievie
  does not watch a retention term yet.
- **"Definitief" and back-ups.** The texts say what is true: gone from Brievie for good,
  but a back-up made earlier still holds the letter until that back-up expires (the two
  confirm questions above, PRIVACY and SELFHOST).
