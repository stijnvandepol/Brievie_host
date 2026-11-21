# Briefstatus: a letter's status, kept in step with its tasks

Date: 2026-11-21. Covers `Brievie_host` (backend, `web/`) and, as a short list of fields
and screens, `Brievie_ios`. Design only; nothing here is built. Plan:
`docs/superpowers/plans/2026-11-21-host-briefstatus.md`.

**Neighbours.** "Mijn taken" (`2026-11-21-mijn-taken-design.md`, cited "MT S 6"; on `main`
since PR #36); the rights design `2026-11-19-zichtbaarheid-postkamer-inrichting-design.md`
("RS 4.9.3"); the trash and duplicates (DECISIONS "Trash for letters", "Duplicate
letters"). Research: `.superpowers/onderzoek/reports/Brievie vergeleken met mailbox en
ticketsysteem.md` ("R 1") and its notes.

## 1. Intent

Stijn (21-11-2026): under a person, their letters have a status (open, handled, ...), and
that status stays in step with the letter's tasks. One look at a person answers: *what of
this post is still open, what waits on somebody else, what is late, what is done*.

## 2. What exists (verified on `main`, 24c0318)

- `Document.status`: `processing`, `needs_review`, `failed`, `done` (done = confirmed by
  a person or the autopilot). The Inbox (the page "Te doen"; its rename to "Inbox" lands
  separately, this design uses the new name) derives `inbox_state`: `new` (processing),
  `review` (needs_review/failed), `unassigned` (done, no addressee), `""` (sorted).
- `Task`: `status` open/done, `deadline` (a day), `completed_at/by`, `assigned_to/by`,
  `amount`, `term_quote`; the only writers are `tasks.services.complete_task`,
  `reopen_task`, `set_deadline`, `tasks.assignment.assign`, plus task creation in
  `documents.review._write_confirmation`, deletion of the AI's open tasks in
  `documents.services.reopen_document`, the move in `documents.duplicates._merge_tasks`
  and deletion in `documents.purge._delete_rows`.
- "Mijn taken" (`tasks.services.my_tasks`, `count_my_tasks`): open tasks by date group
  (`overdue`, `within_week`, `later`, `no_date`), today in Europe/Amsterdam
  (`task_today`), partial index `tasks_task_open`.
- History: `DocumentEvent` kinds `task_completed`, `task_reopened`, `task_assigned`,
  `task_unassigned`, `task_deadline_set` with actor (or origin `system`, no actor).
- Persons: `listPersons` counts letters per person with one grouped SQL under the policy;
  the web has no person page: "Alle post" filters by person (`?persoon=`), and the iPhone
  has one post scope per person (`DocumentsModel+Scope.swift`).
- Derived-but-stored facts written by one function and repaired nightly already exist:
  `Document.addressee_ids` (`visibility.sync_addressee_ids`, `verify_visibility`) and
  `Document.has_automatic_link` (`organise.sync_automatic`, DECISIONS 2026-11-20).
- PR #39 (`feat/wie-ziet-wat-4`, about to land) changes `people/services.link_member`,
  department membership, `documents/visibility.py` (+ `viewer_reasons`),
  `documents/api.py` (+ `getDocumentAccess`) and adds audit migrations only. It touches
  none of the functions this design changes (`list_documents`, `inbox_counts`,
  `list_persons`, `tasks/services`, `review`, `duplicates`).

## 3. Goals, non-goals, anti-goals

- Goal: every letter has one status, derived from its review and its tasks, shown in the
  Inbox, under a person (with filter and counts) and on the letter; "Te laat" as a marker.
- Goal: "wacht op reactie" (shared, per task, with a date) and "uitgesteld tot" (personal,
  per task, own "Mijn taken" only).
- Goal: "Brief afhandelen" completes the open tasks after a confirmation that names them.
- Non-goal: anything in section 13.
- Anti-goal: a status or count reaching someone who may not read the letter; a status a
  person sets that can disagree with the tasks; anything that closes, completes or moves
  post on its own because a date passed.

## 4. The status

### 4.1 Four statuses and one marker

Per letter, with *today* = `task_today()` (Europe/Amsterdam, as "Mijn taken"):

| Status | API `letter_status` | When |
|---|---|---|
| **Nieuw** | `new` | not reviewed: `Document.status` is `processing`, `needs_review` or `failed` |
| **Openstaand** | `open` | reviewed (`done`), and at least one open task is *active*: not waiting, or its waiting date is today or past |
| **Wacht op reactie** | `waiting` | reviewed, has open tasks, and **every** open task waits until a date after today |
| **Afgehandeld** | `handled` | reviewed and no open task (all done, or the review made none) |

| Marker | API | When |
|---|---|---|
| **Te laat** | `overdue: bool` | reviewed, and an open task is active and its deadline is before today |

- "Te laat" is a marker, not a status (R 1): a letter is "Openstaand, te laat". A waiting
  task is not late while it waits (that is what waiting means); when its date passes and
  the deadline is behind, the letter is "Openstaand, te laat" again.
- A reviewed letter without a name (Inbox "Naam kiezen") is **not** Nieuw: it is judged
  by its tasks like any other. It stays in the Inbox until it has a name.
- The autopilot's confirmation counts as a review (the letter is `done`); "Automatisch door
  Brievie" stays its own marker.
- A reviewed letter without tasks is **Afgehandeld at once** (R 1, item 3). Alternative
  in Besluit B1.
- Nothing is written when a date passes: "waiting ends" and "becomes late" are
  comparisons with today at read time. No job, no clock.

### 4.2 Computed from three task facts cached on the letter (ruling LS1)

The status is never stored. What is stored, on `Document`, are three facts about the
letter's tasks that do not change with the date:

| Column | Meaning |
|---|---|
| `task_open_count` (int, default 0) | open tasks of the letter |
| `task_wait_until` (date, null) | when every open task waits: the earliest of their waiting dates; null when an open task does not wait, or there is none |
| `task_overdue_from` (date, null) | the first day the letter is late: the minimum, over open tasks with a deadline, of `max(deadline + 1 day, waiting_until)` (`waiting_until` left out when null); null when no open task has a deadline |

Then, for *today* T:

```
new      = status != 'done'
handled  = status = 'done' AND task_open_count = 0
waiting  = status = 'done' AND task_open_count > 0 AND task_wait_until > T
open     = status = 'done' AND task_open_count > 0 AND (task_wait_until IS NULL OR task_wait_until <= T)
overdue  = status = 'done' AND task_overdue_from <= T
```

`task_overdue_from` is exact: a task is late on day D when `D > deadline` and it is not
waiting (`waiting_until` null or `<= D`), so from `max(deadline + 1, waiting_until)`.

**Why cached facts and not an `EXISTS` on tasks.** A status filter ("Openstaand" under
Piet) must sit in the `WHERE` before the 200-row cap, and counts must cover all of a
person's letters. An `EXISTS (open task)` inside `visible_documents` runs the task policy
(which runs the document policy) per task row; DECISIONS 2026-11-20 measured that a join
per row before the sort costs ~120 ms at 20,000 tasks, and `waiting_letters` already
splits its count to avoid running the policy for every letter. Three plain columns on the
letter row keep every list on the existing indexes (GIN `addressee_ids`, a new partial
index below) and every count in one pass.

**Why this does not drift.** The columns are written by one function,
`apps.tasks.letter_state.sync(document)`, in the same transaction as every task change,
under the letter's row lock; a source test names its callers; the nightly
`verify_visibility_job` recomputes them for every letter and repairs (counts only, like
`addressee_ids`). They are facts about tasks, not a status anybody sets: nobody can make a
letter "Afgehandeld" while a task is open.

**Index:** `documents_document_open_tasks` on (`workspace`, `-created_at`) `WHERE status =
'done' AND task_open_count > 0`: the lists and counts of Openstaand, Wacht op reactie and
Te laat (a small share of all post). Nieuw uses the existing `documents_document_review`
plus `processing`; Afgehandeld uses the recent index (most post).

### 4.3 What changes the status

| Event | Status effect | History |
|---|---|---|
| Review confirmed, with tasks | Nieuw → Openstaand | `confirmed` (exists) |
| Review confirmed, without tasks | Nieuw → Afgehandeld | `confirmed` + `letter_handled` (origin `system`, no actor) |
| Last open task ticked off (by a person, or a merge's twin) | → Afgehandeld | `task_completed` (actor) + `letter_handled` (`system`, no actor) |
| "Brief afhandelen" | → Afgehandeld | one `task_completed` per task (actor) + `letter_handled` (origin `user`, actor) |
| A task reopened ("Toch niet klaar") on a handled letter | → Openstaand | `task_reopened` (actor) + `letter_reopened` (`system`, no actor) |
| A merge moves an open task onto a handled letter | → Openstaand | `task_moved` (exists) + `letter_reopened` (`system`) |
| "Wacht op reactie tot …" on the last active task | → Wacht op reactie | `task_waiting` (actor, date) |
| "Niet meer wachten" | → Openstaand | `task_waiting_ended` (actor) |
| The waiting date arrives | → Openstaand | none (nothing is written; see below) |
| A deadline passes | marker Te laat on | none |
| `reopenDocument` (AI's confirmation taken back) | → Nieuw | `reopened` (exists) |
| Trash / restore | none (a trashed letter is out of every list) | exists |

- A `letter_handled` / `letter_reopened` line is written only for a reviewed letter that
  is not in the trash, and only when `sync` sees the handled-ness change. Transitions
  into and out of "Wacht op reactie" are told by the task's own lines.
- **When the waiting date arrives** nothing is written: the line "Anne wacht op een
  reactie tot 3 december" already says when it ends, and a job that writes history at
  midnight would be the only automatic writer of letter history without a cause. The task
  is back in "Mijn taken" with the marker "Wachten voorbij sinds 3 dec" (6.3); the iPhone
  reminds locally (11); a notice inside Brievie is Besluit B3.

## 5. "Wacht op reactie" and "Uitgesteld tot"

Two different things, never merged (R 1 item 2):

| | Wacht op reactie | Uitgesteld tot |
|---|---|---|
| Meaning | the other party is at bat (I replied, objected, asked for a payment plan) | I pick this up later myself |
| Lives on | the task: `Task.waiting_until` (date, null) | a row per task and person: `TaskSnooze(task, user, until)` |
| Who sees it | everyone who sees the letter | only the person who snoozed |
| Effect on the letter's status | yes (4.1) | none |
| Effect on "Te laat" | suspends it while waiting | none (and cannot hide a term, see below) |
| Effect on "Mijn taken" | leaves the open groups for everyone, into "Wacht op reactie (n)" | leaves *my* open groups, into "Uitgesteld (n)" |
| Who may set it | whoever may edit the letter (role member+, `require_editable`; not through emergency access only), as `completeTask` (ruling LS4) | any member who sees the task, a viewer included (it changes nothing shared) |
| Date | after today, at most a year ahead (422 otherwise) | after today, at most a year ahead, and **not after the task's deadline** (422 `snooze_after_deadline`; Besluit B2) |
| History | `task_waiting` (with the date), `task_waiting_ended`, with actor | none: personal, not on the letter, not in the audit log |
| On completion | cleared with the completion (no own line) | the caller's and others' snooze rows of the task are deleted |
| On a merge | moves with the task | moves with the task (rows follow the task id) |
| On purge | gone with the task | gone with the task (cascade) |

- A task's waiting date may lie after its deadline (the sender may grant time; Besluit
  B2). The letter then shows the deadline as "termijn was 1 nov" in plain grey text,
  without the late marker, until the waiting ends.
- The same date again changes nothing and writes no line (as `setTaskDeadline`).
- No note with the waiting (what am I waiting for): see 13.

## 6. Where it shows

### 6.1 Under a person (web: "Alle post" with a person; iPhone: the person's scope)

- Heading: "Post van Piet". Below it one line of counts, only the non-zero ones, in this
  order: "2 nieuw, 3 openstaand, 1 te laat, 2 wachten op reactie". Counts are of what
  *this member* sees of Piet's post (an office admin with "Beheerders lezen alle post"
  off sees fewer, and nothing says how many are hidden).
- A status filter (React Aria `ToggleButtonGroup`, one choice): "Alles", "Nieuw (2)",
  "Openstaand (3)", "Wacht op reactie (2)", "Afgehandeld (14)", and a checkbox "Alleen te
  laat (1)". URL: `?persoon=…&status=nieuw|openstaand|wacht|afgehandeld&telaat=true`.
- Each row gets a status badge: "Nieuw" (info), "Openstaand" (neutral), "Wacht op reactie
  tot 3 dec" (neutral), "Afgehandeld" (success), plus "Te laat" (warning, in words, never
  colour alone).
- The same filter and badges apply to "Alle post" without a person (all post the member
  sees); the count line then reads "Je post: …".
- Order stays newest first (the 200 cap and its index); "Openstaand" sorted by deadline is
  in 13.
- "Personen": each person's name links to their post ("Post bekijken").

### 6.2 Inbox

The Inbox keeps its action badges ("Controleren", "Naam kiezen", "Bezig…"). Only rows
that are reviewed (`unassigned`) also get the status badge and "Te laat": a nameless
letter with a late payment must not look like any other. No "Wacht op reactie" list in the
Inbox: the Inbox is for intake.

### 6.3 Mijn taken

- Waiting tasks leave the date groups and the nav count, and sit in a collapsed group at
  the bottom: "Wacht op reactie (2)", each row "Wacht op reactie tot 3 dec" with "Niet
  meer wachten" (ruling LS5: collapsed, not hidden, because post has legal terms).
- My snoozed tasks leave the date groups and the count, and sit in a collapsed group
  "Uitgesteld (1)", each row "Uitgesteld tot 1 dec" with "Niet meer uitstellen".
- A task whose waiting date arrived is back in its date group with the marker "Wachten
  voorbij sinds 3 dec" until someone acts (completes it, waits again, or "Niet meer
  wachten").
- Row actions (next to the existing ones): "Wacht op reactie…" (member+), "Uitstellen…"
  (everyone who sees the row).
- Counts: `mine`, `overdue`, `within_week`, `needs_date`, `to_pick_up` leave waiting tasks
  and the caller's snoozed tasks out; new `waiting` and `snoozed`.

### 6.4 The letter

- Under the title: the status badge and, when late, "Te laat".
- Button **"Brief afhandelen"** (member+, editable, status Openstaand or Wacht op
  reactie). Dialog "Brief afhandelen?": "Deze taken worden afgevinkt:" and the list of open
  tasks (description, date, amount). When a payment is among them, a required checkbox:
  "De betaling is gedaan of geregeld" (one checkbox for all payments, each named with its
  amount above). Buttons "Afhandelen" / "Annuleren".
- Each open task: "Wacht op reactie…" / "Niet meer wachten", "Uitstellen…".
- A handled letter has no "reopen" button: "Toch niet klaar" on a task reopens it (4.3).

## 7. "Brief afhandelen": a confirmation the server checks

`handleDocument` takes the ids of the open tasks the client showed and
`payments_confirmed`:

1. The letter is loaded as the member (404 `document_not_found` like an unknown id),
   locked, `require_editable`.
2. Not reviewed → 409 `document_not_reviewed`.
3. The set of open task ids differs from `task_ids` (one was added, ticked or reopened
   meanwhile) → 409 `tasks_changed`; nothing is completed.
4. A `pay` task in the set and `payments_confirmed` false → 409 `payment_not_confirmed`.
5. Each task completed through the one completion path (`task_completed`, actor), then one
   `letter_handled` line with the actor.
6. An already handled letter with `task_ids` empty: 200, nothing written (idempotent).

So no client (web, iPhone, an old app) can tick off a payment without the server seeing
that a person confirmed it.

## 8. Visibility and privacy

- Every list, filter and count of letters starts at `visibility.visible_documents`
  (`tests/test_visibility_seams.py`). The new columns are on the letter row: who may not
  read the letter does not see its status, and no count includes it.
- `TaskSnooze`: workspace table, RLS enabled and forced; a row is visible and writable only
  to its own user, and only while that user sees the task (the task policy). Purge as the
  system cascades it.
- `letter_state.sync` reads the tasks and writes the three columns as the system (the
  caller was checked with `require_editable` first; a confirm that hands the letter away
  must still write them, as the confirm's status write does). It returns nothing to the
  caller.
- History lines follow the letter's sight (existing). Snoozes are in no history or audit
  log.
- No log line names a description, sender, amount or date of a task.

## 9. API (additive, under `/api/v1`)

| Operation | Method, path | Auth | Answer |
|---|---|---|---|
| `listDocuments` | + query `letter_status: str = ""` (`new`, `open`, `waiting`, `handled`), `overdue: bool = False` | as now | `list[DocumentSummaryOut]`; 422 `validation_error` for another status |
| `countLetterStatuses` | GET `/workspaces/{id}/documents/status-counts?person_id=` | session or device | `LetterStatusCountsOut` |
| `handleDocument` | POST `/workspaces/{id}/documents/{document_id}/handle` body `HandleDocumentIn` | member+, session or device | `DocumentOut`; 404, 409 `document_not_reviewed`, `tasks_changed`, `payment_not_confirmed` |
| `setTaskWaiting` | POST `/workspaces/{id}/tasks/{task_id}/waiting` body `{until: date \| null = null}` (null ends it) | member+ | `TaskItemOut`; 404, 409 `task_done`, 422 `validation_error` |
| `snoozeTask` | POST `/workspaces/{id}/tasks/{task_id}/snooze` body `{until: date \| null = null}` (null ends it) | any role | `TaskItemOut`; 404, 409 `task_done`, 422 `validation_error`, 422 `snooze_after_deadline` |
| `listMyTasks` | + query `include_waiting: bool = False` | as now | waiting and own snoozed rows only with it (groups `waiting`, `snoozed`) |

- `DocumentSummaryOut` (and so `DocumentOut`) + `letter_status: str = ""`, `overdue: bool
  = False`, `open_task_count: int = 0`, `waiting_until: date | None = None` (only for
  `waiting`).
- `LetterStatusCountsOut`: `new`, `open`, `waiting`, `handled`, `overdue` (ints).
  `person_id` unknown or archived: the counts of nothing (zeros), not a 404 (a person list
  is a directory).
- `HandleDocumentIn`: `task_ids: list[UUID] = []`, `payments_confirmed: bool = False`.
- `TaskItemOut` and `TaskOut` (on a letter) + `waiting_until: date | None = None`,
  `wait_over: bool = False` (waiting date today or past, still set), `snoozed_until: date
  | None = None` (the caller's own, only while after today). `group` gains `waiting` and
  `snoozed` (only with `include_waiting`).
- `MyTaskCountsOut` + `waiting: int = 0`, `snoozed: int = 0`.
- `DocumentEventOut` + `on_date: date | None = None`; kinds gain `task_waiting`,
  `task_waiting_ended`, `letter_handled`, `letter_reopened` (clients skip unknown kinds).
- Why `include_waiting` defaults to false: the iPhone app plans its reminders and calendar
  from `listMyTasks`; an older app then simply stops reminding of waiting and snoozed
  tasks, and never meets an unknown `group` value.
- Performance: `listDocuments` with `letter_status`, `countLetterStatuses` (with and
  without a person), `listMyTasks` and `countMyTasks` join the `visibility-perf` job:
  median <= 250 ms at 100,000 letters as a plain member, a department member, a sorter and
  an admin, E on and off. If `handled` in `countLetterStatuses` does not fit, it is
  dropped from the count (the filter stays) and the web shows "Afgehandeld" without a
  number (ruling LS2).

## 10. Model changes

- `Document`: `task_open_count` (PositiveIntegerField, default 0), `task_wait_until`
  (DateField, null), `task_overdue_from` (DateField, null); partial index
  `documents_document_open_tasks`. Backfill in the migration as the system per workspace
  (as `0014_document_has_automatic_link`), reversible (drop the columns).
- `DocumentEvent.on_date` (DateField, null); kinds `task_waiting`, `task_waiting_ended`,
  `letter_handled`, `letter_reopened`.
- `Task.waiting_until` (DateField, null).
- New `TaskSnooze(WorkspaceModel)`: `task` (FK, CASCADE), `user` (FK, CASCADE), `until`
  (date); unique (`task`, `user`); RLS enabled and forced, policy "own rows of visible
  tasks".
- Lock order for every task change: the letter's row first (`FOR NO KEY UPDATE`), then the
  task, as the confirm and the merge already do; `tasks.services._get_for_update` changes
  accordingly.

## 11. iOS (planned in `Brievie_ios`)

- Sync the API; decode the new fields (all defaulted); add `countLetterStatuses`,
  `handleDocument`, `setTaskWaiting`, `snoozeTask` to the generator config.
- **Person scope:** status badge and "Te laat" per row; a status filter (segmented
  control: Alles, Nieuw, Openstaand, Wacht, Afgehandeld) and the count line of 6.1.
- **Letter:** status under the title; "Brief afhandelen" opens a sheet listing the tasks
  it closes, the payment checkbox, and sends `task_ids` (handles 409 `tasks_changed` by
  reloading the sheet).
- **Task row** (letter and Mijn taken): swipe or menu "Wacht op reactie…" (date picker,
  hint text of 12) and "Uitstellen…" (date picker capped at the deadline); the collapsed
  groups of 6.3 (`include_waiting=true`).
- **Reminders:** `ReminderPlanner` skips waiting and snoozed tasks (the default
  `listMyTasks` leaves them out) and plans one local reminder at 09:00 on a task's
  `waiting_until`: title "Wachten voorbij", body the task type only (no letter text unless
  "toon afzender"), same rule as today's reminders.
- An older server (no `letter_status`): the app shows no status, as now.

## 12. Texts (Dutch B1 source, English)

| Dutch | English | Where |
|---|---|---|
| Nieuw | New | status |
| Openstaand | Open | status |
| Wacht op reactie | Waiting for a reply | status |
| Wacht op reactie tot 3 dec | Waiting for a reply until 3 Dec | badge |
| Afgehandeld | Handled | status |
| Te laat | Overdue | marker (glossary already uses "overdue" for the group) |
| Post van Piet | Piet's post | heading |
| 3 openstaand, 1 te laat, 2 wachten op reactie | 3 open, 1 overdue, 2 waiting for a reply | count line (Lingui plurals) |
| Je post | Your post | count line without a person |
| Alleen te laat | Overdue only | filter |
| Brief afhandelen | Mark letter as handled | button |
| Brief afhandelen? | Mark this letter as handled? | dialog title |
| Deze taken worden afgevinkt: | These tasks will be ticked off: | dialog |
| De betaling is gedaan of geregeld | The payment has been made or arranged | required checkbox |
| Afhandelen | Mark as handled | dialog button |
| Wacht op reactie… | Waiting for a reply… | task action |
| Tot wanneer wacht je op een reactie? | Until when are you waiting for a reply? | dialog |
| Op die dag komt de taak terug in Mijn taken. Iedereen die deze brief ziet, ziet dat je wacht. | On that day the task comes back in My tasks. Everyone who sees this letter sees that you are waiting. | dialog hint |
| Niet meer wachten | Stop waiting | task action |
| Wachten voorbij sinds 3 dec | Waiting ended on 3 Dec | marker |
| termijn was 1 nov | deadline was 1 Nov | waiting task past its deadline |
| Uitstellen… | Snooze… | task action |
| Tot wanneer stel je dit uit? | Until when do you want to snooze this? | dialog |
| Alleen jij ziet dat je dit uitstelt. Op die dag staat de taak weer in je lijst. Uitstellen kan tot de datum van de taak. | Only you see that you snoozed this. On that day the task is back in your list. You can snooze until the task's date. | dialog hint |
| Uitgesteld tot 1 dec | Snoozed until 1 Dec | badge |
| Niet meer uitstellen | Unsnooze | action |
| Wacht op reactie (2) / Uitgesteld (1) | Waiting for a reply (2) / Snoozed (1) | Mijn taken groups |
| Post bekijken | View post | Personen |
| Geen brieven met deze status. | No letters with this status. | empty filter |
| *History* | | |
| Anne heeft de brief afgehandeld. | Anne marked the letter as handled. | `letter_handled`, user |
| De brief is afgehandeld: er staat geen taak meer open. | The letter is handled: no task is open any more. | `letter_handled`, system |
| De brief staat weer open: er staat weer een taak open. | The letter is open again: a task is open again. | `letter_reopened`, system |
| Anne wacht op een reactie tot 3 december: Betaal de aanslag. | Anne is waiting for a reply until 3 December: Pay the assessment. | `task_waiting` |
| Anne wacht niet meer op een reactie: Betaal de aanslag. | Anne stopped waiting for a reply: Pay the assessment. | `task_waiting_ended` |
| *Errors* | | |
| Deze brief is nog niet nagekeken. | This letter has not been checked yet. | `document_not_reviewed` |
| De taken van deze brief zijn net veranderd. Bekijk ze opnieuw. | The tasks of this letter just changed. Please look at them again. | `tasks_changed` |
| Vink aan dat de betaling is gedaan of geregeld. | Tick that the payment has been made or arranged. | `payment_not_confirmed` |
| Deze taak is al afgerond. | This task is already done. | `task_done` |
| Kies een datum na vandaag, binnen een jaar. | Choose a date after today, within a year. | `validation_error` |
| Je kunt niet uitstellen tot na de datum van de taak. | You cannot snooze past the task's date. | `snooze_after_deadline` |
| Deze status kennen we niet. | We do not know this status. | `validation_error` |

New glossary lines: Briefstatus → letter status (`letter_status`); Openstaand → open;
Wacht op reactie → waiting (`waiting_until`); Afgehandeld → handled; Te laat → overdue
(marker); Uitgesteld → snoozed (`TaskSnooze`); Brief afhandelen → handle a letter
(`handleDocument`).

## 13. Not in this design

- **Delegation layers** (zien, behandelen, behandelen namens, "X namens Y" in the log):
  its own design on top of the rights model.
- **A substitute per person** (vervanger with an end date): needs the delegation layers.
- **iCal feed per person**: a separate outgoing integration; it would read "Mijn taken".
- **Forwarding a letter by e-mail**: needs outgoing mail (SMTP), not built.
- A note with "wacht op reactie" (what am I waiting for): free text on a shared task; add
  when asked.
- "Openstaand" sorted by deadline under a person: newest first in v1.
- A dossier that proposes to reopen when new post about it arrives (R 1 item 4): Brievie
  has no dossiers.
- **Deliberately never** (R 1 section 4): SLA clocks, team queues, custom statuses, round
  robin, "inloggen als", automatic closing after a waiting time.

## 14. Rulings (calls made on the owner's behalf)

| # | Ruling | Why | Cost if wrong |
|---|---|---|---|
| LS1 | Status derived at read time from three date-free task facts cached on the letter by one writer, repaired nightly | a status filter before the cap and counts per person stay on indexes; the per-row task policy is too slow (measured precedent) | three columns to keep right; a source test and the nightly repair guard them |
| LS2 | If `handled` in the counts misses 250 ms, it is dropped from the count only | Afgehandeld is most post; its number matters least | "Afgehandeld" shows without a number |
| LS3 | "Te laat" is a marker; a waiting task is not late | R 1; waiting means the other party has the next move | a late payment while waiting is not flagged; its deadline stays visible in grey |
| LS4 | Whoever may edit the letter may set "wacht op reactie", like ticking off | one rule for all task changes | a colleague parks someone else's task; the history names who |
| LS5 | Waiting tasks in a collapsed group in "Mijn taken", not hidden | post has legal terms; out of the count, still in sight | a slightly longer page |
| LS6 | "Brief afhandelen" sends the task ids and a payment confirmation; the server refuses a mismatch | the confirmation is real for every client | one extra 409 path |
| LS7 | No job and no history line when a waiting date arrives | nothing changed; the line holds the date | no trace of the moment; the app reminds |
| LS8 | Lines `letter_handled` / `letter_reopened` only for changes into or out of Afgehandeld | the history stays readable; waiting has its own task lines | none known |

## 15. Besluiten voor Stijn

Only questions whose answer changes what gets built.

- **B1. Een brief zonder taken: na nakijken meteen Afgehandeld?** Default (built unless
  you say otherwise): yes, like archiving a mail; reclame and informatieve brieven leave
  every open list at once. Alternative: it stays "Openstaand" until someone presses "Brief
  afhandelen". That adds a stored `handled_at` with its own history and a "Weer
  openzetten" button, and makes the status partly something a person sets.
- **B2. Mag wachten of uitstellen voorbij de termijn van de taak?** Default: *wachten*
  mag (de afzender kan uitstel geven; de termijn blijft grijs zichtbaar), *uitstellen*
  niet (je eigen lijst mag nooit een termijn verbergen; 422 `snooze_after_deadline`).
  Alternatives: both capped at the deadline, or both free.
- **B3. Een melding in Brievie zelf als het wachten voorbij is?** Default: no; the task
  comes back in "Mijn taken" with "Wachten voorbij", and the iPhone reminds locally.
  Alternative: a nightly job leaves a notice (new notice code) for the assignee, else who
  set the wait; web users then see it without the app.
